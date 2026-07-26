"""Etiquetas convenidas de `memory_entries.tags`, compartidas entre apps.

`tags` es libre por diseño, pero unas pocas etiquetas son un **contrato**: las
escribe un worker y las lee el api-server. Vivir en `shared-domain` es lo que
impide que las dos mitades se desincronicen — la misma razón por la que
`tool_names.is_unwired_platform_builtin` acabó aquí.
"""

from __future__ import annotations

__all__ = ["RETRO_TAG", "retro_plan_tag"]

#: Marca una memoria como retrospectiva de plan (la escribe el beat `plan_retro`).
RETRO_TAG = "plan_retro"


def retro_plan_tag(plan_id: str) -> str:
    """Ata una retrospectiva a SU plan (`task_wf_34`).

    Las retros se guardaban con `tags` fijo a `["plan_retro"]`, así que una vez
    escritas no había forma de saber de qué plan eran y el detalle del plan no
    podía enseñar la suya: se escribían para nadie. Va en `tags` y no en una
    columna nueva porque `memory_entries` ya tiene ese campo para exactamente
    esto, y una migración para un puntero opcional no se paga.

    Las retros escritas ANTES de este cambio no llevan la etiqueta y no se
    pueden atribuir: se degradan (el plan simplemente no enseña retro) en vez de
    intentar un backfill por coincidencia de texto, que emparejaría mal en
    cuanto dos planes del mismo proyecto compartan título.
    """
    return f"plan:{plan_id}"
