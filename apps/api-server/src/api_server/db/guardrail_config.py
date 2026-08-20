"""Las tres capas de guardrails, persistidas y resueltas (prod-03 task_prod03_07/08).

`shared_guardrails.layers.resolve_config` sabe fusionar plataforma → tenant →
proyecto desde el Plan 11 —candados incluidos— y hasta hoy **solo se llamaba
desde tests y desde el dispatch con dos capas**: no había ni tabla, ni baseline
sembrado, ni forma de que un tenant endureciera sus guardrails sin ir proyecto
por proyecto. Este módulo es la mitad de persistencia que faltaba
(guardrails-4).

Qué vive aquí
-------------
* el modelo :class:`GuardrailConfig` de la tabla ``guardrail_configs``
  (migración 0132), una fila por capa;
* el CRUD por capa, con la comprobación que le da sentido a los candados:
  escribir una capa inferior resuelve con ``strict=True`` contra la de
  plataforma, así que **intentar relajar o eliminar un guardrail `locked` falla**
  con :class:`LockedFieldOverrideError` en vez de ignorarse en silencio. Hasta
  hoy `LockedFieldOverrideError` no tenía ni un llamante fuera de tests: los
  candados eran una promesa del docstring de `layers.py`;
* :func:`get_effective_guardrail_config`, el resolvedor cacheado que consumen el
  dispatch del worker y el chat de planning.

Compatibilidad con las dos capas viejas
---------------------------------------
Antes de esta tabla la config vivía en ``platform_settings.guardrails_config`` y
``projects.guardrails_config`` (ADR 0102 D3, migración 0110), y hay
despliegues con datos ahí. La resolución mira **primero la tabla nueva y, si esa
capa no tiene fila, la columna vieja**. Esa es la dirección segura: mientras
nadie escriba en `guardrail_configs`, la plataforma se comporta exactamente
igual que antes; el día que se escribe una capa, gana la nueva. Lo contrario
—migrar el dato y confiar en haber encontrado todos los lectores— falla dejando
runs SIN guardrails, que es el modo de fallo que no queremos.

Retirar las dos columnas viejas es trabajo de otro plan, y no puede hacerse sin
migrar su contenido a esta tabla.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from shared_guardrails.config import parse_config
from shared_guardrails.exceptions import GuardrailConfigError
from shared_guardrails.layers import (
    LayerConfig,
    LayerName,
    LockedFieldOverrideError,
    ResolvedConfig,
    resolve_config,
)
from sqlalchemy import Integer, String, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from api_server.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

#: Tope de tamaño de la config efectiva serializada. Mismo número que el cap del
#: dispatch (ADR 0102 D3): lo que no quepa en el task spec no sirve de nada.
MAX_EFFECTIVE_CONFIG_BYTES = 64_000

#: TTL de la caché de config efectiva. Corto y con invalidación explícita al
#: escribir, igual que la caché de platform settings: ante la duda gana la
#: frescura, porque un guardrail rancio es una guarda relajada.
_CACHE_PREFIX = "guardrailcfg:"
_CACHE_TTL_SECONDS = 30


class GuardrailConfig(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Una capa de config declarativa de guardrails.

    ``tenant_id`` es NULL **solo** en la capa de plataforma, y la BD lo exige
    (CHECK ``ck_guardrail_configs_scope_columns``). La RLS de la migración 0132
    deja LEER el baseline de plataforma desde cualquier tenant —es la capa que
    todos heredan y no contiene dato de nadie— pero no escribirlo.
    """

    __tablename__ = "guardrail_configs"

    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    tenant_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    project_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    #: Contador de escrituras, para invalidar caché sin releer el JSONB entero.
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)


# Reexportado para que los llamantes no tengan que importar de dos sitios.
__all__ = [
    "MAX_EFFECTIVE_CONFIG_BYTES",
    "GuardrailConfig",
    "LockedFieldOverrideError",
    "delete_layer_config",
    "get_effective_guardrail_config",
    "get_layer_config",
    "invalidate_effective_config_cache",
    "set_layer_config",
]

