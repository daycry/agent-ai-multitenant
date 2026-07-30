"""Unit — ``worktree_coordinates``/``worktree_layout``: ÚNICA derivación de coordenadas.

Hallazgo #10a: 5+ sitios reconstruían a mano ``BareRepoLayout`` + la rama del plan,
propensos a divergir. Remate I-2/I-3 (auditoría 2026-07-10): el golden clava los
STRINGS LITERALES esperados (no una comparación contra las mismas primitivas que el
helper llama por dentro — eso era tautológico: un cambio en ``BareRepoLayout`` movía
ambos lados a la vez y el test quedaba verde mientras la identidad DooD con los
worktrees ya existentes en el named volume se rompía). Y la guarda anti-normalización
usa un ``data_root`` con segmento ``x/..`` que un ``resolve()`` SÍ reescribiría también
en el runner Linux de CI (un path absoluto limpio era un no-op de string allí).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from workers.plan_git import make_plan_branch_name, worktree_coordinates, worktree_layout

pytestmark = pytest.mark.unit

_DATA_ROOT = "/var/lib/docker/volumes/agentic-platform-agent-data/_data"
_TENANT = "demo"
_PROJECT = "api-ci"
_PLAN_ID = "019f1397-afaf-7ed3-8bdc-40d60f5e10dd"
_PLAN_SLUG = "api-codeigniter-4-endpoints"


def test_golden_literal_paths_and_branch() -> None:
    """Los strings EXACTOS que van al daemon como bind source / a git como rama.

    Literales a propósito: si ``BareRepoLayout`` cambia un segmento (``projects/``,
    ``repos/``, ``worktrees/``, el sufijo ``.git``) o ``make_plan_branch_name``
    cambia el formato, este test se pone rojo — los worktrees YA provisionados en
    el named volume viven bajo estos paths y una deriva los dejaría huérfanos."""
    layout, branch = worktree_coordinates(
        data_root=_DATA_ROOT,
        tenant_slug=_TENANT,
        project_slug=_PROJECT,
        plan_id=_PLAN_ID,
        plan_slug=_PLAN_SLUG,
    )
    base = f"{_DATA_ROOT}/projects/{_TENANT}/{_PROJECT}"
    assert layout.bare_repo_path(_PROJECT).as_posix() == f"{base}/repos/{_PROJECT}.git"
    assert layout.worktree_path("task-123").as_posix() == f"{base}/worktrees/task-123"
    assert branch == "plan/019f1397-api-codeigniter-4-endpoints"
    # Y sigue siendo lo que emite la primitiva de branch (fuente única).
    assert branch == make_plan_branch_name(_PLAN_ID, _PLAN_SLUG)


def test_no_path_normalization_even_with_collapsible_segments() -> None:
    """Guarda anti-``resolve()`` EFECTIVA en CI: un data_root con ``x/..`` se
    preserva VERBATIM. ``pathlib`` puro no colapsa ``..`` (correcto aquí);
    ``resolve()``/``os.path.realpath`` sí lo harían — y en el path DooD daemon-side
    eso significa un bind source distinto del que el daemon conoce."""
    dirty_root = f"{_DATA_ROOT}/x/.."
    layout, _ = worktree_coordinates(
        data_root=dirty_root,
        tenant_slug=_TENANT,
        project_slug=_PROJECT,
        plan_id=_PLAN_ID,
        plan_slug=_PLAN_SLUG,
    )
    assert (
        layout.bare_repo_path(_PROJECT).as_posix()
        == f"{dirty_root}/projects/{_TENANT}/{_PROJECT}/repos/{_PROJECT}.git"
    )


def test_worktree_layout_is_the_layout_of_worktree_coordinates() -> None:
    """``worktree_layout`` es la primitiva de layout que ``worktree_coordinates``
    usa por dentro — el sitio que solo necesita el layout (la resolución read-only
    del review, sin plan a mano) la llama directamente y NO puede divergir."""
    layout = worktree_layout(data_root=_DATA_ROOT, tenant_slug=_TENANT, project_slug=_PROJECT)
    via_coordinates, _ = worktree_coordinates(
        data_root=_DATA_ROOT,
        tenant_slug=_TENANT,
        project_slug=_PROJECT,
        plan_id=_PLAN_ID,
        plan_slug=_PLAN_SLUG,
    )
    assert layout == via_coordinates
    assert layout.data_root == Path(_DATA_ROOT)


def test_execution_module_does_not_hand_roll_the_layout() -> None:
    """Contrato de fuente única (estilo test_state_key_contract): ``execution.py``
    no construye ``BareRepoLayout(...)`` a mano — el remate I-2 dejó
    ``_resolve_review_worktree`` sobre ``worktree_layout`` y nadie debe volver a
    la derivación manual en el módulo DooD-crítico."""
    import workers.execution as execution_module

    source = Path(execution_module.__file__).read_text(encoding="utf-8")
    assert not re.search(r"BareRepoLayout\s*\(", source), (
        "execution.py reconstruye BareRepoLayout a mano; usa worktree_layout()/"
        "worktree_coordinates() de workers.plan_git (fuente única, hallazgo #10a)"
    )


def test_empty_plan_slug_still_derives_a_branch() -> None:
    _, branch = worktree_coordinates(
        data_root=_DATA_ROOT,
        tenant_slug=_TENANT,
        project_slug=_PROJECT,
        plan_id=_PLAN_ID,
        plan_slug="",
    )
    assert branch == make_plan_branch_name(_PLAN_ID, "")
    assert branch.startswith("plan/")
