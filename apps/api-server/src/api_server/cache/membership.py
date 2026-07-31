"""Caché Redis del lookup de membership (prod-13 · task_prod13_21, perf-10).

`_active_membership_role` corre en CADA request de cada endpoint tenant-scoped
—`require_tenant_member`, `require_tenant_admin`, `require_can_approve_plan` y la
factoría `require_tenant_role`—, y cada una era un round-trip a PostgreSQL para
leer una fila que cambia una vez al mes.

Lo que distingue esta caché de la de `platform_settings` es que **cachea una
decisión de autorización**. El riesgo 5 del plan lo dice sin rodeos: una entrada
mal invalidada mantiene dentro a un usuario al que le acaban de retirar el
acceso, o como admin a uno degradado. De ahí las tres decisiones de diseño:

1. **TTL de 30 s**, la mitad del máximo que permitía el plan. No es un ajuste de
   rendimiento: es el techo de lo que puede durar un permiso fantasma si alguna
   invalidación se pierde.

2. **La invalidación NO depende de que el endpoint se acuerde.** Las membresías
   se escriben desde cuatro sitios distintos (el panel de admin, SCIM, el mapeo
   de grupos de SSO y la siembra de tenant) y el quinto lo escribirá alguien
   dentro de seis meses. En vez de sembrar llamadas por todos ellos —el patrón
   que este repo ya sabe que se olvida (`docs/03-guides/verificar-antes-de-
   implementar.md`, apartado 5)— la invalidación cuelga de eventos del ORM sobre
   la propia clase: cualquier INSERT/UPDATE/DELETE de una membresía que pase por
   SQLAlchemy la dispara, y se ejecuta **después del COMMIT**, cuando el cambio
   ya es visible para los demás (invalidar antes deja la puerta a que un lector
   concurrente repueble con el valor viejo).

   El precio de esa elección, dicho en voz alta: un UPDATE en SQL crudo que no
   pase por el ORM NO invalida. Es aceptable porque ninguna vía de aplicación
   escribe así, y porque el TTL acota el resto.

3. **Best-effort sobre el caché, nunca sobre la autorización.** Redis caído
   degrada a la consulta de siempre contra PostgreSQL. Lo que NUNCA se hace es
   fallar abierto en el permiso.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import event
from sqlalchemy.orm import Session, object_session
from sqlalchemy.orm.attributes import get_history

from api_server.db.models import UserOrganizationMembership

_log = structlog.get_logger(__name__)

# Techo de lo que puede vivir un permiso fantasma si una invalidación se pierde.
# El plan permitía hasta 60 s; se usa la mitad porque lo que se cachea es un
# permiso, no un ajuste.
MEMBERSHIP_CACHE_TTL_SECONDS = 30

_CACHE_PREFIX = "membership:"

# Clave de `Session.info` donde los eventos de mapper dejan los pares
# (user_id, tenant_id) tocados hasta que el COMMIT los confirma.
_PENDING_KEY = "_membership_cache_dirty"


def membership_cache_key(user_id: UUID | str, tenant_id: UUID | str) -> str:
    """La clave de caché de una membresía.

    Lleva SIEMPRE el `tenant_id` además del `user_id`: el mismo usuario puede ser
    admin en un tenant y simple miembro en otro, y una clave por usuario a secas
    le daría en uno el rol que tiene en el otro."""
    return f"{_CACHE_PREFIX}{tenant_id}:{user_id}"


async def _cached_read(key: str) -> tuple[bool, str | None]:
    """`(hit, rol)`. Un fallo de Redis es `(False, None)`: se cae a la BD.

    El import de `auth.deps` es perezoso a propósito: `auth.deps` importa este
    módulo, así que un import a nivel de módulo cerraría el ciclo.
    """
    try:
        from api_server.auth.deps import get_redis

        raw = await get_redis().get(key)
    except Exception:  # Redis caído / no configurado: PostgreSQL sigue mandando
        return (False, None)
    if raw is None:
        return (False, None)
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):  # pragma: no cover - basura en la clave
        return (False, None)
    if not isinstance(payload, dict) or "r" not in payload:  # pragma: no cover - idem
        return (False, None)
    role = payload["r"]
    return (True, str(role) if role is not None else None)


async def _cache_write(key: str, role: str | None) -> None:
    """Guarda el rol —o la AUSENCIA de membresía, que es el caso más común y el
    que más se beneficia de no ir a la BD—. Best-effort: nunca rompe la lectura."""
    try:
        from api_server.auth.deps import get_redis

        await get_redis().setex(key, MEMBERSHIP_CACHE_TTL_SECONDS, json.dumps({"r": role}))
    except Exception:
        return


async def invalidate_membership_cache(user_id: UUID | str, tenant_id: UUID | str) -> None:
    """Borra la entrada de `(user_id, tenant_id)`. Idempotente y best-effort."""
    try:
        from api_server.auth.deps import get_redis

        await get_redis().delete(membership_cache_key(user_id, tenant_id))
    except Exception:
        return


async def cached_membership_role(
    *,
    user_id: UUID,
    tenant_id: UUID,
    loader: Callable[[], Awaitable[str | None]],
) -> str | None:
    """El rol de la membresía activa de `user_id` en `tenant_id`, o `None`.

    Sirve de Redis cuando hay entrada fresca; si no, llama a `loader` (la
    consulta de verdad) y cachea el resultado — incluido el `None` de "no es
    miembro". Sin Redis es exactamente la consulta de antes.
    """
    key = membership_cache_key(user_id, tenant_id)
    hit, role = await _cached_read(key)
    if hit:
        return role
    role = await loader()
    await _cache_write(key, role)
    return role


# ---------------------------------------------------------------------------
# Invalidación automática: eventos de ORM sobre la propia clase
# ---------------------------------------------------------------------------
# Eventos de MAPPER (no de `Session`): SQLAlchemy los despacha solo para esta
# clase, así que el resto de los flush de la aplicación no pagan nada. Sería
# irónico que una tarea de rendimiento metiese un recorrido de `session.dirty` en
# cada flush del sistema.


def _values_of(target: object, attr: str) -> set[str]:
    """Valores actuales E históricos de un atributo.

    Se miran los dos porque un UPDATE que cambiase la identidad de la membresía
    tiene que invalidar también la entrada VIEJA. Sobre-invalidar es inocuo (un
    fallo de caché); sub-invalidar es el agujero de autorización."""
    values: set[str] = set()
    current = getattr(target, attr, None)
    if current is not None:
        values.add(str(current))
    try:
        history = get_history(target, attr)
    except Exception:  # pragma: no cover - objeto detached / atributo expirado
        return values
    for value in (*history.deleted, *history.unchanged, *history.added):
        if value is not None:
            values.add(str(value))
    return values


def _mark_dirty(target: UserOrganizationMembership) -> None:
    session = object_session(target)
    if session is None:  # pragma: no cover - defensivo
        return
    pending: set[tuple[str, str]] = session.info.setdefault(_PENDING_KEY, set())
    for user_id in _values_of(target, "user_id"):
        for tenant_id in _values_of(target, "tenant_id"):
            pending.add((user_id, tenant_id))


@event.listens_for(UserOrganizationMembership, "after_insert")
@event.listens_for(UserOrganizationMembership, "after_update")
@event.listens_for(UserOrganizationMembership, "after_delete")
def _membership_written(_mapper: Any, _connection: Any, target: Any) -> None:
    """Anota la membresía tocada; el borrado real de la clave espera al COMMIT."""
    _mark_dirty(target)


@event.listens_for(Session, "after_commit")
def _flush_membership_invalidations(session: Session) -> None:
    """Borra las entradas de las membresías escritas en esta transacción.

    Se hace DESPUÉS del commit, cuando el cambio ya es visible para las demás
    conexiones: invalidar antes deja una ventana en la que un lector concurrente
    repuebla la caché con el valor viejo — el peor de los dos mundos, porque
    entonces el valor rancio dura el TTL entero DESPUÉS del cambio.
    """
    pending = session.info.pop(_PENDING_KEY, None)
    if not pending:
        return

    async def _delete_all() -> None:
        from api_server.auth.deps import get_redis

        redis = get_redis()
        for user_id, tenant_id in pending:
            await redis.delete(membership_cache_key(user_id, tenant_id))

    coro = _delete_all()
    try:
        # `after_commit` de una `AsyncSession` corre dentro del greenlet que
        # SQLAlchemy usa para puentear sync↔async, así que este es el puente
        # oficial para volver al bucle desde un handler síncrono.
        from sqlalchemy.util import await_only

        await_only(coro)
    except Exception as exc:
        # Un fallo aquí NO puede tumbar un commit ya hecho. Se registra —porque
        # significa que hay un permiso que puede tardar hasta el TTL en morir— y
        # se sigue.
        coro.close()
        _log.warning(
            "membership_cache.invalidation_failed",
            error=str(exc),
            entries=len(pending),
        )


__all__ = [
    "MEMBERSHIP_CACHE_TTL_SECONDS",
    "cached_membership_role",
    "invalidate_membership_cache",
    "membership_cache_key",
]