_Scope = Literal["platform", "tenant", "project"]


def _scope_filters(
    scope: _Scope, tenant_id: UUID | None, project_id: UUID | None
) -> tuple[Any, ...]:
    if scope == "platform":
        return (GuardrailConfig.scope == "platform",)
    if scope == "tenant":
        return (GuardrailConfig.scope == "tenant", GuardrailConfig.tenant_id == tenant_id)
    return (GuardrailConfig.scope == "project", GuardrailConfig.project_id == project_id)


async def get_layer_config(
    session: AsyncSession,
    scope: _Scope,
    *,
    tenant_id: UUID | None = None,
    project_id: UUID | None = None,
) -> GuardrailConfig | None:
    """La fila de UNA capa, o ``None`` si esa capa no está configurada."""
    stmt = select(GuardrailConfig).where(*_scope_filters(scope, tenant_id, project_id))
    return (await session.execute(stmt)).scalar_one_or_none()


async def set_layer_config(
    session: AsyncSession,
    scope: _Scope,
    config: dict[str, Any] | None,
    *,
    tenant_id: UUID | None = None,
    project_id: UUID | None = None,
    actor_id: UUID | None = None,
) -> GuardrailConfig:
    """Crea o actualiza una capa, **rechazando** lo que rompa un candado.

    Dos validaciones, en este orden:

    1. la config tiene que parsear (`parse_config`) — un hook inexistente o un
       `on_error` inventado se rechazan aquí, no en el sandbox;
    2. si la capa es `tenant` o `project`, se resuelve contra la de PLATAFORMA
       con ``strict=True``: relajar, sobrescribir o eliminar un guardrail
       `locked` levanta :class:`LockedFieldOverrideError`. En modo no estricto
       el intento se ignoraría y quedaría solo anotado — silencioso justo donde
       el operador necesita un «no».

    El caller es dueño de la transacción. La caché de config efectiva se
    invalida aquí mismo: dejarlo al caller es cómo se sirve una guarda vieja.
    """
    parse_config(dict(config) if config else None)
    if scope != "platform":
        _assert_locked_rules_survive(scope, config, await _platform_layer(session))

    row = await get_layer_config(session, scope, tenant_id=tenant_id, project_id=project_id)
    if row is None:
        row = GuardrailConfig(
            scope=scope,
            tenant_id=tenant_id,
            project_id=project_id,
            config=dict(config or {}),
            version=1,
            updated_by=actor_id,
        )
        session.add(row)
    else:
        row.config = dict(config or {})
        row.version = row.version + 1
        row.updated_by = actor_id
    await session.flush()
    # `created_at` / `updated_at` los pone el servidor (server_default / onupdate
    # SQL), así que tras el flush el objeto NO los tiene cargados. Sin este
    # refresh, leerlos fuera de la transacción dispara una carga perezosa que en
    # el contexto async revienta con `MissingGreenlet` — y lo hace en el
    # serializador de la respuesta, lejos de aquí.
    await session.refresh(row)
    await invalidate_effective_config_cache(tenant_id=tenant_id, project_id=project_id)
    return row


async def delete_layer_config(
    session: AsyncSession,
    scope: _Scope,
    *,
    tenant_id: UUID | None = None,
    project_id: UUID | None = None,
) -> bool:
    """Retira una capa. ``False`` si no había nada que retirar.

    La capa de PLATAFORMA no se borra por esta vía: es el baseline y su
    ausencia dejaría a toda la plataforma sin candados. Se edita, no se quita.
    """
    if scope == "platform":
        raise GuardrailConfigError("La capa de plataforma es el baseline: se edita, no se elimina.")
    row = await get_layer_config(session, scope, tenant_id=tenant_id, project_id=project_id)
    if row is None:
        return False
    await session.delete(row)
    await session.flush()
    await invalidate_effective_config_cache(tenant_id=tenant_id, project_id=project_id)
    return True


