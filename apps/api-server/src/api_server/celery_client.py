"""Lightweight Celery producer for api-server (Plan 06.11 task_06_11_01).

api-server runs **no** Celery tasks — it only *enqueues* them onto the
shared broker by name (the `workers` package owns the implementations).
A bare ``Celery(broker=...)`` is all `send_task` needs, so we never
import the `workers` package: that keeps the app boundary clean and
mirrors `orchestrator.dispatch`, which enqueues `workers.run_execution`
the same way.

The single producer here is `enqueue_ingestion`, called by
`upload_document` right after the document row is flushed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from functools import lru_cache
from typing import Any
from uuid import UUID

import structlog
from celery import Celery

from api_server.config import get_settings

_log = structlog.get_logger("api_server.celery_client")

_INGEST_TASK = "workers.ingest_document"
_INGEST_QUEUE = "ingestion"

# The notification-dispatcher send task + its default lane (Plan 10). The
# api-server only PRODUCES onto the shared broker by name — it never imports
# the notification_dispatcher package (same clean app boundary as ingestion).
# The dispatcher owns the implementation, the retry/backoff policy, and the
# DLQ. The manual-retry endpoint (task_10_13) re-enqueues a dead-lettered send
# through this producer.
_SEND_NOTIFICATION_TASK = "notification_dispatcher.send_notification"
_NOTIFICATIONS_DEFAULT_QUEUE = "notifications.default"

# The notification-dispatcher fan-out task (Plan 10 task_10_04): given a
# domain event ({event_type, tenant_id, context}) it resolves the tenant's
# subscribed channels (most-specific-wins preferences, quiet-hours, template
# render) and enqueues one send per surviving channel. The api-server only
# PRODUCES it by name (clean app boundary — never imports the dispatcher).
# Plan 11 task_11_21 fires a `guardrail_alert` event through this path.
_DISPATCH_EVENT_TASK = "notification_dispatcher.dispatch_event"
_EVENTS_PRIORITY_QUEUE = "notifications.priority"

# The restore background jobs (Plan 12 task_12_12). A restore is LONG +
# DESTRUCTIVE, so the api-server NEVER runs it inline — it enqueues one of these
# by name onto the privileged lane (a restore touches infra + secrets) and then
# polls the job's status via the result backend (`get_restore_job_status`). The
# `workers.restore_task` module owns the implementation + the double-confirmation
# / verify-before-restore / per-tenant-isolation guards.
_RESTORE_FULL_TASK = "workers.run_restore"
_RESTORE_PER_TENANT_TASK = "workers.run_restore_per_tenant"
_RESTORE_QUEUE = "privileged"

# Las sondas de destino remoto de backup (prod-15 task_gov_app_boundary_11,
# hallazgo api-9). Hasta 2026-08-19 el router las ejecutaba EN PROCESO, con dos
# `from workers…` diferidos que construían boto3/paramiko/rclone dentro del
# api-server. No era sólo acoplamiento: los adaptadores resuelven sus
# credenciales de `os.environ` DEL PROCESO QUE LOS EJECUTA, y el api-server no
# declara ninguna `WORKERS_*` — las credenciales de destino viven en la lane
# `privileged` (servicio `workers-backup`). O sea que la sonda daba FAIL en
# cuanto el destino tenía credencial. Ahora se encolan POR NOMBRE, como el
# restore, y corren donde están los secretos (`workers.backup_probe_task`).
_BACKUP_TEST_DESTINATION_TASK = "workers.backup_test_destination"
_BACKUP_LIST_REMOTE_TASK = "workers.backup_list_remote"
# La MISMA lane que el restore, porque es la única que lleva las
# `WORKERS_BACKUP_*`. Corre con `--concurrency=1` y drena también el backup
# nocturno, así que las sondas van con `expires`: ver `_probe_backup_worker`.
_BACKUP_PROBE_QUEUE = _RESTORE_QUEUE

# Las puertas de seguridad del marketplace (prod-13 `task_prod13_01`, hallazgo
# perf-1 de la auditoría de producción). bandit + semgrep por `subprocess` con
# 120 s de plazo CADA UNO, más la prueba de humo del sandbox: hasta cuatro
# minutos. `asyncio.to_thread` ya evitaba que congelasen el event loop, pero no
# los saca del HTTP — el request sigue durando lo mismo y lo corta el proxy.
#
# Lane PROPIA, y no una de las que ya había, porque ninguna puede absorber un
# trabajo de cuatro minutos sin un riesgo ya documentado en este repo: `default`
# y `test`/`review` los drenan pools de `--concurrency=2` que también atienden
# los agent-runs y `stack_exec` (la auto-inanición que motivó `workers-aux`: un
# run bloqueado esperando la cola `test` ocupa el slot que esa cola necesita), y
# `privileged` va a `--concurrency=1` detrás del backup nocturno. Declarar la
# cola obliga a drenarla: el ADR 0083 retiró `heavy` y `gpu` por ser colas sin
# consumidor, y `tests/unit/test_compose_generator.py` compara las colas drenadas
# con `QUEUE_NAMES`, así que una lane huérfana se pone roja sola.
#
# Estas constantes son PÚBLICAS a propósito: `workers.marketplace_gates` declara
# las suyas y un test compara las dos parejas. Un nombre distinto en cada lado
# deja el mensaje en el broker para siempre y el endpoint devuelve 202 igual.
MARKETPLACE_GATES_TASK = "workers.marketplace_run_install_gates"
MARKETPLACE_GATES_QUEUE = "marketplace"

# The human Memorizer task (Plan 16 task_16_15). When a human task reaches
# `done` (auto_approve submit, or a peer reviewer's approval) the inbox/review
# endpoint enqueues this by name so the Memorizer distils the HumanWorkSession
# into MemoryEntries. The `workers.memorizer` module owns the implementation;
# the api-server only PRODUCES it by name (clean app boundary, same as the
# execution Memorizer trigger that lives in the workers package).
_MEMORIZE_HUMAN_WS_TASK = "workers.memorize_human_work_session"
_MEMORIZE_QUEUE = "default"

# The córtex affective distiller (Córtex F2, ADR 0075). After a córtex turn is
# persisted, POST /owner/cortex/turns enqueues this by name so the distiller
# (Ollama-local, fail-open) scores the turn → delta PAD + razón off the hot-path
# — the dial updates ~1-2s after the answer. The `workers.cortex_affect` module
# owns the implementation; the api-server only PRODUCES it by name (clean app
# boundary, same as the Memorizer trigger).
_CORTEX_DISTILL_AFFECT_TASK = "workers.cortex_distill_affect"
_CORTEX_AFFECT_QUEUE = "default"

# The córtex identity reflection (Córtex F3, ADR 0074/0077). A background loop
# (scheduled by F4's beat) that synthesises the owner's recent turns into a
# rewritten narrative + a CLAMPED trait/baseline adjustment, versioned and never
# auto-forgotten. The `POST /owner/cortex/reflect` endpoint also fires it by name
# for a manual/test pass. Ollama-local, fail-open. The `workers.cortex_reflection`
# module owns the implementation; the api-server only PRODUCES it by name.
_CORTEX_REFLECT_TASK = "workers.cortex_reflect"
_CORTEX_REFLECT_QUEUE = "default"


@lru_cache(maxsize=1)
def get_celery_client() -> Celery:
    """Process-global producer bound to the broker. Cached so we don't
    rebuild the connection pool on every enqueue.

    Also wired to the result backend so `AsyncResult(job_id)` resolves the state
    a background task wrote (the restore job's progress/result, task_12_12). The
    api-server still only PRODUCES + READS state — it runs no tasks."""
    settings = get_settings()
    return Celery(broker=settings.broker_url, backend=settings.result_backend)


def reset_celery_client_cache() -> None:
    """Drop the cached client (tests that swap the broker URL)."""
    get_celery_client.cache_clear()


async def enqueue_ingestion(document_id: UUID) -> bool:
    """Hand a freshly-uploaded document to the ingestion worker.

    Best-effort: a broker failure is logged and swallowed so the upload
    still returns 201 — the document is already persisted as `pending`
    and the beat sweep `workers.sweep_pending_documents` re-enqueues it.
    Returns True iff the task was published.

    `send_task` does blocking socket I/O, so we run it off the event
    loop (same approach as `orchestrator.dispatch`).
    """
    try:
        await asyncio.to_thread(
            get_celery_client().send_task,
            _INGEST_TASK,
            args=[str(document_id)],
            queue=_INGEST_QUEUE,
        )
    except Exception as exc:
        _log.warning("ingestion.enqueue_failed", document_id=str(document_id), error=str(exc))
        return False
    return True


async def enqueue_marketplace_install_gates(*, installation_id: UUID, tenant_id: UUID) -> bool:
    """Encola las puertas de seguridad de una instalación (prod-13 `task_prod13_01`).

    NO es best-effort como `enqueue_ingestion`, y la diferencia importa: un
    documento que no se encola sigue `pending` y lo re-encola el barrido de beat,
    mientras que una instalación que no se encola se queda en `analyzing` sin
    nadie que la mueva. Por eso aquí el fallo se **devuelve** (False) en vez de
    tragarse: el llamante lo escribe en el informe de puertas, así que el cliente
    que consulta el recurso de estado ve que la petición se aceptó y el análisis
    no arrancó, en lugar de esperar un veredicto que no va a llegar.

    Por el broker viajan sólo dos identificadores. Nada de config, nada de
    permisos, nada de rutas: el worker lo lee todo de la BD bajo el `tenant_id`
    que se le pasa, que es también la única forma de que un mensaje del broker no
    pueda ampliar por sí mismo el alcance de lo que la task toca.

    `send_task` hace I/O de socket bloqueante, así que va fuera del event loop
    (mismo patrón que el resto del módulo).
    """
    try:
        await asyncio.to_thread(
            get_celery_client().send_task,
            MARKETPLACE_GATES_TASK,
            kwargs={"installation_id": str(installation_id), "tenant_id": str(tenant_id)},
            queue=MARKETPLACE_GATES_QUEUE,
        )
    except Exception as exc:
        _log.warning(
            "marketplace.gates.enqueue_failed",
            installation_id=str(installation_id),
            error=str(exc),
        )
        return False
    return True


async def enqueue_clone_project_repo(project_id: UUID) -> bool:
    """Encola el clone/fetch autenticado del repo de un proyecto (ADR 0072).

    Best-effort: un fallo del broker se loguea y se traga — la config git ya está
    persistida y el clone se puede re-disparar (acción "Sincronizar"). Corre el
    ``send_task`` (I/O bloqueante) fuera del event loop."""
    try:
        await asyncio.to_thread(
            get_celery_client().send_task,
            "workers.clone_project_repo",
            args=[str(project_id)],
            queue="default",
        )
    except Exception as exc:
        _log.warning("clone_repo.enqueue_failed", project_id=str(project_id), error=str(exc))
        return False
    return True


async def run_stack_command_and_wait(
    *, tenant_id: UUID, task_id: UUID, command: str, timeout_s: int, cwd: str | None = None
) -> dict[str, Any]:
    """Enqueue ``workers.run_stack_command`` and BLOCK for its result (ADR 0093).

    Unlike the fire-and-forget enqueues above, ``stack_exec`` is synchronous: the
    agent needs ``rc``+logs back before continuing, so we wait on the result
    backend. The command runs in the project's runtime template (where the
    toolchain exists), NOT in the agent sandbox. Routed to the ``test`` queue so
    it never competes with the agent-run slot that is blocked waiting on this
    (deadlock avoidance, ADR 0093). The blocking send+get runs off the event loop.
    """
    request = {
        "tenant_id": str(tenant_id),
        "task_id": str(task_id),
        "command": command,
        "timeout_s": int(timeout_s),
        # ADR 0093 (2026-07-24): optional working dir relative to the worktree.
        "cwd": cwd,
    }

    def _send_and_wait() -> dict[str, Any]:
        async_result = get_celery_client().send_task(
            "workers.run_stack_command", args=[request], queue="test"
        )
        # Wait the command's own budget + a margin for container spin-up/teardown.
        result = async_result.get(timeout=timeout_s + 120)
        return dict(result) if isinstance(result, dict) else {}

    return await asyncio.to_thread(_send_and_wait)


async def compute_plan_code_diff_and_wait(
    *,
    tenant_slug: str,
    project_slug: str,
    plan_id: str,
    plan_slug: str,
    timeout_s: int = 60,
) -> dict[str, Any]:
    """Delegar el diff de código de un plan al WORKER y BLOQUEAR por su resultado.

    El git corre sobre el bare real, que solo el worker ve (posee el volumen
    ``agent-data`` y el ``data_root`` correcto; la api-server no lo monta → si lo
    calculara en proceso daría ``FileNotFoundError`` → 500). Mismo patrón síncrono
    que :func:`run_stack_command_and_wait`. Devuelve el dict del worker
    (``{ok: True, ...}`` / ``{ok: False, error}``); un fallo de broker/timeout se
    traduce a ``{ok: False, error}`` para que el endpoint responda 404, nunca 500."""
    request = {
        "tenant_slug": tenant_slug,
        "project_slug": project_slug,
        "plan_id": plan_id,
        "plan_slug": plan_slug,
    }

    def _send_and_wait() -> dict[str, Any]:
        async_result = get_celery_client().send_task(
            "workers.compute_plan_code_diff", args=[request], queue="default"
        )
        result = async_result.get(timeout=timeout_s)
        return dict(result) if isinstance(result, dict) else {"ok": False, "error": "empty result"}

    try:
        return await asyncio.to_thread(_send_and_wait)
    except Exception as exc:
        _log.warning("code_diff.enqueue_failed", plan_id=plan_id, error=str(exc))
        return {"ok": False, "error": "diff worker unavailable"}


async def enqueue_open_plan_pr(project_id: UUID, plan_id: UUID, *, title: str, body: str) -> bool:
    """Encola el auto-PR de un plan (ADR 0072 fase 2): push autenticado de la rama
    + apertura del PR/MR por proveedor. La rama se deriva en el worker de
    ``plan_id`` + ``title``. Best-effort."""
    try:
        await asyncio.to_thread(
            get_celery_client().send_task,
            "workers.open_plan_pr",
            args=[str(project_id), str(plan_id), title, body],
            queue="default",
        )
    except Exception as exc:
        _log.warning("plan_pr.enqueue_failed", project_id=str(project_id), error=str(exc))
        return False
    return True


async def enqueue_compose_review_runtime(request: dict[str, Any]) -> bool:
    """Encola el spawn de un review-runtime / app-preview (ADR 0062/0130).

    Reutiliza la task ``workers.compose_review_runtime`` (cola ``review``): crea
    la fila ``review_sessions``, resuelve el worktree y lanza el contenedor. El
    ``request`` lleva ``kind`` ('plan'|'preview'), ``plan_id`` opcional,
    ``preview_ref`` (rama a previsualizar) y ``expires_in_seconds``. Best-effort:
    un fallo del broker no rompe el endpoint (el operador reintenta)."""
    try:
        await asyncio.to_thread(
            get_celery_client().send_task,
            "workers.compose_review_runtime",
            kwargs={"request": request},
            queue="review",
        )
    except Exception as exc:
        _log.warning("review_runtime.enqueue_failed", error=str(exc))
        return False
    return True


async def revoke_execution_job(job_id: str) -> bool:
    """Revoke a *queued* execution Celery job (cooperative cancellation).

    Dropped before it starts if still queued. NO ``terminate``: hard-killing the
    worker child of an already-running job would orphan its agent container (which
    is what actually burns LLM budget). A running job is stopped by the worker's
    cooperative poll of ``cancel_requested_at`` (it kills the container and
    finalises as ``cancelled``). Best-effort: the DB flag is the source of truth,
    so a broker failure is logged and swallowed. ``control.revoke`` does blocking
    socket I/O, so it runs off the event loop. Returns True iff revoke was published.
    """
    try:
        await asyncio.to_thread(get_celery_client().control.revoke, job_id)
    except Exception as exc:
        _log.warning("execution.revoke_failed", job_id=job_id, error=str(exc))
        return False
    return True


def revoke_job_callback(job_id: str) -> Callable[[], Awaitable[None]]:
    """An after-commit callback (for ``schedule_after_commit``) that revokes a
    queued Celery job, dropping the bool result so it matches the
    ``Awaitable[None]`` contract. A factory (not a default-arg lambda) so the
    captured ``job_id`` is unambiguous and mypy can infer the type. Shared by the
    task/plan/project cancellation paths (prod-06 cancel_01/cancel_02)."""

    async def _cb() -> None:
        await revoke_execution_job(job_id)

    return _cb


async def enqueue_notification_send(
    send_request: dict[str, Any],
    *,
    queue: str = _NOTIFICATIONS_DEFAULT_QUEUE,
) -> bool:
    """Re-enqueue one notification send onto the dispatcher's lane (task_10_13).

    Used by the manual-retry endpoint to re-drive a dead-lettered
    ``NotificationLog`` through the notification-dispatcher's normal send path
    (which owns the retry/backoff + DLQ policy). ``send_request`` is the same
    JSON-safe payload ``notification_dispatcher.tasks.SendRequest.as_dict``
    produces (``channel_id`` / ``event_type`` / ``tenant_id`` / ``target`` /
    ``body`` / ``structured``).

    Returns True iff the task was published. ``send_task`` does blocking socket
    I/O, so we run it off the event loop (same approach as `enqueue_ingestion`).
    A broker failure raises so the caller can surface it (the endpoint has
    already not committed its log row in that case — unlike the best-effort
    ingestion enqueue, a manual retry that can't reach the broker must fail
    loudly rather than silently drop the user's action).
    """
    await asyncio.to_thread(
        get_celery_client().send_task,
        _SEND_NOTIFICATION_TASK,
        args=[send_request],
        queue=queue,
    )
    return True


async def enqueue_event_dispatch(
    event: dict[str, Any],
    *,
    queue: str = _EVENTS_PRIORITY_QUEUE,
) -> bool:
    """Fan a domain event out to its subscribed channels via the dispatcher.

    Enqueues ``notification_dispatcher.dispatch_event`` (task_10_04) onto the
    dispatcher's lane. ``event`` is the JSON-safe payload the dispatcher's
    ``IncomingEvent.from_dict`` expects (``event_type`` / ``tenant_id`` /
    ``context`` / optional ``locale``). The dispatcher owns recipient
    resolution (the tenant's subscribed channels / Tenant-Admin preferences),
    quiet-hours, template render, and the per-channel send + retry/DLQ — the
    api-server never imports it (clean app boundary).

    Best-effort: a broker failure is logged and swallowed (returns False) so
    the work that produced the event still completes — an alert is a
    notification, not a transaction the caller must roll back on a broker
    outage. ``send_task`` does blocking socket I/O, so we run it off the event
    loop (same approach as :func:`enqueue_ingestion`).
    """
    try:
        await asyncio.to_thread(
            get_celery_client().send_task,
            _DISPATCH_EVENT_TASK,
            args=[event],
            queue=queue,
        )
    except Exception as exc:
        _log.warning(
            "event_dispatch.enqueue_failed",
            event_type=str(event.get("event_type", "")),
            tenant_id=str(event.get("tenant_id") or ""),
            error=str(exc),
        )
        return False
    return True


async def enqueue_memorize_human_work_session(work_session_id: UUID) -> bool:
    """Hand a finished HumanWorkSession to the Memorizer (Plan 16 task_16_15).

    Called by the inbox submit (``auto_approve``) and the peer-review approve
    endpoints right after the Task reaches ``done`` — the human's deliverable
    is final, so the Memorizer can distil it into MemoryEntries cited back at
    this work session.

    Best-effort: a broker failure is logged and swallowed (returns False) so the
    human's delivery is never rolled back on a Memorizer-side outage — the
    memory is a nice-to-have, not part of the delivery transaction.
    ``send_task`` does blocking socket I/O, so we run it off the event loop
    (same approach as :func:`enqueue_ingestion`).
    """
    try:
        await asyncio.to_thread(
            get_celery_client().send_task,
            _MEMORIZE_HUMAN_WS_TASK,
            args=[str(work_session_id)],
            queue=_MEMORIZE_QUEUE,
        )
    except Exception as exc:
        _log.warning(
            "memorize_human_ws.enqueue_failed",
            work_session_id=str(work_session_id),
            error=str(exc),
        )
        return False
    return True


async def enqueue_cortex_distill_affect(turn_id: UUID) -> bool:
    """Hand a freshly-persisted córtex turn to the affective distiller (Córtex F2).

    Called by ``POST /owner/cortex/turns`` right after the cortex turn row is
    committed — fire-and-forget, off the hot-path. The distiller scores the turn
    (Ollama-local, fail-open) and writes a ``cortex_affect_snapshots`` row + the
    live Redis state + a telemetry frame; the appraisal NEVER blocks the answer.

    Best-effort: a broker failure is logged and swallowed (returns False) so the
    turn the owner already received is never rolled back on a distiller-side
    outage (the affect dial is a nice-to-have, not part of the turn transaction).
    ``send_task`` does blocking socket I/O, so we run it off the event loop (same
    approach as :func:`enqueue_ingestion`).
    """
    try:
        await asyncio.to_thread(
            get_celery_client().send_task,
            _CORTEX_DISTILL_AFFECT_TASK,
            args=[str(turn_id)],
            queue=_CORTEX_AFFECT_QUEUE,
        )
    except Exception as exc:
        _log.warning("cortex_affect.enqueue_failed", turn_id=str(turn_id), error=str(exc))
        return False
    return True


async def enqueue_cortex_reflection(owner_user_id: UUID) -> bool:
    """Trigger one córtex identity-reflection pass for an owner (Córtex F3).

    Called by ``POST /owner/cortex/reflect`` for a manual/test pass (F4's beat
    schedules the recurring cadence). The reflection synthesises recent turns into
    a rewritten narrative + a clamped trait/baseline adjustment (Ollama-local,
    fail-open), versioned in ``cortex_identity_history``.

    Best-effort: a broker failure is logged and swallowed (returns False) so a
    manual trigger that can't reach the broker degrades gracefully (the reflection
    is a background nice-to-have, not a transaction). ``send_task`` does blocking
    socket I/O, so we run it off the event loop (same approach as
    :func:`enqueue_ingestion`).
    """
    try:
        await asyncio.to_thread(
            get_celery_client().send_task,
            _CORTEX_REFLECT_TASK,
            args=[str(owner_user_id)],
            queue=_CORTEX_REFLECT_QUEUE,
        )
    except Exception as exc:
        _log.warning(
            "cortex_reflection.enqueue_failed", owner_user_id=str(owner_user_id), error=str(exc)
        )
        return False
    return True


async def enqueue_restore(
    backup_id: str,
    *,
    confirm: str,
    tenant_id: str | None = None,
) -> str:
    """Enqueue a restore background job (Plan 12 task_12_12) and return its job id.

    A restore is LONG + DESTRUCTIVE, so it runs as a Celery background job, NOT
    inline on this request thread. When ``tenant_id`` is ``None`` a FULL-stack
    restore (``workers.run_restore``) is enqueued; otherwise a SELECTIVE
    per-tenant restore (``workers.run_restore_per_tenant``) that touches ONLY
    that tenant's data.

    ``confirm`` is the double-confirmation token the operator supplied — it is
    forwarded verbatim to the task, where the engine refuses unless it matches
    (the full restore wants the bundle id; the per-tenant restore wants
    ``<tenant_id>@<backup_id>``). The api-server does NOT re-derive or weaken it.

    Returns the Celery job id the UI then polls via :func:`get_restore_job_status`.
    A broker failure RAISES so the endpoint surfaces it (a restore the operator
    explicitly triggered must fail loudly, never be silently dropped).
    ``send_task`` does blocking socket I/O, so we run it off the event loop.
    """
    if tenant_id is None:
        async_result = await asyncio.to_thread(
            get_celery_client().send_task,
            _RESTORE_FULL_TASK,
            args=[backup_id],
            kwargs={"confirm": confirm},
            queue=_RESTORE_QUEUE,
        )
    else:
        async_result = await asyncio.to_thread(
            get_celery_client().send_task,
            _RESTORE_PER_TENANT_TASK,
            args=[backup_id],
            kwargs={"tenant_id": tenant_id, "confirm": confirm},
            queue=_RESTORE_QUEUE,
        )
    return str(async_result.id)


async def _probe_backup_worker(task_name: str, config: dict[str, Any], *, timeout_s: float) -> Any:
    """Encolar una sonda de backup y ESPERAR su resultado. ``None`` si no llega.

    Mismo patrón síncrono que :func:`compute_plan_code_diff_and_wait`: el
    api-server produce por nombre y relaya lo que devuelve el worker, así que el
    contrato HTTP de los dos endpoints no cambia — sólo cambia **dónde** se
    ejecuta la red.

    Dos plazos, y no son redundantes:

    * ``expires`` — si la sonda no ha ARRANCADO cuando vence, el broker la
      descarta. La lane ``privileged`` es ``--concurrency=1`` y drena el backup
      nocturno; sin esto, una sonda encolada detrás de un backup de media hora se
      ejecutaría cuando el operador hace rato que cerró el panel. Una sonda de
      alcanzabilidad caducada no vale nada: mejor no correrla.
    * ``get(timeout=…)`` — acota la ESPERA de este proceso. Sin él, el hilo del
      executor se queda colgado del backend de resultados para siempre, y
      ``to_thread`` no puede matarlo.

    Cualquier fallo —broker caído, plazo vencido, la tarea reventó— devuelve
    ``None``, que el llamante traduce a un fallo acotado con motivo. NUNCA se
    propaga: un botón de «probar conectividad» que da 500 no le dice nada al
    operador que un FAIL con detalle no le diga mejor.
    """

    def _send_and_wait() -> Any:
        async_result = get_celery_client().send_task(
            task_name,
            args=[config],
            queue=_BACKUP_PROBE_QUEUE,
            expires=timeout_s,
        )
        return async_result.get(timeout=timeout_s)

    try:
        return await asyncio.to_thread(_send_and_wait)
    except Exception as exc:
        _log.warning(
            "backup.probe.enqueue_failed",
            task=task_name,
            destination=str(config.get("name", "")),
            error=str(exc)[:300],
        )
        return None


async def probe_backup_destination_and_wait(
    config: dict[str, Any], *, timeout_s: float
) -> dict[str, Any] | None:
    """«Probar conectividad» de un destino, ejecutado en el worker.

    ``config`` es la config NO SECRETA del destino (``{type, name, …}``), la que
    ``validate_backup_destinations`` deja pasar por su allow-list — ninguna
    credencial viaja por el broker; las resuelve el worker desde su entorno.

    Devuelve el ``{"ok": bool, "detail": str}`` del worker, o ``None`` si no
    contestó dentro del plazo.
    """
    result = await _probe_backup_worker(_BACKUP_TEST_DESTINATION_TASK, config, timeout_s=timeout_s)
    return dict(result) if isinstance(result, dict) else None


async def list_remote_backup_entries_and_wait(
    config: dict[str, Any], *, timeout_s: float
) -> list[str] | None:
    """Nombres de los objetos de UN destino remoto, enumerados en el worker.

    Una llamada por destino (no una por lista) para que el «best-effort por
    destino» del listado de restore siga siendo por destino: uno inalcanzable no
    puede vaciar la lista de los demás.

    Devuelve la lista de nombres, o ``None`` si el worker no contestó — que el
    llamante trata igual que un destino que falla: se salta.
    """
    result = await _probe_backup_worker(_BACKUP_LIST_REMOTE_TASK, config, timeout_s=timeout_s)
    return [str(name) for name in result] if isinstance(result, list) else None


async def get_restore_job_status(job_id: str) -> dict[str, Any]:
    """Read a restore background job's status from the result backend (task_12_12).

    Returns a JSON-safe ``{job_id, state, progress, result, error}`` snapshot the
    UI polls:

      * ``state``    — Celery's task state (``PENDING`` / ``PROGRESS`` /
        ``SUCCESS`` / ``FAILURE``).
      * ``progress`` — the ``{phase, message}`` meta the task reported while
        in-flight (``PROGRESS``), else ``None``.
      * ``result``   — the engine's JSON-safe result dict on ``SUCCESS``, else
        ``None``.
      * ``error``    — a non-leaky error string on ``FAILURE``, else ``None``.

    Reading ``AsyncResult`` touches the result backend (blocking socket I/O), so
    we run it off the event loop. The api-server only READS this state — it runs
    no tasks.
    """
    return await asyncio.to_thread(_read_restore_status, job_id)


def _read_restore_status(job_id: str) -> dict[str, Any]:
    """Synchronous AsyncResult read (run via ``to_thread``)."""
    async_result = get_celery_client().AsyncResult(job_id)
    state = str(async_result.state)
    progress: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error: str | None = None

    if state == "SUCCESS":
        raw = async_result.result
        if isinstance(raw, dict):
            result = raw
    elif state == "FAILURE":
        # `async_result.result` is the exception instance; str() is non-leaky
        # (the engines never echo secret material into their error messages).
        error = str(async_result.result)
    else:
        # PENDING / PROGRESS / any custom in-flight state — the meta carries the
        # {phase, message} the task reported (None for an unknown/PENDING job).
        info = async_result.info
        if isinstance(info, dict):
            progress = info

    return {
        "job_id": job_id,
        "state": state,
        "progress": progress,
        "result": result,
        "error": error,
    }


__all__ = [
    "enqueue_clone_project_repo",
    "enqueue_cortex_distill_affect",
    "enqueue_cortex_reflection",
    "enqueue_event_dispatch",
    "enqueue_ingestion",
    "enqueue_memorize_human_work_session",
    "enqueue_notification_send",
    "enqueue_open_plan_pr",
    "enqueue_restore",
    "get_celery_client",
    "get_restore_job_status",
    "list_remote_backup_entries_and_wait",
    "probe_backup_destination_and_wait",
    "reset_celery_client_cache",
]


async def enqueue_browse_session(session_id: UUID) -> bool:
    """Lanza la sesión de navegación que el owner acaba de APROBAR (ADR 0080).

    No es best-effort silencioso: si el broker falla, el llamador lo sabe y se
    lo dice al owner — una sesión aprobada que nunca corre es peor que un error
    (el córtex se quedaría esperando un resultado que no va a llegar). La fila
    queda en ``approved``, así que re-aprobar/reintentar es seguro."""
    try:
        await asyncio.to_thread(
            get_celery_client().send_task,
            "workers.browse_session",
            args=[str(session_id)],
            queue="default",
        )
    except Exception as exc:
        _log.warning("browse.enqueue_failed", session_id=str(session_id), error=str(exc))
        return False
    return True
