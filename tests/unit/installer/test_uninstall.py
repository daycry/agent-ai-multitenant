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
    report = RealDataPurger(fs).purge(_DATA_ROOT)
    assert f"{_DATA_ROOT}/postgres" in fs.removed
    assert f"{_DATA_ROOT}/vault" in fs.removed
    assert _DATA_ROOT in fs.removed
    # Nothing survived, so this purge may call itself complete.
    assert report.complete is True
    # The log names the categories that were wiped.
    blob = "\n".join(report.lines)
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


# ---------------------------------------------------------------------------
# La purga informa de lo que NO pudo borrar (auditoría 2026-08-27, medio).
#
# El defecto: `rmtree(ignore_errors=True)` + un log de éxito incondicional. Sobre
# una raíz de datos montada en un disco dedicado —que es lo que recomienda el
# propio remedio del prereq de disco— el `rmtree` falla con «Device or resource
# busy», el `ignore_errors` se lo traga y el operador lee «Datos ELIMINADOS»
# con el `.env` (contraseña de Postgres, claves de MinIO, secreto JWT y las tres
# Fernet) intacto en disco. Devuelve la máquina creyéndola limpia.
# ---------------------------------------------------------------------------
def test_purge_reports_the_paths_it_could_not_delete() -> None:
    fs = FakeFileSystem(
        existing={f"{_DATA_ROOT}/postgres", f"{_DATA_ROOT}/minio", _DATA_ROOT},
        fail_on={f"{_DATA_ROOT}/postgres": "Device or resource busy"},
    )
    report = RealDataPurger(fs).purge(_DATA_ROOT)

    assert report.complete is False, "una purga con un fallo no puede darse por completa"
    survivors = {left.path for left in report.leftovers}
    assert f"{_DATA_ROOT}/postgres" in survivors
    # El motivo viaja con la ruta: sin él el operador no sabe si desmontar, matar
    # un contenedor o cambiar permisos.
    assert any("busy" in left.reason for left in report.leftovers)
    # Lo que sí se borró se sigue borrando: un fallo no aborta el resto.
    assert f"{_DATA_ROOT}/minio" in fs.removed


def test_purge_treats_a_surviving_env_file_as_a_security_warning() -> None:
    # El .env es el único fichero cuya supervivencia tiene consecuencias de
    # SEGURIDAD y no sólo de espacio: lleva todos los secretos de la instalación.
    env_path = f"{_DATA_ROOT}/.env"
    fs = FakeFileSystem(
        existing={env_path, _DATA_ROOT},
        fail_on={env_path: "Permission denied"},
    )
    report = RealDataPurger(fs).purge(_DATA_ROOT)

    assert env_path in {left.path for left in report.leftovers}
    blob = "\n".join(report.lines)
    assert "SEGURIDAD" in blob.upper()
    assert ".env" in blob


def test_purge_deletes_the_env_even_when_the_root_cannot_be_removed() -> None:
    # Punto de montaje: la raíz no se puede borrar, pero el .env sí — y se borra
    # ANTES de intentarlo, para que su borrado no dependa del de la raíz.
    env_path = f"{_DATA_ROOT}/.env"
    fs = FakeFileSystem(
        existing={env_path, _DATA_ROOT},
        fail_on={_DATA_ROOT: "Device or resource busy"},
    )
    report = RealDataPurger(fs).purge(_DATA_ROOT)

    assert env_path in fs.removed
    assert env_path not in {left.path for left in report.leftovers}


def test_purge_empties_the_root_it_cannot_remove_and_says_so() -> None:
    # El caso legítimo del punto de montaje: vaciar el contenido equivale a
    # borrarlo para los datos, pero NO es lo mismo que «raíz eliminada», así que
    # el log tiene que distinguirlo en vez de mentir.
    leftover_child = f"{_DATA_ROOT}/ollama"
    fs = FakeFileSystem(
        existing={_DATA_ROOT, leftover_child},
        fail_on={_DATA_ROOT: "Device or resource busy"},
        children={_DATA_ROOT: ["ollama"]},
    )
    report = RealDataPurger(fs).purge(_DATA_ROOT)

    # El hijo que quedaba se ha eliminado uno a uno.
    assert leftover_child in fs.removed
    # Nada sobrevive => la purga es completa aunque la raíz siga ahí.
    assert report.complete is True
    blob = "\n".join(report.lines)
    assert "vaciada" in blob and "no eliminada" in blob.lower()
    assert _DATA_ROOT in blob


def test_purge_catches_a_silent_failure_where_the_path_survives_without_error() -> None:
    # El modo de fallo que `ignore_errors=True` producía por diseño: el borrado
    # «va bien» y la ruta sigue ahí. La comprobación POSTERIOR es lo que lo caza.
    fs = FakeFileSystem(existing={f"{_DATA_ROOT}/postgres"}, undeletable={f"{_DATA_ROOT}/postgres"})
    report = RealDataPurger(fs).purge(_DATA_ROOT)

    assert report.complete is False
    assert f"{_DATA_ROOT}/postgres" in {left.path for left in report.leftovers}


def test_uninstaller_never_reports_success_when_data_survived() -> None:
    # La línea final del desinstalador se derivaba de «se llamó a purge()», no de
    # que borrase. Ahora se deriva del informe.
    fs = FakeFileSystem(
        existing={f"{_DATA_ROOT}/postgres", _DATA_ROOT},
        fail_on={f"{_DATA_ROOT}/postgres": "Device or resource busy"},
    )
    out = io.StringIO()
    uninstaller = Uninstaller(
        teardown=RealStackTeardown(_COMPOSE_DIR, FakeCommandRunner()),
        purger=RealDataPurger(fs),
        confirmer=ScriptedConfirmer(name_answer=PROJECT_NAME, yes_answers=[True, True]),
        out=out,
    )
    result = uninstaller.run(
        UninstallRequest(deployment_name=PROJECT_NAME, data_root=_DATA_ROOT, purge_data=True)
    )

    assert result.leftovers, "el resultado tiene que llevar lo que sobrevivió"
    text = out.getvalue()
    assert "PURGA INCOMPLETA" in text
    assert "Datos ELIMINADOS." not in text
    # La ruta concreta y el motivo, para que el operador pueda actuar.
    assert f"{_DATA_ROOT}/postgres" in text
    assert "busy" in text
