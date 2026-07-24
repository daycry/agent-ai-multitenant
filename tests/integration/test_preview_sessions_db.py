"""ADR 0130 — preview sessions at the DB layer.

Exercises the migration 0118 (plan_id nullable + kind + CHECK constraints) and
the repo queries against real Postgres:

  * ``list_active_preview_sessions(project_id=)`` finds a PROJECT preview
    (plan_id NULL, kind='preview', spec->>'project_id' match);
  * ``list_active_preview_sessions(plan_id=)`` finds a PLAN preview;
  * ``list_review_sessions_for_plan`` EXCLUDES previews (kind filter);
  * the autostart idempotency guard is NOT satisfied by a preview — a
    human-validation review still autostarts when the plan validates.

NOT RUN by the implementing agent (needs a live Postgres at TEST_PG_*).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


async def _seed(dsn: str) -> dict[str, UUID]:
    ids = {
        "tenant": uuid4(),
        "project": uuid4(),
        "plan": uuid4(),
        "review": uuid4(),
        "proj_preview": uuid4(),
        "plan_preview": uuid4(),
    }
    now = datetime.now(UTC)
    exp = now + timedelta(hours=24)
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE review_sessions, plans, projects, organizations RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'Org', 'org-0130')",
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, slug, status, is_template,"
            " repository_config)"
            " VALUES ($1, $2, 'Backend', 'backend', 'active', false, $3::jsonb)",
            ids["project"],
            ids["tenant"],
            json.dumps({"review_image": "backend:latest"}),
        )
        await conn.execute(
            "INSERT INTO plans (id, tenant_id, project_id, title, slug, status, specification)"
            " VALUES ($1, $2, $3, 'Plan', 'a-plan', 'pending_human_validation', '{}'::jsonb)",
            ids["plan"],
            ids["tenant"],
            ids["project"],
        )

        async def _mk(sid: UUID, *, plan_id: UUID | None, kind: str, spec: dict) -> None:
            await conn.execute(
                "INSERT INTO review_sessions"
                " (id, tenant_id, plan_id, spec, status, container_ids, expires_at, kind)"
                " VALUES ($1, $2, $3, $4::jsonb, 'running', '[]'::jsonb, $5, $6)",
                sid,
                ids["tenant"],
                plan_id,
                json.dumps(spec),
                exp,
                kind,
            )

        # A real human-validation review for the plan.
        await _mk(ids["review"], plan_id=ids["plan"], kind="plan", spec={})
        # A PROJECT preview (no plan) — associated via spec.project_id.
        await _mk(
            ids["proj_preview"],
            plan_id=None,
            kind="preview",
            spec={"project_id": str(ids["project"])},
        )
        # A PLAN preview (carries the plan_id but kind='preview').
        await _mk(
            ids["plan_preview"],
            plan_id=ids["plan"],
            kind="preview",
            spec={"project_id": str(ids["project"])},
        )
        return ids
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_migration_allows_null_plan_id_preview(
    _migrated: None, migrations_pg_dsn: str
) -> None:
    # _seed inserts a plan_id-NULL preview row; if the migration didn't drop the
    # NOT NULL (or the CHECK rejected it) the insert would raise.
    await _seed(migrations_pg_dsn)


@pytest.mark.asyncio
async def test_preview_queries_and_kind_filter(_migrated: None, migrations_pg_dsn: str) -> None:
    from api_server.db.review_session_repo import (
        list_active_preview_sessions,
        list_review_sessions_for_plan,
    )

    ids = await _seed(migrations_pg_dsn)
    async_dsn = migrations_pg_dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(async_dsn)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session:
            # migrations_user BYPASSRLS (same as the autostart wiring test), so no
            # tenant GUC is needed; the seeded rows carry an explicit tenant.
            proj_previews = await list_active_preview_sessions(session, project_id=ids["project"])
            plan_previews = await list_active_preview_sessions(session, plan_id=ids["plan"])
            plan_reviews = await list_review_sessions_for_plan(session, ids["plan"])
    finally:
        await engine.dispose()

    # project query → the project preview only (plan_id NULL)
    assert [s.id for s in proj_previews] == [ids["proj_preview"]]
    # plan query → the plan preview only
    assert [s.id for s in plan_previews] == [ids["plan_preview"]]
    # the plan's human-validation lookup EXCLUDES both previews
    assert [s.id for s in plan_reviews] == [ids["review"]]


@pytest.mark.asyncio
async def test_preview_does_not_block_autostart(_migrated: None, migrations_pg_dsn: str) -> None:
    from api_server.db.domain import Plan
    from api_server.review_autostart import build_review_autostart_request

    ids = await _seed(migrations_pg_dsn)
    # Drop the real human-validation review so ONLY previews remain for the plan.
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute("DELETE FROM review_sessions WHERE id = $1", ids["review"])
    finally:
        await conn.close()

    async_dsn = migrations_pg_dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(async_dsn)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session:
            plan = await session.get(Plan, ids["plan"])
            assert plan is not None
            payload = await build_review_autostart_request(
                session, plan=plan, tenant_id=ids["tenant"]
            )
    finally:
        await engine.dispose()

    # A plan preview is active, but it must NOT satisfy the idempotency guard —
    # the human-validation review still needs to autostart.
    assert payload is not None
    assert payload["plan_id"] == str(ids["plan"])
