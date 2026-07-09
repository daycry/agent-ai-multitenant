"""The state that flows through the agent loop graph (task_02_10).

`AgentState` is the LangGraph state schema. List-valued fields that
*accumulate* across nodes (`context`, `reflections`, `steps`) carry an
`operator.add` reducer; scalar fields are replaced by whichever node
last wrote them.

CONTRATO ENTRE MÓDULOS (hallazgo H6, 2026-07-07): ``graph.py`` Y ``providers.py``
leen/escriben estas claves POR STRING — renombrar una sin tocar el otro lado
compila y rompe en silencio (el TypedDict no protege los ``state.get("...")``).
Claves compartidas hoy: ``status``, ``last_decision``, ``last_observation``,
``review_retries``, ``last_review_feedback``, ``guidance_nudge``,
``repetition_warning``, ``progress_summary``, ``output``, ``review_passed``,
``is_review``, ``system_preamble``, ``context``, ``steps``, ``guardrail_events``.
La clave ``written_files`` (inyectada solo en el estado que ve la self-review) ya
NO es mágica: vive TIPADA en :class:`ReviewState` (subclase ``total=False``), y el
test-contrato deriva las claves inyectadas de esa jerarquía de tipos. Si añades o
renombras una clave, actualiza AMBOS módulos y este listado.

Desde 2026-07-08 el contrato es EJECUTABLE: ``tests/test_state_key_contract.py``
escanea ambos módulos con AST y falla si algún ``state[...]``/``state.get(...)``
usa una clave que no exista en este TypedDict (o en su lista de inyectadas) — el
rename silencioso rompe en CI, no en producción.
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
# The AUTHORITATIVE self-review could not certify the output (ADR 0087): an
# inconclusive verdict, or an exhausted retry budget. The run is NOT aborted and
# NOT silently passed — it is escalated to a human (the worker maps this to a
# `blocked` task surfaced via the human inbox). The deliverable is preserved.
STATUS_NEEDS_HUMAN_REVIEW = "needs_human_review"


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

    # F1.6c (auditoría 2026-07-02): run de REVIEW → `_system_content` usa el
    # contrato del reviewer (_REVIEW_RUN_SYSTEM) en vez del del implementador.
    is_review: bool

    # Working memory — every node may append context fragments.
    context: Annotated[list[dict[str, Any]], operator.add]
    reflections: Annotated[list[str], operator.add]

    last_decision: dict[str, Any] | None
    last_observation: dict[str, Any] | None

    # Sticky intra-run feedback (A1): the AUTHORITATIVE review's last rejection
    # feedback and the latest repetition warning. SCALARS (replaced, not
    # accumulated) — `_decide_messages` renders them ALWAYS, OUTSIDE the bounded
    # context tail, so they stay in front of the model until acted on (the
    # feedback was getting buried/evicted from the context window and the agent
    # re-produced the same rejected output). `None` until first set.
    last_review_feedback: str | None
    repetition_warning: str | None

    # F2b (auditoría 2026-07-02): dos escalares sticky más.
    # `progress_summary`: resumen SIEMPRE-visible de lo ya hecho (iteración
    # N/límite + ficheros ya escritos) — el modelo solo veía los últimos 8 items
    # de contexto y re-leía/re-escribía para reconstruir lo perdido (la causa
    # raíz del read-churn que los backstops cortan a posteriori).
    # `guidance_nudge`: los nudges de research/churn, que antes viajaban como
    # items de `context` evictables por esa misma ventana.
    progress_summary: str | None
    guidance_nudge: str | None

    output: str | None
    review_retries: int
    review_passed: bool | None

    # Set when the loop parks on a sensitive action: {category, action}.
    approval: dict[str, Any] | None

    # The steps_log: an append-only record of everything the agent did.
    steps: Annotated[list[dict[str, Any]], operator.add]

    # Guardrail events (ADR 0102 / g1): triggered post_tool guardrails accumulated
    # across the run; the worker persists them (RLS) from the result envelope.
    guardrail_events: Annotated[list[dict[str, Any]], operator.add]


class ReviewState(AgentState, total=False):
    """El estado que ve la self-review: ``AgentState`` MÁS la clave ``written_files``
    que ``graph.py`` inyecta en el dict derivado antes de llamar a ``model.review``.

    Antes ``written_files`` era una clave mágica sin tipo (una lista literal
    ``_INJECTED_KEYS`` en el test-contrato); tiparla aquí como subclase ``total=False``
    (opcional) la hace visible a mypy y al scanner AST — el rename silencioso de la
    ÚNICA clave inyectada se convierte también en un fallo tipado, sin tocar el código
    caliente del loop (hallazgo #6, H6-real). Cada entrada es ``{"path", "content"}``."""

    written_files: list[dict[str, str]]


def initial_state(
    task: AgentTask, *, system_preamble: str | None = None, is_review: bool = False
) -> AgentState:
    """A fresh state for a task at the start of an execution.

    `system_preamble` (Plan 06.18 task_06_18_13) carries the assigned skills'
    prompt fragments to prepend to the model's system prompt; `None` keeps the
    historical prompt untouched. `is_review` (F1.6c) selects the reviewer's own
    system contract instead of the implementer's.
    """
    return AgentState(
        task=task,
        iteration=0,
        status=STATUS_RUNNING,
        abort_code=None,
        system_preamble=system_preamble,
        is_review=is_review,
        context=[],
        reflections=[],
        last_decision=None,
        last_observation=None,
        last_review_feedback=None,
        repetition_warning=None,
        progress_summary=None,
        guidance_nudge=None,
        output=None,
        review_retries=0,
        review_passed=None,
        approval=None,
        steps=[],
        guardrail_events=[],
    )
