"""Per-task acceptance-criteria generation (ADR 0021, provider-agnostic).

Feature A generates ``acceptance_criteria`` for a WHOLE plan draft
(``LLMPlanningModel.pm_plan_draft``); this module generates them for a SINGLE
task on demand — the "Generar con IA" button in the task detail and the backfill
script. It NEVER persists: it returns a proposal the operator reviews (and, when
the task already had criteria, confirms against a comparison) before saving via
``PUT /projects/{pid}/tasks/{tid}``.

Reuses the planner's JSON extraction and cleaner so criteria are normalised
IDENTICALLY everywhere (trim, cap count/length). Desde el ADR 0162 el
normalizador CONSERVA la forma estructurada de un criterio ejecutable; este
módulo la aplana igualmente a texto, y sólo él, porque su contrato de salida es
una propuesta en prosa que el operador revisa — ver :func:`_extract_criteria`.
"""

from __future__ import annotations

import json
from typing import Any

from shared_llm.base import LLMProvider
from shared_llm.types import Message

from api_server.chat.planning_llm import (
    _MAX_ACCEPTANCE_CRITERIA,
    _clean_acceptance_criteria,
    _extract_json,
    criterion_text,
)

# Same definition of a good criterion the planner uses (planning_llm.py:389-398):
# concrete, verifiable, plain language, NOT executable commands.
_CRITERION_RULE = (
    "Cada criterio es una condición CONCRETA y VERIFICABLE que define cuándo la "
    "tarea está HECHA (p.ej. 'composer audit no reporta vulnerabilidades pendientes', "
    "'el endpoint GET /hello responde 200 con el JSON acordado'). Redáctalos en "
    "lenguaje claro, NO como comandos a ejecutar."
)


def _summarise_context(project_context: dict[str, Any]) -> str:
    """A compact project identity note (name + description) for the prompt. The
    full RAG context (``responder.build_project_context``) is intentionally NOT
    used here — criteria generation is a cheap, focused call."""
    name = str(project_context.get("name") or "").strip()
    desc = str(project_context.get("description") or "").strip()
    parts: list[str] = []
    if name:
        parts.append(f"Proyecto: {name}")
    if desc:
        parts.append(f"Descripción del proyecto: {desc[:500]}")
    return "\n".join(parts)


# A sibling task's decisions (e.g. an agreed response contract) must not be
# contradicted by this task's criteria — that is exactly what blocked the CI4
# "Implementar controladores" run (crit "ResponseTrait" vs a sibling contract
# "{message, meta}"). We feed a compact digest of the plan's OTHER tasks so the
# generator stays coherent. Caps keep the prompt bounded.
_MAX_SIBLING_CRITERIA = 3
_MAX_SIBLING_CONTEXT_LEN = 2000


def format_sibling_context(siblings: list[tuple[str, list[Any]]]) -> str:
    """Compact digest of sibling tasks (``title`` + a few of their acceptance
    criteria) for the generation prompt, so this task's criteria stay CONSISTENT
    with decisions a sibling already fixed (a response contract, an error format,
    …). Returns ``""`` when there is nothing usable; caps per-sibling criteria and
    total length.

    ADR 0162: los criterios llegan ya normalizados y desde la opción A pueden
    venir ESTRUCTURADOS, así que el texto se saca con ``criterion_text`` y no con
    ``str(c)`` — que pintaría el ``repr`` del diccionario y el modelo leería
    ``{'description': …}`` como si fuera el criterio de la hermana."""
    parts: list[str] = []
    for raw_title, criteria in siblings:
        title = str(raw_title).strip()
        if not title:
            continue
        crits = [t for c in criteria[:_MAX_SIBLING_CRITERIA] if (t := criterion_text(c))]
        parts.append(f"- {title}: {'; '.join(crits)}" if crits else f"- {title}")
    return "\n".join(parts)[:_MAX_SIBLING_CONTEXT_LEN]


