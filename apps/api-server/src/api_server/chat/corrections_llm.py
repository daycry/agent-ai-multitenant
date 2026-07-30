"""Tareas correctivas desde el motivo de rechazo humano (ADR 0107).

Cuando el validador rechaza un plan con un `rejection_reason`, este módulo
convierte ese motivo en 1-N tareas correctivas con el MISMO contrato JSON
de tareas que ``LLMPlanningModel.pm_plan_draft`` — así el resto del ciclo
(spec del plan, sync al Kanban, asignación por rol) las trata como a
cualquier otra tarea. Patrón calcado de :mod:`criteria_llm`: helper async
provider-agnóstico que NO persiste; el router es dueño del provider y del
spec.

La normalización es deliberadamente defensiva (el LLM devuelve prosa,
ids duplicados, deps inventadas...): ids únicos con prefijo ``fix-``,
``depends_on`` acotado a ids finales de la tanda + ids existentes del
plan, criterios limpiados con el cleaner del planner y ``origin:
"correction"`` en todas.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from shared_llm.base import LLMProvider
from shared_llm.types import Message

from api_server.chat.criteria_llm import _CRITERION_RULE, _first_json_value
from api_server.chat.plan_corrections import CORRECTION_ORIGIN
from api_server.chat.planning_llm import (
    _DEFAULT_COMPLEXITY,
    _VALID_COMPLEXITY,
    _clean_acceptance_criteria,
)

# Techos que mantienen el prompt acotado con motivos largos (markdown del
# validador) o planes grandes.
_MAX_REASON_LEN = 4000
_MAX_DIGEST_TASKS = 40
_MAX_DIGEST_LEN = 4000
_MAX_CORRECTIVE_TASKS = 10


def _existing_tasks_digest(existing_tasks: list[dict[str, Any]]) -> str:
    """``- id [rol] título`` por tarea existente: suficiente para que el LLM
    referencie dependencias reales y no re-proponga trabajo ya entregado."""
    lines: list[str] = []
    for task in existing_tasks[:_MAX_DIGEST_TASKS]:
        tid = str(task.get("id") or "").strip()
        title = str(task.get("title") or "").strip()
        if not tid or not title:
            continue
        role = str(task.get("role") or "").strip()
        lines.append(f"- {tid} [{role}] {title}" if role else f"- {tid} {title}")
    return "\n".join(lines)[:_MAX_DIGEST_LEN]


def build_corrections_messages(
    *,
    rejection_reason: str,
    plan_title: str,
    plan_summary: str,
    existing_tasks: list[dict[str, Any]],
) -> list[Message]:
    """Mensajes (system, user) que piden convertir el motivo de rechazo en
    tareas correctivas mínimas con el contrato JSON de ``pm_plan_draft``."""
    roles = sorted({str(t.get("role") or "").strip() for t in existing_tasks} - {""})
    system = (
        "Eres el PROJECT MANAGER procesando el RECHAZO de la validación humana de un "
        "plan YA EJECUTADO. El validador ha rechazado la entrega con un motivo. "
        "Convierte ese motivo en las tareas CORRECTIVAS mínimas y accionables que lo "
        "resuelven. Responde ÚNICAMENTE con un objeto JSON, sin texto alrededor, con "
        'esta forma EXACTA:\n{"tasks": [{"id": "fix-1", "title": "<acción>", '
        '"description": "<qué corregir y dónde>", "role": "<rol>", '
        '"complexity": "<xs|s|m|l|xl>", "depends_on": [], '
        '"acceptance_criteria": ["<criterio verificable>"]}]}\n'
        "Reglas: ids únicos con prefijo fix-; `depends_on` referencia SOLO ids "
        "declarados en tu respuesta o ids de las tareas EXISTENTES del plan; NO "
        "re-hagas trabajo ya entregado — solo lo que corrige el motivo; tareas "
        "atómicas y las MÍNIMAS necesarias. Cada tarea lleva 2-5 "
        "`acceptance_criteria`. " + _CRITERION_RULE + " NO implementes ni escribas "
        "código: solo las tareas. Responde en el MISMO idioma que el motivo. "
        f"Roles del equipo: {roles or '(genérico)'}."
    )
    lines = [f"Plan rechazado: {plan_title}"]
    if plan_summary.strip():
        lines.append(f"Resumen del plan: {plan_summary.strip()[:1000]}")
    digest = _existing_tasks_digest(existing_tasks)
    if digest:
        lines.append("Tareas EXISTENTES del plan (ya ejecutadas):\n" + digest)
    lines.append(
        "MOTIVO DEL RECHAZO del validador humano:\n" + rejection_reason.strip()[:_MAX_REASON_LEN]
    )
    return [
        Message(role="system", content=system),
        Message(role="user", content="\n\n".join(lines)),
    ]


def _unique_fix_id(base: str, taken: set[str]) -> str:
    """``fix-<base>`` único frente a los ids ya tomados (spec + tanda)."""
    candidate = base if base.startswith("fix-") else f"fix-{base}"
    if candidate not in taken:
        return candidate
    n = 2
    while f"{candidate}-{n}" in taken:
        n += 1
    return f"{candidate}-{n}"


def normalise_corrective_tasks(raw: Any, existing_ids: Iterable[str]) -> list[dict[str, Any]]:
    """Coerciona la respuesta del LLM (``{"tasks": [...]}`` o lista suelta) en
    tareas correctivas válidas para `specification.tasks`. Descarta lo
    inutilizable; con nada usable devuelve ``[]``."""
    raw_tasks = raw.get("tasks") if isinstance(raw, dict) else raw
    if not isinstance(raw_tasks, list):
        return []

    known = set(existing_ids)
    taken = set(known)
    tasks: list[dict[str, Any]] = []
    raw_to_final: dict[str, str] = {}
    for i, t in enumerate(raw_tasks[:_MAX_CORRECTIVE_TASKS]):
        if not isinstance(t, dict):
            continue
        title = str(t.get("title") or t.get("name") or "").strip()
        if not title:
            continue
        raw_id = str(t.get("id") or f"c{i + 1}").strip() or f"c{i + 1}"
        final_id = _unique_fix_id(raw_id, taken)
        taken.add(final_id)
        raw_to_final.setdefault(raw_id, final_id)
        complexity = str(t.get("complexity") or "").strip().lower()
        if complexity not in _VALID_COMPLEXITY:
            complexity = _DEFAULT_COMPLEXITY
        tasks.append(
            {
                "id": final_id,
                "title": title[:255],
                "description": str(t.get("description") or "").strip(),
                "role": str(t.get("role") or "").strip(),
                "complexity": complexity,
                "depends_on": [str(d) for d in (t.get("depends_on") or []) if isinstance(d, str)],
                "acceptance_criteria": _clean_acceptance_criteria(t.get("acceptance_criteria")),
                "origin": CORRECTION_ORIGIN,
            }
        )

    final_ids = {t["id"] for t in tasks}
    for t in tasks:
        deps: list[str] = []
        for dep in t["depends_on"]:
            resolved = raw_to_final.get(dep, dep)
            if resolved == t["id"]:
                continue
            if resolved in final_ids or resolved in known:
                deps.append(resolved)
        t["depends_on"] = deps
    return tasks


async def generate_corrective_tasks(
    provider: LLMProvider,
    *,
    rejection_reason: str,
    plan_title: str,
    plan_summary: str,
    existing_tasks: list[dict[str, Any]],
    model: str | None,
) -> list[dict[str, Any]]:
    """Pide al provider las tareas correctivas y devuelve la lista normalizada
    (vacía si el modelo no produjo nada usable). NO persiste; el caller es
    dueño del ciclo de vida del provider (``aclose``)."""
    messages = build_corrections_messages(
        rejection_reason=rejection_reason,
        plan_title=plan_title,
        plan_summary=plan_summary,
        existing_tasks=existing_tasks,
    )
    resp = await provider.complete(messages, model=model, max_tokens=2048, temperature=0.3)
    existing_ids = [str(t.get("id") or "") for t in existing_tasks]
    return normalise_corrective_tasks(_first_json_value(resp.content or ""), existing_ids)


__all__ = [
    "build_corrections_messages",
    "generate_corrective_tasks",
    "normalise_corrective_tasks",
]
