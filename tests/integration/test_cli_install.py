"""Unattended CLI install — install.sh / python -m installer_backend.cli (task_15_10).

Exercises the headless install orchestration (:mod:`installer_backend.cli`) with
EVERY host-touching action MOCKED behind the same injectable seams the wizard
uses (prereq checker, the install :class:`StepExecutor`, the finalize
self-destruct lifecycle, the credential builder) — NO real ``docker compose``,
NO real Vault, NO writes to ``/data``. The real bindings are exercised only by
the plan's Tests Humanos (``human_15_02``: "Modo CLI desatendido funciona").

Coverage (per the task contract):
  * the CLI parses ``install.yaml`` into the validated InstallerConfig;
  * it runs the orchestration steps IN ORDER against the mock executor;
  * a prereq FAIL aborts with a non-zero exit BEFORE any provisioning runs;
  * a malformed config is rejected (non-zero exit, no provisioning);
  * the headless run produces the SAME step pipeline as the wizard
    (``installer_backend.install.INSTALL_STEP_ORDER`` / the SSE stream).
"""

from __future__ import annotations

import io
import logging

import pytest
import yaml
from fastapi.testclient import TestClient
from installer_backend.cli import (
    ExitCode,
    HeadlessInstaller,
    StubCredentialBuilder,
    build_default_installer,
    headless_pipeline,
    load_install_config,
    main,
    run_install,
)
from installer_backend.config import InstallerConfig
from installer_backend.finalize import FinalizeService
from installer_backend.install import (
    INSTALL_STEP_ORDER,
    InstallStep,
    StepExecutionError,
    StepExecutor,
)
from installer_backend.main import create_app, get_step_executor
from installer_backend.seams import (
    PrereqChecker,
    PrereqResult,
    PrereqStatus,
    StubInstallerLifecycle,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# A valid install.yaml — throwaway placeholder secrets, nothing real committed.
# ---------------------------------------------------------------------------
_VALID_CONFIG: dict[str, object] = {
    "system": {"domain": "agentic.example.com", "environment": "production"},
    "resources": {"worker_replicas": 2, "worker_memory_gib": 4, "gpu_enabled": False},
    "storage": {
        "data_root": "/data/agent-platform",
        "minio_bucket": "agentic-platform",
        "minio_access_key": "throwaway-access",
        "minio_secret_key": "throwaway-secret-value-123",
    },
    "providers": {"ollama": {"enabled": True, "endpoint": "http://ollama:11434"}},
    "tenant": {"tenant_name": "Acme", "admin_email": "admin@acme.com"},
}


def _valid_yaml() -> str:
    return yaml.safe_dump(_VALID_CONFIG, sort_keys=False)


# ---------------------------------------------------------------------------
# Recording fakes for the seams.
# ---------------------------------------------------------------------------
class _RecordingExecutor:
    """A :class:`StepExecutor` that records the order steps were executed in."""

    def __init__(self, fail_at: InstallStep | None = None) -> None:
        self.executed: list[InstallStep] = []
        self.fail_at = fail_at

    def execute(self, step: InstallStep, config: dict[str, object]) -> list[str]:
        self.executed.append(step)
        if self.fail_at is not None and step is self.fail_at:
            raise StepExecutionError("paso de aprovisionamiento simulado falló.")
        return [f"{step.value}: hecho (simulado)."]


class _FakePrereqChecker:
    """A configurable :class:`PrereqChecker` (all-OK by default)."""

    def __init__(self, results: list[PrereqResult] | None = None) -> None:
        self._results = (
            results
            if results is not None
            else [
                PrereqResult(key="docker", label="Docker Engine", status=PrereqStatus.OK),
                PrereqResult(key="compose", label="Docker Compose v2", status=PrereqStatus.OK),
                PrereqResult(key="ram", label="RAM", status=PrereqStatus.OK),
                PrereqResult(key="disk", label="Disco", status=PrereqStatus.OK),
                PrereqResult(
                    key="gpu",
                    label="GPU NVIDIA (opcional)",
                    status=PrereqStatus.WARN,
                    required=False,
                ),
            ]
        )

    def check_all(self) -> list[PrereqResult]:
        return list(self._results)


def _installer(
    *,
    prereq: PrereqChecker | None = None,
    executor: StepExecutor | None = None,
    out: io.StringIO | None = None,
) -> tuple[HeadlessInstaller, io.StringIO, StubInstallerLifecycle]:
    """Build a HeadlessInstaller wired to fakes; return it + its stdout + lifecycle."""

    stream = out if out is not None else io.StringIO()
    lifecycle = StubInstallerLifecycle()
    inst = HeadlessInstaller(
        prereq_checker=prereq if prereq is not None else _FakePrereqChecker(),
        executor=executor if executor is not None else _RecordingExecutor(),
        credential_builder=StubCredentialBuilder(),
        finalize=FinalizeService(lifecycle=lifecycle),
        out=stream,
    )
    return inst, stream, lifecycle


# ---------------------------------------------------------------------------
# Config parsing.
# ---------------------------------------------------------------------------
def test_cli_parses_install_yaml() -> None:
    config = load_install_config(_valid_yaml())
    assert isinstance(config, InstallerConfig)
    assert config.system.domain == "agentic.example.com"
    assert config.tenant.admin_email == "admin@acme.com"
    assert config.providers.ollama.enabled is True


def test_malformed_yaml_is_rejected() -> None:
    # Not even valid YAML (unterminated flow mapping).
    with pytest.raises(Exception) as exc:  # CliError, carries ExitCode.CONFIG.
        load_install_config("system: {domain: ")
    assert getattr(exc.value, "code", None) == ExitCode.CONFIG


def test_non_mapping_yaml_is_rejected() -> None:
    with pytest.raises(Exception) as exc:
        load_install_config("- just\n- a\n- list\n")
    assert getattr(exc.value, "code", None) == ExitCode.CONFIG


def test_config_failing_field_validation_is_rejected() -> None:
    bad = dict(_VALID_CONFIG)
    bad["system"] = {"domain": "http://has-scheme/and/path", "environment": "production"}
    with pytest.raises(Exception) as exc:
        load_install_config(yaml.safe_dump(bad))
    assert getattr(exc.value, "code", None) == ExitCode.CONFIG


def test_config_with_no_provider_enabled_is_rejected() -> None:
    # Cross-field rule: at least one LLM provider must be enabled.
    bad = dict(_VALID_CONFIG)
    bad["providers"] = {}
    with pytest.raises(Exception) as exc:
        load_install_config(yaml.safe_dump(bad))
    assert getattr(exc.value, "code", None) == ExitCode.CONFIG


# ---------------------------------------------------------------------------
# Orchestration runs the steps IN ORDER against the mock executor.
# ---------------------------------------------------------------------------
def test_runs_steps_in_pipeline_order() -> None:
    executor = _RecordingExecutor()
    inst, _out, lifecycle = _installer(executor=executor)
    config = load_install_config(_valid_yaml())

    inst.run(config)

    # Every provisioning step ran, in the canonical pipeline order.
    assert executor.executed == list(INSTALL_STEP_ORDER)
    # The full phase list = prereqs + the pipeline + finalize.
    assert inst.phases == list(headless_pipeline())
    # A successful install self-destructs the installer (one-time reveal done).
    assert lifecycle.destroyed is True


def test_successful_install_returns_exit_ok(tmp_path) -> None:
    cfg = tmp_path / "install.yaml"
    cfg.write_text(_valid_yaml(), encoding="utf-8")
    inst, out, lifecycle = _installer()
    code = run_install(str(cfg), installer=inst, out=out)
    assert code == ExitCode.OK
    assert lifecycle.destroyed is True


# ---------------------------------------------------------------------------
# Prereq failure ABORTS with a non-zero exit BEFORE provisioning.
# ---------------------------------------------------------------------------
def test_prereq_failure_aborts_before_provisioning() -> None:
    failing = _FakePrereqChecker(
        results=[
            PrereqResult(
                key="docker",
                label="Docker Engine",
                status=PrereqStatus.FAIL,
                detail="Docker no detectado.",
                remediation="Instala Docker Engine.",
            )
        ]
    )
    executor = _RecordingExecutor()
    inst, _out, lifecycle = _installer(prereq=failing, executor=executor)
    config = load_install_config(_valid_yaml())

    with pytest.raises(Exception) as exc:
        inst.run(config)

    assert getattr(exc.value, "code", None) == ExitCode.PREREQ
    # NOT A SINGLE provisioning step ran — the gate aborted first.
    assert executor.executed == []
    # Only the prereq phase was recorded; no pipeline / finalize.
    assert inst.phases == ["prereqs"]
    # No self-destruct on an aborted install.
    assert lifecycle.destroyed is False


def test_provisioning_failure_halts_with_provision_exit() -> None:
    # Fail at the Vault bootstrap step: earlier steps ran, later ones did not.
    executor = _RecordingExecutor(fail_at=InstallStep.BOOTSTRAP_VAULT)
    inst, _out, lifecycle = _installer(executor=executor)
    config = load_install_config(_valid_yaml())

    with pytest.raises(Exception) as exc:
        inst.run(config)

    assert getattr(exc.value, "code", None) == ExitCode.PROVISION
    # Steps up to (and including) the failing one ran; the rest were skipped.
    assert executor.executed == [
        InstallStep.GENERATE_CONFIG,
        InstallStep.PULL_IMAGES,
        InstallStep.START_STACK,
        InstallStep.RUN_MIGRATIONS,
        InstallStep.BOOTSTRAP_VAULT,
    ]
    assert InstallStep.SEED_TENANT not in executor.executed
    # A halted install never reaches finalize / self-destruct.
    assert "finalize" not in inst.phases
    assert lifecycle.destroyed is False


# ---------------------------------------------------------------------------
# The headless run produces the SAME step pipeline as the wizard.
# ---------------------------------------------------------------------------
def test_headless_pipeline_matches_wizard_install_order() -> None:
    # The CLI's provisioning phases are exactly the wizard's INSTALL_STEP_ORDER.
    pipeline = headless_pipeline()
    middle = pipeline[1:-1]  # strip the prereqs gate + the finalize reveal.
    assert list(middle) == [step.value for step in INSTALL_STEP_ORDER]
    assert pipeline[0] == "prereqs"
    assert pipeline[-1] == "finalize"


def test_headless_executor_order_equals_wizard_stream_order() -> None:
    """The steps the CLI drives match the steps the wizard's SSE stream drives.

    Run the SAME FakeStepExecutor through (a) the wizard's /api/install/stream
    route and (b) the headless CLI, and assert both executed the identical
    ordered step sequence — proof the two share one pipeline.
    """

    # (a) wizard: drive the SSE route with a recording executor.
    wizard_exec = _RecordingExecutor()
    app = create_app()
    app.dependency_overrides[get_step_executor] = lambda: wizard_exec
    with TestClient(app) as client:
        resp = client.post("/api/install/stream", json={"config": {}})
        assert resp.status_code == 200
        # Drain the SSE stream so the generator runs to completion.
        _ = resp.text
    app.dependency_overrides.clear()

    # (b) CLI: drive the headless installer with its own recording executor.
    cli_exec = _RecordingExecutor()
    inst, _out, _lc = _installer(executor=cli_exec)
    inst.run(load_install_config(_valid_yaml()))

    assert wizard_exec.executed == cli_exec.executed == list(INSTALL_STEP_ORDER)


# ---------------------------------------------------------------------------
# The default installer (stub-wired) runs end-to-end with no host.
# ---------------------------------------------------------------------------
def test_default_installer_runs_with_stub_seams() -> None:
    out = io.StringIO()
    inst = build_default_installer(out)
    inst.run(load_install_config(_valid_yaml()))
    # All provisioning steps + the full phase list ran through the stubs.
    assert inst.phases == list(headless_pipeline())


def test_install_emits_no_secret_to_python_logs(tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    """The CLI prints the reveal to STDOUT (once) but logs no secret to logging."""

    caplog.set_level(logging.DEBUG)
    cfg = tmp_path / "install.yaml"
    cfg.write_text(_valid_yaml(), encoding="utf-8")
    builder = StubCredentialBuilder()
    inst, _out, _lc = _installer()
    inst.credential_builder = builder
    inst.run(load_install_config(_valid_yaml()))

    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    for secret in (builder.admin_password, builder.vault_root_token, *builder.vault_unseal_keys):
        assert secret not in log_text


# ---------------------------------------------------------------------------
# main() / run_install() — file reading, exit codes, the one-time reveal.
# ---------------------------------------------------------------------------
def test_main_install_success(tmp_path, capsys) -> None:
    cfg = tmp_path / "install.yaml"
    cfg.write_text(_valid_yaml(), encoding="utf-8")
    code = main(["install", "--config", str(cfg)])
    assert code == int(ExitCode.OK)
    out = capsys.readouterr().out
    # The one-time reveal printed the admin username derived from the config.
    assert "admin@acme.com" in out


def test_main_missing_config_file_is_config_error(tmp_path) -> None:
    missing = tmp_path / "does-not-exist.yaml"
    code = main(["install", "--config", str(missing)])
    assert code == int(ExitCode.CONFIG)


def test_main_malformed_config_is_config_error(tmp_path) -> None:
    cfg = tmp_path / "install.yaml"
    cfg.write_text("system: {domain: ", encoding="utf-8")
    code = main(["install", "--config", str(cfg)])
    assert code == int(ExitCode.CONFIG)


def test_run_install_propagates_prereq_failure_as_cli_error(tmp_path) -> None:
    cfg = tmp_path / "install.yaml"
    cfg.write_text(_valid_yaml(), encoding="utf-8")
    failing = _FakePrereqChecker(
        results=[PrereqResult(key="ram", label="RAM", status=PrereqStatus.FAIL, detail="2 GiB")]
    )
    inst, out, _lc = _installer(prereq=failing)
    # run_install does NOT swallow failures — it raises CliError carrying the
    # exit code; main() is what maps that to a process exit code.
    with pytest.raises(Exception) as exc:
        run_install(str(cfg), installer=inst, out=out)
    assert getattr(exc.value, "code", None) == ExitCode.PREREQ


def test_main_usage_error_on_bad_args() -> None:
    # Missing the required `install` subcommand → usage error, not a crash.
    code = main([])
    assert code == int(ExitCode.USAGE)
