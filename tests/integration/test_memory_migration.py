"""Integration tests for migration 0020 — memory_entries + pgvector + HNSW
(Plan 04 task_04_02).

Brings the schema up to head and verifies, against the real Postgres:

  - the `vector` extension is enabled,
  - the table exists with the columns the ORM expects,
  - the embedding column is `vector(768)`,
  - the HNSW index `ix_memory_entries_embedding_hnsw` is present,
  - the CHECK constraints `ck_memory_entries_*` are wired,
  - RLS is enabled + the `memory_entries_tenant_isolation` policy
    actually hides another tenant's rows when the session sets
    `app.tenant_id`.

The migration is exercised by `command.upgrade(... "head")`. We skip
the downgrade test for the same reason migration 0017 does — the test
DB lifecycle owns rollback, so we don't pay the dual-direction cost on
every PR.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture()
def schema_at_head(alembic_config) -> None:
    """Run the migrations up to head. `alembic.command.upgrade` does
    its own `asyncio.run`, which clashes with `@pytest.mark.asyncio`
    tests — so we keep it inside a sync fixture and let pytest order
    things for us."""
    command.upgrade(alembic_config, "head")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _query_one(conn: asyncpg.Connection, sql: str, *args) -> asyncpg.Record | None:
    return await conn.fetchrow(sql, *args)


async def _seed_two_tenants(dsn: str) -> tuple[UUID, UUID, UUID]:
    """Two tenants + one user per tenant. Returns (tenant_a, tenant_b, user_a)."""
    tenant_a = uuid4()
    tenant_b = uuid4()
    user_a = uuid4()
    user_b = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE memory_entries, plans, conversations, projects, agents,"
            " user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES"
            " ($1, $2, $3), ($4, $5, $6), ($7, $8, $9)",
            tenant_a,
            "Tenant A",
            "tenant-a-mem",
            tenant_b,
            "Tenant B",
            "tenant-b-mem",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-mem",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3), ($4, $5, $6)",
            user_a,
            "alice@mem.test",
            "h",
            user_b,
            "bob@mem.test",
            "h",
        )
    finally:
        await conn.close()
    return tenant_a, tenant_b, user_a


# ===========================================================================
# Schema
# ===========================================================================
@pytest.mark.asyncio
async def test_pgvector_extension_is_enabled(schema_at_head, migrations_pg_dsn: str) -> None:
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        row = await _query_one(conn, "SELECT extname FROM pg_extension WHERE extname = 'vector'")
        assert row is not None
        assert row["extname"] == "vector"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_memory_entries_table_has_expected_columns(
    schema_at_head, migrations_pg_dsn: str
) -> None:
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        rows = await conn.fetch(
            "SELECT column_name, data_type, is_nullable"
            " FROM information_schema.columns"
            " WHERE table_name = 'memory_entries'"
        )
        cols = {r["column_name"]: r for r in rows}
        # Owner pointer trio + scope + type + embedding + housekeeping.
        for col in (
            "id",
            "tenant_id",
            "scope",
            "type",
            "content",
            "embedding",
            "user_id",
            "team_id",
            "project_id",
            "source_execution_id",
            "agent_id",
            "tags",
            "metadata",
            "created_at",
            "updated_at",
            "deleted_at",
        ):
            assert col in cols, f"column {col!r} missing from memory_entries"
        # Embedding is the only column where pgvector's data type
        # shows up as `USER-DEFINED` in the information_schema view.
        assert cols["embedding"]["data_type"] == "USER-DEFINED"
        assert cols["embedding"]["is_nullable"] == "YES"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_embedding_column_is_vector_768(schema_at_head, migrations_pg_dsn: str) -> None:
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        # pgvector stores the dimensionality on pg_attribute.atttypmod.
        row = await _query_one(
            conn,
            "SELECT format_type(atttypid, atttypmod) AS t"
            " FROM pg_attribute"
            " WHERE attrelid = 'memory_entries'::regclass"
            "   AND attname = 'embedding'",
        )
        assert row is not None
        assert row["t"] == "vector(768)"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_hnsw_index_is_present(schema_at_head, migrations_pg_dsn: str) -> None:
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        row = await _query_one(
            conn,
            "SELECT indexdef FROM pg_indexes"
            " WHERE tablename = 'memory_entries'"
            "   AND indexname = 'ix_memory_entries_embedding_hnsw'",
        )
        assert row is not None
        # Two markers: the access method (hnsw) and the operator class
        # (vector_cosine_ops).
        assert "hnsw" in row["indexdef"]
        assert "vector_cosine_ops" in row["indexdef"]
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_check_constraints_are_wired(schema_at_head, migrations_pg_dsn: str) -> None:
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        rows = await conn.fetch(
            "SELECT conname FROM pg_constraint"
            " WHERE conrelid = 'memory_entries'::regclass"
            "   AND contype = 'c'"  # CHECK
        )
        names = {r["conname"] for r in rows}
        for ck in (
            "ck_memory_entries_scope",
            "ck_memory_entries_type",
            "ck_memory_entries_scope_pointer",
        ):
            assert ck in names, f"check constraint {ck!r} missing"
    finally:
        await conn.close()


# ===========================================================================
# RLS — the migration enables it, but only an actual cross-tenant probe
# proves the policy works.
# ===========================================================================
@pytest.mark.asyncio
async def test_rls_blocks_cross_tenant_reads(schema_at_head, migrations_pg_dsn: str) -> None:
    """As the `app_user` (NOBYPASSRLS), setting `app.tenant_id = A` must
    return only tenant A's rows, never B's. We seed both as the
    migrations user (BYPASSRLS) so the seed itself doesn't trip RLS."""
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()

    tenant_a, tenant_b, user_a = await _seed_two_tenants(migrations_pg_dsn)

    # Seed one memory_entry per tenant using the BYPASSRLS user.
    mig_conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await mig_conn.execute(
            "INSERT INTO memory_entries (id, tenant_id, scope, type, content, user_id)"
            " VALUES ($1, $2, 'private', 'episodic', $3, $4),"
            "        ($5, $6, 'private', 'episodic', $7, $4)",
            uuid4(),
            tenant_a,
            "alice's private memory",
            user_a,
            uuid4(),
            tenant_b,
            "bob's private memory",
        )
    finally:
        await mig_conn.close()

    # Now connect as app_user (RLS enforced). Without setting the GUC
    # we should see nothing.
    from tests.integration.conftest import (
        PG_APP_PASSWORD,
        PG_APP_USER,
        PG_HOST,
        PG_PORT,
        PG_TEST_DB,
    )

    app_conn = await asyncpg.connect(
        f"postgresql://{PG_APP_USER}:{PG_APP_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_TEST_DB}"
    )
    try:
        # No tenant set → policy denies → zero rows.
        rows = await app_conn.fetch("SELECT id FROM memory_entries")
        assert rows == []

        # Tenant A set → only A's row.
        await app_conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(tenant_a))
        rows = await app_conn.fetch("SELECT content FROM memory_entries")
        assert len(rows) == 1
        assert rows[0]["content"] == "alice's private memory"

        # Switch to tenant B → only B's row.
        await app_conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(tenant_b))
        rows = await app_conn.fetch("SELECT content FROM memory_entries")
        assert len(rows) == 1
        assert rows[0]["content"] == "bob's private memory"
    finally:
        await app_conn.close()


@pytest.mark.asyncio
async def test_scope_pointer_check_rejects_inconsistent_row(
    schema_at_head, migrations_pg_dsn: str
) -> None:
    """A `private` row without a user_id must be rejected by the DB."""
    tenant_a, _, _ = await _seed_two_tenants(migrations_pg_dsn)
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        with pytest.raises(asyncpg.IntegrityConstraintViolationError) as info:
            await conn.execute(
                "INSERT INTO memory_entries (id, tenant_id, scope, type, content)"
                " VALUES ($1, $2, 'private', 'episodic', 'no owner')",
                uuid4(),
                tenant_a,
            )
        assert "ck_memory_entries_scope_pointer" in str(info.value)
    finally:
        await conn.close()
