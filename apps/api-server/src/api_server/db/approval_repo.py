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

from shared_domain.approval_action import action_fingerprint, canonical_tool_key, changed_args
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
from api_server.db.task_audit_repo import append_audit_event

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


# ---------------------------------------------------------------------------
# Categoría que la política NO lista — ADR 0153 (C)
# ---------------------------------------------------------------------------
# ESPEJO EXACTO de `agent_runtime.approval`. Los dos procesos no se importan
# entre sí (aquél corre dentro del sandbox, sin BD y sin api-server), así que la
# única defensa contra la deriva es el test que compara las dos implementaciones
# caso a caso (`tests/unit/test_unlisted_approval_category.py`). Si tocas una,
# toca la otra.

#: Clave HERMANA de `categories` que dice qué pasa con una categoría que el mapa
#: no nombra. Vocabulario: `auto` | `human_required`.
UNLISTED_CATEGORY_KEY = "unlisted_category"

_AUTO = "auto"
_HUMAN_REQUIRED = "human_required"
_DECISIONS = frozenset({_AUTO, _HUMAN_REQUIRED})

#: Default de :data:`UNLISTED_CATEGORY_KEY` cuando la política no la trae, según
#: su `preset`. Es el MISMO criterio con el que se siembran los cuatro presets:
#: estricto donde una acción sensible sin revisar cuesta caro, laxo donde gatear
#: lo no listado pararía los runs autónomos constantemente (y una cola de
#: aprobaciones que nadie atiende enseña a aprobar sin leer, que es peor que no
#: tener gate).
UNLISTED_DEFAULT_BY_PRESET: dict[str, str] = {
    "sandbox": _AUTO,
    "development": _AUTO,
    "production": _HUMAN_REQUIRED,
    "customer-external": _HUMAN_REQUIRED,
}

#: Sin clave y sin preset reconocible: se PARA. Ante una política que no se sabe
#: interpretar, preguntar es recuperable; dejar correr una acción sensible, no.
UNLISTED_FALLBACK_DECISION = _HUMAN_REQUIRED


def _policy_categories(policy: dict[str, Any]) -> dict[str, Any]:
    """El mapa `categories`, aceptando también la forma «mapa desnudo».

    Un `categories` que no es un mapa no se puede leer: se trata como vacío, o
    sea que TODA categoría cae al camino de lo no listado (fail-closed si la
    política tampoco declara preset), en vez de dejar pasar todo en silencio.
    """
    categories = policy.get("categories", policy)
    return categories if isinstance(categories, dict) else {}


def _unlisted_decision(policy: dict[str, Any]) -> tuple[str, str]:
    """``(decisión, por qué)`` para una categoría que la política no lista.

    El «por qué» viaja hasta el humano que recibe la aprobación: una solicitud
    sin motivo se aprueba sin leer, y esta es justo la que necesita leerse (para
    de más porque la política está incompleta, no porque la acción sea rara).
    """
    raw = policy.get(UNLISTED_CATEGORY_KEY)
    if isinstance(raw, str):
        value = raw.strip().lower()
        if value in _DECISIONS:
            return value, f"su clave `{UNLISTED_CATEGORY_KEY}` dice «{value}»"
        # Un valor ilegible (un typo de `human_required`, p. ej.) NO se resuelve
        # cayendo al preset: el autor pedía algo y no sabemos qué. Se para, y el
        # motivo lo dice para que se corrija.
        return (
            UNLISTED_FALLBACK_DECISION,
            f"su clave `{UNLISTED_CATEGORY_KEY}` tiene un valor que no se "
            f"entiende («{raw}»), así que se para por seguridad (fail-closed)",
        )
    preset = policy.get("preset")
    if isinstance(preset, str):
        slug = preset.strip().lower()
        derived = UNLISTED_DEFAULT_BY_PRESET.get(slug)
        if derived is not None:
            return derived, f"su preset es «{slug}»"
        return (
            UNLISTED_FALLBACK_DECISION,
            f"su preset («{preset}») no es reconocible, así que se para por "
            f"seguridad (fail-closed)",
        )
    return (
        UNLISTED_FALLBACK_DECISION,
        "no declara preset ni "
        f"`{UNLISTED_CATEGORY_KEY}`, así que se para por seguridad (fail-closed)",
    )


