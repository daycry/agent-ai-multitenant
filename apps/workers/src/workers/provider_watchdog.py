"""Vigía de credenciales LLM (ADR 0122).

Dolor real: una credencial caducada (claude_sdk, dos veces) mataba runs en
silencio — tareas ``blocked`` sin aviso hasta la inspección manual. El beat
``workers.provider_watchdog`` (cada 30 min) sondea cada proveedor ACTIVO con
el probe de liveness existente (``api_server.llm_providers.liveness``) y
notifica por el pipeline de notificaciones:

  - transición sana→caída → ``provider_credential_invalid`` (event_type que
    el dispatcher ya conoce — lo emite también el worker cuando un run muere
    por credencial);
  - caída persistente → recordatorio solo pasadas ``REMIND_AFTER_S``;
  - caída→sana → ``provider_recovered``.

El estado entre pasadas (último status + último aviso) vive en el Redis del
worker; el núcleo (:func:`_watch_providers`) recibe prober/notifier/state
inyectados y es puro — TDD con fakes. Un proveedor que revienta el probe
cuenta como caído (con el error como detail) y NUNCA rompe la pasada.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import structlog

from workers.celery_app import app
from workers.config import get_settings

_log = structlog.get_logger(__name__)

#: Recordatorio de una caída persistente: cada 6 h, no cada pasada.
REMIND_AFTER_S = 6 * 3600

FAIL_EVENT = "provider_credential_invalid"
RECOVERY_EVENT = "provider_recovered"


@dataclass(frozen=True)
class ProviderRow:
    provider_id: str
    name: str
    kind: str


class WatchdogState(Protocol):
    async def get(self, provider_id: str) -> dict[str, Any] | None: ...
    async def set(self, provider_id: str, value: dict[str, Any]) -> None: ...


class WatchdogNotifier(Protocol):
    def publish(self, event: dict[str, Any]) -> None: ...


Prober = Callable[[ProviderRow], Awaitable[tuple[bool, str]]]


def _event(event_type: str, row: ProviderRow, detail: str) -> dict[str, Any]:
    return {
        "event_type": event_type,
        # Señal de PLATAFORMA (System Admin), como las alertas de rotación:
        # la credencial es global, no de un tenant.
        "tenant_id": None,
        "context": {
            "provider_id": row.provider_id,
            "provider_name": row.name,
            "provider_kind": row.kind,
            "detail": detail,  # secret-free por contrato del probe
        },
    }


async def _watch_providers(
    *,
    providers: list[ProviderRow],
    prober: Prober,
    notifier: WatchdogNotifier,
    state: WatchdogState,
    now: datetime,
) -> dict[str, int]:
    """Una pasada del vigía. Devuelve contadores para el log del beat."""
    checked = 0
    unhealthy = 0
    notified = 0
    for row in providers:
        checked += 1
        try:
            ok, detail = await prober(row)
        except Exception as exc:
            # Un probe roto (Vault caído, red) ES una señal de mala salud —
            # y jamás rompe la pasada de los demás proveedores.
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        prev = await state.get(row.provider_id) or {}
        prev_status = prev.get("status", "ok")

        if not ok:
            unhealthy += 1
            last_raw = prev.get("last_notified_at")
            last = datetime.fromisoformat(last_raw) if last_raw else None
            overdue = last is None or (now - last).total_seconds() > REMIND_AFTER_S
            if prev_status != "fail" or overdue:
                notifier.publish(_event(FAIL_EVENT, row, detail))
                notified += 1
                await state.set(
                    row.provider_id,
                    {"status": "fail", "last_notified_at": now.isoformat()},
                )
            else:
                await state.set(
                    row.provider_id,
                    {"status": "fail", "last_notified_at": last_raw},
                )
        else:
            if prev_status == "fail":
                notifier.publish(_event(RECOVERY_EVENT, row, detail))
                notified += 1
            await state.set(row.provider_id, {"status": "ok", "last_notified_at": None})
    return {"checked": checked, "unhealthy": unhealthy, "notified": notified}


# ---------------------------------------------------------------------------
# Cableado real (DB + Vault + probe + Redis + Celery) — integración.
# ---------------------------------------------------------------------------
_STATE_KEY = "watchdog:provider:{provider_id}"


class RedisWatchdogState:
    """Estado en el Redis del worker (JSON por proveedor, TTL 7 días)."""

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def get(self, provider_id: str) -> dict[str, Any] | None:
        raw = await self._redis.get(_STATE_KEY.format(provider_id=provider_id))
        if not raw:
            return None
        try:
            value = json.loads(raw)
        except (ValueError, TypeError):
            return None
        return value if isinstance(value, dict) else None

    async def set(self, provider_id: str, value: dict[str, Any]) -> None:
        await self._redis.set(
            _STATE_KEY.format(provider_id=provider_id),
            json.dumps(value),
            ex=7 * 24 * 3600,
        )


async def _load_active_providers(sessionmaker: Any) -> list[ProviderRow]:
    from sqlalchemy import text as sa_text

    async with sessionmaker() as session:
        rows = await session.execute(
            sa_text("SELECT id, display_name, kind FROM llm_providers WHERE is_active = true")
        )
        return [
            ProviderRow(provider_id=str(r[0]), name=str(r[1]), kind=str(r[2]))
            for r in rows.fetchall()
        ]


def _build_real_prober(sessionmaker: Any) -> Prober:
    """Prober real: fila → secret de Vault → probe de liveness del api-server."""

    async def prober(row: ProviderRow) -> tuple[bool, str]:
        from api_server.llm_providers.liveness import probe_provider
        from api_server.routers.llm_providers import get_provider_vault_store
        from sqlalchemy import text as sa_text

        async with sessionmaker() as session:
            found = await session.execute(
                sa_text("SELECT base_url, secret_vault_path FROM llm_providers WHERE id = :pid"),
                {"pid": row.provider_id},
            )
            base_url, vault_path = found.one()
        secret: dict[str, str] = {}
        if vault_path:
            store = get_provider_vault_store()
            if store is not None:
                secret = store.read_secret(str(vault_path))
        result = await probe_provider(kind=row.kind, base_url=base_url, secret=secret)
        return result.ok, result.detail

    return prober


@app.task(name="workers.provider_watchdog")  # type: ignore[untyped-decorator]
def provider_watchdog_task() -> dict[str, int]:
    """Pasada del vigía (beat cada 30 min). Best-effort: nunca rompe el beat."""
    settings = get_settings()

    async def _main() -> dict[str, int]:
        from redis.asyncio import Redis
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from workers.db import worker_engine
        from workers.standup import CeleryStandupNotifier

        engine = worker_engine(settings)
        redis = Redis.from_url(settings.events_redis_url, decode_responses=True)
        try:
            sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
            providers = await _load_active_providers(sessionmaker)
            return await _watch_providers(
                providers=providers,
                prober=_build_real_prober(sessionmaker),
                notifier=CeleryStandupNotifier(broker_url=settings.broker_url),
                state=RedisWatchdogState(redis),
                now=datetime.now(tz=UTC),
            )
        finally:
            await redis.aclose()
            await engine.dispose()

    try:
        return asyncio.run(_main())
    except Exception:
        _log.exception("provider_watchdog.run_failed")
        return {"checked": 0, "unhealthy": 0, "notified": 0}
