"""Drain post-run de los efectos de orquestación del agente (AUD16-02).

Las tools de orquestación del runtime no tocan la plataforma (el sandbox no
tiene BD): emiten un *effect* validado que viaja dentro del step ``tool_call``
de ``steps_log``. El drain worker-side previsto en el plan 02 («Fase E») nunca
aterrizó, así que durante meses esos efectos murieron en el contenedor con
``ok=true`` — éxito falso.

Este módulo cierra el ciclo para la única tool con consumidor real:
``task_comment`` → una fila :class:`PlanComment` con ``target_kind='task'`` y
``target_ref`` = el id de la tarea en la spec del plan — exactamente la forma
que el rail comentarios→prompt del orquestador ya lee
(``dispatch._read_relevant_comments``), de modo que la nota del agente llega a
los humanos (UI del plan) y a los runs posteriores de la misma tarea.

Best-effort por contrato: un fallo aquí se loguea y JAMÁS rompe un run ya
terminado. El ``task_id`` que argumentó el agente se ignora a propósito — el
comentario se ancla SIEMPRE a la tarea del propio run (un agente no puede
comentar tareas ajenas).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_log = structlog.get_logger(__name__)

# Cota de spam: un run no puede sembrar más de N comentarios, y cada uno viaja
# truncado (mismo espíritu que los caps del rail comentarios→prompt).
_MAX_COMMENTS_PER_RUN = 10
_MAX_COMMENT_CHARS = 2_000

# Prefijo de procedencia: PlanComment.author_user_id es NULL para un agente
# (no hay fila users); el prefijo deja claro en la UI quién habla.
_AGENT_COMMENT_PREFIX = "[agente]"


def extract_task_comment_effects(steps: Any) -> list[str]:
    """Los bodies de los ``task_comment`` con efecto emitido, en orden.

    Solo cuentan los steps ``tool_call`` con ``result.ok`` y
    ``result.output.effect == 'task_comment'`` (la forma exacta que emite
    ``OrchestrationTools.task_comment``). Nombre de tool namespace-stripped
    (un MCP/custom que delegue en la builtin cuenta igual). Formas raras o
    llamadas fallidas se ignoran sin ruido — steps_log es entrada no confiable.
    """
    out: list[str] = []
    for step in steps or []:
        if not isinstance(step, dict) or step.get("kind") != "tool_call":
            continue
        tool = str(step.get("tool") or "").rsplit(".", 1)[-1]
        if tool != "task_comment":
            continue
        result = step.get("result")
        if not isinstance(result, dict) or not result.get("ok"):
            continue
        output = result.get("output")
        if not isinstance(output, dict) or output.get("effect") != "task_comment":
            continue
        body = str(output.get("body") or "").strip()
        if body:
            out.append(body[:_MAX_COMMENT_CHARS])
        if len(out) >= _MAX_COMMENTS_PER_RUN:
            break
    return out


async def drain_task_comment_effects(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    steps: Any,
    task_id: UUID,
    tenant_id: UUID,
) -> int:
    """Persiste como :class:`PlanComment` los ``task_comment`` del run.

    Devuelve cuántos comentarios se aplicaron. Sin plan asociado (task suelta)
    no hay dónde anclar el comentario → 0 con log. Best-effort: cualquier
    excepción se loguea y devuelve 0 — nunca rompe el post-proceso del run.
    """
    bodies = extract_task_comment_effects(steps)
    if not bodies:
        return 0
    try:
        from api_server.chat.sync_to_kanban import PLAN_TASK_SPEC_ID_KEY
        from api_server.db.domain import Task
        from api_server.db.plan_comment import PlanComment

        async with sessionmaker() as session, session.begin():
            task = await session.get(Task, task_id)
            if task is None or task.tenant_id != tenant_id or task.plan_id is None:
                _log.info(
                    "orchestration_drain.no_plan_anchor",
                    task_id=str(task_id),
                    found=task is not None,
                )
                return 0
            spec_id = str((task.inputs or {}).get(PLAN_TASK_SPEC_ID_KEY) or "").strip()
            for body in bodies:
                session.add(
                    PlanComment(
                        tenant_id=task.tenant_id,
                        plan_id=task.plan_id,
                        # Sin spec_id no se puede anclar a la tarea; el nivel
                        # plan conserva la nota en vez de perderla.
                        target_kind="task" if spec_id else "plan",
                        target_ref=spec_id or None,
                        author_user_id=None,
                        content=f"{_AGENT_COMMENT_PREFIX} {body}",
                    )
                )
        _log.info(
            "orchestration_drain.applied",
            task_id=str(task_id),
            comments=len(bodies),
        )
        return len(bodies)
    except Exception:
        _log.warning("orchestration_drain.failed", task_id=str(task_id), exc_info=True)
        return 0


__all__ = ["drain_task_comment_effects", "extract_task_comment_effects"]