def requires_human_approval(policy: dict[str, Any] | None, category: str) -> bool:
    """True if `category` needs a human under this project's policy.

    The policy JSONB is `{"categories": {<category>: "auto" |
    "human_required"}}` (a bare `{<category>: ...}` map is also accepted). The
    ``human_question`` category (ADR 0114) is ALWAYS human-required, whatever
    the policy says.

    ADR 0153: una categoría que el mapa NO lista ya no cae a un ``"auto"`` fijo
    —fail-open—; la decide la política (``unlisted_category``), en su defecto el
    ``preset``, y si no hay nada legible se falla CERRADO.

    Una política ausente/vacía es otra cosa y NO es de este ADR: la resuelve el
    ADR 0104 heredando el preset por defecto de plataforma (en el worker,
    ``_resolve_effective_approval_policy``), así que aquí sigue devolviendo
    False — fallar cerrado aquí gatearía todo run de un proyecto recién creado
    antes de que ese preset llegue a aplicarse.
    """
    if category == HUMAN_QUESTION_CATEGORY:
        return True
    if not policy:
        return False
    categories = _policy_categories(policy)
    if category in categories:
        return str(categories[category]).strip().lower() == _HUMAN_REQUIRED
    return _unlisted_decision(policy)[0] == _HUMAN_REQUIRED


def unlisted_category_reason(policy: dict[str, Any] | None, category: str) -> str | None:
    """Por qué el gate paró en una categoría que la política NO lista.

    ``None`` cuando no aplica: la política lista la categoría (se explica sola —
    la política la nombra y la decide) o no para. La cadena solo aparece en el
    caso nuevo, el que un humano no puede deducir mirando la solicitud, y se
    persiste en la ``ApprovalRequest`` como clave hermana ``gate_reason``.
    """
    if not policy:
        return None
    if category in _policy_categories(policy):
        return None
    decision, why = _unlisted_decision(policy)
    if decision != _HUMAN_REQUIRED:
        return None
    return (
        f"La política del proyecto no lista la categoría «{category}» y {why}: "
        f"se exige revisión humana (ADR 0153)."
    )


# ---------------------------------------------------------------------------
# Qué autoriza una aprobación humana — ADR 0135 (G1+S1+T1+N3)
# ---------------------------------------------------------------------------
#: Cuántas acciones ya aprobadas de la task viajan al run siguiente (las más
#: recientes primero). Hermano del `_HUMAN_ANSWERS_MAX` del ADR 0114: la lista
#: es una CAPACIDAD que se entrega al sandbox, así que va acotada.
APPROVED_ACTIONS_MAX = 20
#: Cuántas aprobaciones previas de la task se miran para armar el delta (N3) y
#: contar las repeticiones. Mayor que el tope de arriba a propósito: contar mal
#: las repeticiones desactivaría el techo del bucle.
_PRIOR_APPROVALS_SCAN = 50


def _tool_and_args(action: Any) -> tuple[str, Any]:
    """El par ``(tool, args)`` de un ``ApprovalRequest.action`` persistido.

    Lo que se hashea es **lo que la UI enseñó**, y la UI vuelca el `action`
    entero; las anotaciones que este módulo añade para el revisor (N3) viven en
    claves HERMANAS, nunca dentro de `args`, justo para que no toquen la huella.
    """
    if not isinstance(action, dict):
        return "", None
    return str(action.get("tool") or ""), action.get("args")


