"""Tests del hook `commit-msg` que exige los trailers de plan.

Plan prod-15 `task_gov_trailers_09`, decisión **D2 opción B**: los trailers
`Plan-Id` / `Task-Id` son obligatorios en los commits de **tareas de plan**
(ramas `plan/*`) y opcionales en mantenimiento.

Por qué B y no A (hook siempre): la regla escrita "trailers obligatorios" nunca
fue la real. Medido el 2026-07-29 sobre este repo: **643 de 1460** commits
no-merge llevan `Plan-Id` (44 %), y 10 de los últimos 30. Un hook que los exija
siempre convierte cada commit de mantenimiento en una pelea; uno que los exija
donde importan hace que la regla escrita y la practicada coincidan, que es lo
que pedía el hallazgo quality-9.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_HOOK = _ROOT / "scripts" / "check_commit_trailers.py"

sys.path.insert(0, str(_ROOT / "scripts"))

from check_commit_trailers import check_message  # noqa: E402

_WITH_TRAILERS = """feat(users): add POST /users

Body of the commit.

Plan-Id: 01H7K-implementar-auth-oauth
Task-Id: task_xyz123
"""

_WITHOUT_TRAILERS = """feat(users): add POST /users

Body of the commit, no trailers at all.
"""


# ---------------------------------------------------------------------------
# En rama plan/* los trailers son obligatorios
# ---------------------------------------------------------------------------
def test_accepts_message_with_trailers_on_plan_branch() -> None:
    ok, reason = check_message(_WITH_TRAILERS, branch="plan/06.8-rbac-enforcement")
    assert ok, reason


def test_rejects_message_without_trailers_on_plan_branch() -> None:
    ok, reason = check_message(_WITHOUT_TRAILERS, branch="plan/06.8-rbac-enforcement")
    assert not ok
    assert "Plan-Id" in reason and "Task-Id" in reason


def test_rejects_message_with_only_plan_id_on_plan_branch() -> None:
    """`Plan-Id` sin `Task-Id` no traza la tarea: media trazabilidad no vale."""
    message = _WITH_TRAILERS.replace("Task-Id: task_xyz123\n", "")
    ok, reason = check_message(message, branch="plan/x")
    assert not ok
    assert "Task-Id" in reason


def test_trailer_must_be_a_trailer_not_prose() -> None:
    """Mencionar "Plan-Id" en el cuerpo no cuenta como trailer.

    Un trailer va al principio de su línea, en el bloque final. Si valiera
    cualquier aparición del texto, el hook sería decorativo.
    """
    message = "fix: algo\n\nHablo del Plan-Id y del Task-Id en prosa, sin ponerlos.\n"
    ok, _reason = check_message(message, branch="plan/x")
    assert not ok


# ---------------------------------------------------------------------------
# Fuera de plan/* son opcionales (la mitad B de la decisión)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "branch",
    ["master", "work/validacion-2026-07-30", "hotfix/algo", "docs/algo", "HEAD"],
)
def test_maintenance_branches_do_not_need_trailers(branch: str) -> None:
    ok, reason = check_message(_WITHOUT_TRAILERS, branch=branch)
    assert ok, reason


def test_merge_commit_is_exempt_even_on_plan_branch() -> None:
    """Los merges los redacta git, no una tarea."""
    ok, reason = check_message("Merge branch 'master' into plan/x\n", branch="plan/x")
    assert ok, reason


@pytest.mark.parametrize("prefix", ["fixup!", "squash!", "amend!", "Revert"])
def test_rewrite_commits_are_exempt(prefix: str) -> None:
    """`fixup!`/`squash!` heredan el mensaje del commit destino al rebasar."""
    ok, reason = check_message(f"{prefix} feat(x): algo\n", branch="plan/x")
    assert ok, reason


# ---------------------------------------------------------------------------
# El hook es ejecutable de verdad, no solo una función
# ---------------------------------------------------------------------------
def test_hook_script_exits_nonzero_on_a_bad_plan_commit(tmp_path: Path) -> None:
    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_text(_WITHOUT_TRAILERS, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(_HOOK), str(msg), "--branch", "plan/x"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "Plan-Id" in (proc.stdout + proc.stderr)


def test_hook_script_exits_zero_on_master(tmp_path: Path) -> None:
    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_text(_WITHOUT_TRAILERS, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(_HOOK), str(msg), "--branch", "master"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_hook_is_registered_in_pre_commit_config() -> None:
    """Un hook que no está en `.pre-commit-config.yaml` no corre nunca.

    El patrón dominante de esta base (§5 de
    `verificar-antes-de-implementar.md`): mecanismo entregado, cero llamantes.
    """
    config = (_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert "check_commit_trailers.py" in config, (
        "el hook existe pero pre-commit no lo llama: sería mecanismo sin llamante"
    )
    assert "commit-msg" in config, "el hook debe declarar stage `commit-msg`"
