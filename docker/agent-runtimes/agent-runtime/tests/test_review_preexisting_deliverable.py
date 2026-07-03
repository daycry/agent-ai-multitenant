"""El self-review debe VER y ACEPTAR trabajo pre-existente (caso 019f27cc, 2026-07-03).

Un run cuyo entregable YA existía en el worktree (tarea re-ejecutada tras un
reset, trabajo hecho por un run previo/escalado — algo habitual) no escribe
nada, y el self-review lo rechazaba en bucle hasta escalar. Dos defectos
compuestos:

1. **Crowding del harvest**: `_harvest_worktree_files(prefer=escritos ESTE run)`
   — con `prefer` vacío el orden es alfabético y el cap de 40 ficheros dejaba
   FUERA el entregable (`docs/...` pierde contra decenas de `app/*` de un
   scaffold real): el reviewer literalmente no podía verlo.
2. **Framing de autoría**: el prompt etiquetaba el harvest como «Files the
   agent wrote», invitando a rechazar trabajo correcto por no haberse escrito
   en ESTE run.

Fix: los paths referenciados en la task (descripción + criterios + output del
agente) entran PRIMERO en el harvest, y el prompt juzga el ESTADO ACTUAL del
workspace declarando válido el trabajo pre-existente.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_runtime.graph import _harvest_worktree_files, _referenced_paths
from agent_runtime.providers import _review_messages


def test_referenced_paths_extracts_from_task_and_output() -> None:
    state: dict[str, Any] = {
        "task": {
            "title": "Definir contrato",
            "description": "Escribir `docs/contrato-respuesta-json.md` con el contrato.",
            "acceptance_criteria": [
                {"description": "Existe docs/contrato-respuesta-json.md con §2.2 y §3"},
                "El fichero app/Config/Routes.php declara la ruta v1",
            ],
        },
        "output": "He verificado docs/contrato-respuesta-json.md (9077B).",
    }
    refs = _referenced_paths(state)
    assert "docs/contrato-respuesta-json.md" in refs
    assert "app/Config/Routes.php" in refs
    # Sin duplicados aunque el path aparezca en varios sitios.
    assert refs.count("docs/contrato-respuesta-json.md") == 1


def test_referenced_paths_catches_root_level_filenames() -> None:
    """Caso 019f27ed (matriz de pruebas): `phpunit.xml` vive en la RAÍZ del
    worktree — sin barra. La primera versión del regex exigía un «/», así que
    el criterio «phpunit.xml válido» no entraba en prefer y el crowding lo
    dejaba fuera del harvest → fail en bucle otra vez. Un nombre de fichero
    suelto con extensión debe capturarse; un número de versión (1.0.0) no."""
    state: dict[str, Any] = {
        "task": {
            "title": "Matriz de pruebas",
            "description": "Configurar phpunit.xml con los testsuites.",
            "acceptance_criteria": ["phpunit.xml declara testsuites y coverage"],
        },
        "output": "meta.version==1.0.0 verificado; escribí tests/_support/CITestCase.php",
    }
    refs = _referenced_paths(state)
    assert "phpunit.xml" in refs
    assert "tests/_support/CITestCase.php" in refs
    assert "1.0.0" not in refs  # un número de versión no es un fichero


def test_harvest_prefers_referenced_paths_over_alphabetical_crowding(tmp_path: Path) -> None:
    """Con 45 ficheros de scaffold alfabéticamente ANTERIORES, el entregable
    referenciado sobrevive al cap de 40 gracias a `prefer`."""
    for i in range(45):
        d = tmp_path / "app" / f"m{i:02d}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "file.php").write_text("<?php // scaffold", encoding="utf-8")
    target = tmp_path / "docs" / "contrato-respuesta-json.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# Contrato", encoding="utf-8")

    files = _harvest_worktree_files(tmp_path, ["docs/contrato-respuesta-json.md"])

    paths = [f["path"] for f in files]
    assert "docs/contrato-respuesta-json.md" in paths
    assert paths[0] == "docs/contrato-respuesta-json.md"  # preferido → primero


def test_review_prompt_judges_state_not_authorship() -> None:
    """El prompt del self-review presenta el workspace como ESTADO ACTUAL
    acumulado y declara válido el trabajo pre-existente — no exige que los
    ficheros los haya escrito ESTE run."""
    state: dict[str, Any] = {
        "task": {"title": "T", "description": "d", "acceptance_criteria": ["c"]},
        "output": "hecho",
        "written_files": [{"path": "docs/x.md", "content": "# x"}],
    }
    body = "\n".join(m.content for m in _review_messages(state))
    assert "Files the agent wrote" not in body
    assert "Current workspace state" in body
    assert "previous run" in body  # instrucción explícita: lo pre-existente cuenta
