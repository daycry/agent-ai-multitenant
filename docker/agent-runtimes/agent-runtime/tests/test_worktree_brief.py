"""P0-6 (investigación 2026-07-11): brief inicial del worktree en re-dispatch.

En un re-dispatch el worktree acumula el trabajo de intentos anteriores, pero
el implementador arrancaba CIEGO a lo que ya hay en disco y quemaba iteraciones
re-listando/re-leyendo — justo el read-churn que los backstops combaten a
posteriori. `perceive` ahora siembra un overview acotado (solo paths, con las
exclusiones del harvest del reviewer) para que el agente sepa desde el turno 1
qué existe ya. Worktree vacío → sin bloque (primer intento, sin ruido).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from agent_runtime.__main__ import run_task
from agent_runtime.review_harvest import worktree_file_list

_FINISH_ONLY = {
    "kind": "scripted",
    "decisions": [{"kind": "finish", "output": "done"}],
    "reviews": [{"passed": True}],
}


def _run(spec: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> list[dict[str, Any]]:
    rc = run_task(spec)
    assert rc == 0
    out = capsys.readouterr().out
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def _perceive_step(events: list[dict[str, Any]]) -> dict[str, Any]:
    steps = [
        e["step"]
        for e in events
        if e.get("event") == "step" and e["step"].get("node") == "perceive"
    ]
    assert len(steps) == 1, steps
    return steps[0]


def _spec() -> dict[str, Any]:
    return {
        "task": {"id": "t-1", "title": "Seguir la tarea", "description": "x"},
        "model": dict(_FINISH_ONLY),
    }


# ---------------------------------------------------------------------------
# worktree_file_list — el escaneo acotado, con las exclusiones del harvest.
# ---------------------------------------------------------------------------
def test_lists_files_excluding_vcs_and_deps(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.php").write_text("<?php", encoding="utf-8")
    (tmp_path / "README.md").write_text("hola", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref", encoding="utf-8")
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "dep.php").write_text("<?php", encoding="utf-8")

    files = worktree_file_list(tmp_path)
    assert "src/app.php" in files
    assert "README.md" in files
    assert not any(".git" in f or "vendor" in f for f in files)


def test_empty_or_missing_root_yields_empty(tmp_path: Path) -> None:
    assert worktree_file_list(tmp_path) == []
    assert worktree_file_list(tmp_path / "no-existe") == []


def test_bounded_to_max_files(tmp_path: Path) -> None:
    for i in range(80):
        (tmp_path / f"f{i:03}.txt").write_text("x", encoding="utf-8")
    files = worktree_file_list(tmp_path)
    assert len(files) <= 60


# ---------------------------------------------------------------------------
# perceive siembra el overview (vía run_task, con AGENT_WORKSPACE_ROOT).
# ---------------------------------------------------------------------------
def test_perceive_reports_existing_worktree_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "Invoice.php").write_text("<?php", encoding="utf-8")
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))

    step = _perceive_step(_run(_spec(), capsys))
    assert "1 existing file" in step["summary"]


def test_perceive_stays_quiet_on_empty_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))
    step = _perceive_step(_run(_spec(), capsys))
    assert "existing file" not in step["summary"]