def _assert_locked_rules_survive(
    scope: _Scope,
    config: dict[str, Any] | None,
    platform_raw: dict[str, Any] | None,
) -> None:
    """Resuelve en estricto: si toca un candado de plataforma, levanta."""
    if not platform_raw:
        return
    layer: LayerName = "tenant" if scope == "tenant" else "project"
    resolve_config(
        LayerConfig.from_dict("platform", platform_raw),
        LayerConfig.from_dict("tenant", config) if layer == "tenant" else None,
        LayerConfig.from_dict("project", config) if layer == "project" else None,
        strict=True,
    )


# ---------------------------------------------------------------------------
# Resolución efectiva (el servicio que consumen dispatch y chat)
# ---------------------------------------------------------------------------


async def _platform_layer(session: AsyncSession) -> dict[str, Any] | None:
    """La capa de plataforma: tabla nueva, y si no hay fila, la columna vieja."""
    row = await get_layer_config(session, "platform")
    if row is not None and row.config:
        return dict(row.config)
    from api_server.db import platform_settings

    legacy = await platform_settings.get_guardrails_config(session)
    return dict(legacy) if legacy else None


async def _tenant_layer(session: AsyncSession, tenant_id: UUID | None) -> dict[str, Any] | None:
    if tenant_id is None:
        return None
    row = await get_layer_config(session, "tenant", tenant_id=tenant_id)
    return dict(row.config) if row is not None and row.config else None


async def _project_layer(session: AsyncSession, project_id: UUID | None) -> dict[str, Any] | None:
    if project_id is None:
        return None
    row = await get_layer_config(session, "project", project_id=project_id)
    if row is not None and row.config:
        return dict(row.config)
    from api_server.db.domain import Project

    project = await session.get(Project, project_id)
    legacy = getattr(project, "guardrails_config", None) if project is not None else None
    return dict(legacy) if legacy else None


async def resolve_effective_layers(
    session: AsyncSession,
    *,
    tenant_id: UUID | None,
    project_id: UUID | None,
) -> ResolvedConfig:
    """Las tres capas fusionadas, sin caché ni serialización.

    Devuelve el :class:`ResolvedConfig` completo —config + provenance +
    `rejected_overrides`— porque la UI de capas necesita poder decir «este check
    lo puso la plataforma y está bloqueado» y «el tenant intentó relajar aquél».
    """
    return resolve_config(
        LayerConfig.from_dict("platform", await _platform_layer(session)),
        LayerConfig.from_dict("tenant", await _tenant_layer(session, tenant_id)),
        LayerConfig.from_dict("project", await _project_layer(session, project_id)),
    )


async def get_effective_guardrail_config(
    session: AsyncSession,
    *,
    tenant_id: UUID | None,
    project_id: UUID | None = None,
) -> dict[str, Any] | None:
    """La config efectiva serializada, cacheada y acotada.

    ``None`` cuando no hay ninguna capa configurada — el runtime cae entonces a
    su baseline, que es lo que hace hoy. Si el resultado no cabe en
    :data:`MAX_EFFECTIVE_CONFIG_BYTES` se degrada a la capa de plataforma sola,
    igual que el dispatch: mejor los candados obligatorios solos que ninguno.

    Lleva una clave ``version`` **hermana** de ``guardrails``, no dentro:
    `parse_config` solo mira ``guardrails``, así que el runtime la ignora sin
    enterarse y quien cachea puede comparar sin re-derivar la config entera.
    """
    cache_key = _cache_key(tenant_id, project_id)
    hit, cached = await _cached_read(cache_key)
    if hit:
        return cached if isinstance(cached, dict) else None

    resolved = await resolve_effective_layers(session, tenant_id=tenant_id, project_id=project_id)
    out: dict[str, Any] | None = None if resolved.config.is_empty else resolved.config.to_dict()
    if out is not None and len(json.dumps(out)) > MAX_EFFECTIVE_CONFIG_BYTES:
        platform_only = resolve_config(
            LayerConfig.from_dict("platform", await _platform_layer(session))
        )
        out = platform_only.config.to_dict()
        if len(json.dumps(out)) > MAX_EFFECTIVE_CONFIG_BYTES:
            out = None
    if out is not None:
        out["version"] = await effective_config_version(
            session, tenant_id=tenant_id, project_id=project_id
        )
    await _cache_write(cache_key, out)
    return out


