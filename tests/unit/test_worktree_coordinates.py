"""Unit — ``worktree_coordinates`` es la ÚNICA derivación de coordenadas de worktree.

Hallazgo #10a: 5+ sitios reconstruían a mano ``BareRepoLayout`` + la rama del plan,
propensos a divergir. Este golden test CLAVA que el helper produce EXACTAMENTE los
mismos strings (bare path, worktree path, branch) que la derivación manual histórica
— la red que protege la identidad container-side == daemon-side de los binds DooD:
cualquier normalización futura (``resolve()``/realpath) que rompa la identidad de path
rompe aquí, no en producción (workspaces mal montados en runs en vuelo).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from workers.git_repos import BareRepoLayout
from workers.plan_git import make_plan_branch_name, worktree_coordinates

pytestmark = pytest.mark.unit

_DATA_ROOT = "/var/lib/docker/volumes/agentic-platform-agent-data/_data"
_TENANT = "demo"
_PROJECT = "api-ci"
_PLAN_ID = "019f1397-afaf-7ed3-8bdc-40d60f5e10dd"
_PLAN_SLUG = "api-codeigniter-4-endpoints"


def test_matches_the_manual_derivation_byte_for_byte() -> None:
    layout, branch = worktree_coordinates(
        data_root=_DATA_ROOT,
        tenant_slug=_TENANT,
        project_slug=_PROJECT,
        plan_id=_PLAN_ID,
        plan_slug=_PLAN_SLUG,
    )
    manual = BareRepoLayout(data_root=Path(_DATA_ROOT), tenant_slug=_TENANT, project_slug=_PROJECT)
    # branch idéntica a make_plan_branch_name (lo que usan hoy los 5 sitios).
    assert branch == make_plan_branch_name(_PLAN_ID, _PLAN_SLUG)
    # bare + worktree paths idénticos (strings verbatim que van al daemon como bind).
    assert str(layout.bare_repo_path(_PROJECT)) == str(manual.bare_repo_path(_PROJECT))
    assert str(layout.worktree_path("task-123")) == str(manual.worktree_path("task-123"))
    assert str(layout.worktree_path(f"review-{_PLAN_ID[:8]}")) == str(
        manual.worktree_path(f"review-{_PLAN_ID[:8]}")
    )


def test_no_path_normalization_preserves_dood_identity() -> None:
    """El data_root daemon-side se preserva VERBATIM (sin resolve/realpath), o los
    binds /workspace container-side==daemon-side dejarían de coincidir."""
    layout, _ = worktree_coordinates(
        data_root=_DATA_ROOT,
        tenant_slug=_TENANT,
        project_slug=_PROJECT,
        plan_id=_PLAN_ID,
        plan_slug=_PLAN_SLUG,
    )
    # Normalizamos el separador SOLO para el test (en Windows Path usa '\\'; en el
    # worker Linux se mantiene POSIX): el data_root se preserva VERBATIM, sin que un
    # resolve()/realpath lo reescriba a una ruta distinta.
    as_posix = str(layout.bare_repo_path(_PROJECT)).replace("\\", "/")
    assert as_posix.startswith(_DATA_ROOT)


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
