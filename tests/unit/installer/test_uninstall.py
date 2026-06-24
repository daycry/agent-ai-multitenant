"""Unit tests for the real uninstall seams (Plan prod-01 task_18 / deploy-1).

`RealStackTeardown` + `RealDataPurger` turn uninstall from a no-op into real
destruction — `docker compose down` (no ``-v``) + ``rmtree`` of the data tree —
driven here with a FakeCommandRunner / FakeFileSystem (no Docker, no disk). The
last test wires the real purger into the Uninstaller to prove the double
confirmation still gates the (now real) wipe.
"""

from __future__ import annotations

import io

import pytest
from installer_backend.command_runner import FakeCommandRunner
from installer_backend.compose_generator import PROJECT_NAME
from installer_backend.real_teardown import (
    FakeFileSystem,
    RealDataPurger,
    RealStackTeardown,
)
from installer_backend.uninstall import (
    ScriptedConfirmer,
    Uninstaller,
    UninstallRequest,
)

pytestmark = pytest.mark.unit

_COMPOSE_DIR = "/srv/agentic"
_COMPOSE_FILE = f"{_COMPOSE_DIR}/docker-compose.yml"
_DATA_ROOT = "/data/agent-platform"


def _teardown(runner: FakeCommandRunner, *, compose_exists: bool = True) -> RealStackTeardown:
    existing = {_COMPOSE_FILE} if compose_exists else set()
    return RealStackTeardown(_COMPOSE_DIR, runner, fs=FakeFileSystem(existing=existing))


def test_stack_teardown_runs_compose_down_without_volumes_by_default() -> None:
    runner = FakeCommandRunner()
    _teardown(runner).down(PROJECT_NAME, remove_volumes=False)
    assert runner.calls == [("docker", "compose", "-p", PROJECT_NAME, "-f", _COMPOSE_FILE, "down")]


def test_stack_teardown_adds_dash_v_when_remove_volumes() -> None:
    runner = FakeCommandRunner()
    _teardown(runner).down(PROJECT_NAME, remove_volumes=True)
    assert runner.calls[0][-1] == "-v"


def test_stack_teardown_falls_back_without_f_when_compose_missing() -> None:
    # Install aborted before GENERATE_CONFIG → no docker-compose.yml: tear down
    # by project name (Compose uses the container labels), not a failing -f.
    runner = FakeCommandRunner()
    _teardown(runner, compose_exists=False).down(PROJECT_NAME, remove_volumes=False)
    assert runner.calls == [("docker", "compose", "-p", PROJECT_NAME, "down")]


def test_stack_teardown_is_tolerant_of_a_nonzero_down() -> None:
    runner = FakeCommandRunner(fail_on=("docker", "compose"))
    # Must NOT raise — teardown is best-effort.
    lines = _teardown(runner).down(PROJECT_NAME, remove_volumes=False)
    assert any("rc=" in line for line in lines)


def test_data_purger_removes_existing_dirs_by_category_and_the_root() -> None:
    fs = FakeFileSystem(
        existing={
            f"{_DATA_ROOT}/postgres",
            f"{_DATA_ROOT}/vault",
            f"{_DATA_ROOT}/minio",
            _DATA_ROOT,
        }
    )
    lines = RealDataPurger(fs).purge(_DATA_ROOT)
    assert f"{_DATA_ROOT}/postgres" in fs.removed
    assert f"{_DATA_ROOT}/vault" in fs.removed
    assert _DATA_ROOT in fs.removed
    # The log names the categories that were wiped.
    blob = "\n".join(lines)
    assert "base de datos" in blob
    assert "secretos (Vault)" in blob


def test_data_purger_skips_absent_categories() -> None:
    fs = FakeFileSystem(existing={f"{_DATA_ROOT}/postgres"})
    RealDataPurger(fs).purge(_DATA_ROOT)
    # Only the dir that existed was removed (no spurious rmtree of absent dirs).
    assert fs.removed == [f"{_DATA_ROOT}/postgres"]


def test_double_confirmation_still_gates_the_real_purge() -> None:
    fs = FakeFileSystem(existing={f"{_DATA_ROOT}/postgres", _DATA_ROOT})
    # name OK + double-confirm yes, but the purge's extra confirmation is denied.
    confirmer = ScriptedConfirmer(name_answer=PROJECT_NAME, yes_answers=[True, False])
    uninstaller = Uninstaller(
        teardown=RealStackTeardown(_COMPOSE_DIR, FakeCommandRunner()),
        purger=RealDataPurger(fs),
        confirmer=confirmer,
        out=io.StringIO(),
    )
    result = uninstaller.run(
        UninstallRequest(deployment_name=PROJECT_NAME, data_root=_DATA_ROOT, purge_data=True)
    )
    assert result.data_purged is False
    assert fs.removed == [], "purge ran despite the extra confirmation being denied"


def test_purge_runs_when_both_confirmations_pass() -> None:
    fs = FakeFileSystem(existing={f"{_DATA_ROOT}/postgres", _DATA_ROOT})
    confirmer = ScriptedConfirmer(name_answer=PROJECT_NAME, yes_answers=[True, True])
    uninstaller = Uninstaller(
        teardown=RealStackTeardown(_COMPOSE_DIR, FakeCommandRunner()),
        purger=RealDataPurger(fs),
        confirmer=confirmer,
        out=io.StringIO(),
    )
    result = uninstaller.run(
        UninstallRequest(deployment_name=PROJECT_NAME, data_root=_DATA_ROOT, purge_data=True)
    )
    assert result.data_purged is True
    assert f"{_DATA_ROOT}/postgres" in fs.removed
