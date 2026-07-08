"""Ciclo de correcciones tras el rechazo humano de un plan (ADR 0107).

Cuando el validador rechaza un plan en `pending_human_validation`, el
motivo (`rejection_reason` de la sesión de review) puede convertirse en
tareas correctivas que viven en el MISMO plan:

  - las tareas van al listado plano `specification.tasks` con el
    marcador ``origin: "correction"`` (así el sync scope=selection las
    materializa sin cambios);
  - la meta del ciclo vive en `specification.corrections[]`:
    ``{session_id, reason, task_ids, created_at, status}`` con
    ``status`` ∈ {proposed, accepted}.

`plans.specification` es JSONB: la columna solo se marca dirty al
REEMPLAZAR el dict completo (no usamos ``flag_modified`` en el código),
por eso todos los helpers de aquí devuelven un dict nuevo y no mutan el
de entrada.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

CORRECTION_ORIGIN = "correction"

CORRECTION_STATUS_PROPOSED = "proposed"
CORRECTION_STATUS_ACCEPTED = "accepted"


def mark_corrections_accepted(spec: dict[str, Any], task_ids: Iterable[str]) -> dict[str, Any]:
    """Devuelve un spec NUEVO con las entradas de `corrections` cuya
    ``task_ids`` interseca la selección marcadas como aceptadas.

    La intersección se acumula en ``accepted_task_ids`` (unión ordenada,
    sin duplicados) para que un re-accept parcial no pierda historia.
    Entradas sin intersección quedan intactas.
    """
    selected = set(task_ids)
    new_spec = dict(spec)
    corrections: list[dict[str, Any]] = []
    for raw in new_spec.get("corrections") or []:
        entry = dict(raw)
        hit = set(entry.get("task_ids") or []) & selected
        if hit:
            entry["status"] = CORRECTION_STATUS_ACCEPTED
            previous = entry.get("accepted_task_ids") or []
            entry["accepted_task_ids"] = sorted(set(previous) | hit)
        corrections.append(entry)
    new_spec["corrections"] = corrections
    return new_spec


__all__ = [
    "CORRECTION_ORIGIN",
    "CORRECTION_STATUS_ACCEPTED",
    "CORRECTION_STATUS_PROPOSED",
    "mark_corrections_accepted",
]