def build_criteria_messages(
    *,
    title: str,
    description: str | None,
    existing: list[Any],
    project_context: dict[str, Any],
    sibling_context: str = "",
) -> list[Message]:
    """Build the (system, user) messages asking the LLM to propose 2-5 descriptive
    acceptance criteria for ONE task. When ``existing`` is non-empty the model is
    told to REFINE/COMPLETE them (keep the good ones), never ignore them — so
    "Regenerar" takes the current criteria into account instead of starting blind.
    ``sibling_context`` (a digest of the plan's other tasks) keeps this task's
    criteria coherent with decisions a sibling already made."""
    system = (
        "Eres un asistente técnico que define CRITERIOS DE ACEPTACIÓN para una tarea "
        "de desarrollo. Responde ÚNICAMENTE con un objeto JSON, sin texto alrededor, "
        'con la forma EXACTA {"acceptance_criteria": ["<criterio>", "<criterio>"]}. '
        f"Entre 2 y {_MAX_ACCEPTANCE_CRITERIA} criterios. " + _CRITERION_RULE + " "
        "NO escribas código ni implementes la tarea: solo los criterios. Responde en "
        "el MISMO idioma que la tarea (castellano si la tarea está en castellano)."
    )
    lines = [f"Título de la tarea: {title}"]
    if description and description.strip():
        lines.append(f"Descripción de la tarea: {description.strip()}")
    ctx = _summarise_context(project_context)
    if ctx:
        lines.append(ctx)
    if sibling_context.strip():
        lines.append(
            "Criterios de las tareas HERMANAS de este plan (para COHERENCIA — NO los "
            "contradigas; en particular, si tu tarea remite a «el contrato acordado» u "
            "otra decisión compartida, RESPÉTALA y no impongas un formato o estructura "
            "distinta):\n" + sibling_context.strip()
        )
    if existing:
        # ADR 0162: `criterion_text` y no `{c}` — un criterio ejecutable llega
        # aquí como diccionario y su `repr` no es lo que hay que refinar.
        joined = "\n".join(f"- {t}" for c in existing if (t := criterion_text(c)))
        lines.append(
            "Criterios actuales (REFÍNALOS y COMPLÉTALOS conservando los que sean "
            "buenos; no los ignores):\n" + joined
        )
    user = "\n\n".join(lines)
    return [Message(role="system", content=system), Message(role="user", content=user)]


def _first_json_value(text: str) -> Any:
    """Decode the FIRST complete JSON object/array embedded in ``text``, tolerating
    leading prose, markdown fences, and trailing notes (even ones that contain
    braces — where a greedy ``{.*}`` extractor over-captures and fails). Falls back
    to the planner's greedy extractor, then ``None``."""
    s = (text or "").strip()
    decoder = json.JSONDecoder()
    for i, ch in enumerate(s):
        if ch in "{[":
            try:
                value, _end = decoder.raw_decode(s[i:])
            except json.JSONDecodeError:
                continue
            return value
    return _extract_json(s) or None


def _extract_criteria(text: str) -> list[str]:
    """Parse the LLM reply into a cleaned criteria list. Accepts the requested
    wrapper ``{"acceptance_criteria": [...]}`` and a bare JSON array (with or
    without surrounding prose); anything unusable yields ``[]``.

    **Aplana a texto A PROPÓSITO, y es el único sitio del sistema donde eso sigue
    siendo correcto (ADR 0162).** El normalizador del planner ya no aplana —ahí
    el aplanado era el defecto—, pero el contrato de ESTE endpoint es prosa: lo
    que devuelve es una PROPUESTA que un operador lee y confirma en la UI
    (``GeneratedAcceptanceCriteria.acceptance_criteria: list[str]``), y la
    reformulación de la opción A dice quién declara el comando — **quien acaba de
    escribir el test**, en ``submit_result``, no un generador que trabaja antes
    de que el código exista. Un modelo al que se le pide algo que no puede
    comprobar escribe algo *plausible*, y un ``--filter LoginTest`` inventado que
    falla se lee como «el código está roto»."""
    value = _first_json_value(text)
    raw = value.get("acceptance_criteria") if isinstance(value, dict) else value
    return [t for c in _clean_acceptance_criteria(raw) if (t := criterion_text(c))]


async def generate_task_acceptance_criteria(
    provider: LLMProvider,
    *,
    title: str,
    description: str | None,
    existing: list[Any],
    project_context: dict[str, Any],
    model: str | None,
    sibling_context: str = "",
) -> list[str]:
    """Drive ``provider`` to propose acceptance criteria for one task. Returns a
    cleaned list (possibly empty if the model produced nothing usable). Does NOT
    persist; the caller owns the provider lifecycle (``aclose``). ``sibling_context``
    keeps the proposal coherent with the plan's other tasks (see
    :func:`format_sibling_context`)."""
    messages = build_criteria_messages(
        title=title,
        description=description,
        existing=existing,
        project_context=project_context,
        sibling_context=sibling_context,
    )
    resp = await provider.complete(messages, model=model, max_tokens=1024, temperature=0.3)
    return _extract_criteria(resp.content or "")


__all__ = [
    "build_criteria_messages",
    "format_sibling_context",
    "generate_task_acceptance_criteria",
]
