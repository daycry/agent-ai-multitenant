"""Unit tests for the real CredentialBuilder (Plan prod-01 task_17 / secrets-1).

`RealCredentialBuilder` reads the credentials a `RealStepExecutor` captured
during the install (Vault init + the seeded admin password) and packs them into
the one-time `InstallCredentials` reveal — without minting anything itself and
without persisting any secret in the repo tree.
"""

from __future__ import annotations

import pytest
from installer_backend.cli import CliError, ExitCode, RealCredentialBuilder
from installer_backend.command_runner import FakeCommandRunner
from installer_backend.config import InstallerConfig
from installer_backend.config_generators import (
    FakeDataTreeProvisioner,
    FakeEnvFileWriter,
    GeneratedSecrets,
)
from installer_backend.install import InstallStep
from installer_backend.real_step_executor import RealStepExecutor
from installer_backend.vault_bootstrap import FakeVaultClient

pytestmark = pytest.mark.unit


def _executor(cfg: InstallerConfig, secrets: GeneratedSecrets) -> RealStepExecutor:
    return RealStepExecutor(
        compose_dir="/srv/agentic",
        runner=FakeCommandRunner(),
        env_writer=FakeEnvFileWriter(),
        tree=FakeDataTreeProvisioner(),
        vault_client_factory=lambda _cfg: FakeVaultClient(),
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
    assert creds.admin_password == ex.seeded_admin_password
    assert creds.vault_root_token == "fake-root-token"
    assert len(creds.vault_unseal_keys) == 5


def test_build_repr_is_redacted(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    ex = _executor(installer_config, gen_secrets)
    ex.execute(InstallStep.BOOTSTRAP_VAULT, {})
    ex.execute(InstallStep.SEED_TENANT, {})
    creds = RealCredentialBuilder(ex).build(installer_config)
    assert "fake-root-token" not in repr(creds)
    assert "redacted" in repr(creds).lower()


def test_build_fails_loud_when_bootstrap_or_seed_did_not_run(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    ex = _executor(installer_config, gen_secrets)
    # Neither BOOTSTRAP_VAULT nor SEED_TENANT ran → no real credentials.
    with pytest.raises(CliError) as excinfo:
        RealCredentialBuilder(ex).build(installer_config)
    assert excinfo.value.code is ExitCode.PROVISION
