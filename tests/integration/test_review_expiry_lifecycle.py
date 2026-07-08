"""Integration tests — review-runtime expiry lifecycle (C8 F40/F41).

Drives ``workers.maintenance._expire_review_runtimes`` against the real Postgres:

  * an OVERDUE ``running`` session → ``expired`` AND its plan
    ``pending_human_validation`` → ``blocked`` (F40),
  * a TERMINAL session with leftover ``container_ids`` → containers reaped and
    ``container_ids`` cleared, but the ROW SURVIVES (F41 + ADR 0107: el
    veredicto y el ``rejection_reason`` son historia que consumen el panel y
    generate-corrections; el soft-delete original destruía esa historia — visto
    en vivo con el plan CI4). The ``docker rm -f`` is best-effort and no-ops
    without a daemon, so only the DB effects are asserted here,
  * the sweep is IDEMPOTENT: a second pass neither re-transitions the (now
    ``blocked``) plan nor re-lists the already-reaped session (its
    ``container_ids`` are empty).

NOT RUN by the implementing agent (needs a live Postgres at TEST_PG_*). The
container-reaping branch needs Docker and is covered by the unit test for
``_reap_review_containers`` returning 0 without a daemon.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from api_server.db.domain import Plan
from api_server.db.models import ReviewSession
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


@pytest.fixture()
def workers_settings(monkeypatch: pytest.MonkeyPatch, migrations_pg_dsn: str, test_redis_url: str):
    async_dsn = migrations_pg_dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    monkeypatch.setenv("WORKERS_DATABASE_URL", async_dsn)
    monkeypatch.setenv("WORKERS_EVENTS_REDIS_URL", test_redis_url)
    from workers.config import get_settings, reset_settings_cache

    reset_settings_cache()
    yield get_settings()
    reset_settings_cache()


async def _seed(dsn: str) -> dict[str, UUID]:
    ids = {
        "tenant": uuid4(),
        "project": uuid4(),
        "plan": uuid4(),
        "overdue_session": uuid4(),
        "terminal_session": uuid4(),
    }
    now = datetime.now(UTC)
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE review_sessions, plans, projects, organizations" " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T exp', 't-c8-expiry')",
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status, is_template)"
            " VALUES ($1, $2, 'P', 'active', false)",
            ids["project"],
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO plans (id, tenant_id, project_id, title, status)"
            " VALUES ($1, $2, $3, 'Plan', 'pending_human_validation')",
            ids["plan"],
            ids["tenant"],
            ids["project"],
        )
        spec = json.dumps({"plan_title": "Plan", "owner_user_id": None})
        # Overdue running session — expires in the past.
        await conn.execute(
            "INSERT INTO review_sessions"
            " (id, tenant_id, plan_id, spec, status, container_ids, expires_at)"
            " VALUES ($1, $2, $3, $4::jsonb, 'running', '[]'::jsonb, $5)",
            ids["overdue_session"],
            ids["tenant"],
            ids["plan"],
            spec,
            now - timedelta(hours=1),
        )
        # Terminal session with leftover containers — reap candidate.
        await conn.execute(
            "INSERT INTO review_sessions"
            " (id, tenant_id, plan_id, spec, status, container_ids, expires_at)"
            " VALUES ($1, $2, $3, $4::jsonb, 'approved', $5::jsonb, $6)",
            ids["terminal_session"],
            ids["tenant"],
            ids["plan"],
            spec,
            json.dumps(["agentic-review-abc"]),
            now + timedelta(hours=10),
        )
        return ids
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_expiry_blocks_plan_and_soft_deletes_terminal(
    _migrated: None, workers_settings: object, migrations_pg_dsn: str
) -> None:
    from workers.maintenance import _expire_review_runtimes

    ids = await _seed(migrations_pg_dsn)

    result = await _expire_review_runtimes(workers_settings)  # type: ignore[arg-type]

    assert result["expired"] == 1
    assert result["reaped"] == 1
    assert "error" not in result

    engine = create_async_engine(workers_settings.database_url)  # type: ignore[attr-defined]
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessionmaker() as session:
            plan = await session.get(Plan, ids["plan"])
            overdue = await session.get(ReviewSession, ids["overdue_session"])
            terminal = await session.get(ReviewSession, ids["terminal_session"])
        # F40: the overdue session expired + the plan is now blocked.
        assert overdue is not None and overdue.status == "expired"
        assert plan is not None and plan.status == "blocked"
        # F41 + ADR 0107: containers reaped (ids cleared) pero la fila SOBREVIVE
        # — el veredicto/motivo siguen visibles para el panel y para
        # generate-corrections.
        assert terminal is not None
        assert terminal.deleted_at is None
        assert terminal.container_ids == []

        # Idempotent: a second pass is a clean no-op (plan already blocked; the
        # reaped session has no container_ids left ⇒ not re-listed).
        second = await _expire_review_runtimes(workers_settings)  # type: ignore[arg-type]
        assert second["expired"] == 0
        assert second["reaped"] == 0
        async with sessionmaker() as session:
            plan2 = await session.get(Plan, ids["plan"])
        assert plan2 is not None and plan2.status == "blocked"
    finally:
        await engine.dispose()
