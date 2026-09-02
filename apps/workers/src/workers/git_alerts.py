"""Credenciales git caducadas: que se sepa antes del `pr_error` (`task_cv_45`, G-10).

Auditoría 2026-09-01: nada vigilaba la caducidad de un PAT o una clave SSH;
se descubría en el `pr_error` de un plan ya `completed`. Aquí se reconoce el
fallo de autenticación en cualquier error de git/PR y se emite
`git_credential_failed` (tenant-scoped), throttled por clave para que un
remoto caído no dispare cien avisos.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable

import structlog

_log = structlog.get_logger(__name__)

#: Ventana de silencio por clave (proyecto/plan) entre dos avisos iguales.
THROTTLE_S = 6 * 3600

_PATTERNS = (
    re.compile(r"authentication failed", re.I),
    re.compile(r"could not read username", re.I),
    re.compile(r"permission denied \(publickey\)", re.I),
    re.compile(r"bad credentials", re.I),
    re.compile(r"\b401\b"),
    re.compile(r"\b403\b"),
    re.compile(r"invalid (username or )?password", re.I),
    re.compile(r"remote: permission to .* denied", re.I),
)


def looks_like_git_credential_failure(text: str | None) -> bool:
    """¿El error de git/PR huele a credencial caducada o sin permisos?"""
    if not text:
        return False
    return any(p.search(text) for p in _PATTERNS)


async def _redis_throttle(key: str) -> bool:
    """``SET NX EX`` sobre el Redis de eventos; si Redis no responde, se avisa
    igual (mejor un aviso de más que una credencial caducada en silencio)."""
    try:
        from redis.asyncio import Redis

        from workers.config import get_settings

        redis = Redis.from_url(get_settings().events_redis_url, decode_responses=True)
        try:
            return bool(
                await redis.set(f"git_credential_failed:{key}", "1", nx=True, ex=THROTTLE_S)
            )
        finally:
            await redis.aclose()
    except Exception as exc:
        _log.info("git_alerts.throttle_unavailable", error=str(exc))
        return True


async def notify_git_credential_failed(
    *,
    tenant_id: str,
    subject: str,
    key: str,
    reason: str,
    throttle: Callable[[str], Awaitable[bool]] | None = None,
) -> bool:
    """Emite `git_credential_failed` si la clave no está en su ventana de
    silencio. Devuelve si se emitió. Best-effort: nunca lanza."""
    try:
        allowed = await (throttle or _redis_throttle)(key)
        if not allowed:
            return False
        from api_server.celery_client import enqueue_event_dispatch

        await enqueue_event_dispatch(
            {
                "event_type": "git_credential_failed",
                "tenant_id": tenant_id,
                "context": {"subject": subject, "reason": str(reason)[:500], "key": key},
            }
        )
        return True
    except Exception as exc:
        _log.warning("git_alerts.notify_failed", key=key, error=str(exc))
        return False


__all__ = ["THROTTLE_S", "looks_like_git_credential_failure", "notify_git_credential_failed"]
