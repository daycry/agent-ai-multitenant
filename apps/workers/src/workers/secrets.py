"""Credential injection for agent-runtime containers (task_02_08).

Agents never receive credentials in their environment — env vars leak
into `docker inspect`, crash dumps, child processes and logs. Instead
each secret is materialised as a file on the worker host and bind-
mounted **read-only** at /run/secrets/<name> inside the container,
exactly like a Docker Compose `secrets:` entry. The agent reads the
file; the worker wipes the staging area the moment the container exits.

    Vault  →  staged secret file (worker host)  →  read-only bind mount
           →  /run/secrets/<name>  inside the container.

Plan 02 Fase B wires the file → /run/secrets stage and a static
provider for tests. `SecretsProvider` is the seam a Vault-backed
provider plugs into; binding it to a live Vault is a later hardening
pass (the Vault service already runs in docker-compose).

Host-side note: the staging directory is a per-launch `mkdtemp` and is
removed on cleanup. Files are written 0444 (read-only) and the directory
0755 so the container's non-root uid 1000 — which differs from the
worker's uid — can still read them, mirroring Compose's own secret
semantics. The platform is a single-machine Docker Compose deployment
(CLAUDE.md): the trust boundary is the container and the network, not
other host users.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from docker.types import Mount

# Where Docker Compose / Swarm conventionally expose secrets.
SECRETS_DIR = "/run/secrets"

# A secret name must be a single safe path component: it becomes a file
# name under the staging dir. Must start with an alphanumeric / _ / - so
# "..", "../escape" and dotfiles are rejected.
_VALID_NAME = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*")


class SecretsProvider(Protocol):
    """Source of secret values, keyed by name. A Vault-backed
    implementation plugs in here without touching the staging code."""

    def fetch(self, keys: Sequence[str]) -> dict[str, str]: ...


@dataclass(frozen=True)
class StaticSecretsProvider:
    """In-memory provider — used by tests and local development."""

    values: dict[str, str] = field(default_factory=dict)

    def fetch(self, keys: Sequence[str]) -> dict[str, str]:
        return {key: self.values[key] for key in keys}


@dataclass
class StagedSecrets:
    """A staged set of secrets: the bind mounts to hand to the runner,
    and the on-disk staging directory to remove afterwards."""

    mounts: list[Any]  # docker.types.Mount
    staging_dir: Path

    def cleanup(self) -> None:
        """Remove the staging directory and every secret it holds.

        Files are written read-only; chmod them back before unlink so
        cleanup works on Windows too (read-only files block deletion)."""
        for child in self.staging_dir.glob("*"):
            with contextlib.suppress(OSError):
                child.chmod(0o600)
        shutil.rmtree(self.staging_dir, ignore_errors=True)

    def __enter__(self) -> StagedSecrets:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.cleanup()


def _validate_name(name: str) -> None:
    if not name or len(name) > 255 or not _VALID_NAME.fullmatch(name):
        raise ValueError(f"invalid secret name: {name!r}")


def stage_secrets(
    secrets: Mapping[str, str],
    *,
    base_dir: str | None = None,
) -> StagedSecrets:
    """Write `secrets` to a private staging directory and return the
    read-only bind mount that exposes them at /run/secrets.

    `base_dir` overrides where the staging directory is created (tests
    point it at a tmp_path).
    """
    # Validate every name before touching disk — a bad name must not
    # leave a half-built staging directory behind.
    for name in secrets:
        _validate_name(name)

    staging = Path(tempfile.mkdtemp(prefix="agent-secrets-", dir=base_dir))
    # 0755 so the container's uid 1000 can traverse + list the mount;
    # the random mkdtemp name is the host-side obscurity.
    os.chmod(staging, 0o755)

    for name, value in secrets.items():
        secret_file = staging / name
        secret_file.write_text(value, encoding="utf-8")
        os.chmod(secret_file, 0o444)

    mount = Mount(target=SECRETS_DIR, source=str(staging), type="bind", read_only=True)
    return StagedSecrets(mounts=[mount], staging_dir=staging)
