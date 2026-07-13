"""La task que ejecuta una sesión de navegación aprobada (ADR 0080).

Puente BD ↔ contenedor. Tres puertas antes de que se abra un navegador, y las
tres se comprueban AQUÍ (no basta con que el endpoint que aprueba las mirara):

  1. la sesión existe y está en ``approved`` (gate humano, ADR 0080 §3);
  2. el **kill-switch** de plataforma sigue encendido — si el owner apagó el
     navegador entre la aprobación y la ejecución, no se navega;
  3. el guion se ejecuta en el `browser-runtime` efímero y hardened.

El resultado (texto saneado) se persiste en la fila; el modelo lo lee con
``browse_result``. Un fallo deja la sesión ``failed`` con su causa: nunca queda
colgada en ``running``.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from workers.browse_runner import run_browse_container
from workers.celery_app import app
from workers.config import Settings, get_settings

_log = structlog.get_logger("workers.browse_task")


@app.task(name="workers.browse_session")  # type: ignore[untyped-decorator]
def browse_session(session_id: str) -> dict[str, Any]:
    """Celery entry point: ejecuta la sesión de navegación `session_id`."""
    return asyncio.run(_run_browse(get_settings(), session_id))


async def _run_browse(
    settings: Settings, session_id: str, *, sessionmaker: Any = None
) -> dict[str, Any]:
    from api_server.db.browse_repo import (
        get_browse_session,
        mark_done,
        mark_failed,
        mark_running,
    )
    from api_server.db.platform_settings import get_cortex_browser_enabled

    # BYPASSRLS: browse_sessions del córtex es tenant-less (tenant_id NULL), como
    # la memoria del córtex — el worker accede sin `app.tenant_id`. `sessionmaker`
    # inyectable es un seam de test; en producción es None y se abre el engine.
    engine = None if sessionmaker is not None else create_async_engine(settings.database_url)
    if sessionmaker is None:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessionmaker() as session, session.begin():
            row = await get_browse_session(session, UUID(session_id))
            if row is None:
                return {"session_id": session_id, "skipped": "not_found"}
            if row.status != "approved":
                # No es un error: una sesión rechazada o ya corrida no se relanza.
                return {"session_id": session_id, "skipped": row.status}
            if not await get_cortex_browser_enabled(session):
                await mark_failed(session, row, error="el navegador del córtex está deshabilitado")
                return {"session_id": session_id, "skipped": "kill_switch_off"}
            steps = list(row.steps or [])
            budgets = dict(row.budgets or {})
            await mark_running(session, row)

        try:
            ok, payload = run_browse_container(
                settings,
                session_id=session_id,
                status="approved",  # ya validado arriba; el runner lo re-exige igual
                steps=steps,
                budgets=budgets,
            )
        except Exception as exc:  # el runtime petó (docker caído, imagen ausente…)
            # La sesión está en `running`: NO puede quedar colgada. La bajamos a
            # `failed` con la causa — el docstring lo promete.
            ok, payload = False, {"error": f"fallo al ejecutar la sesión: {exc}"[:500]}

        async with sessionmaker() as session, session.begin():
            row = await get_browse_session(session, UUID(session_id))
            if row is None:  # pragma: no cover — borrada a mitad de vuelo
                return {"session_id": session_id, "skipped": "vanished"}
            if ok:
                await mark_done(session, row, result=payload)
            else:
                await mark_failed(session, row, error=str(payload.get("error", "fallo"))[:500])
        _log.info("browse.session_finished", session=session_id, ok=ok)
        return {"session_id": session_id, "ok": ok}
    finally:
        if engine is not None:
            await engine.dispose()


__all__ = ["browse_session"]
