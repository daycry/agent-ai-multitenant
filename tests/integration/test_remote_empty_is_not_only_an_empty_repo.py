"""`remote_empty` NO significa «repositorio vacío», y el aviso lo afirmaba.

H3 del recorrido E2E del 2026-08-29
(``docs/roadmap/2026-08-29-hallazgos-e2e-hello-world-v2.md``): el formulario de
git precarga ``main`` y el repositorio de pruebas usa ``master``. Nada en la
pantalla avisa de la discrepancia.

Lo que hace la plataforma en ese caso es pedirle a
``BareRepoManager.align_default_branch`` que alinee la rama CONFIGURADA, y ésta
devuelve ``remote_empty`` cuando ``refs/remotes/origin/<rama>`` no resuelve —
sin distinguir por qué. El panel traducía ese estado como:

    «El remoto no tiene la rama por defecto (repo vacío). Haz un push inicial;
     hasta entonces el PR del plan no podrá abrirse.»

Dos cosas mal, y la segunda es cara: **afirma una causa que el dato no
respalda**, y el consejo que da —«haz un push inicial»— crearía en el remoto una
rama ``main`` que no debería existir, junto a la ``master`` que sí tiene todo el
trabajo. Es la misma familia que todo el ADR 0162: una señal que dice algo
distinto de lo que ocurre.

Este test fija el hecho en el sitio donde NACE —el valor que devuelve
``align_default_branch``— para que el aviso no pueda volver a suponer la causa:
un remoto con historia y con otra rama por defecto produce EXACTAMENTE el mismo
``remote_empty`` que un remoto de verdad vacío.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.integration._git_helpers import seed_bare_repo

pytestmark = pytest.mark.integration


def _manager_pointing_at(tmp_path: Path, remote_bare: Path):
    from workers.git_repos import BareRepoLayout, BareRepoManager

    layout = BareRepoLayout(data_root=tmp_path / "data", tenant_slug="t", project_slug="p")
    mgr = BareRepoManager(layout)
    mgr.ensure_repo("backend", remote_url=str(remote_bare))
    mgr.fetch_remote("backend")
    return mgr


def test_a_remote_with_history_on_master_also_reports_remote_empty(tmp_path: Path) -> None:
    """El caso H3 exacto: remoto con trabajo en ``master``, proyecto con ``main``."""
    remote_bare = tmp_path / "remote" / "backend.git"
    seed_bare_repo(remote_bare, default_branch="master")
    mgr = _manager_pointing_at(tmp_path, remote_bare)

    # El remoto NO está vacío: tiene su rama con su commit.
    listed = subprocess.run(
        ["git", "branch", "--list"],
        cwd=str(remote_bare),
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "master" in listed, "el fixture no sembró el remoto: el test pasaría en vacío"

    # Y aun así, alinear la rama CONFIGURADA (`main`) devuelve `remote_empty`.
    assert mgr.align_default_branch("backend", "main") == "remote_empty"

    # Mientras que alinear la rama REAL del remoto funciona perfectamente: lo que
    # falla no es el repositorio, es la rama que el proyecto tiene configurada.
    assert mgr.align_default_branch("backend", "master") == "created"


def test_a_genuinely_empty_remote_reports_the_same_thing(tmp_path: Path) -> None:
    """La otra mitad de la ambigüedad, para que la pareja no se pueda romper por
    un lado sólo: el estado es indistinguible entre las dos causas."""
    remote_bare = tmp_path / "remote" / "backend.git"
    remote_bare.mkdir(parents=True)
    subprocess.run(["git", "init", "--bare", str(remote_bare)], check=True, capture_output=True)
    mgr = _manager_pointing_at(tmp_path, remote_bare)

    assert mgr.align_default_branch("backend", "main") == "remote_empty"
