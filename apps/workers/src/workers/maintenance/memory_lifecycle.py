"""Caducidad y compactación de memorias — ``workers.maintain_memory_lifecycle`` (task_mem_11).

Beat diario (04:10, tras la purga de las 03:xx y antes del GC de las 04:30). Dos pasadas,
las dos idempotentes y best-effort (una falla no tumba a la otra ni al beat):

  1. **Archiva** (soft) las ``episodic`` sin ``kind = summary``, con ``recall_count = 0`` y de
     más de N días (``memory.archive_after_days``, default 90) contados desde
     ``greatest(created_at, memory.usage_tracking_since)``: lo anterior a la medición del
     uso no caduca hasta que pasen N días de medición real.
  2. **Propone** una revisión de compactación por proyecto con sus memorias ``disputed``. NO la
     aplica: un ``tenant_admin`` la confirma desde la API (la cola la pinta ``task_ui_31``).

NUNCA borra: archiva con motivo y deja fila de ``audit_log`` por memoria. La regla vive en
``api_server.memorizer.lifecycle`` (compartida con la API); aquí sólo el arranque, el ajuste y
la sesión. Corre con la sesión admin BYPASSRLS del worker (mantenimiento cross-tenant), y cada
sentencia lleva ``tenant_id``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker

from workers.celery_app import app
from workers.config import Settings, get_settings
from workers.db import worker_engine

_log = structlog.get_logger("workers.maintenance")


@app.task(name="workers.maintain_memory_lifecycle")  # type: ignore[untyped-decorator]
def maintain_memory_lifecycle() -> dict[str, Any]:
    """Archiva las memorias caducadas y propone la compactación de las disputadas."""
    return asyncio.run(_maintain_memory_lifecycle_async(get_settings()))


async def _maintain_memory_lifecycle_async(settings: Settings) -> dict[str, Any]:
    """Núcleo async (los tests lo llaman con los settings de la BD de prueba)."""
    from api_server.db.platform_settings import (
        get_memory_archive_after_days,
        get_memory_usage_tracking_since,
    )
    from api_server.memorizer.lifecycle import archive_expired, propose_compactions

    engine = worker_engine(settings)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    out: dict[str, Any] = {"archived": 0, "proposals": 0}
    try:
        try:
            async with sessionmaker() as session:
                days = await get_memory_archive_after_days(session)
                usage_since = await get_memory_usage_tracking_since(session)
            async with sessionmaker() as session, session.begin():
                out["archived"] = await archive_expired(
                    session, older_than_days=days, usage_since=usage_since
                )
            out["older_than_days"] = days
        except Exception as exc:  # una pasada no tumba a la otra
            _log.warning("maintenance.memory_lifecycle.archive_failed", error=str(exc))
            out["archive_error"] = str(exc)
        try:
            async with sessionmaker() as session, session.begin():
                out["proposals"] = await propose_compactions(session)
        except Exception as exc:
            _log.warning("maintenance.memory_lifecycle.propose_failed", error=str(exc))
            out["propose_error"] = str(exc)
        _log.info("maintenance.memory_lifecycle.done", **out)
        return out
    finally:
        await engine.dispose()
