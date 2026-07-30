"""Barrido periódico de fetch de los remotos git de los proyectos (ADR 0098).

El re-sync del remoto solo tenía el botón manual «Sincronizar»
(``POST /projects/{id}/git/sync``); sin él, el bare de un proyecto se quedaba
atrás hasta que un humano se acordaba. Este beat recorre los proyectos con
``git_config.remote_url`` y reutiliza el clone/fetch AUTENTICADO de ADR 0072
(:func:`workers.repo_clone._clone_project_repo_async`: resuelve credencial de
Vault, configura el remoto y hace ``fetch --prune``) por proyecto, best-effort
— un remoto caído o una credencial caducada se loguea y NO aborta el resto.

Gated por el platform setting ``git_fetch_sweep_enabled`` (default OFF, ADR
0098: sondear remotos de terceros es una decisión consciente del System Admin)
con cadencia cron configurable (``WORKERS_GIT_FETCH_CRON``, leída por beat al
boot). El webhook de push y el merge directo real siguen gated por diseño.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text as sa_text

from workers.beat_schedule import GIT_FETCH_BEAT_ENTRY
from workers.celery_app import app
from workers.config import Settings, get_settings
from workers.db import worker_sessionmaker

_log = structlog.get_logger("workers.git_remote_sweep")

__all__ = ["GIT_FETCH_BEAT_ENTRY", "sweep_project_git_remotes"]


@app.task(name="workers.sweep_project_git_remotes")  # type: ignore[untyped-decorator]
def sweep_project_git_remotes() -> dict[str, Any]:
    """Entry point Celery. Best-effort: nunca propaga (beat conserva su
    cadencia pase lo que pase con la red o las credenciales)."""
    settings = get_settings()
    try:
        return asyncio.run(_sweep_project_git_remotes_async(settings))
    except Exception as exc:  # pragma: no cover — best-effort: never crash beat
        _log.exception("git_remote_sweep.failed", error=str(exc))
        return {"enabled": None, "status": f"error:{exc}"}


async def _sweep_project_git_remotes_async(
    settings: Settings,
    *,
    session_factory: Any | None = None,
    clone: Any | None = None,
) -> dict[str, Any]:
    """Async core. ``session_factory``/``clone`` son inyectables para tests
    (una factory de sesiones fake y un clone grabador); en producción la
    sesión sale del engine del worker (BYPASSRLS: el sweep es de plataforma,
    recorre todos los tenants) y el clone es el de ADR 0072."""
    from api_server.db import platform_settings

    async with worker_sessionmaker(settings, override=session_factory) as sessions:
        async with sessions() as db:
            enabled = await platform_settings.get_git_fetch_sweep_enabled(db)
        if not enabled:
            _log.info("git_remote_sweep.skipped", reason="disabled")
            return {"enabled": False, "skipped": True}
        async with sessions() as db:
            rows = await db.execute(
                sa_text(
                    "SELECT id FROM projects"
                    " WHERE deleted_at IS NULL"
                    " AND git_config->>'remote_url' IS NOT NULL"
                )
            )
            project_ids = [row[0] for row in rows.all()]

    if clone is None:
        from workers.repo_clone import _clone_project_repo_async

        clone = _clone_project_repo_async

    fetched = failed = 0
    for pid in project_ids:
        try:
            result = await clone(UUID(str(pid)), settings=settings)
            status_txt = str((result or {}).get("status", ""))
            if status_txt.startswith("error"):
                failed += 1
                _log.warning("git_remote_sweep.project_failed", project_id=str(pid))
            else:
                fetched += 1
        except Exception as exc:  # un proyecto roto no aborta el resto
            failed += 1
            _log.warning("git_remote_sweep.project_failed", project_id=str(pid), error=str(exc))
    _log.info("git_remote_sweep.done", projects=len(project_ids), fetched=fetched, failed=failed)
    return {
        "enabled": True,
        "projects": len(project_ids),
        "fetched": fetched,
        "failed": failed,
    }
