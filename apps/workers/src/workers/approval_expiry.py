"""Sweep de caducidad de aprobaciones (prod-03 task_prod03_05).

`expire_stale_requests` existía desde el Plan 02 —implementada, testeada y con
su propio ADR (0016)— y **nadie la llamaba**: un `grep` en `apps/` solo
encontraba su definición. Consecuencia real: una solicitud que ningún humano
atiende deja la ejecución en `awaiting_human_approval` PARA SIEMPRE, ocupando
la tarea y sin que nada lo cierre. El propio ADR 0016 lo anotaba como pendiente
(«falta el job periódico (Celery beat) que la invoque — es wiring de
despliegue») y así se quedó dos meses. Es el patrón dominante de esta base:
mecanismo entregado, cero llamantes.

Este módulo es ese llamante: la task Celery ``workers.expire_stale_approvals``,
cableada al beat cada 15 min por :mod:`workers.beat_schedule`.

Multi-tenancy
-------------
El sweep escribe con el rol BYPASSRLS del worker, donde RLS **no acota nada**.
Así que el scope es explícito y por tenant: primero
:func:`tenants_with_stale_approvals` dice qué tenants tienen algo que caducar, y
luego se caduca **tenant a tenant, cada uno en su propia transacción**. Un
tenant que falle no arrastra a los demás, y ninguna escritura sale sin
`tenant_id`. Un tenant no puede disparar ni programar este sweep: la cadencia
vive en el proceso beat de plataforma y el interruptor es un platform setting que
solo un System Admin escribe.

Best-effort
-----------
Como los demás jobs de beat (:mod:`workers.human_escalation`,
:mod:`workers.fx_fetcher`), un fallo se registra y NO se propaga: beat tiene que
seguir tickeando.

Carrera con el revisor
----------------------
Comparte el guard atómico de la resolución humana
(``approval_repo.claim_pending_approval``): si un humano resuelve entre el SELECT
y el UPDATE, esta pasada se salta la fila en vez de pisar su decisión. Sin ese
guard, añadir este job habría introducido una SEGUNDA carrera — por eso el plan
exige el orden task_prod03_04 → task_prod03_05.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker

from workers.celery_app import app
from workers.config import Settings, get_settings
from workers.db import worker_engine

_log = structlog.get_logger("workers.approval_expiry")

#: Evento de notificación que emite el timeout. Se REUTILIZA ``task_blocked``,
#: que ya está en el registro cerrado del dispatcher
#: (``notification_dispatcher.event_mapping.EVENT_REGISTRY``) y fanea a los
#: admins del tenant: al caducar, la tarea acaba precisamente `blocked`. Un
#: `approval_timed_out` nuevo habría que darlo de alta en el registro, en el
#: catálogo del api-server y en las plantillas —tres ficheros de otros carriles—
#: y hasta entonces el dispatcher lo DESCARTA en silencio. El enrutado de
#: alertas propio queda en prod-08, como dice el plan.
APPROVAL_TIMEOUT_EVENT = "task_blocked"


@dataclass(frozen=True)
class ApprovalTimeoutNotice:
    """Una notificación que el sweep quiere enviar.

    Misma forma que :class:`workers.human_escalation.EscalationNotice`:
    ``event_type`` + ``tenant_id`` (que acota el fan-out) + ``context`` para la
    plantilla. Se construyen dentro de la transacción y se envían DESPUÉS de
    commitear — un broker caído no puede deshacer una caducidad ya persistida.
    """

    event_type: str
    tenant_id: str
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CeleryApprovalExpiryNotifier:
    """Notificador por defecto — encola el evento por NOMBRE en la vía priority.

    El worker solo PRODUCE ``notification_dispatcher.dispatch_event``; nunca
    importa el paquete del dispatcher (frontera limpia entre apps), igual que
    :class:`workers.human_escalation.CeleryHumanEscalationNotifier`.
    """

    broker_url: str
    dispatch_task: str = "notification_dispatcher.dispatch_event"
    priority_queue: str = "notifications.priority"

    def notify(self, notice: ApprovalTimeoutNotice) -> None:
        from celery import Celery

        Celery(broker=self.broker_url).send_task(
            self.dispatch_task,
            args=[
                {
                    "event_type": notice.event_type,
                    "tenant_id": notice.tenant_id,
                    "context": dict(notice.context),
                }
            ],
            queue=self.priority_queue,
        )


@app.task(name="workers.expire_stale_approvals")  # type: ignore[untyped-decorator]
def expire_stale_approvals() -> dict[str, Any]:
    """Caduca las solicitudes de aprobación sin atender (scheduled).

    Honra el platform setting ``approval_expiry_enabled`` (el OFF vivo de un
    System Admin) y lee la ventana de ``approval.timeout_hours`` (default 24 h)
    en CADA pasada, así que cambiarla surte efecto sin reiniciar el beat.
    Best-effort: un fallo se registra, nunca se lanza.
    """
    settings = get_settings()
    notifier = CeleryApprovalExpiryNotifier(broker_url=settings.broker_url)
    return asyncio.run(_expire_stale_approvals(settings, notifier=notifier))


async def _collect_notices(
    session: Any,
    expired: list[Any],
) -> list[ApprovalTimeoutNotice]:
    """Construye los avisos de las solicitudes caducadas, dentro de la txn.

    Necesita el título de la tarea y el nombre del proyecto, que no viven en la
    fila de la solicitud. Se leen aquí (la sesión sigue abierta) y el aviso
    resultante ya es un objeto plano: enviarlo no vuelve a tocar la BD.
    """
    from api_server.db.domain import Project, Task

    notices: list[ApprovalTimeoutNotice] = []
    for request in expired:
        task = await session.get(Task, request.task_id)
        project = await session.get(Project, request.project_id)
        notices.append(
            ApprovalTimeoutNotice(
                event_type=APPROVAL_TIMEOUT_EVENT,
                tenant_id=str(request.tenant_id),
                context={
                    "task_id": str(request.task_id),
                    "task_title": task.title if task is not None else "",
                    "project_name": project.name if project is not None else "",
                    "approval_request_id": str(request.id),
                    "approval_category": str(request.category),
                    "reason": request.reason or "approval request timed out",
                },
            )
        )
    return notices


async def _expire_stale_approvals(
    settings: Settings,
    *,
    notifier: Any | None,
) -> dict[str, Any]:
    """Núcleo async — es dueño del ciclo de vida del engine.

    ``notifier`` es inyectable para que los tests capturen los avisos sin broker.
    """
    # Import perezoso — evita pagar el coste de importar api_server en workers
    # que nunca enrutan el beat (igual que workers.human_escalation).
    from api_server.db.approval_repo import (
        expire_stale_requests,
        get_approval_expiry_enabled,
        get_approval_timeout_hours,
        tenants_with_stale_approvals,
    )

    engine = worker_engine(settings)
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

        async with sessionmaker() as db:
            if not await get_approval_expiry_enabled(db):
                _log.info("approval_expiry.skipped", reason="disabled")
                return {"enabled": False, "skipped": True}
            timeout_hours = await get_approval_timeout_hours(db)
            tenants: list[UUID] = await tenants_with_stale_approvals(
                db, timeout_hours=timeout_hours
            )

        expired_total = 0
        failed_tenants = 0
        notices: list[ApprovalTimeoutNotice] = []
        for tenant_id in tenants:
            try:
                async with sessionmaker() as db, db.begin():
                    expired = await expire_stale_requests(
                        db, timeout_hours=timeout_hours, tenant_id=tenant_id
                    )
                    notices.extend(await _collect_notices(db, list(expired)))
                    expired_total += len(expired)
            except Exception as exc:  # un tenant no arrastra a los demás
                failed_tenants += 1
                _log.warning(
                    "approval_expiry.tenant_error", tenant_id=str(tenant_id), error=str(exc)
                )
    except Exception as exc:  # pragma: no cover — defensivo: beat no debe morir
        _log.warning("approval_expiry.error", error=str(exc))
        return {"enabled": True, "ok": False, "error": str(exc)}
    finally:
        await engine.dispose()

    # Fuera de la transacción: la caducidad ya está persistida, así que un broker
    # caído solo cuesta el aviso, no la consistencia.
    if notifier is not None:
        for notice in notices:
            try:
                notifier.notify(notice)
            except Exception as exc:  # pragma: no cover — best-effort
                _log.warning("approval_expiry.notify_failed", error=str(exc))

    _log.info(
        "approval_expiry.done",
        tenants=len(tenants),
        expired=expired_total,
        timeout_hours=timeout_hours,
    )
    return {
        "enabled": True,
        "ok": True,
        "tenants": len(tenants),
        "expired": expired_total,
        "failed_tenants": failed_tenants,
        "timeout_hours": timeout_hours,
    }


__all__ = [
    "APPROVAL_TIMEOUT_EVENT",
    "ApprovalTimeoutNotice",
    "CeleryApprovalExpiryNotifier",
    "expire_stale_approvals",
]
