"""Integration tests for tenant-defined custom chat modes
(Plan 03 task_03_08).

Verifies the full path: persist a custom_chat_modes row, load it via
the repository, feed the dict into `resolve_mode_config`, and confirm
the resolver returns the tenant's spec instead of the planning
fallback.

Multi-tenant isolation is verified end-to-end (B never sees A's modes).
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from api_server.chat.modes import (
    BUILTIN_MODES,
    BuiltinChatMode,
    resolve_mode_config,
)
from api_server.db import domain  # noqa: F401  — register the metadata
from api_server.db.conversation import ChatMode
from api_server.db.custom_chat_mode import CustomChatMode
from api_server.db.custom_chat_mode_repo import load_tenant_custom_modes
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------
async def _seed_tenants(dsn: str) -> tuple[UUID, UUID]:
    tenant_a = uuid4()
    tenant_b = uuid4()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE custom_chat_modes, messages, conversations, projects,"
            " agents, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            tenant_a,
            "Tenant A",
            "tenant-a-cm",
            tenant_b,
            "Tenant B",
            "tenant-b-cm",
        )
    finally:
        await conn.close()

    return tenant_a, tenant_b


def _engine(admin_database_url: str):
    return create_async_engine(admin_database_url, echo=False)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def schema_ready(alembic_config) -> None:
    command.upgrade(alembic_config, "head")


# ===========================================================================
# Tests
# ===========================================================================
def test_load_returns_empty_when_no_custom_modes_for_tenant(
    schema_ready, admin_database_url: str, migrations_pg_dsn: str
) -> None:
    tenant_a, _ = asyncio.run(_seed_tenants(migrations_pg_dsn))

    async def _run() -> dict[str, object]:
        engine = _engine(admin_database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with session_factory() as session:
                return await load_tenant_custom_modes(session, tenant_a)
        finally:
            await engine.dispose()

    assert asyncio.run(_run()) == {}


def test_custom_mode_persisted_and_resolved_via_registry(
    schema_ready, admin_database_url: str, migrations_pg_dsn: str
) -> None:
    """A custom row turns into a CustomModeSpec the resolver picks up."""
    tenant_a, _ = asyncio.run(_seed_tenants(migrations_pg_dsn))

    async def _run() -> tuple[dict[str, object], object]:
        engine = _engine(admin_database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with session_factory() as session:
                session.add(
                    CustomChatMode(
                        tenant_id=tenant_a,
                        name="design-review",
                        label_es="Revisión de diseño",
                        label_en="Design review",
                        system_prompt="Modo Design Review: el equipo critica una propuesta.",
                        allowed_tools=["file_read", "task_comment"],
                        planning_subgraph=False,
                    )
                )
                await session.commit()
            async with session_factory() as session:
                registry = await load_tenant_custom_modes(session, tenant_a)
            cfg = resolve_mode_config(
                ChatMode.CUSTOM.value,
                custom_mode_name="design-review",
                custom_modes=registry,
            )
            return registry, cfg
        finally:
            await engine.dispose()

    registry, cfg = asyncio.run(_run())

    assert set(registry) == {"design-review"}
    assert cfg.name == "design-review"
    assert cfg.label_es == "Revisión de diseño"
    assert "Design Review" in cfg.system_prompt
    assert set(cfg.allowed_tools) == {"file_read", "task_comment"}
    assert cfg.planning_subgraph is False


def test_custom_modes_are_tenant_scoped(
    schema_ready, admin_database_url: str, migrations_pg_dsn: str
) -> None:
    """B inserts its own mode named 'design-review'; A's load must
    still return only A's row (RLS would enforce this in prod; we run
    BYPASSRLS here, so the repository's explicit WHERE protects us)."""
    tenant_a, tenant_b = asyncio.run(_seed_tenants(migrations_pg_dsn))

    async def _run() -> tuple[dict[str, object], dict[str, object]]:
        engine = _engine(admin_database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with session_factory() as session:
                session.add(
                    CustomChatMode(
                        tenant_id=tenant_a,
                        name="design-review",
                        label_es="A — Design Review",
                        label_en="A — Design Review",
                        system_prompt="Variante de Tenant A.",
                        allowed_tools=["task_comment"],
                    )
                )
                session.add(
                    CustomChatMode(
                        tenant_id=tenant_b,
                        name="design-review",
                        label_es="B — Design Review",
                        label_en="B — Design Review",
                        system_prompt="Variante de Tenant B.",
                        allowed_tools=["file_read"],
                    )
                )
                await session.commit()

            async with session_factory() as session:
                registry_a = await load_tenant_custom_modes(session, tenant_a)
            async with session_factory() as session:
                registry_b = await load_tenant_custom_modes(session, tenant_b)
            return registry_a, registry_b
        finally:
            await engine.dispose()

    registry_a, registry_b = asyncio.run(_run())

    assert registry_a["design-review"].label_es.startswith("A —")
    assert registry_b["design-review"].label_es.startswith("B —")
    # No cross-tenant bleed:
    assert registry_a["design-review"].label_es != registry_b["design-review"].label_es


def test_resolver_falls_back_when_custom_name_not_in_registry(
    schema_ready, admin_database_url: str, migrations_pg_dsn: str
) -> None:
    """A conversation that references a custom mode whose row was soft-
    deleted (or never created) gets the safe planning fallback — NOT
    silently the execution preset with its shell access."""
    tenant_a, _ = asyncio.run(_seed_tenants(migrations_pg_dsn))

    async def _run() -> dict[str, object]:
        engine = _engine(admin_database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with session_factory() as session:
                return await load_tenant_custom_modes(session, tenant_a)
        finally:
            await engine.dispose()

    registry = asyncio.run(_run())
    cfg = resolve_mode_config(
        ChatMode.CUSTOM.value,
        custom_mode_name="missing-mode",
        custom_modes=registry,
    )
    # Fallback prompt is planning's; the label echoes the requested name.
    assert cfg.label_es == "missing-mode"
    assert cfg.system_prompt == BUILTIN_MODES[BuiltinChatMode.PLANNING.value].system_prompt
