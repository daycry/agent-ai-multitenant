"""Migration coverage for ``llm_providers`` (Plan 11.2 task_11_2_01).

Migration 0070 creates the ``llm_providers`` table — the platform-global
catalog of LLM provider runtime configuration (the four ADR-0021 paths:
claude_sdk / copilot / azure_foundry / ollama). ADR 0028 makes the table
platform-global (no ``tenant_id``) and, unlike the read-open
``model_prices`` / ``exchange_rates`` catalogs, with **NO RLS policy at
all** — access is gated entirely at the application layer (the
``system_admin`` endpoints on the BYPASSRLS admin engine). These tests
assert the end state the plan requires:

  - after ``upgrade head`` the table exists with every column the task
    names, and **without a ``tenant_id``** (platform-global);
  - ``base_url`` / ``secret_vault_path`` are nullable, ``config`` /
    ``is_active`` / ``display_name`` / ``kind`` are NOT NULL, and the
    ``config`` / ``is_active`` server defaults ({} / true) apply;
  - the ``ck_llm_providers_kind`` CHECK accepts the four valid kinds and
    rejects an out-of-set value at the DB level;
  - RLS is **NOT** enabled and there is **no policy** on the table (the
    ADR-0028 "no RLS, system_admin endpoints only" decision is provable
    at the schema, not just in app code);
  - **no secret column exists** — only ``secret_vault_path`` (a pointer),
    never a column that could hold a credential value;
  - the migration is reversible (head -> 0069 -> head) and the table
    comes and goes with it.

Seeding uses the BYPASSRLS migrations role (the same role the
``system_admin`` admin engine uses in production) — there is no
app_user / tenant path to this table by design.
"""

from __future__ import annotations

import asyncio
import json
from uuid import UUID

import asyncpg
import pytest
from alembic import command
from uuid6 import uuid7

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
async def _fetchval(dsn: str, sql: str, *args: object) -> object:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval(sql, *args)
    finally:
        await conn.close()


async def _fetchrow(dsn: str, sql: str, *args: object) -> asyncpg.Record | None:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchrow(sql, *args)
    finally:
        await conn.close()


async def _execute(dsn: str, sql: str, *args: object) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(sql, *args)
    finally:
        await conn.close()


async def _fetch(dsn: str, sql: str, *args: object) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


_COLS_SQL = """
    SELECT column_name, is_nullable
      FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = $1
"""


def _columns(dsn: str, table: str) -> dict[str, str]:
    """Map ``column_name -> is_nullable`` ('YES'/'NO')."""
    rows = asyncio.run(_fetch(dsn, _COLS_SQL, table))
    return {str(r["column_name"]): str(r["is_nullable"]) for r in rows}


def _table_exists(dsn: str, table: str) -> bool:
    val = asyncio.run(_fetchval(dsn, "SELECT to_regclass($1)", f"public.{table}"))
    return val is not None


def _rls_enabled(dsn: str, table: str) -> bool:
    val = asyncio.run(
        _fetchval(
            dsn,
            "SELECT relrowsecurity FROM pg_class WHERE oid = $1::regclass",
            table,
        )
    )
    return bool(val)


def _policy_count(dsn: str, table: str) -> int:
    val = asyncio.run(
        _fetchval(dsn, "SELECT count(*) FROM pg_policies WHERE tablename = $1", table)
    )
    return int(val)  # type: ignore[arg-type]


def _index_names(dsn: str, table: str) -> set[str]:
    rows = asyncio.run(_fetch(dsn, "SELECT indexname FROM pg_indexes WHERE tablename = $1", table))
    return {str(r["indexname"]) for r in rows}


def _seed_provider(
    dsn: str,
    *,
    provider_id: UUID,
    kind: str,
    display_name: str = "P",
    base_url: str | None = None,
    secret_vault_path: str | None = None,
) -> None:
    asyncio.run(
        _execute(
            dsn,
            "INSERT INTO llm_providers"
            " (id, kind, slug, display_name, base_url, secret_vault_path)"
            " VALUES ($1, $2, $6, $3, $4, $5)",
            provider_id,
            kind,
            display_name,
            base_url,
            secret_vault_path,
            str(provider_id),
        )
    )


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------
def test_table_and_columns_exist(alembic_config: object, admin_pg_dsn: str) -> None:
    """After upgrade head the table exists with every column the task names,
    and WITHOUT a tenant_id (it is platform-global)."""
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    assert _table_exists(admin_pg_dsn, "llm_providers")
    cols = _columns(admin_pg_dsn, "llm_providers")
    expected = {
        "id",
        "kind",
        "slug",
        "display_name",
        "base_url",
        "secret_vault_path",
        "config",
        "is_active",
        "created_at",
        "updated_at",
    }
    missing = expected - set(cols)
    assert not missing, f"llm_providers missing columns: {missing}"

    # Platform-global: there is NO tenant_id column (ADR 0028).
    assert "tenant_id" not in cols

    # Nullability: base_url / secret_vault_path are optional; the rest NOT NULL.
    assert cols["base_url"] == "YES"
    assert cols["secret_vault_path"] == "YES"
    assert cols["kind"] == "NO"
    assert cols["display_name"] == "NO"
    assert cols["config"] == "NO"
    assert cols["is_active"] == "NO"


