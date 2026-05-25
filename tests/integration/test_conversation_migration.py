"""Verify migration 0014 creates conversations/messages with RLS + FKs.

The migration also promotes two soft-FKs to real ones now that both
sides exist:

  - plans.conversation_id          -> conversations.id  ON DELETE SET NULL
  - conversations.related_plan_id  -> plans.id          ON DELETE SET NULL
  - messages.related_plan_id       -> plans.id          ON DELETE SET NULL

We test for each of these explicitly, plus the table-level CHECKs (the
author_kind <-> author_*_id invariant on messages and the
current_mode/custom_mode_name invariant on conversations), plus the
round-trip head -> down -> head.
"""

from __future__ import annotations

import asyncio

import asyncpg
import pytest
from alembic import command

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# helpers (mirror those of test_migrations_v2)
# ---------------------------------------------------------------------------
async def _fetch_all(dsn: str, sql: str) -> list[tuple]:
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(sql)
        return [tuple(r) for r in rows]
    finally:
        await conn.close()


def _tables(dsn: str) -> set[str]:
    rows = asyncio.run(
        _fetch_all(dsn, "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    )
    return {r[0] for r in rows}


def _rls_enabled_tables(dsn: str) -> set[str]:
    rows = asyncio.run(
        _fetch_all(
            dsn,
            """
            SELECT c.relname
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public'
               AND c.relrowsecurity = true
            """,
        )
    )
    return {r[0] for r in rows}


def _policies(dsn: str) -> set[tuple[str, str]]:
    rows = asyncio.run(
        _fetch_all(
            dsn,
            """
            SELECT tablename, policyname
              FROM pg_policies
             WHERE schemaname = 'public'
            """,
        )
    )
    return {(r[0], r[1]) for r in rows}


def _foreign_keys(dsn: str, table: str) -> set[tuple[str, str, str]]:
    rows = asyncio.run(
        _fetch_all(
            dsn,
            f"""
            SELECT kcu.column_name, ccu.table_name, ccu.column_name
              FROM information_schema.table_constraints tc
              JOIN information_schema.key_column_usage kcu
                ON tc.constraint_name = kcu.constraint_name
              JOIN information_schema.constraint_column_usage ccu
                ON tc.constraint_name = ccu.constraint_name
             WHERE tc.constraint_type = 'FOREIGN KEY'
               AND tc.table_name = '{table}'
            """,
        )
    )
    return {tuple(r) for r in rows}


def _check_constraints(dsn: str, table: str) -> set[str]:
    rows = asyncio.run(
        _fetch_all(
            dsn,
            f"""
            SELECT conname
              FROM pg_constraint
             WHERE conrelid = '{table}'::regclass
               AND contype = 'c'
            """,
        )
    )
    return {r[0] for r in rows}


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------
def test_upgrade_head_creates_chat_tables(alembic_config, admin_pg_dsn: str) -> None:
    command.upgrade(alembic_config, "head")
    tables = _tables(admin_pg_dsn)
    assert "conversations" in tables
    assert "messages" in tables


def test_chat_tables_have_rls(alembic_config, admin_pg_dsn: str) -> None:
    command.upgrade(alembic_config, "head")
    enabled = _rls_enabled_tables(admin_pg_dsn)
    assert {"conversations", "messages"} <= enabled


def test_chat_tables_have_tenant_isolation_policies(alembic_config, admin_pg_dsn: str) -> None:
    command.upgrade(alembic_config, "head")
    policies = _policies(admin_pg_dsn)
    assert ("conversations", "conversations_tenant_isolation") in policies
    assert ("messages", "messages_tenant_isolation") in policies


def test_conversations_foreign_keys(alembic_config, admin_pg_dsn: str) -> None:
    command.upgrade(alembic_config, "head")
    fks = _foreign_keys(admin_pg_dsn, "conversations")
    targets = {(col, tgt) for (col, tgt, _) in fks}
    assert ("project_id", "projects") in targets
    assert ("created_by", "users") in targets
    # related_plan_id was a soft-FK in the ORM; the migration promotes it.
    assert ("related_plan_id", "plans") in targets


def test_messages_foreign_keys(alembic_config, admin_pg_dsn: str) -> None:
    command.upgrade(alembic_config, "head")
    fks = _foreign_keys(admin_pg_dsn, "messages")
    targets = {(col, tgt) for (col, tgt, _) in fks}
    assert ("conversation_id", "conversations") in targets
    assert ("author_user_id", "users") in targets
    assert ("author_agent_id", "agents") in targets
    assert ("related_plan_id", "plans") in targets


def test_plans_conversation_id_promoted_to_real_fk(alembic_config, admin_pg_dsn: str) -> None:
    """plans.conversation_id was a soft-FK in migration 0002; once the
    conversations table exists, migration 0014 turns it into a real FK
    so deleting a conversation correctly nulls out the back-link."""
    command.upgrade(alembic_config, "head")
    fks = _foreign_keys(admin_pg_dsn, "plans")
    targets = {(col, tgt) for (col, tgt, _) in fks}
    assert ("conversation_id", "conversations") in targets


def test_messages_check_constraint_present(alembic_config, admin_pg_dsn: str) -> None:
    command.upgrade(alembic_config, "head")
    names = _check_constraints(admin_pg_dsn, "messages")
    assert "ck_messages_author_kind_consistency" in names


def test_conversations_check_constraint_present(alembic_config, admin_pg_dsn: str) -> None:
    command.upgrade(alembic_config, "head")
    names = _check_constraints(admin_pg_dsn, "conversations")
    assert "ck_conversations_custom_mode_name_consistency" in names


def test_messages_author_kind_check_blocks_bad_insert(alembic_config, admin_pg_dsn: str) -> None:
    """The CHECK is the last line of defence — a bug in the API layer
    must not be able to land an inconsistent row."""
    command.upgrade(alembic_config, "head")

    async def _try_bad_insert() -> Exception | None:
        conn = await asyncpg.connect(admin_pg_dsn)
        try:
            # author_kind='user' with NO author_user_id should fail the CHECK.
            await conn.execute(
                """
                INSERT INTO messages
                    (id, tenant_id, conversation_id, author_kind,
                     content, mode, attachments, is_summary)
                VALUES
                    ($1::uuid, $2::uuid, $3::uuid, 'user',
                     '', 'planning', '[]'::jsonb, false)
                """,
                "00000000-0000-0000-0000-000000000001",
                "00000000-0000-0000-0000-000000000002",
                "00000000-0000-0000-0000-000000000003",
            )
        except asyncpg.PostgresError as exc:
            return exc
        finally:
            await conn.close()
        return None

    exc = asyncio.run(_try_bad_insert())
    assert exc is not None, "the CHECK should have rejected this insert"
    # ck_messages_author_kind_consistency is the constraint name.
    assert "author_kind" in str(exc) or "ck_messages_author_kind_consistency" in str(exc)


def test_round_trip_down_up_preserves_schema(alembic_config, admin_pg_dsn: str) -> None:
    """head -> 0013 -> head leaves both new tables in place."""
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "0013_task_status_widen")
    tables = _tables(admin_pg_dsn)
    assert "conversations" not in tables
    assert "messages" not in tables

    command.upgrade(alembic_config, "head")
    tables = _tables(admin_pg_dsn)
    assert "conversations" in tables
    assert "messages" in tables

    enabled = _rls_enabled_tables(admin_pg_dsn)
    assert {"conversations", "messages"} <= enabled
