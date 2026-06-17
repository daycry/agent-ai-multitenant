"""The no-silent-stubs guard (Plan prod-01 task_19 / deploy-1).

A simulation-wired install/uninstall must NEVER run silently as if it were real.
Without ``--dry-run`` the CLI aborts with a clear error; with ``--dry-run`` it
runs the simulation behind an explicit banner. The default (no ``--dry-run``)
wires the REAL host bindings.
"""

from __future__ import annotations

import io

import pytest
from installer_backend.cli import (
    CliError,
    ExitCode,
    StubPrereqChecker,
    _assert_real_install_seams,
    build_default_installer,
    run_install,
)
from installer_backend.config import InstallerConfig
from installer_backend.real_step_executor import RealStepExecutor

pytestmark = pytest.mark.unit

_VALID_YAML = """\
system:
  domain: agentic.example.com
  environment: production
resources:
  worker_replicas: 2
  worker_memory_gib: 4
  gpu_enabled: false
  embedding_model: nomic-embed-text
storage:
  data_root: /data/agent-platform
  minio_bucket: agentic-platform
  minio_access_key: throwaway-access
  minio_secret_key: throwaway-secret-value-123
providers:
  ollama:
    enabled: true
    endpoint: http://o:11434
tenant:
  tenant_name: Acme Corp
  admin_email: admin@acme.com
"""


def test_guard_aborts_simulation_without_dry_run(installer_config: InstallerConfig) -> None:
    sim = build_default_installer(io.StringIO(), installer_config, dry_run=True)
    with pytest.raises(CliError) as excinfo:
        _assert_real_install_seams(sim, dry_run=False)
    assert excinfo.value.code is ExitCode.PROVISION
    assert "--dry-run" in str(excinfo.value)


def test_guard_is_a_noop_under_dry_run(installer_config: InstallerConfig) -> None:
    sim = build_default_installer(io.StringIO(), installer_config, dry_run=True)
    _assert_real_install_seams(sim, dry_run=True)  # no raise


def test_default_installer_wires_real_seams(installer_config: InstallerConfig) -> None:
    inst = build_default_installer(io.StringIO(), installer_config, dry_run=False)
    assert isinstance(inst.executor, RealStepExecutor)
    assert not isinstance(inst.prereq_checker, StubPrereqChecker)
    _assert_real_install_seams(inst, dry_run=False)  # no raise


def test_run_install_aborts_when_simulation_seams_without_dry_run(
    tmp_path, installer_config: InstallerConfig
) -> None:
    cfg_path = tmp_path / "install.yaml"
    cfg_path.write_text(_VALID_YAML, encoding="utf-8")
    sim = build_default_installer(io.StringIO(), installer_config, dry_run=True)
    with pytest.raises(CliError) as excinfo:
        run_install(str(cfg_path), installer=sim, dry_run=False, out=io.StringIO())
    assert excinfo.value.code is ExitCode.PROVISION


def test_run_install_dry_run_prints_banner_and_succeeds(
    tmp_path, installer_config: InstallerConfig
) -> None:
    cfg_path = tmp_path / "install.yaml"
    cfg_path.write_text(_VALID_YAML, encoding="utf-8")
    out = io.StringIO()
    sim = build_default_installer(out, installer_config, dry_run=True)
    code = run_install(str(cfg_path), installer=sim, dry_run=True, out=out)
    assert int(code) == int(ExitCode.OK)
    assert "SIMULACIÓN" in out.getvalue()