def test_no_secret_value_column(alembic_config: object, admin_pg_dsn: str) -> None:
    """The credential VALUE is never stored — only the Vault POINTER. There
    must be no column whose name suggests it holds a secret value (api_key,
    token, password, ...); the only secret-adjacent column is the pointer
    ``secret_vault_path``."""
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    cols = set(_columns(admin_pg_dsn, "llm_providers"))
    forbidden = {
        "api_key",
        "apikey",
        "token",
        "oauth_token",
        "password",
        "secret",
        "credential",
        "client_secret",
    }
    leaked = cols & forbidden
    assert not leaked, f"llm_providers must not hold a secret value column: {leaked}"
    assert "secret_vault_path" in cols, "the Vault pointer column must exist"


def test_index_present(alembic_config: object, admin_pg_dsn: str) -> None:
    """The kind/active lookup index used by the runtime factory exists."""
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    names = _index_names(admin_pg_dsn, "llm_providers")
    assert "ix_llm_providers_kind_active" in names


def test_no_rls_no_policy(alembic_config: object, admin_pg_dsn: str) -> None:
    """ADR 0028: the table is platform-global with NO RLS and NO policy —
    access is gated by the system_admin endpoints, not by row security."""
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    assert (
        _rls_enabled(admin_pg_dsn, "llm_providers") is False
    ), "llm_providers must NOT have RLS enabled (ADR 0028)"
    assert (
        _policy_count(admin_pg_dsn, "llm_providers") == 0
    ), "llm_providers must have no RLS policy (ADR 0028)"


def test_defaults_apply(alembic_config: object, admin_pg_dsn: str, migrations_pg_dsn: str) -> None:
    """A minimal row takes the server defaults: config {} and is_active true."""
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    provider_id = uuid7()
    _seed_provider(migrations_pg_dsn, provider_id=provider_id, kind="ollama")

    row = asyncio.run(
        _fetchrow(
            admin_pg_dsn,
            "SELECT config, is_active, base_url, secret_vault_path"
            " FROM llm_providers WHERE id = $1",
            provider_id,
        )
    )
    assert row is not None
    assert json.loads(str(row["config"])) == {}
    assert row["is_active"] is True
    assert row["base_url"] is None
    assert row["secret_vault_path"] is None


def test_kind_check_accepts_all_four(
    alembic_config: object, migrations_pg_dsn: str, admin_pg_dsn: str
) -> None:
    """The CHECK accepts each of the four valid ADR-0021 kinds."""
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    for kind in ("claude_sdk", "copilot", "azure_foundry", "ollama"):
        provider_id = uuid7()
        _seed_provider(migrations_pg_dsn, provider_id=provider_id, kind=kind)
        got = asyncio.run(
            _fetchval(admin_pg_dsn, "SELECT kind FROM llm_providers WHERE id = $1", provider_id)
        )
        assert got == kind


def test_kind_check_rejects_unknown(alembic_config: object, migrations_pg_dsn: str) -> None:
    """The ck_llm_providers_kind CHECK rejects a value outside the closed set."""
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        _seed_provider(migrations_pg_dsn, provider_id=uuid7(), kind="openai")


def test_round_trip_config_and_endpoint(
    alembic_config: object, migrations_pg_dsn: str, admin_pg_dsn: str
) -> None:
    """A provider round-trips a non-secret JSONB config + a base_url + a
    Vault pointer (the secret VALUE is never here, only the pointer)."""
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    provider_id = uuid7()
    config = {"default_model": "gpt-4o", "enabled_models": ["gpt-4o", "gpt-4o-mini"]}
    asyncio.run(
        _execute(
            migrations_pg_dsn,
            "INSERT INTO llm_providers"
            " (id, kind, slug, display_name, base_url, secret_vault_path, config, is_active)"
            " VALUES ($1, 'azure_foundry', $5, 'Azure (prod)', $2, $3, $4::jsonb, false)",
            provider_id,
            "https://apim.example.test/openai",
            "platform/llm/" + str(provider_id),
            json.dumps(config),
            str(provider_id),
        )
    )
    row = asyncio.run(
        _fetchrow(
            admin_pg_dsn,
            "SELECT base_url, secret_vault_path, config, is_active"
            " FROM llm_providers WHERE id = $1",
            provider_id,
        )
    )
    assert row is not None
    assert row["base_url"] == "https://apim.example.test/openai"
    assert row["secret_vault_path"] == "platform/llm/" + str(provider_id)
    assert json.loads(str(row["config"])) == config
    assert row["is_active"] is False


def test_migration_is_reversible(alembic_config: object, admin_pg_dsn: str) -> None:
    """head -> 0069 -> head: the table is dropped on downgrade and recreated
    on the second upgrade (idempotent, fully reversible)."""
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    assert _table_exists(admin_pg_dsn, "llm_providers")

    command.downgrade(alembic_config, "0069_human_task_assignments")  # type: ignore[arg-type]
    assert not _table_exists(admin_pg_dsn, "llm_providers")

    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    assert _table_exists(admin_pg_dsn, "llm_providers")
    # And still no RLS / policy after the replay.
    assert _rls_enabled(admin_pg_dsn, "llm_providers") is False
    assert _policy_count(admin_pg_dsn, "llm_providers") == 0
