"""The state that flows through the agent loop graph (task_02_10).

`AgentState` is the LangGraph state schema. List-valued fields that
*accumulate* across nodes (`context`, `reflections`, `steps`) carry an
`operator.add` reducer; scalar fields are replaced by whichever node
last wrote them.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, NotRequired, TypedDict

# Execution status vocabulary — shared with the `executions` table.
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_ABORTED = "aborted"
# Parked on a sensitive action awaiting a human decision (task_02_33).
STATUS_AWAITING_APPROVAL = "awaiting_human_approval"


class AgentTask(TypedDict):
    """The unit of work handed to the agent loop."""

    id: str
    title: str
    description: str
    # The task's definition of "done" (worker merges it into the spec). Drives
    # the decision prompt so read/write/test behaviour follows the TASK, not a
    # blanket rule. Absent for tasks without criteria (backward-compat).
    acceptance_criteria: NotRequired[list[Any]]


class AgentState(TypedDict):
    """State threaded through perceive → … → self_review."""

    task: AgentTask
    iteration: int
    status: str
    abort_code: str | None

    # Preámbulo a prepender al system prompt EFECTIVO (Plan 06.18 task_06_18_13,
    # ADR 0050): los `prompt_fragment` de las skills asignadas al agente,
    # concatenados. `None`/"" = sin inyección → el system prompt queda intacto
    # (backward-compat). Escalar, replicado tal cual a cada turno.
    system_preamble: str | None

    # Working memory — every node may append context fragments.
    context: Annotated[list[dict[str, Any]], operator.add]
    reflections: Annotated[list[str], operator.add]

    last_decision: dict[str, Any] | None
    last_observation: dict[str, Any] | None

    output: str | None
    review_retries: int
    review_passed: bool | None

    # Set when the loop parks on a sensitive action: {category, action}.
    approval: dict[str, Any] | None

    # The steps_log: an append-only record of everything the agent did.
    steps: Annotated[list[dict[str, Any]], operator.add]


def initial_state(task: AgentTask, *, system_preamble: str | None = None) -> AgentState:
    """A fresh state for a task at the start of an execution.

    `system_preamble` (Plan 06.18 task_06_18_13) carries the assigned skills'
    prompt fragments to prepend to the model's system prompt; `None` keeps the
    historical prompt untouched.
    """
    return AgentState(
        task=task,
        iteration=0,
        status=STATUS_RUNNING,
        abort_code=None,
        system_preamble=system_preamble,
        context=[],
        reflections=[],
        last_decision=None,
        last_observation=None,
        output=None,
        review_retries=0,
        review_passed=None,
        approval=None,
        steps=[],
    )