async def effective_config_version(
    session: AsyncSession,
    *,
    tenant_id: UUID | None,
    project_id: UUID | None = None,
) -> str:
    """Huella de las tres capas: ``p<v>.t<v>.j<v>``, con ``-`` si la capa falta.

    No es un hash del contenido a propósito: los contadores de `version` son
    baratos de leer, cambian en CADA escritura (aunque el JSON quede igual) y
    dicen QUÉ capa se movió, que es lo que hace falta para depurar «este run
    corrió con otra config». Una capa que no existe se marca distinto de una
    capa en su versión 0: no tener regla no es lo mismo que tener una vacía.
    """
    platform = await get_layer_config(session, "platform")
    tenant = (
        await get_layer_config(session, "tenant", tenant_id=tenant_id)
        if tenant_id is not None
        else None
    )
    project = (
        await get_layer_config(session, "project", project_id=project_id)
        if project_id is not None
        else None
    )
    return ".".join(
        f"{prefix}{row.version if row is not None else '-'}"
        for prefix, row in (("p", platform), ("t", tenant), ("j", project))
    )


# ---------------------------------------------------------------------------
# Caché (mismo patrón y mismas cautelas que `db.platform_settings`)
# ---------------------------------------------------------------------------


def _cache_key(tenant_id: UUID | None, project_id: UUID | None) -> str:
    return f"{_CACHE_PREFIX}{tenant_id or '-'}:{project_id or '-'}"


async def _cached_read(key: str) -> tuple[bool, Any]:
    try:
        from api_server.auth.deps import get_redis

        raw = await get_redis().get(key)
    except Exception:  # Redis caído: la BD sigue siendo la verdad
        return (False, None)
    if raw is None:
        return (False, None)
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):  # pragma: no cover - basura en la clave
        return (False, None)
    if not isinstance(payload, dict):  # pragma: no cover - idem
        return (False, None)
    return (True, payload.get("v"))


async def _cache_write(key: str, value: dict[str, Any] | None) -> None:
    try:
        from api_server.auth.deps import get_redis

        await get_redis().setex(key, _CACHE_TTL_SECONDS, json.dumps({"v": value}))
    except Exception:  # best-effort
        return


async def invalidate_effective_config_cache(
    *, tenant_id: UUID | None = None, project_id: UUID | None = None
) -> None:
    """Purga lo que la escritura de una capa puede haber dejado rancio.

    El alcance sube con la capa, porque la herencia baja: tocar la plataforma
    afecta a TODAS las combinaciones, tocar un tenant a todos sus proyectos, y
    tocar un proyecto solo a él. Best-effort e idempotente.
    """
    try:
        from api_server.auth.deps import get_redis

        redis = get_redis()
        if tenant_id is None:
            pattern = f"{_CACHE_PREFIX}*"
        elif project_id is None:
            pattern = f"{_CACHE_PREFIX}{tenant_id}:*"
        else:
            pattern = _cache_key(tenant_id, project_id)
        keys = [k async for k in redis.scan_iter(match=pattern)]
        if keys:
            await redis.delete(*keys)
    except Exception:
        return


def config_updated_at(row: GuardrailConfig | None) -> datetime | None:
    """`updated_at` de una capa, o ``None`` — para cabeceras condicionales."""
    return row.updated_at if row is not None else None
