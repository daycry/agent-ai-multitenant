"""Migración 0150 — `ix_documents_kb_created` (`task_ui_21`): aditiva y reversible."""

from __future__ import annotations

import asyncpg
import pytest
from alembic import command

pytestmark = pytest.mark.integration

_INDEX = "ix_documents_kb_created"


async def _indexdef(dsn: str) -> str | None:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval("SELECT indexdef FROM pg_indexes WHERE indexname = $1", _INDEX)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_index_upgrade_downgrade_upgrade(alembic_config, migrations_pg_dsn: str) -> None:
    import asyncio

    await asyncio.to_thread(command.upgrade, alembic_config, "head")
    definition = await _indexdef(migrations_pg_dsn)
    assert definition is not None
    assert "kb_id, created_at DESC, id" in definition
    assert "deleted_at IS NULL" in definition

    await asyncio.to_thread(command.downgrade, alembic_config, "0149_project_integrations")
    assert await _indexdef(migrations_pg_dsn) is None

    await asyncio.to_thread(command.upgrade, alembic_config, "head")
    assert await _indexdef(migrations_pg_dsn) is not None
