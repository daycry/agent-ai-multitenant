"""Cerrojo de instancia única para las tareas beat con efecto en disco (`task_cv_42`).

Auditoría 2026-09-01 (G-04): beat no garantiza instancia única. Dos beats —un
despliegue solapado, un ``compose up`` con el viejo aún vivo— encolan la misma
tarea dos veces, y ``acks_late`` reentrega la que un worker no llegó a
confirmar: dos backups con dos quiesces, dos podas sobre la misma carpeta.
``expires`` en la entrada del beat descarta la copia que espera de más; este
cerrojo (``SET key token NX EX ttl``, el patrón de ``workers.run_lock``) evita
que dos copias corran A LA VEZ.

Es una guarda, no una puerta: si Redis no responde, la tarea corre igual (como
antes) y se registra. Lo peor que puede pasar sin cerrojo es lo que ya pasaba;
lo peor que puede pasar con una puerta cerrada es un mantenimiento que nunca
vuelve a correr.
"""

from __future__ import annotations

import contextlib
import functools
import secrets
from collections.abc import Callable
from typing import Any, TypeVar

import structlog

_log = structlog.get_logger(__name__)

_F = TypeVar("_F", bound=Callable[..., Any])

LOCK_PREFIX = "beat:"


def lock_key(name: str) -> str:
    return f"{LOCK_PREFIX}{name}"


def _default_redis() -> Any:
    from redis import Redis

    from workers.config import get_settings

    return Redis.from_url(get_settings().events_redis_url, decode_responses=True)


def beat_singleton(
    name: str, *, ttl_s: int, redis_factory: Callable[[], Any] | None = None
) -> Callable[[_F], _F]:
    """Envuelve una tarea beat síncrona para que sólo corra una copia a la vez.

    ``ttl_s`` acota un worker que muere con el cerrojo tomado: pasado ese tiempo
    la siguiente copia entra. Debe ser mayor que la duración normal de la tarea
    y menor que su cadencia. Si otra copia está corriendo devuelve
    ``{"skipped": "already_running", "lock": <key>}`` sin ejecutar nada.
    """
    key = lock_key(name)

    def decorate(fn: _F) -> _F:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            token = secrets.token_hex(8)
            redis: Any = None
            try:
                redis = (redis_factory or _default_redis)()
                acquired = bool(redis.set(key, token, nx=True, ex=ttl_s))
            except Exception as exc:
                _log.warning(
                    "maintenance.singleton.redis_unavailable",
                    lock=key,
                    error=str(exc),
                    detail="sin Redis la tarea corre igual: el cerrojo es una guarda",
                )
                return fn(*args, **kwargs)
            if not acquired:
                _log.warning(
                    "maintenance.singleton.already_running",
                    lock=key,
                    detail="otra copia de la tarea está corriendo (dos beats o reentrega)",
                )
                return {"skipped": "already_running", "lock": key}
            try:
                return fn(*args, **kwargs)
            finally:
                with contextlib.suppress(Exception):
                    if redis.get(key) == token:
                        redis.delete(key)

        wrapper._beat_singleton = key  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    return decorate


__all__ = ["LOCK_PREFIX", "beat_singleton", "lock_key"]
