"""Unit tests for the real install StepExecutor (Plan prod-01 task_16 / deploy-1).

`RealStepExecutor` is the binding that turns the install pipeline from a
simulacrum into a real provisioner. Driven here against in-memory fakes
(FakeCommandRunner / FakeVaultClient / FakeEnvFileWriter / FakeDataTreeProvisioner)
so the ORCHESTRATION — which files are written, the docker compose argv + order,
fail propagation, Vault bootstrap, the seed — is verified WITHOUT a Docker host.
The real subprocess/hvac calls are exercised only by the e2e / human tests.
"""

from __future__ import annotations

import pytest
from installer_backend.command_runner import FakeCommandRunner
from installer_backend.compose_generator import PROJECT_NAME
from installer_backend.config import InstallerConfig
from installer_backend.config_generators import (
    FakeDataTreeProvisioner,
    FakeEnvFileWriter,
    GeneratedSecrets,
)
from installer_backend.install import InstallStep, StepExecutionError, StepExecutor
from installer_backend.real_step_executor import RealStepExecutor
from installer_backend.vault_bootstrap import FakeVaultClient

pytestmark = pytest.mark.unit

_COMPOSE_DIR = "/srv/agentic"
_COMPOSE_FILE = f"{_COMPOSE_DIR}/docker-compose.yml"


def _executor(
    cfg: InstallerConfig,
    secrets: GeneratedSecrets,
    *,
    runner: FakeCommandRunner | None = None,
    vault: FakeVaultClient | None = None,
) -> tuple[RealStepExecutor, FakeCommandRunner, FakeEnvFileWriter, FakeDataTreeProvisioner]:
    runner = runner or FakeCommandRunner()
    writer = FakeEnvFileWriter()
    tree = FakeDataTreeProvisioner()
    vault_client = vault or FakeVaultClient()
    ex = RealStepExecutor(
        compose_dir=_COMPOSE_DIR,
        runner=runner,
        env_writer=writer,
        tree=tree,
        vault_client_factory=lambda _cfg: vault_client,
        cfg=cfg,
        secrets=secrets,
    )
    return ex, runner, writer, tree


def test_real_executor_satisfies_the_step_executor_protocol(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    ex, *_ = _executor(installer_config, gen_secrets)
    assert isinstance(ex, StepExecutor)


def test_generate_config_writes_the_four_files_with_modes(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    ex, _runner, writer, tree = _executor(installer_config, gen_secrets)
    ex.execute(InstallStep.GENERATE_CONFIG, {})

    assert writer.modes[f"{_COMPOSE_DIR}/docker-compose.yml"] == 0o640
    assert writer.modes[f"{_COMPOSE_DIR}/.env"] == 0o600
    assert writer.modes[f"{_COMPOSE_DIR}/config/global.yaml"] == 0o640
    assert writer.modes[f"{_COMPOSE_DIR}/caddy/Caddyfile"] == 0o644
    # The Caddyfile must exist before `up` (the compose bind-mounts it).
    assert "reverse_proxy admin-panel:3000" in writer.written[f"{_COMPOSE_DIR}/caddy/Caddyfile"]
    # The /data tree was provisioned.
    assert tree.provisioned, "data tree was not provisioned"


def test_generate_config_env_carries_no_dev_secret_marker_in_prod(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    ex, _runner, writer, _tree = _executor(installer_config, gen_secrets)
    ex.execute(InstallStep.GENERATE_CONFIG, {})
    env_text = writer.written[f"{_COMPOSE_DIR}/.env"]
    # Production .env must not carry a ${VAR:-default} dev marker.
    assert ":-" not in env_text


def test_docker_steps_issue_expected_argv_in_order(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    ex, runner, _writer, _tree = _executor(installer_config, gen_secrets)
    ex.execute(InstallStep.PULL_IMAGES, {})
    ex.execute(InstallStep.START_STACK, {})
    ex.execute(InstallStep.RUN_MIGRATIONS, {})

    prefix = ("docker", "compose", "-p", PROJECT_NAME, "-f", _COMPOSE_FILE)
    assert runner.calls[0] == (*prefix, "pull")
    assert runner.calls[1] == (*prefix, "up", "-d", "--wait")
    assert runner.calls[2] == (*prefix, "run", "--rm", "migrations")
    # All ran with cwd == compose_dir.
    assert runner.cwds == [_COMPOSE_DIR, _COMPOSE_DIR, _COMPOSE_DIR]


def test_a_failing_docker_step_raises_step_execution_error(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    runner = FakeCommandRunner(
        fail_on=("docker", "compose", "-p", PROJECT_NAME, "-f", _COMPOSE_FILE, "up")
    )
    ex, _runner, _writer, _tree = _executor(installer_config, gen_secrets, runner=runner)
    with pytest.raises(StepExecutionError):
        ex.execute(InstallStep.START_STACK, {})


def test_bootstrap_vault_runs_the_orchestration_and_captures_init(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    vault = FakeVaultClient()
    ex, *_ = _executor(installer_config, gen_secrets, vault=vault)
    ex.execute(InstallStep.BOOTSTRAP_VAULT, {})

    assert ex.vault_bootstrap_result is not None
    assert ex.vault_bootstrap_result.init is not None  # fresh init
    assert ex.vault_bootstrap_result.init.root_token == "fake-root-token"
    assert ex.vault_bootstrap_result.kv_enabled is True


def test_seed_tenant_captures_password_and_never_puts_it_in_argv(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    ex, runner, _writer, _tree = _executor(installer_config, gen_secrets)
    ex.execute(InstallStep.SEED_TENANT, {})

    assert ex.seeded_admin_password, "the seed must generate an admin password"
    pw = ex.seeded_admin_password
    # The password is passed via env pass-through, NEVER on the command line.
    for argv in runner.calls:
        assert pw not in argv, "admin password leaked into argv"
    # The init_tenant entrypoint was invoked.
    seed_call = runner.calls[-1]
    assert "api_server.seeds.init_tenant" in seed_call
    assert "INIT_ADMIN_PASSWORD" in seed_call  # the -e pass-through flag (no value)


def test_no_step_returns_a_log_line_containing_a_secret(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    ex, *_ = _executor(installer_config, gen_secrets)
    lines: list[str] = []
    lines += ex.execute(InstallStep.GENERATE_CONFIG, {})
    lines += ex.execute(InstallStep.BOOTSTRAP_VAULT, {})
    lines += ex.execute(InstallStep.SEED_TENANT, {})
    blob = "\n".join(lines)
    assert "fake-root-token" not in blob
    assert (ex.seeded_admin_password or "x") not in blob
