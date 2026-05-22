"""Integration tests: credentials reach the agent container as files
under /run/secrets, never as environment variables (task_02_08).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from workers.config import Settings
from workers.container import AgentContainerRunner, ContainerSpec
from workers.secrets import SECRETS_DIR, StaticSecretsProvider, stage_secrets

from ._docker_helpers import (
    BASE_IMAGE,
    docker_client,
    ensure_base_image,
    last_json_line,
    requires_docker,
)

pytestmark = [pytest.mark.integration, requires_docker]

# Reads everything under /run/secrets and reports it together with the
# container's environment variable names.
_READ_SECRETS = r"""
import json, os

directory = "/run/secrets"
files = sorted(os.listdir(directory)) if os.path.isdir(directory) else []
values = {}
for name in files:
    with open(os.path.join(directory, name)) as handle:
        values[name] = handle.read()

print(json.dumps({
    "files": files,
    "values": values,
    "env_keys": sorted(os.environ.keys()),
}))
"""

# Tries to write into the secret mount — must fail (mounted read-only).
_WRITE_SECRET = r"""
import json

read_only = False
try:
    with open("/run/secrets/token", "w") as handle:
        handle.write("tampered")
except OSError:
    read_only = True

print(json.dumps({"read_only": read_only}))
"""


@pytest.fixture(scope="module", autouse=True)
def _base_image() -> None:
    client = docker_client()
    try:
        ensure_base_image(client)
    finally:
        client.close()


def _run(spec: ContainerSpec) -> object:
    return AgentContainerRunner(Settings()).run(spec)


def test_secret_is_readable_under_run_secrets(tmp_path: Path) -> None:
    with stage_secrets({"openai_api_key": "sk-test-abc123"}, base_dir=str(tmp_path)) as staged:
        result = _run(
            ContainerSpec(
                image=BASE_IMAGE,
                command=["python", "-c", _READ_SECRETS],
                extra_mounts=tuple(staged.mounts),
            )
        )
        assert result.exit_code == 0, result.logs
        report = last_json_line(result.logs)
        assert "openai_api_key" in report["files"]
        assert report["values"]["openai_api_key"] == "sk-test-abc123"


def test_secret_value_never_enters_the_environment(tmp_path: Path) -> None:
    secret = "sk-super-secret-xyz"
    with stage_secrets({"api_key": secret}, base_dir=str(tmp_path)) as staged:
        result = _run(
            ContainerSpec(
                image=BASE_IMAGE,
                command=["python", "-c", _READ_SECRETS],
                extra_mounts=tuple(staged.mounts),
            )
        )
        assert result.exit_code == 0, result.logs
        # Not in the container's declared environment ...
        assert secret not in " ".join(result.config_env)
        # ... and no env var even carries the secret's name.
        assert "api_key" not in last_json_line(result.logs)["env_keys"]


def test_secret_mount_is_read_only(tmp_path: Path) -> None:
    with stage_secrets({"token": "t0ken"}, base_dir=str(tmp_path)) as staged:
        result = _run(
            ContainerSpec(
                image=BASE_IMAGE,
                command=["python", "-c", _WRITE_SECRET],
                extra_mounts=tuple(staged.mounts),
            )
        )
        assert result.exit_code == 0, result.logs
        assert last_json_line(result.logs)["read_only"] is True


def test_mount_targets_the_conventional_secrets_dir(tmp_path: Path) -> None:
    staged = stage_secrets({"k": "v"}, base_dir=str(tmp_path))
    try:
        assert len(staged.mounts) == 1
        assert staged.mounts[0]["Target"] == SECRETS_DIR
        assert staged.mounts[0]["ReadOnly"] is True
    finally:
        staged.cleanup()


def test_staged_files_are_not_writable_and_cleanup_removes_them(tmp_path: Path) -> None:
    staged = stage_secrets({"k": "v"}, base_dir=str(tmp_path))
    secret_file = staged.staging_dir / "k"
    assert secret_file.read_text() == "v"
    if os.name != "nt":  # POSIX permission bits are meaningful here
        assert (secret_file.stat().st_mode & 0o222) == 0

    staged.cleanup()
    assert not staged.staging_dir.exists()


def test_invalid_secret_name_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="invalid secret name"):
        stage_secrets({"../escape": "v"}, base_dir=str(tmp_path))


def test_static_provider_returns_only_requested_keys() -> None:
    provider = StaticSecretsProvider({"a": "1", "b": "2", "c": "3"})
    assert provider.fetch(["a", "c"]) == {"a": "1", "c": "3"}
