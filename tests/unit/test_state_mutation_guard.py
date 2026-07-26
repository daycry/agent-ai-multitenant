"""T4 (ciclo-vida, c1/c10): guard estático — el estado de Task/Plan solo cambia
por la puerta correcta.

Escanea con AST los tres árboles de producción (api-server, orchestrator,
workers) buscando asignaciones crudas ``<task|plan>.status = ...`` (y
``setattr(obj, "status", ...)``) y exige que CADA sitio esté en la allowlist
auditada de abajo. Un sitio nuevo rompe CI: o se encamina por
``transition_task_status`` / ``transition_plan_status`` (o un helper aquí
justificado) o se añade aquí CON justificación en revisión.

La allowlist es de IGUALDAD, no de subconjunto: si un sitio legítimo
desaparece (refactor), también hay que quitarlo de aquí — mantiene la lista
honesta en ambas direcciones.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]

_SCAN_ROOTS = (
    "apps/api-server/src/api_server",
    "apps/orchestrator/src/orchestrator",
    "apps/workers/src/workers",
)

# Nombres que delatan que el objeto mutado es una Task o un Plan (heurística
# deliberadamente amplia: task, task_row, refreshed_task, plan, plan_row...).
_HINTS = ("task", "plan")

# --- Sitios LEGALES de mutación cruda (ruta relativa posix, función) --------
# Cada grupo con su porqué. Todo lo demás debe usar las funciones de transición.
_ALLOWED: frozenset[tuple[str, str]] = frozenset(
    {
        # LAS puertas canónicas: la mutación vive dentro del gate.
        ("apps/api-server/src/api_server/task_state_machine.py", "transition_task_status"),
        ("apps/api-server/src/api_server/chat/plan_state_machine.py", "transition_plan_status"),
        # Motor de acciones humanas (Plan 06 task_06_34b4) — vocabulario propio
        # de 4/5 acciones con su tabla auditada; la variante librería y la HTTP.
        ("apps/api-server/src/api_server/task_lifecycle.py", "reject_review"),
        ("apps/api-server/src/api_server/task_lifecycle.py", "escalate_if_exhausted"),
        ("apps/api-server/src/api_server/task_lifecycle.py", "apply_human_action"),
        ("apps/api-server/src/api_server/routers/task_lifecycle.py", "apply_human_action"),
        # T7c: desbloqueo con reset de reintentos (ready/backlog según DAG).
        ("apps/api-server/src/api_server/routers/task_lifecycle.py", "apply_task_retry"),
        # Motor de aprobaciones (ADR 0020): parquear/reanudar/expirar.
        ("apps/api-server/src/api_server/db/approval_repo.py", "request_approval_if_needed"),
        ("apps/api-server/src/api_server/db/approval_repo.py", "resolve_approval"),
        ("apps/api-server/src/api_server/db/approval_repo.py", "expire_stale_requests"),
        # Cancelación en cascada de tareas + ejecuciones (prod-06 cancel_01).
        ("apps/api-server/src/api_server/db/execution_repo.py", "cancel_tasks_and_executions"),
        # Edge documentado en la state machine: revert de un dispatch que no
        # pudo encolarse (in_progress → ready).
        ("apps/orchestrator/src/orchestrator/dispatch.py", "_revert_to_ready"),
    }
)


def _hints(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return any(h in node.id.lower() for h in _HINTS)
    if isinstance(node, ast.Attribute):
        return any(h in node.attr.lower() for h in _HINTS)
    return False


def _scan_source(source: str, relpath: str) -> set[tuple[str, str]]:
    """(relpath, función) por cada asignación cruda de `.status` sobre Task/Plan."""
    tree = ast.parse(source)
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    def func_of(node: ast.AST) -> str:
        cur: ast.AST | None = node
        while cur in parents:
            cur = parents[cur]
            if isinstance(cur, ast.FunctionDef | ast.AsyncFunctionDef):
                return cur.name
        return "<module>"

    found: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == "status"
                    and _hints(target.value)
                ):
                    found.add((relpath, func_of(node)))
        elif isinstance(node, ast.AugAssign):
            target = node.target
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "status"
                and _hints(target.value)
            ):
                found.add((relpath, func_of(node)))
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "setattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "status"
            and _hints(node.args[0])
        ):
            found.add((relpath, func_of(node)))
    return found


def _scan_tree() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for root in _SCAN_ROOTS:
        for path in (_REPO_ROOT / root).rglob("*.py"):
            rel = path.relative_to(_REPO_ROOT).as_posix()
            try:
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):  # pragma: no cover — defensivo
                continue
            try:
                found |= _scan_source(source, rel)
            except SyntaxError:  # pragma: no cover — defensivo
                continue
    return found


def test_guard_detects_a_seeded_raw_mutation() -> None:
    """Auto-test del escáner: caza asignación, aug-assign y setattr."""
    seeded = _scan_source(
        "async def sneak(task, plan):\n"
        "    task.status = 'done'\n"
        "    setattr(plan, 'status', 'completed')\n",
        "seeded.py",
    )
    assert seeded == {("seeded.py", "sneak")}


def test_no_raw_status_mutation_outside_the_gates() -> None:
    found = _scan_tree()
    new_sites = found - _ALLOWED
    gone_sites = _ALLOWED - found
    assert not new_sites, (
        "Asignación cruda de .status sobre Task/Plan fuera de las puertas de "
        f"transición: {sorted(new_sites)}. Encamínala por transition_task_status/"
        "transition_plan_status o añádela a la allowlist CON justificación."
    )
    assert not gone_sites, (
        f"Sitios de la allowlist que ya no existen: {sorted(gone_sites)}. "
        "Quítalos de la allowlist para mantenerla honesta."
    )
