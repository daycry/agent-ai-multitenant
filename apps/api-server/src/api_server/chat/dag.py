"""Plan-task DAG validation (Plan 03 task_03_15).

A persisted plan declares its tasks as a flat list with
``depends_on: [task_id, ...]`` per task. We refuse to persist a plan
whose dependency graph contains a cycle — otherwise the orchestrator
would deadlock waiting on dependencies that depend back on the task
it just promoted.

The check is pure-Python (no DB, no async) so it can run inside the
Pydantic validator, the planning sub-graph, and the tests with the
same call. Iterative DFS with three-colour marking (white/gray/black)
detects cycles in O(V + E) and reports the offending cycle so the
client gets a useful 422 message.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class TaskRef:
    """One node of the DAG check — just the bits we need."""

    id: str
    depends_on: tuple[str, ...]


class DAGCycleError(ValueError):
    """Raised when a cycle is detected. ``cycle`` lists the ids in
    visit order, starting and ending with the same id."""

    def __init__(self, cycle: list[str]):
        self.cycle = cycle
        super().__init__(f"cycle detected: {' -> '.join(cycle)}")


# Three-colour DFS marks.
_WHITE = 0  # unvisited
_GRAY = 1  # on the current stack — meeting another GRAY = cycle
_BLACK = 2  # fully explored


def validate_dag(tasks: Iterable[TaskRef | dict[str, object]]) -> None:
    """Raise `DAGCycleError` if the task graph has a cycle.

    Accepts either `TaskRef` instances or plain dicts with at least
    `id` and `depends_on` keys (so callers can pass the spec's raw
    tasks list without converting first).
    """
    nodes: dict[str, tuple[str, ...]] = {}
    for raw in tasks:
        ref = _coerce(raw)
        if ref.id in nodes:
            # Duplicates are validated upstream by Pydantic; this is a
            # defensive guard.
            raise ValueError(f"duplicate task id: {ref.id}")
        nodes[ref.id] = ref.depends_on

    colour: dict[str, int] = {tid: _WHITE for tid in nodes}

    for start in nodes:
        if colour[start] != _WHITE:
            continue
        _dfs_iterative(start, nodes, colour)


def _coerce(raw: TaskRef | dict[str, object]) -> TaskRef:
    if isinstance(raw, TaskRef):
        return raw
    tid = raw.get("id")
    if not isinstance(tid, str) or not tid:
        raise ValueError("task is missing a string `id`")
    depends_on_raw = raw.get("depends_on") or []
    if not isinstance(depends_on_raw, list):
        raise ValueError(f"task {tid!r}.depends_on must be a list")
    return TaskRef(id=tid, depends_on=tuple(str(x) for x in depends_on_raw))


def _dfs_iterative(
    start: str,
    nodes: dict[str, tuple[str, ...]],
    colour: dict[str, int],
) -> None:
    """Iterative DFS marking nodes WHITE → GRAY → BLACK.

    The stack holds tuples ``(node, dep_iterator)``. When the iterator
    is exhausted the node is BLACK and we pop. Hitting a GRAY neighbour
    means we found the back-edge that closes a cycle — we reconstruct
    the cycle from the current stack and raise.
    """
    stack: list[tuple[str, list[str]]] = [(start, list(nodes.get(start, ())))]
    colour[start] = _GRAY

    while stack:
        node, remaining = stack[-1]
        if not remaining:
            colour[node] = _BLACK
            stack.pop()
            continue
        nxt = remaining.pop(0)
        if nxt not in nodes:
            # Unknown dependency — leave that to the Pydantic validator
            # to flag; for cycle detection we just skip it.
            continue
        c = colour[nxt]
        if c == _BLACK:
            continue
        if c == _GRAY:
            # Reconstruct the cycle: walk the stack from the matching
            # node forward; close it with `nxt` for readability.
            path = [frame[0] for frame in stack]
            try:
                first = path.index(nxt)
            except ValueError:
                first = 0
            raise DAGCycleError(path[first:] + [nxt])
        colour[nxt] = _GRAY
        stack.append((nxt, list(nodes.get(nxt, ()))))


__all__ = ["DAGCycleError", "TaskRef", "validate_dag"]
