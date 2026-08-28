"""Unit tests for the real CredentialBuilder (Plan prod-01 task_17 / secrets-1).

`RealCredentialBuilder` reads the credentials a `RealStepExecutor` captured
during the install and packs them into the one-time `InstallCredentials` reveal —
without minting anything itself and without persisting any secret in the repo
tree.

Lo que cambió con el ADR 0161 es de dónde salen esas credenciales: ya no las
mintea el instalador contra un Vault del host, sino el one-shot `bootstrap` que
corre dentro de la red del stack, y el ejecutor las LEE de su stdout. Aquí se
guioniza esa línea de revelado; el constructor de credenciales no ha cambiado, y
que siga en verde es justo lo que interesa comprobar.
"""

from __future__ import annotations

import json

import pytest
from installer_backend.cli import CliError, ExitCode, RealCredentialBuilder
from installer_backend.command_runner import CommandResult, FakeCommandRunner
from installer_backend.compose_generator import BOOTSTRAP_SERVICE, PROJECT_NAME
from installer_backend.config import InstallerConfig
from installer_backend.config_generators import (
    FakeDataTreeProvisioner,
    FakeEnvFileWriter,
    GeneratedSecrets,
)
from installer_backend.install import InstallStep
from installer_backend.real_step_executor import BOOTSTRAP_REVEAL_EVENT, RealStepExecutor

pytestmark = pytest.mark.unit

_COMPOSE_DIR = "/srv/agentic"
_ROOT_TOKEN = "hvs.token-de-mentira-para-tests"
_ADMIN_PASSWORD = "contrasena-de-mentira"
_UNSEAL_KEYS = tuple(f"share-de-mentira-{i}" for i in range(1, 6))

_BOOTSTRAP_ARGV = (
    "docker",
    "compose",
    "-p",
    PROJECT_NAME,
    "-f",
    f"{_COMPOSE_DIR}/docker-compose.yml",
    "run",
    "--rm",
    BOOTSTRAP_SERVICE,
)


def _bootstrap_runner() -> FakeCommandRunner:
    """El one-shot, con su línea de revelado tal y como sale de Compose."""

    payload = {
        "event": BOOTSTRAP_REVEAL_EVENT,
        "already_initialized": False,
        "unseal_keys": list(_UNSEAL_KEYS),
        "root_token": _ROOT_TOKEN,
        "key_threshold": 3,
        "kv_mount": "secret",
        "kv_enabled": True,
        "policies_written": ["api-server", "workers"],
        "admin_password": _ADMIN_PASSWORD,
        "admin_user_created": True,
    }
    line = "bootstrap-1  | " + json.dumps(payload)
    return FakeCommandRunner(responses={_BOOTSTRAP_ARGV: CommandResult(0, (line,))})


def _executor(cfg: InstallerConfig, secrets: GeneratedSecrets) -> RealStepExecutor:
    return RealStepExecutor(
        compose_dir=_COMPOSE_DIR,
        runner=_bootstrap_runner(),
        env_writer=FakeEnvFileWriter(),
        tree=FakeDataTreeProvisioner(),
        cfg=cfg,
        secrets=secrets,
    )


def test_build_returns_real_vault_init_and_seeded_password(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    ex = _executor(installer_config, gen_secrets)
    ex.execute(InstallStep.BOOTSTRAP_VAULT, {})
    ex.execute(InstallStep.SEED_TENANT, {})

    creds = RealCredentialBuilder(ex).build(installer_config)

    assert creds.admin_username == "admin@acme.com"
    assert creds.admin_password == ex.seeded_admin_password == _ADMIN_PASSWORD
    assert creds.vault_root_token == _ROOT_TOKEN
    assert creds.vault_unseal_keys == _UNSEAL_KEYS


def test_build_repr_is_redacted(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    ex = _executor(installer_config, gen_secrets)
    ex.execute(InstallStep.BOOTSTRAP_VAULT, {})
    ex.execute(InstallStep.SEED_TENANT, {})
    creds = RealCredentialBuilder(ex).build(installer_config)
    assert _ROOT_TOKEN not in repr(creds)
    assert "redacted" in repr(creds).lower()


def test_build_fails_loud_when_bootstrap_or_seed_did_not_run(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    ex = _executor(installer_config, gen_secrets)
    # Neither BOOTSTRAP_VAULT nor SEED_TENANT ran → no real credentials.
    with pytest.raises(CliError) as excinfo:
        RealCredentialBuilder(ex).build(installer_config)
    assert excinfo.value.code is ExitCode.PROVISION
