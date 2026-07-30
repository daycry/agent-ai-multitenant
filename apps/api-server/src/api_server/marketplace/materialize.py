"""Materialización install→catálogo (ADR 0100, pieza 2 — opción c).

Instalar un listing dejaba solo «intent + permission»: ninguna capacidad que
un agente pudiera invocar. Al pasar una instalación a ``ENABLED`` este paso
TRANSACCIONAL upserta la fila nativa del catálogo del tenant, cortado por
``implementation_type`` (la línea real de la dependencia de infra):

- ``kind=skill`` → fila ``Skill`` (prompt_fragment: texto, no ejecuta código).
- ``kind∈{tool,mcp_server}`` con ``implementation_type∈{mcp_tool,http_endpoint}``
  → fila ``Tool`` (ejecutan por red: la infra MCP/egress ya existe).
- ``implementation_type∈{python_function,docker_command}`` → **DIFERIDO** (sin
  fila): ejecutan código arbitrario y exigen el sandbox out-of-process que el
  api-server no tiene (ADR 0081 Fase B/C). Queda documentado en el resultado.

Idempotente por ``source_installation_id`` (re-enable resucita la fila
soft-borrada; re-install no duplica). Colisión de nombre contra
``uq_tools_tenant_name``/skills → sufijo determinista ``-mkt-XXXXXX``, nunca
insert silencioso. ``dematerialize_installation`` (uninstall/revoke/disable)
soft-borra las filas materializadas EN LA MISMA transacción (no-orfandad).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.domain import (
    Skill,
    SkillCategory,
    Tool,
    ToolCategory,
    ToolImplementationType,
    ToolSecurityLevel,
)
from api_server.db.marketplace import (
    MarketplaceInstallation,
    MarketplaceListing,
    MarketplaceListingKind,
)
from api_server.schemas.catalog import normalize_tool_name

logger = structlog.get_logger("api_server.marketplace.materialize")

# implementation_types que ejecutan POR RED → materializables ya (ADR 0100).
_NETWORK_IMPL_TYPES = frozenset(
    {ToolImplementationType.MCP_TOOL.value, ToolImplementationType.HTTP_ENDPOINT.value}
)
# Los que ejecutan código arbitrario → diferidos hasta el sandbox (ADR 0081 B/C).
_DEFERRED_IMPL_TYPES = frozenset(
    {ToolImplementationType.PYTHON_FUNCTION.value, ToolImplementationType.DOCKER_COMMAND.value}
)


class MaterializeError(Exception):
    """El manifest no da para una fila válida del catálogo cerrado (422)."""


@dataclass(frozen=True)
class MaterializeResult:
    """Qué produjo la materialización (viaja al audit row del install)."""

    materialized: bool
    kind: str
    catalog_id: str | None = None
    catalog_name: str | None = None
    deferred_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "materialized": self.materialized,
            "kind": self.kind,
            "catalog_id": self.catalog_id,
            "catalog_name": self.catalog_name,
            "deferred_reason": self.deferred_reason,
        }


def _skill_category(manifest: dict[str, Any]) -> str:
    raw = str(manifest.get("category") or "").strip().lower()
    if raw in {c.value for c in SkillCategory}:
        return raw
    # Catálogo cerrado (ADR 0050): sin categoría válida, el bucket honesto de
    # material externo de investigación/uso general.
    return SkillCategory.RESEARCH.value


def _tool_category(manifest: dict[str, Any], impl_type: str) -> str:
    raw = str(manifest.get("category") or "").strip().lower()
    if raw in {c.value for c in ToolCategory}:
        return raw
    if impl_type == ToolImplementationType.MCP_TOOL.value:
        return ToolCategory.MCP.value
    return ToolCategory.NETWORK.value


async def _dedupe_name(
    session: AsyncSession,
    model: type[Tool] | type[Skill],
    *,
    tenant_id: Any,
    name: str,
    installation_id: Any,
) -> str:
    """Nombre libre de colisión: si ya existe una fila VIVA de otro origen con
    ese nombre, sufijo determinista ``-mkt-XXXXXX`` (id de la instalación)."""
    clash = (
        await session.execute(
            select(model.id).where(
                model.tenant_id == tenant_id,
                model.name == name,
                model.deleted_at.is_(None),
                model.source_installation_id != installation_id,
            )
        )
    ).first()
    if clash is None:
        return name
    return f"{name}-mkt-{str(installation_id).replace('-', '')[:6]}"


async def materialize_installation(
    session: AsyncSession,
    *,
    installation: MarketplaceInstallation,
    listing: MarketplaceListing,
) -> MaterializeResult:
    """Upsert de la capacidad nativa al pasar la instalación a ``ENABLED``.

    El caller es dueño de la transacción: si esto levanta
    :class:`MaterializeError`, el enable entero debe abortar (nunca un
    ``ENABLED`` sin capacidad cuando era materializable).
    """
    manifest: dict[str, Any] = dict(listing.manifest or {})

    if listing.kind == MarketplaceListingKind.SKILL.value:
        prompt_fragment = str(manifest.get("prompt_fragment") or manifest.get("body") or "").strip()
        if not prompt_fragment:
            raise MaterializeError("skill manifest has no prompt_fragment/body")
        existing = (
            await session.execute(
                select(Skill).where(Skill.source_installation_id == installation.id)
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.prompt_fragment = prompt_fragment
            existing.description = manifest.get("description") or listing.description
            existing.source_version = installation.version
            existing.deleted_at = None  # re-enable resucita
            await session.flush()
            return MaterializeResult(
                materialized=True,
                kind="skill",
                catalog_id=str(existing.id),
                catalog_name=existing.name,
            )
        name = await _dedupe_name(
            session,
            Skill,
            tenant_id=installation.tenant_id,
            name=str(manifest.get("name") or listing.name)[:120],
            installation_id=installation.id,
        )
        skill = Skill(
            tenant_id=installation.tenant_id,
            name=name,
            category=_skill_category(manifest),
            description=manifest.get("description") or listing.description,
            prompt_fragment=prompt_fragment,
            is_builtin=False,
            source_listing_id=listing.id,
            source_installation_id=installation.id,
            source_version=installation.version,
        )
        session.add(skill)
        await session.flush()
        return MaterializeResult(
            materialized=True, kind="skill", catalog_id=str(skill.id), catalog_name=skill.name
        )

    # kind ∈ {tool, mcp_server}
    impl_type = str(
        manifest.get("implementation_type")
        or (
            ToolImplementationType.MCP_TOOL.value
            if listing.kind == MarketplaceListingKind.MCP_SERVER.value
            else ""
        )
    ).strip()
    if impl_type in _DEFERRED_IMPL_TYPES:
        # Diferido honesto: ejecuta código arbitrario → necesita el sandbox
        # out-of-process (ADR 0081 Fase B/C). ENABLED queda como intent.
        return MaterializeResult(
            materialized=False,
            kind=listing.kind,
            deferred_reason=(
                f"implementation_type {impl_type!r} requires the out-of-process "
                "sandbox (ADR 0081 Phase B/C); install stays intent-only"
            ),
        )
    if impl_type not in _NETWORK_IMPL_TYPES:
        raise MaterializeError(
            f"listing manifest has no materialisable implementation_type ({impl_type!r})"
        )
    implementation_ref = str(manifest.get("implementation_ref") or "").strip()
    if not implementation_ref:
        raise MaterializeError("tool manifest has no implementation_ref")

    existing_tool = (
        await session.execute(select(Tool).where(Tool.source_installation_id == installation.id))
    ).scalar_one_or_none()
    if existing_tool is not None:
        existing_tool.implementation_ref = implementation_ref
        existing_tool.input_schema = dict(manifest.get("input_schema") or {})
        existing_tool.description = manifest.get("description") or listing.description
        existing_tool.source_version = installation.version
        existing_tool.deleted_at = None
        await session.flush()
        return MaterializeResult(
            materialized=True,
            kind=listing.kind,
            catalog_id=str(existing_tool.id),
            catalog_name=existing_tool.name,
        )
    name = await _dedupe_name(
        session,
        Tool,
        tenant_id=installation.tenant_id,
        name=normalize_tool_name(str(manifest.get("name") or listing.name)) or "mkt-tool",
        installation_id=installation.id,
    )
    tool = Tool(
        tenant_id=installation.tenant_id,
        name=name,
        description=manifest.get("description") or listing.description,
        category=_tool_category(manifest, impl_type),
        implementation_type=impl_type,
        implementation_ref=implementation_ref,
        input_schema=dict(manifest.get("input_schema") or {}),
        # Mínimo privilegio para código de terceros (mismo default que el
        # import MCP, ADR 0052) — el operador puede subirlo después.
        security_level=ToolSecurityLevel.SANDBOXED.value,
        is_builtin=False,
        source_listing_id=listing.id,
        source_installation_id=installation.id,
        source_version=installation.version,
    )
    session.add(tool)
    await session.flush()
    return MaterializeResult(
        materialized=True, kind=listing.kind, catalog_id=str(tool.id), catalog_name=tool.name
    )


async def dematerialize_installation(session: AsyncSession, *, installation_id: Any) -> int:
    """Soft-borra las filas materializadas por esta instalación (misma txn).

    Uninstall/revoke/disable: la capacidad no puede sobrevivir a su permiso.
    Devuelve cuántas filas se retiraron (0 = nada materializado, p. ej. slice
    diferido o instalación antigua)."""
    retired = 0
    now = datetime.now(UTC)

    async def _retire_tools() -> int:
        rows = (
            (
                await session.execute(
                    select(Tool).where(
                        Tool.source_installation_id == installation_id,
                        Tool.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            row.deleted_at = now
        return len(rows)

    async def _retire_skills() -> int:
        rows = (
            (
                await session.execute(
                    select(Skill).where(
                        Skill.source_installation_id == installation_id,
                        Skill.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            row.deleted_at = now
        return len(rows)

    retired = await _retire_tools() + await _retire_skills()
    if retired:
        await session.flush()
    return retired


__all__ = [
    "MaterializeError",
    "MaterializeResult",
    "dematerialize_installation",
    "materialize_installation",
]