async def _approved_requests_of_task(
    session: AsyncSession, *, task_id: UUID, tenant_id: UUID, limit: int
) -> list[ApprovalRequest]:
    """Las solicitudes APROBADAS de esta task, más recientes primero.

    El predicado ``tenant_id`` no es decorativo: los dos llamantes de este
    lector (el worker que monta el spec y el motor que anota el delta) corren
    con roles BYPASSRLS, donde RLS no acota nada. Es la única defensa, igual que
    en el lector hermano del ADR 0114.

    ``human_question`` queda fuera: una respuesta de ``ask_human`` (ADR 0114) no
    es la autorización de una tool —su ``args`` es la pregunta— y viaja por su
    propio raíl (``human_answers``).
    """
    result = await session.execute(
        select(ApprovalRequest)
        .where(
            ApprovalRequest.task_id == task_id,
            ApprovalRequest.tenant_id == tenant_id,
            ApprovalRequest.status == ApprovalRequestStatus.APPROVED,
            ApprovalRequest.category != HUMAN_QUESTION_CATEGORY,
        )
        .order_by(ApprovalRequest.resolved_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def read_approved_actions(
    session: AsyncSession,
    *,
    task_id: UUID,
    tenant_id: UUID,
    limit: int = APPROVED_ACTIONS_MAX,
) -> list[dict[str, Any]]:
    """Las acciones que un humano ya autorizó en ESTA task (ADR 0135).

    El worker las serializa en el spec como ``approved_actions`` y el gate del
    sandbox (:class:`agent_runtime.approval.ApprovalGate`) las canjea antes de
    aparcar. Cada entrada es ``{tool, args_hash, category, resolved_at}``: viaja
    la HUELLA, no los argumentos, porque el `args` de un ``write_file`` puede
    ser un fichero entero y el spec viaja en una variable de entorno.

    Una fila cuya acción no admite huella canónica (sin tool, args no
    serializables) se descarta: sin huella no hay comparación posible, y sin
    comparación la única respuesta segura es volver a preguntar.
    """
    rows = await _approved_requests_of_task(
        session, task_id=task_id, tenant_id=tenant_id, limit=limit
    )
    actions: list[dict[str, Any]] = []
    for row in rows:
        tool, args = _tool_and_args(row.action)
        digest = action_fingerprint(tool, args)
        if digest is None:
            continue
        actions.append(
            {
                "tool": canonical_tool_key(tool),
                "args_hash": digest,
                "category": row.category,
                "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
            }
        )
    return actions


async def _prior_approval_context(
    session: AsyncSession,
    *,
    task_id: UUID,
    tenant_id: UUID,
    tool: str,
    args: Any,
) -> dict[str, Any] | None:
    """Lo que el revisor necesita para decidir en dos segundos (N3).

    El operador eligió N3 —«re-aparcar, pero enseñando el diff»— precisamente
    porque un LLM no es determinista: si la acción es un «casi igual» de una que
    ya aprobó, la nueva solicitud lleva la anterior y el delta. Y si es la MISMA
    exacta, lleva cuántas veces la aprobó ya, que es la señal de que el bucle
    está vivo y hay que llamar a alguien en vez de seguir aprobando.

    ``None`` cuando no hay nada que contar (la solicitud queda byte a byte como
    antes de este ADR).
    """
    digest = action_fingerprint(tool, args)
    key = canonical_tool_key(tool)
    if not key:
        return None
    rows = await _approved_requests_of_task(
        session, task_id=task_id, tenant_id=tenant_id, limit=_PRIOR_APPROVALS_SCAN
    )
    exact = 0
    closest: dict[str, Any] | None = None
    for row in rows:
        prior_tool, prior_args = _tool_and_args(row.action)
        if canonical_tool_key(prior_tool) != key:
            continue
        prior_digest = action_fingerprint(prior_tool, prior_args)
        if digest is not None and prior_digest == digest:
            exact += 1
        elif closest is None:
            closest = {
                "request_id": str(row.id),
                "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
                "args": prior_args,
                "changed_args": changed_args(prior_args, args),
            }
    if not exact and closest is None:
        return None
    return {"same_action_approved_times": exact, "closest_prior": closest}


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

    ADR 0135 (N3): cuando esta acción se parece a una que el humano YA aprobó en
    esta misma task, la solicitud se persiste con una clave hermana
    ``prior_approvals`` que lleva la anterior y el delta. Es una ANOTACIÓN para
    el revisor: no toca ``tool`` ni ``args``, que es lo que se hashea.

    ADR 0153 (C): cuando lo que para la acción es que la política NO LISTA su
    categoría, la solicitud lleva además ``gate_reason`` — la misma clase de
    anotación hermana. Sin ella el revisor ve una parada que no puede explicar
    (la política no nombra esa categoría por ningún sitio) y la aprueba sin
    leer, que es exactamente el hábito que un gate nuevo no debe crear.
    """
    if not requires_human_approval(project.human_approval_policy, category):
        return None

    reason = unlisted_category_reason(project.human_approval_policy, category)
    if reason is not None:
        action = {**action, "gate_reason": reason}

    if category != HUMAN_QUESTION_CATEGORY:
        tool, args = _tool_and_args(action)
        context = await _prior_approval_context(
            session,
            task_id=execution.task_id,
            tenant_id=execution.tenant_id,
            tool=tool,
            args=args,
        )
        if context is not None:
            action = {**action, "prior_approvals": context}

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

    ADR 0135 — el techo del bucle: aprobar **gasta un reintento**. Hasta ahora
    esto no tocaba ``retry_count`` (solo lo bumpeaban los rechazos de review),
    así que aprobar→re-ejecutar→re-aparcar era literalmente infinito, y con
    coste: los presupuestos son por EJECUCIÓN, o sea que cada re-despacho
    estrenaba techo de tokens entero. Al llegar a ``max_retries`` la task queda
    ``blocked`` con un evento de auditoría legible en vez de seguir girando.
    Rechazar NO gasta reintento: ya bloquea por sí solo.
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
            task.assigned_agent_id = None
            await _spend_retry_or_block(session, task, request)
    else:
        if execution is not None:
            execution.status = ExecutionStatus.ABORTED
            execution.abort_code = APPROVAL_REJECTED_ABORT_CODE
            execution.completed_at = datetime.now(UTC)
        if task is not None:
            task.status = TaskStatus.BLOCKED

    await session.flush()
    return request


#: Motivo del bloqueo por techo de re-aprobaciones — lo lee el operador en el
#: histórico de la tarea, así que se nombra una sola vez.
APPROVAL_RETRY_CAPPED_KIND = "approval_retry_capped"


async def _spend_retry_or_block(
    session: AsyncSession, task: Task, request: ApprovalRequest
) -> None:
    """Cobra un reintento a la task aprobada y decide si aún puede re-ejecutar.

    Por debajo del techo vuelve a ``backlog`` (el comportamiento del ADR 0020);
    al alcanzarlo queda ``blocked`` con un evento de auditoría que dice cuántas
    veces se aprobó ESTA MISMA acción — que es la pregunta que se hace quien
    encuentra la tarea parada.

    ``human_question`` NO paga: responder a un ``ask_human`` (ADR 0114) no es
    re-intentar una acción, es darle al agente el dato que le faltaba, y ese
    raíl es non-terminal por diseño. Cobrárselo bloquearía una tarea por hacer
    tres preguntas legítimas — una regresión sobre una feature ya entregada, y
    fuera de lo que este ADR viene a acotar.
    """
    if request.category == HUMAN_QUESTION_CATEGORY:
        task.status = TaskStatus.BACKLOG
        return

    max_retries = task.max_retries if task.max_retries is not None else 3
    task.retry_count = (task.retry_count or 0) + 1
    if task.retry_count < max_retries:
        task.status = TaskStatus.BACKLOG
        return

    task.status = TaskStatus.BLOCKED
    tool, args = _tool_and_args(request.action)
    repeats = 0
    digest = action_fingerprint(tool, args)
    if digest is not None:
        for row in await _approved_requests_of_task(
            session,
            task_id=task.id,
            tenant_id=task.tenant_id,
            limit=_PRIOR_APPROVALS_SCAN,
        ):
            if row.id == request.id:
                continue
            prior_tool, prior_args = _tool_and_args(row.action)
            if action_fingerprint(prior_tool, prior_args) == digest:
                repeats += 1
    await append_audit_event(
        session,
        tenant_id=task.tenant_id,
        task_id=task.id,
        kind=APPROVAL_RETRY_CAPPED_KIND,
        actor="approval-engine",
        payload={
            "escalated": True,
            "reason": "approval_retry_limit_reached",
            "retry_count": task.retry_count,
            "max_retries": max_retries,
            "category": request.category,
            "tool": canonical_tool_key(tool),
            "same_action_approved_times": repeats,
        },
    )


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
