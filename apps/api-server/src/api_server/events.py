"""Domain event publisher — producer side of the `events:tasks` bus.

The orchestrator (`apps/orchestrator`) consumes this stream to drive
task assignment. The contract (stream name, fields, event types) is
documented in ADR 0011; the consumer-side mirror lives in
`orchestrator.events`.

Publishing is best-effort: a Redis blip must never fail the DB write
that triggered the event. Callers wrap nothing — `publish_task_event`
swallows and logs its own errors.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import structlog
from redis.asyncio import Redis

from api_server.db.domain import Task

_log = structlog.get_logger("api_server.events")

# Stream every task domain event lands on. Single global stream;
# consumers fan out by `tenant_id` if they need to.
EVENTS_STREAM = "events:tasks"

EVENT_TASK_CREATED = "task.created"
EVENT_TASK_STATUS_CHANGED = "task.status_changed"

# Cap the stream so a long-lived dev Redis doesn't grow unbounded.
# Approximate trimming (`~`) lets Redis trim in efficient batches.
_MAXLEN = 10_000

# Cota del stream POR PROYECTO. Mucho más corta que la del global a propósito:
# su único consumidor es el socket del tablero, que arranca en `now - 15 s`
# (`_KANBAN_REPLAY_WINDOW_MS`) y nunca mira más atrás. Guardar 10.000 entradas
# por proyecto sería pagar memoria por un histórico que nadie lee — el histórico
# de verdad son las filas de `tasks` en PostgreSQL.
_PROJECT_MAXLEN = 500

# TTL deslizante del stream por proyecto, por el mismo motivo que el de las
# ejecuciones: `maxlen` acota lo que PESA cada stream, no CUÁNTOS hay. Sin
# caducidad quedaría una clave por proyecto para siempre, incluida la de cada
# proyecto borrado. Un día basta y sobra: lo que el socket puede pedir son los
# últimos 15 segundos.
_PROJECT_STREAM_TTL_S = 24 * 3600

# TTL deslizante del stream EN VIVO de una ejecución. `maxlen` acota lo que pesa
# cada stream, pero no cuántos hay: sin caducidad, la plataforma dejaba una clave
# `exec:{id}` en Redis **por cada run, para siempre**, y eso crece de forma
# monótona con el uso. Es seguro caducarlo porque el stream es solo el canal en
# vivo — el histórico que pinta el visor sale de `executions.steps_log`, en
# PostgreSQL. Deslizante y no fijo desde la creación: un run largo sigue
# refrescándolo y no se le corta el directo por debajo.
_EXECUTION_STREAM_TTL_S = 7 * 24 * 3600


def project_task_events_stream(project_id: str) -> str:
    """Stream de eventos de tarea de UN proyecto (task_prod13_19, perf-5).

    El tablero abre un socket por proyecto. Mientras el único stream era el
    global, cada socket recibía por la red los eventos de TODOS los proyectos de
    la plataforma y descartaba en Python los que no eran suyos: el tráfico de
    cada tablero crecía con la actividad ajena.

    El nombre deriva del global a propósito (`events:tasks:{id}`) para que un
    `SCAN events:tasks*` los enumere todos — operar sobre claves que no se
    pueden encontrar es su propio problema.
    """
    return f"{EVENTS_STREAM}:{project_id}"


async def _publish(redis: Redis, fields: dict[str, str]) -> None:
    """Escribe el evento en el stream global Y en el de su proyecto.

    **Dual-write, no migración**: el global lo consume el orchestrator con un
    grupo de consumidores (`orchestrator.consumer`), que es quien despacha las
    tareas; dejar de escribirlo pararía el sistema. El por-proyecto solo alimenta
    a `/ws/kanban`. Los dos van en el MISMO pipeline: una ida y vuelta a Redis,
    igual que antes, y ningún camino en el que un evento llegue al tablero pero
    no al despachador.

    Un evento sin `project_id` (que hoy no existe: los dos publicadores lo
    ponen) se escribe solo en el global en vez de fabricar una clave
    `events:tasks:None`.
    """
    project_id = fields.get("project_id")
    try:
        # redis-py types xadd's `fields` with a wide key/value union;
        # `dict` is invariant so a plain dict[str, str] won't match the
        # annotation even though it's a valid argument at runtime.
        pipe = redis.pipeline()
        pipe.xadd(
            EVENTS_STREAM,
            fields,  # type: ignore[arg-type]
            maxlen=_MAXLEN,
            approximate=True,
        )
        if project_id:
            key = project_task_events_stream(project_id)
            pipe.xadd(
                key,
                fields,  # type: ignore[arg-type]
                maxlen=_PROJECT_MAXLEN,
                approximate=True,
            )
            pipe.expire(key, _PROJECT_STREAM_TTL_S)
        await pipe.execute()
    except Exception as exc:  # event bus is best-effort, never fail the caller
        _log.warning("api_server.event_publish_failed", error=str(exc))


async def publish_task_created(redis: Redis, task: Task) -> None:
    """Emit `task.created` after a task row is inserted."""
    await _publish(
        redis,
        {
            "type": EVENT_TASK_CREATED,
            "tenant_id": str(task.tenant_id),
            "project_id": str(task.project_id),
            "task_id": str(task.id),
            "occurred_at": datetime.now(UTC).isoformat(),
            "payload": json.dumps({"status": task.status, "priority": task.priority}),
        },
    )


async def publish_task_status_changed(
    redis: Redis, task: Task, *, old_status: str, new_status: str
) -> None:
    """Emit `task.status_changed` when a PUT moves a task's status."""
    payload: dict[str, Any] = {"old_status": old_status, "new_status": new_status}
    await _publish(
        redis,
        {
            "type": EVENT_TASK_STATUS_CHANGED,
            "tenant_id": str(task.tenant_id),
            "project_id": str(task.project_id),
            "task_id": str(task.id),
            "occurred_at": datetime.now(UTC).isoformat(),
            "payload": json.dumps(payload),
        },
    )


