"""Standup diario del PM agente (ADR 0120).

El beat corre CADA HORA (``workers.daily_standup``); por cada tenant con el
standup habilitado cuya hora configurada coincide con la hora actual UTC se
compone el parte del día: hecho ayer, en curso, bloqueado/escalado, esperando
validación humana y coste LLM de ayer. Los DATOS los calcula SQL
(:func:`_collect_tenant_summary`); el LLM solo REDACTA la prosa — y si el
proveedor falla, se envía la versión estructurada tal cual (fail-open: el
parte nunca se pierde por un proveedor caído). La entrega va por el pipeline
de notificaciones existente como evento ``daily_standup`` (inbox del tenant +
canales configurados), producido por nombre de task hacia la cola del
dispatcher — mismo patrón productor que la rotación de credenciales.

Configuración (platform_settings, aplicable a todos los tenants en v1):
``standup.enabled`` (default True) y ``standup.hour`` (hora UTC, default 8).
La cadencia horaria del beat hace de gate natural de idempotencia: la hora
configurada solo coincide una vez al día; si beat estuviera parado justo esa
hora, ese día no hay parte (aceptado en el ADR — sin marker adicional).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

import structlog

from workers.celery_app import app
from workers.config import Settings, get_settings

_log = structlog.get_logger(__name__)

STANDUP_EVENT = "daily_standup"


@dataclass(frozen=True)
class TenantStandupConfig:
    tenant_id: UUID
    enabled: bool
    hour: int


@dataclass(frozen=True)
class StandupSummary:
    tasks_done_yesterday: int
    plans_closed_yesterday: int
    tasks_in_progress: int
    tasks_blocked: int
    runs_waiting_human: int
    cost_usd_yesterday: float


class StandupNotifier(Protocol):
    def publish(self, event: dict[str, Any]) -> None: ...


def _format_structured(summary: StandupSummary, *, date_label: str) -> str:
    """La versión SIN LLM del parte: determinista, completa, siempre válida.

    Es el cuerpo de fail-open cuando el proveedor no responde y la base
    factual que el LLM recibe para redactar (el LLM nunca inventa los datos).
    """
    return (
        f"Standup {date_label}\n"
        f"- Hecho ayer: {summary.tasks_done_yesterday} tareas, "
        f"{summary.plans_closed_yesterday} planes cerrados\n"
        f"- En curso: {summary.tasks_in_progress} tareas\n"
        f"- Bloqueadas: {summary.tasks_blocked}\n"
        f"- Esperando validación humana: {summary.runs_waiting_human}\n"
        f"- Coste LLM de ayer: ${summary.cost_usd_yesterday:.2f}"
    )


async def _redact(llm: Any, structured: str) -> str:
    """Prosa breve del PM sobre la base factual; fail-open a la estructurada."""
    try:
        response = await llm.complete(
            [
                {
                    "role": "system",
                    "content": (
                        "Eres el Project Manager. Redacta el standup diario en 3-5 "
                        "frases claras en español a partir de estos datos EXACTOS — "
                        "no inventes cifras ni omitas lo que espera a un humano:"
                    ),
                },
                {"role": "user", "content": structured},
            ]
        )
        text = str(getattr(response, "content", "") or "").strip()
        return text or structured
    except Exception:
        _log.warning("standup.redact_failed_fallback_structured", exc_info=True)
        return structured
    finally:
        close = getattr(llm, "aclose", None)
        if close is not None:
            with contextlib.suppress(Exception):  # cierre best-effort
                await close()


async def _run_standup(
    *,
    tenants: list[TenantStandupConfig],
    now: datetime,
    collector: Callable[[UUID], Awaitable[StandupSummary]],
    llm_factory: Callable[[UUID], Any],
    notifier: StandupNotifier,
) -> dict[str, int]:
    """Núcleo puro: gate por hora → collector → redacción → evento.

    Un tenant que falla (collector o publicación) se salta con log y NO
    frena a los demás — el beat mantiene su cadencia pase lo que pase.
    """
    sent = 0
    skipped = 0
    for tenant in tenants:
        if not tenant.enabled or tenant.hour != now.hour:
            skipped += 1
            continue
        try:
            summary = await collector(tenant.tenant_id)
            date_label = now.date().isoformat()
            structured = _format_structured(summary, date_label=date_label)
            body = await _redact(llm_factory(tenant.tenant_id), structured)
            notifier.publish(
                {
                    "event_type": STANDUP_EVENT,
                    "tenant_id": str(tenant.tenant_id),
                    "context": {
                        "date": date_label,
                        "standup_body": body,
                        # Los datos crudos viajan también: los canales que
                        # prefieran plantilla estructurada no dependen de la prosa.
                        "tasks_done_yesterday": summary.tasks_done_yesterday,
                        "runs_waiting_human": summary.runs_waiting_human,
                    },
                }
            )
            sent += 1
        except Exception:
            _log.exception("standup.tenant_failed", tenant_id=str(tenant.tenant_id))
            skipped += 1
    return {"sent": sent, "skipped": skipped}


# ---------------------------------------------------------------------------
# Cableado real (SQL + settings + Celery) — cubierto en integración.
# ---------------------------------------------------------------------------
@dataclass
class CeleryStandupNotifier:
    """Productor por NOMBRE hacia la cola del dispatcher (frontera limpia de
    apps: el worker nunca importa el paquete del dispatcher — mismo patrón
    que la rotación de credenciales)."""

    broker_url: str
    dispatch_task: str = "notification_dispatcher.dispatch_event"
    queue: str = "notifications.default"

    def publish(self, event: dict[str, Any]) -> None:
        from celery import Celery

        Celery(broker=self.broker_url).send_task(self.dispatch_task, args=[event], queue=self.queue)


async def _load_tenant_configs(sessionmaker: Any) -> list[TenantStandupConfig]:
    """Tenants activos + settings de plataforma (v1: config global para todos)."""
    from api_server.db.platform_settings import get_platform_setting
    from sqlalchemy import text as sa_text

    async with sessionmaker() as session:
        enabled = bool(await get_platform_setting(session, "standup.enabled", default=True))
        hour = int(await get_platform_setting(session, "standup.hour", default=8))
        rows = await session.execute(
            sa_text("SELECT id FROM organizations WHERE deleted_at IS NULL")
        )
        return [
            TenantStandupConfig(tenant_id=row[0], enabled=enabled, hour=hour)
            for row in rows.fetchall()
        ]


async def _collect_tenant_summary(sessionmaker: Any, tenant_id: UUID) -> StandupSummary:
    """Los números del parte, por SQL directo (admin engine del worker)."""
    from sqlalchemy import text as sa_text

    now = datetime.now(tz=UTC)
    day_start = datetime(now.year, now.month, now.day, tzinfo=UTC)
    yesterday_start = day_start - timedelta(days=1)

    async with sessionmaker() as session:
        params = {"tid": str(tenant_id), "y0": yesterday_start, "y1": day_start}
        done = await session.execute(
            sa_text(
                "SELECT count(*) FROM tasks WHERE tenant_id = :tid AND status = 'done' "
                "AND updated_at >= :y0 AND updated_at < :y1"
            ),
            params,
        )
        plans = await session.execute(
            sa_text(
                "SELECT count(*) FROM plans WHERE tenant_id = :tid "
                "AND status IN ('completed', 'pending_human_validation') "
                "AND updated_at >= :y0 AND updated_at < :y1"
            ),
            params,
        )
        in_progress = await session.execute(
            sa_text("SELECT count(*) FROM tasks WHERE tenant_id = :tid AND status = 'in_progress'"),
            params,
        )
        blocked = await session.execute(
            sa_text("SELECT count(*) FROM tasks WHERE tenant_id = :tid AND status = 'blocked'"),
            params,
        )
        waiting = await session.execute(
            sa_text(
                "SELECT count(*) FROM executions WHERE tenant_id = :tid "
                "AND status IN ('needs_human_review', 'awaiting_human_approval')"
            ),
            params,
        )
        cost = await session.execute(
            sa_text(
                "SELECT coalesce(sum(total_cost_usd), 0) FROM executions "
                "WHERE tenant_id = :tid AND created_at >= :y0 AND created_at < :y1"
            ),
            params,
        )
        return StandupSummary(
            tasks_done_yesterday=int(done.scalar_one()),
            plans_closed_yesterday=int(plans.scalar_one()),
            tasks_in_progress=int(in_progress.scalar_one()),
            tasks_blocked=int(blocked.scalar_one()),
            runs_waiting_human=int(waiting.scalar_one()),
            cost_usd_yesterday=float(cost.scalar_one()),
        )


def _build_llm_factory(settings: Settings) -> Callable[[UUID], Any]:
    """LLM del catálogo para la redacción (mismo camino que el memorizer)."""

    def factory(_tenant_id: UUID) -> Any:
        from workers.memorizer import _default_llm_factory  # reuso del resolver existente

        return _default_llm_factory(settings)

    return factory


@app.task(name="workers.daily_standup")  # type: ignore[untyped-decorator]
def daily_standup_task() -> dict[str, int]:
    """Beat horario: envía el standup a los tenants cuya hora coincide."""
    settings = get_settings()

    async def _main() -> dict[str, int]:
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from workers.db import worker_engine

        engine = worker_engine(settings)
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        try:
            tenants = await _load_tenant_configs(sessionmaker)
            return await _run_standup(
                tenants=tenants,
                now=datetime.now(tz=UTC),
                collector=lambda tid: _collect_tenant_summary(sessionmaker, tid),
                llm_factory=_build_llm_factory(settings),
                notifier=CeleryStandupNotifier(broker_url=settings.broker_url),
            )
        finally:
            await engine.dispose()

    try:
        return asyncio.run(_main())
    except Exception:
        # Best-effort: el beat nunca pierde su cadencia por un fallo puntual.
        _log.exception("standup.run_failed")
        return {"sent": 0, "skipped": 0}
