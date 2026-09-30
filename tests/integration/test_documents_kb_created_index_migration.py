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
        return await conn.fetchval(
            "SELECT indexdef FROM pg_indexes i JOIN pg_class c ON c.relname = i.indexname"
            " JOIN pg_index x ON x.indexrelid = c.oid"
            " WHERE i.indexname = $1 AND x.indisvalid",
            _INDEX,
        )
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


def test_migration_is_transactional_to_keep_the_migration_lock() -> None:
    """Sin `CONCURRENTLY`/`autocommit_block` (precedente: migración 0142).

    `autocommit_block()` comitea y libera el `pg_advisory_xact_lock` de `env.py`
    que serializa los upgrades concurrentes; `documents` es pequeña y el bloqueo
    de escritura dura menos de un segundo.
    """
    import pathlib

    versions = pathlib.Path(__file__).resolve().parents[2] / "apps/api-server/migrations/versions"
    path = next(versions.glob("*_0150_documents_kb_created_index.py"))
    src = path.read_text(encoding="utf-8")
    assert "autocommit_block()" not in src.split('"""', 2)[2]
    assert "postgresql_concurrently" not in src.split('"""', 2)[2]