# ---------------------------------------------------------------------------
# Per-execution live stream (Plan 02 Fase E).
#
# Each execution gets its own Redis stream `exec:{id}` for real-time
# step events — the WebSocket `/ws/executions/{id}` tails it. Live logs
# go through Redis, not constant DB writes (ADR 0011, Plan 02 §Fase C).
# ---------------------------------------------------------------------------
def execution_stream_key(execution_id: str) -> str:
    """Redis stream key for one execution's live event log."""
    return f"exec:{execution_id}"


async def publish_execution_event(
    redis: Redis,
    execution_id: str,
    *,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    """Emit one event onto an execution's per-run stream (best-effort).

    Renueva además el TTL del stream en la MISMA ida y vuelta que el `xadd`
    (pipeline): sin caducidad quedaba una clave por run en Redis para siempre.
    """
    key = execution_stream_key(execution_id)
    try:
        pipe = redis.pipeline()
        pipe.xadd(
            key,
            {
                "type": event_type,
                "occurred_at": datetime.now(UTC).isoformat(),
                "payload": json.dumps(payload),
            },
            maxlen=_MAXLEN,
            approximate=True,
        )
        pipe.expire(key, _EXECUTION_STREAM_TTL_S)
        await pipe.execute()
    except Exception as exc:  # live stream is best-effort, never fail the caller
        _log.warning("api_server.execution_event_publish_failed", error=str(exc))


# ---------------------------------------------------------------------------
# Per-conversation live stream (Plan 03 Fase A).
#
# Each conversation gets its own Redis stream `conv:{id}` for real-time
# message events — the WebSocket `/ws/conversation/{id}` tails it. Same
# pattern as per-execution streams above.
# ---------------------------------------------------------------------------
EVENT_MESSAGE_CREATED = "message.created"
EVENT_CONVERSATION_MODE_CHANGED = "conversation.mode_changed"


def conversation_stream_key(conversation_id: str) -> str:
    """Redis stream key for one conversation's live event log."""
    return f"conv:{conversation_id}"


async def publish_conversation_event(
    redis: Redis,
    conversation_id: str,
    *,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    """Emit one event onto a conversation's per-chat stream (best-effort)."""
    try:
        await redis.xadd(
            conversation_stream_key(conversation_id),
            {
                "type": event_type,
                "occurred_at": datetime.now(UTC).isoformat(),
                "payload": json.dumps(payload),
            },
            maxlen=_MAXLEN,
            approximate=True,
        )
    except Exception as exc:  # live stream is best-effort, never fail the caller
        _log.warning("api_server.conversation_event_publish_failed", error=str(exc))


async def delete_conversation_stream(redis: Redis, conversation_id: str) -> None:
    """Drop a conversation's live stream (best-effort) so clearing or deleting a
    chat leaves NO orphan events in Redis — otherwise a later WebSocket connect
    would replay messages that no longer exist as ghost entries."""
    try:
        await redis.delete(conversation_stream_key(conversation_id))
    except Exception as exc:  # cleanup is best-effort, never fail the caller
        _log.warning("api_server.conversation_stream_delete_failed", error=str(exc))


# ---------------------------------------------------------------------------
# Per-document live stream (Plan 04 task_04_15) — KB ingestion progress.
#
# The WebSocket `/ws/documents/{document_id}` tails this. Events are
# emitted by the ingestion pipeline at each lifecycle transition
# (pending → processing → chunked → embedded → indexed) so the UI
# bar fills in real time.
# ---------------------------------------------------------------------------
EVENT_DOCUMENT_STATUS = "document.status"
EVENT_DOCUMENT_PROGRESS = "document.progress"


def document_stream_key(document_id: str) -> str:
    """Redis stream key for one document's ingestion progress."""
    return f"doc:{document_id}"


async def publish_document_event(
    redis: Redis,
    document_id: str,
    *,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    """Emit one ingestion event (best-effort, never raises)."""
    try:
        await redis.xadd(
            document_stream_key(document_id),
            {
                "type": event_type,
                "occurred_at": datetime.now(UTC).isoformat(),
                "payload": json.dumps(payload),
            },
            maxlen=_MAXLEN,
            approximate=True,
        )
    except Exception as exc:
        _log.warning("api_server.document_event_publish_failed", error=str(exc))


async def delete_document_stream(redis: Redis, document_id: str) -> None:
    """Drop a document's ingestion stream (best-effort) so deleting a document
    leaves NO orphan events in Redis — same cleanup contract as
    :func:`delete_conversation_stream`. Without this a later WebSocket connect to
    ``/ws/documents/{id}`` would replay ingestion progress for a document that no
    longer exists."""
    try:
        await redis.delete(document_stream_key(document_id))
    except Exception as exc:  # cleanup is best-effort, never fail the caller
        _log.warning("api_server.document_stream_delete_failed", error=str(exc))


# ---------------------------------------------------------------------------
# Per-owner córtex affect telemetry stream (Córtex F2, ADR 0075).
#
# The affective distiller (workers.cortex_distill_affect) publishes one frame
# per processed turn onto the owner's stream `cortex:telemetry:{owner}`; the
# WebSocket `/ws/owner/cortex/telemetry` tails it so the Panel de Mente dials
# update ~1-2s after the response (the appraisal is async, ADR 0075). Same
# best-effort, per-key-per-owner contract as the conversation streams above —
# the owner_user_id is the ONLY isolation axis (the córtex tables are
# tenant-less on BYPASSRLS, ADR 0074), so the key carries it explicitly.
#
# > Honestidad (ADR 0075 §6): the frame is a COMPUTATIONAL affect snapshot,
# > NOT real feelings. The `type:'affect'` payload is a simulation for the
# > live dials, never a claim of consciousness.
# ---------------------------------------------------------------------------
EVENT_CORTEX_AFFECT = "affect"


def cortex_telemetry_stream_key(owner_user_id: str) -> str:
    """Redis stream key for one owner's córtex affect telemetry."""
    return f"cortex:telemetry:{owner_user_id}"


async def publish_cortex_affect_event(
    redis: Redis,
    owner_user_id: str,
    *,
    payload: dict[str, Any],
) -> None:
    """Emit one affect frame onto the owner's telemetry stream (best-effort).

    Mirror of :func:`publish_conversation_event`: never raises (a Redis blip
    must never break the distiller, which already wrote its snapshot). The
    frame the WS forwards is ``{type:'affect', occurred_at, payload:{…}}`` —
    the live PAD/mood/drives + ``appraisal_reason`` the Panel de Mente plots.
    """
    try:
        await redis.xadd(
            cortex_telemetry_stream_key(owner_user_id),
            {
                "type": EVENT_CORTEX_AFFECT,
                "occurred_at": datetime.now(UTC).isoformat(),
                "payload": json.dumps(payload),
            },
            maxlen=_MAXLEN,
            approximate=True,
        )
    except Exception as exc:  # telemetry is best-effort, never fail the caller
        _log.warning("api_server.cortex_affect_event_publish_failed", error=str(exc))


async def delete_cortex_affect_stream(redis: Redis, owner_user_id: str) -> None:
    """Drop an owner's telemetry stream (best-effort) — same cleanup contract as
    :func:`delete_conversation_stream`. The decay-lazy Redis cache key
    (``cortex:affect:{owner}``) is dropped by the affect cache layer; this only
    clears the telemetry tail."""
    try:
        await redis.delete(cortex_telemetry_stream_key(owner_user_id))
    except Exception as exc:  # cleanup is best-effort, never fail the caller
        _log.warning("api_server.cortex_affect_stream_delete_failed", error=str(exc))


# ---------------------------------------------------------------------------
# Plan status stream (`task_wf_32`)
# ---------------------------------------------------------------------------
# El tablero gerencial lista los planes de TODO el tenant, así que su socket es
# de tenant y no de proyecto: uno por proyecto dejaría rancias las tarjetas de
# los demás.
#
# Stream PROPIO y no `events:tasks`: aquel lo consume el orchestrator con
# XREADGROUP para asignar tareas, y un evento de plan ahí solo le añade trabajo
# que descartar. (No lo rompería —`consumer.py` cuenta el malformado y hace
# ACK— pero mezclar dos contratos en un bus para ahorrarse una constante es
# como se acaba con un consumidor que filtra más de lo que procesa.)
PLANS_STREAM = "events:plans"

EVENT_PLAN_STATUS_CHANGED = "plan.status_changed"


async def publish_plan_status_changed(
    redis: Redis,
    *,
    plan_id: str,
    tenant_id: str,
    project_id: str,
    old_status: str,
    new_status: str,
    title: str = "",
) -> None:
    """Emite el cambio de estado de un plan (best-effort, como el resto del bus).

    Lo llaman los TRES servicios que mueven un plan: el api-server por sus
    endpoints, el orchestrator al cerrarse la última tarea y el worker de
    mantenimiento como red de seguridad. Que sea una función y no una línea
    copiada tres veces es lo que hace que el tablero vea los tres caminos —
    dos de ellos (`pending_human_validation` y `blocked`) se escriben con
    UPDATE crudo, saltándose la máquina de estados, así que engancharlo solo a
    `transition_plan_status` no emitiría ninguno de los dos.
    """
    if old_status == new_status:
        return
    try:
        await redis.xadd(
            PLANS_STREAM,
            {
                "type": EVENT_PLAN_STATUS_CHANGED,
                "tenant_id": tenant_id,
                "project_id": project_id,
                "plan_id": plan_id,
                "occurred_at": datetime.now(UTC).isoformat(),
                "payload": json.dumps(
                    {"old_status": old_status, "new_status": new_status, "title": title}
                ),
            },
            maxlen=_MAXLEN,
            approximate=True,
        )
    except Exception as exc:  # el bus es best-effort, nunca tumba al llamante
        _log.warning("api_server.plan_event_publish_failed", error=str(exc))


def publish_plan_transition_after_commit(session: Any, plan: Any, old_status: str) -> None:
    """Publica la transición de `plan` cuando la sesión del request commitee.

    Post-commit y no en línea, por lo mismo que los eventos de tarea: un
    consumidor rápido leería una fila que aún no es durable. Se usa en el
    api-server (donde `schedule_after_commit` existe); los caminos del
    orchestrator y del worker publican a mano tras su propio commit.
    """
    from api_server.auth.deps import get_redis, schedule_after_commit

    new_status = plan.status
    if old_status == new_status:
        return
    plan_id, tenant_id = str(plan.id), str(plan.tenant_id)
    project_id, title = str(plan.project_id), plan.title or ""

    async def _publish() -> None:
        await publish_plan_status_changed(
            get_redis(),
            plan_id=plan_id,
            tenant_id=tenant_id,
            project_id=project_id,
            old_status=old_status,
            new_status=new_status,
            title=title,
        )

    schedule_after_commit(session, _publish)
