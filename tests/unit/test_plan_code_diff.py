"""ADR 0099 — servicio read-only de diff de CÓDIGO de la rama de un plan.

Sobre un bare REAL (tmp): siembra master, crea la rama plan/* con cambios y
verifica que el diff sale de merge-base(default, rama)..rama con resumen
numstat completo, líneas clasificadas para el renderer del visor y truncado
honesto del cuerpo. Las coordenadas salen de worktree_coordinates (la misma
primitiva que provisión/commit/review — nunca reconstruidas a mano).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from api_server.code_diff import PlanCodeDiffError, plan_code_diff
from workers.plan_git import make_plan_branch_name

pytestmark = pytest.mark.unit

_TENANT = "demo"
_PROJECT = "api-ci"
_PLAN_ID = "019f1397-afaf-7ed3-8bdc-40d60f5e10dd"
_PLAN_SLUG = "endpoints"


def _git(*args: str, cwd: Path) -> str:
    out = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "PATH": __import__("os").environ["PATH"],
        },
    )
    return out.stdout


def _seed(tmp_path: Path) -> Path:
    """data_root con el bare del proyecto: master + rama plan/* con cambios."""
    data_root = tmp_path / "data"
    bare = data_root / "projects" / _TENANT / _PROJECT / "repos" / f"{_PROJECT}.git"
    bare.parent.mkdir(parents=True)
    _git("init", "--bare", "-b", "master", str(bare), cwd=tmp_path)

    work = tmp_path / "work"
    _git("clone", str(bare), str(work), cwd=tmp_path)
    (work / "app.py").write_text("print('v1')\n", encoding="utf-8")
    _git("add", ".", cwd=work)
    _git("commit", "-m", "seed", cwd=work)
    _git("push", "origin", "master", cwd=work)

    branch = make_plan_branch_name(_PLAN_ID, _PLAN_SLUG)
    _git("checkout", "-b", branch, cwd=work)
    (work / "app.py").write_text("print('v2')\n", encoding="utf-8")
    (work / "nuevo.py").write_text("x = 1\n", encoding="utf-8")
    _git("add", ".", cwd=work)
    _git("commit", "-m", "cambio del plan", cwd=work)
    _git("push", "origin", branch, cwd=work)
    return data_root


def test_diff_of_plan_branch_against_merge_base(tmp_path: Path) -> None:
    data_root = _seed(tmp_path)
    diff = plan_code_diff(
        data_root,
        tenant_slug=_TENANT,
        project_slug=_PROJECT,
        plan_id=_PLAN_ID,
        plan_slug=_PLAN_SLUG,
    )
    assert diff.unchanged is False
    assert diff.truncated is False
    assert diff.default_branch == "master"
    assert {f["path"] for f in diff.files} == {"app.py", "nuevo.py"}
    added = [ln.content for ln in diff.lines if ln.kind == "added"]
    assert "print('v2')" in added
    assert "x = 1" in added
    removed = [ln.content for ln in diff.lines if ln.kind == "removed"]
    assert "print('v1')" in removed


def test_diff_truncates_huge_bodies(tmp_path: Path) -> None:
    data_root = _seed(tmp_path)
    diff = plan_code_diff(
        data_root,
        tenant_slug=_TENANT,
        project_slug=_PROJECT,
        plan_id=_PLAN_ID,
        plan_slug=_PLAN_SLUG,
        max_diff_chars=50,
    )
    assert diff.truncated is True
    # El resumen por fichero llega COMPLETO aunque el cuerpo se corte.
    assert {f["path"] for f in diff.files} == {"app.py", "nuevo.py"}


def test_missing_branch_is_a_clean_error(tmp_path: Path) -> None:
    data_root = _seed(tmp_path)
    with pytest.raises(PlanCodeDiffError):
        plan_code_diff(
            data_root,
            tenant_slug=_TENANT,
            project_slug=_PROJECT,
            plan_id="019f9999-0000-7000-8000-000000000000",
            plan_slug="no-existe",
        )
