"""Los dos gates que cablean el motor en las RUTAS reales (prod-03 task_prod03_14).

`run_planning_chat_guardrails` y `gate_generate_plan` existen desde el Plan 11
(task_11_22) con test de integración propio y **cero llamantes fuera de tests**:
ni `routers/conversations.py` ni `routers/plans.py` importaban nada de
`api_server.guardrails`. El roadmap del Plan 11 daba `task_11_22` por cableada
(guardrails-9). Esto es el cableado que faltaba.

Vive en su propio módulo, y no dentro de los routers, por dos razones: los dos
routers necesitan lo mismo (correr el motor y decidir qué hacer con un `block`),
y porque el trozo que no es obvio —el de abajo— merece explicarse una sola vez.

Por qué el evento del turno BLOQUEADO se persiste aparte
--------------------------------------------------------
`run_planning_chat_guardrails` persiste sus eventos en la sesión que se le pasa,
y el dueño de la transacción es el llamante. En una ruta de FastAPI esa sesión
la commitea la dependencia **al terminar la request**… si la request termina
bien. Un `block` termina en `HTTPException`, la dependencia hace rollback, y con
él se iría el evento — justo el que más importa, porque es el único turno que la
plataforma llegó a DETENER.

Así que en la rama de bloqueo el evento se vuelve a registrar en una sesión
propia que se commitea sola. No hay riesgo de duplicado: el primer registro
muere con el rollback. Lo que sí hay es una escritura de más en el camino
excepcional, que es exactamente donde se puede pagar.

La config efectiva
------------------
El pipeline se construye con la config efectiva del tenant/proyecto
(`get_effective_guardrail_config`, task_prod03_08), no con el baseline de
planning a secas: si la plataforma bloqueó `pii` o el tenant añadió sus propios
checks, el chat de planning es precisamente uno de los sitios donde tienen que
correr — el texto lo escribe un humano y puede traer cualquier cosa.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from fastapi import HTTPException, status
from shared_guardrails.pipeline import GuardrailPipeline
from shared_guardrails.types import HookPoint, PipelineDecision
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.guardrails.events import GuardrailEventContext, record_pipeline_decision
from api_server.guardrails.planning import (
    AGENT_LABEL_CHAT,
    AGENT_LABEL_GENERATION,
    PlanGateResult,
    build_planning_chat_pipeline,
    gate_generate_plan,
    run_planning_chat_guardrails,
)

_log = structlog.get_logger(__name__)


async def _effective_pipeline(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project_id: UUID | None,
    base: GuardrailPipeline,
) -> GuardrailPipeline:
    """El pipeline del baseline de planning MÁS la config efectiva de las capas.

    Si no hay capas configuradas se usa el baseline tal cual. Best-effort: un
    fallo resolviendo no puede dejar el chat sin responder, así que degrada al
    baseline y lo registra.
    """
    try:
        from api_server.db.guardrail_config import get_effective_guardrail_config

        effective = await get_effective_guardrail_config(
            session, tenant_id=tenant_id, project_id=project_id
        )
        if not effective:
            return base
        merged: dict[str, list[Any]] = {
            hook: list(specs) for hook, specs in base.config.to_dict()["guardrails"].items()
        }
        for hook, specs in (effective.get("guardrails") or {}).items():
            merged.setdefault(hook, []).extend(specs)
        return GuardrailPipeline.from_dict({"guardrails": merged})
    except Exception as exc:  # el baseline es la red de seguridad
        _log.warning("guardrails.route_gate_config_failed", error=str(exc))
        return base


async def _persist_blocked_event(
    decision: PipelineDecision,
    *,
    tenant_id: UUID,
    project_id: UUID | None,
    agent_label: str,
) -> None:
    """Re-registra el evento del turno bloqueado en su propia transacción.

    Ver el docstring del módulo: el 422 va a hacer rollback de la sesión de la
    request, y el evento del único turno que la plataforma DETUVO no puede ser
    el que se pierda. Best-effort: si esto falla, el bloqueo sigue en pie.
    """
    try:
        from api_server.db.session import get_admin_sessionmaker

        sessionmaker = get_admin_sessionmaker()
        async with sessionmaker() as db, db.begin():
            await record_pipeline_decision(
                db,
                decision,
                context=GuardrailEventContext(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    agent_label=agent_label,
                ),
            )
    except Exception as exc:  # pragma: no cover - defensivo
        _log.warning("guardrails.blocked_event_persist_failed", error=str(exc))


def _blocked_reasons(decision: PipelineDecision) -> list[str]:
    return [o.detail for o in decision.triggered_outcomes if o.detail]


async def gate_planning_turn(
    session: AsyncSession,
    *,
    hook: HookPoint,
    text: str,
    tenant_id: UUID,
    project_id: UUID | None,
) -> PipelineDecision:
    """Corre el motor sobre un turno del chat de planning. 422 si bloquea.

    Devuelve la decisión cuando el turno pasa (puede traer avisos: `warn` es
    advisory y ya quedó registrado como evento).
    """
    pipeline = await _effective_pipeline(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        base=build_planning_chat_pipeline(),
    )
    decision = await run_planning_chat_guardrails(
        session,
        hook=hook,
        text=text,
        tenant_id=tenant_id,
        project_id=project_id,
        pipeline=pipeline,
    )
    if not decision.allowed:
        await _persist_blocked_event(
            decision,
            tenant_id=tenant_id,
            project_id=project_id,
            agent_label=AGENT_LABEL_CHAT,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "error": "guardrail_blocked",
                "hook": hook,
                "action": decision.action.value if decision.action else None,
                "reasons": _blocked_reasons(decision),
            },
        )
    return decision


async def gate_plan_generation(
    session: AsyncSession,
    *,
    draft: dict[str, Any],
    tenant_id: UUID,
    project_id: UUID | None,
) -> PlanGateResult:
    """Gate estructural delante de «Generar Plan». 422 con el detalle si falla.

    Se invoca **solo** cuando hay borrador de verdad (ver el llamante): un plan
    vacío es un estado legítimo del producto —se crea la carcasa y se rellena
    después— y pasarlo por un esquema que exige `summary` y al menos una tarea
    convertiría el gate en un bloqueo de la creación de planes.
    """
    result = await gate_generate_plan(
        session, draft=draft, tenant_id=tenant_id, project_id=project_id
    )
    if not result.allowed:
        await _persist_blocked_event(
            result.decision,
            tenant_id=tenant_id,
            project_id=project_id,
            agent_label=AGENT_LABEL_GENERATION,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "error": "plan_draft_rejected",
                "feedback": list(result.feedback),
            },
        )
    return result


__all__ = ["gate_plan_generation", "gate_planning_turn"]
