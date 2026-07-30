"""The human-approval engine (task_02_24 / task_02_27).

When an agent attempts a sensitive action, the engine checks the
project's `human_approval_policy`:

  * `auto`           — the action proceeds, nothing is persisted.
  * `human_required` — the execution is parked in
                       `awaiting_human_approval` and an `ApprovalRequest`
                       row is persisted for a reviewer.

A reviewer resolves the request (approve / reject); an unanswered one
times out after a configurable window (default 24 h) — the request is
marked `timed_out`, its execution aborted and its task blocked.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.domain import (
    ApprovalRequest,
    ApprovalRequestStatus,
    Execution,
    ExecutionStatus,
    Project,
    Task,
    TaskStatus,
)
from api_server.db.platform_settings import get_platform_setting

# Abort code stamped on an execution whose approval request timed out.
APPROVAL_TIMEOUT_ABORT_CODE = "approval_timeout_exceeded"
# Abort code stamped on an execution whose approval request was rejected
# by a human reviewer (ADR 0020).
APPROVAL_REJECTED_ABORT_CODE = "approval_rejected"

# ---------------------------------------------------------------------------
# Ventana de caducidad — platform setting (prod-03 task_prod03_05)
# ---------------------------------------------------------------------------
#: Horas que una solicitud `pending` puede esperar antes de caducar. El ADR 0016
#: dejó 24 h como DEFAULT explícitamente parametrizable; el job de beat
#: (`workers.expire_stale_approvals`) lo lee en cada pasada, así que cambiarlo
#: surte efecto sin reiniciar nada.
APPROVAL_TIMEOUT_HOURS_KEY = "approval.timeout_hours"
DEFAULT_APPROVAL_TIMEOUT_HOURS = 24.0
#: Suelo de cordura: por debajo de 15 min el sweep caducaría solicitudes que un
#: humano ni ha tenido tiempo de ver (y aborta la ejecución al hacerlo).
MIN_APPROVAL_TIMEOUT_HOURS = 0.25
#: Techo: más de un mes esperando no es «pendiente», es abandonada.
MAX_APPROVAL_TIMEOUT_HOURS = 720.0

#: Interruptor vivo del sweep de caducidad (System Admin). ON por defecto: sin él
#: una decisión que nadie toma cuelga la ejecución para siempre, que es
#: literalmente lo que el job existe para evitar.
APPROVAL_EXPIRY_ENABLED_KEY = "approval_expiry_enabled"
DEFAULT_APPROVAL_EXPIRY_ENABLED = True


async def get_approval_timeout_hours(session: AsyncSession) -> float:
    """La ventana de caducidad configurada, clampada al rango sano.

    Un valor no numérico o fuera de rango NO tumba el sweep ni se aplica a
    ciegas: cae al default / al extremo más cercano. Un typo en la UI no puede
    convertir el barrido en «caduca todo lo que lleve 1 segundo».
    """
    raw = await get_platform_setting(
        session, APPROVAL_TIMEOUT_HOURS_KEY, default=DEFAULT_APPROVAL_TIMEOUT_HOURS
    )
    try:
        hours = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_APPROVAL_TIMEOUT_HOURS
    if not math.isfinite(hours):  # NaN / ±inf
        return DEFAULT_APPROVAL_TIMEOUT_HOURS
    return max(MIN_APPROVAL_TIMEOUT_HOURS, min(MAX_APPROVAL_TIMEOUT_HOURS, hours))


async def get_approval_expiry_enabled(session: AsyncSession) -> bool:
    """Whether the approval-expiry sweep is currently enabled."""
    value = await get_platform_setting(
        session, APPROVAL_EXPIRY_ENABLED_KEY, default=DEFAULT_APPROVAL_EXPIRY_ENABLED
    )
    return bool(value)


# ADR 0114: la categoría del ask_human del agente. SIEMPRE requiere humano —
# preguntar a un humano es, por definición, para un humano; no depende de la
# política por categorías del proyecto. Espejo de
# agent_runtime.graph.HUMAN_QUESTION_CATEGORY (los dos paquetes no se importan).
HUMAN_QUESTION_CATEGORY = "human_question"


def requires_human_approval(policy: dict[str, Any] | None, category: str) -> bool:
    """True if `category` needs a human under this project's policy.

    The policy JSONB is `{"categories": {<category>: "auto" |
    "human_required"}}` (a bare `{<category>: ...}` map is also
    accepted). An unlisted category defaults to `auto`. The ``human_question``
    category (ADR 0114) is ALWAYS human-required, whatever the policy says.
    """
    if category == HUMAN_QUESTION_CATEGORY:
        return True
    if not policy:
        return False
    categories = policy.get("categories", policy)
    if not isinstance(categories, dict):
        return False
    return str(categories.get(category, "auto")) == "human_required"


async def request_approval_if_needed(
    session: AsyncSession,
    *,
    execution: Execution,
    project: Project,
    category: str,
    action: dict[str, Any],
) -> ApprovalRequest | None:
    """Evaluate `category` against the project's policy.

    Returns the persisted `ApprovalRequest` and parks the execution in
    `awaiting_human_approval` when a human is required; returns None
    (the action may proceed) otherwise. The caller owns the transaction.
    """
    if not requires_human_approval(project.human_approval_policy, category):
        return None

    request = ApprovalRequest(
        tenant_id=execution.tenant_id,
        execution_id=execution.id,
        task_id=execution.task_id,
        project_id=project.id,
        category=category,
        action=action,
        status=ApprovalRequestStatus.PENDING,
    )
    session.add(request)
    execution.status = ExecutionStatus.AWAITING_HUMAN_APPROVAL

    # ADR 0020: la TAREA también se aparca y el agente queda libre, para
    # que el dispatcher pueda darle otra tarea y para que el board
    # muestre la espera en una columna propia.
    task = await session.get(Task, execution.task_id)
    if task is not None and task.status != TaskStatus.AWAITING_HUMAN_APPROVAL:
        task.status = TaskStatus.AWAITING_HUMAN_APPROVAL
        task.assigned_agent_id = None

    await session.flush()
    return request


async def get_approval_request(session: AsyncSession, request_id: UUID) -> ApprovalRequest | None:
    result = await session.execute(select(ApprovalRequest).where(ApprovalRequest.id == request_id))
    return result.scalar_one_or_none()


async def list_pending_approvals(session: AsyncSession) -> list[ApprovalRequest]:
    """All pending requests, oldest first — the in-app notification feed."""
    result = await session.execute(
        select(ApprovalRequest)
        .where(ApprovalRequest.status == ApprovalRequestStatus.PENDING)
        .order_by(ApprovalRequest.requested_at)
    )
    return list(result.scalars().all())


async def claim_pending_approval(
    session: AsyncSession,
    request_id: UUID,
    *,
    new_status: ApprovalRequestStatus,
    resolved_at: datetime,
    resolver_id: UUID | None = None,
    reason: str | None = None,
) -> bool:
    """Reclamar ATÓMICAMENTE una solicitud `pending` para `new_status`.

    El guard compartido por las tres vías que cierran una solicitud (aprobar,
    rechazar, caducar). Es un `UPDATE ... WHERE id=:id AND status='pending'`:
    la comprobación y la escritura ocurren en la MISMA sentencia, así que la
    decide el motor con el row lock y no una lectura previa del proceso.

    Devuelve ``True`` si esta llamada ganó la transición (1 fila afectada) y
    ``False`` si la perdió (0 filas: otro revisor, o el job de caducidad, llegó
    primero). El llamante solo aplica las transiciones de Execution/Task cuando
    ganó — el bug era justo ese: dos resoluciones simultáneas leían `pending`
    las dos, pasaban las dos y escribían transiciones contradictorias
    (ejecución `done` Y `aborted`, tarea `backlog` Y `blocked`).

    En READ COMMITTED el segundo UPDATE se bloquea en el row lock y, al
    liberarse, RE-EVALÚA el `WHERE` contra la fila nueva: ve `approved` y afecta
    0 filas. No hace falta `SERIALIZABLE` ni un `SELECT FOR UPDATE` aparte.
    """
    result = await session.execute(
        update(ApprovalRequest)
        .where(
            ApprovalRequest.id == request_id,
            ApprovalRequest.status == ApprovalRequestStatus.PENDING,
        )
        .values(
            status=new_status,
            resolved_at=resolved_at,
            resolved_by=resolver_id,
            reason=reason,
        )
        .returning(ApprovalRequest.id)
        .execution_options(synchronize_session=False)
    )
    return result.scalar_one_or_none() is not None


async def resolve_approval(
    session: AsyncSession,
    request: ApprovalRequest,
    *,
    approved: bool,
    resolver_id: UUID | None = None,
    reason: str | None = None,
) -> ApprovalRequest | None:
    """Approve or reject a pending request — ADR 0020.

    APPROVE: the original execution closes as `done`; the task goes
    back to `backlog` with its agent cleared, so the dispatcher re-picks
    when it becomes `ready` again (the original agent may be busy with
    another task by then).

    REJECT: the task is `blocked` — the human said no, and the action
    will not be retried automatically. The reviewer's `reason` lives
    on the `ApprovalRequest` for audit (Opción B del ADR 0020, no
    implementada todavía: pasarlo de vuelta al agente como feedback).

    Devuelve ``None`` cuando la solicitud YA no estaba `pending` — otro revisor
    o el job de caducidad ganó la transición (prod-03 task_prod03_04). En ese
    caso NADA se muta: es la señal con la que el router responde 409 sin tener
    que leer el estado por su cuenta, que es de donde salía la carrera.
    """
    now = datetime.now(UTC)
    won = await claim_pending_approval(
        session,
        request.id,
        new_status=(ApprovalRequestStatus.APPROVED if approved else ApprovalRequestStatus.REJECTED),
        resolved_at=now,
        resolver_id=resolver_id,
        reason=reason,
    )
    if not won:
        return None
    # El UPDATE fue por Core (synchronize_session=False): la instancia en memoria
    # sigue con el estado viejo hasta que se relee.
    await session.refresh(request)

    execution = await session.get(Execution, request.execution_id)
    task = await session.get(Task, request.task_id)

    if approved:
        if execution is not None:
            execution.status = ExecutionStatus.DONE
            execution.completed_at = datetime.now(UTC)
        if task is not None:
            task.status = TaskStatus.BACKLOG
            task.assigned_agent_id = None
    else:
        if execution is not None:
            execution.status = ExecutionStatus.ABORTED
            execution.abort_code = APPROVAL_REJECTED_ABORT_CODE
            execution.completed_at = datetime.now(UTC)
        if task is not None:
            task.status = TaskStatus.BLOCKED

    await session.flush()
    return request


def _stale_pending_filter(cutoff: datetime, tenant_id: UUID | None) -> list[ColumnElement[bool]]:
    """Las dos condiciones de «solicitud caducable», más el scope de tenant."""
    conditions: list[ColumnElement[bool]] = [
        ApprovalRequest.status == ApprovalRequestStatus.PENDING,
        ApprovalRequest.requested_at < cutoff,
    ]
    if tenant_id is not None:
        conditions.append(ApprovalRequest.tenant_id == tenant_id)
    return conditions


async def tenants_with_stale_approvals(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    timeout_hours: float = DEFAULT_APPROVAL_TIMEOUT_HOURS,
) -> list[UUID]:
    """Los tenants que tienen alguna solicitud caducable (prod-03 task_prod03_05).

    El job de beat corre con el rol BYPASSRLS del worker, así que RLS no le
    acota nada: para no barrer «todo a la vez» y respetar el Principio nº1
    (ninguna escritura sin tenant), pide primero la lista y luego caduca
    **tenant a tenant**, cada uno en su propia transacción. Un tenant que falle
    no arrastra a los demás.
    """
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(hours=timeout_hours)
    result = await session.execute(
        select(ApprovalRequest.tenant_id)
        .where(*_stale_pending_filter(cutoff, None))
        .group_by(ApprovalRequest.tenant_id)
    )
    return list(result.scalars().all())


async def expire_stale_requests(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    timeout_hours: float = DEFAULT_APPROVAL_TIMEOUT_HOURS,
    tenant_id: UUID | None = None,
) -> list[ApprovalRequest]:
    """Time out every pending request older than `timeout_hours`.

    A timed-out request aborts its execution and blocks its task — a
    decision nobody made cannot leave the run hanging forever. Returns
    the requests that were expired.

    ``tenant_id`` acota el barrido a UN tenant (Principio nº1: el job corre con
    el rol BYPASSRLS del worker, donde RLS no acota nada, así que el scope tiene
    que ser explícito). ``None`` barre todos los tenants — la firma que usaban
    los tests del motor desde el Plan 02.

    Cada fila se cierra con el MISMO guard atómico que la resolución humana
    (:func:`claim_pending_approval`), así que la carrera aprobar-vs-timeout la
    decide la base de datos: si un revisor resolvió entre el SELECT y el UPDATE,
    esta pasada la salta en vez de pisarle la decisión (riesgo 6 del plan).
    """
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(hours=timeout_hours)
    reason = f"no response within {timeout_hours:g} h"

    result = await session.execute(
        select(ApprovalRequest).where(*_stale_pending_filter(cutoff, tenant_id))
    )
    candidates = list(result.scalars().all())

    expired: list[ApprovalRequest] = []
    for request in candidates:
        won = await claim_pending_approval(
            session,
            request.id,
            new_status=ApprovalRequestStatus.TIMED_OUT,
            resolved_at=now,
            reason=reason,
        )
        if not won:
            # Un humano la resolvió mientras barríamos. Su decisión gana.
            continue
        await session.refresh(request)

        execution = await session.get(Execution, request.execution_id)
        if execution is not None:
            execution.status = ExecutionStatus.ABORTED
            execution.abort_code = APPROVAL_TIMEOUT_ABORT_CODE
        task = await session.get(Task, request.task_id)
        if task is not None:
            task.status = TaskStatus.BLOCKED
        expired.append(request)

    await session.flush()
    return expired
