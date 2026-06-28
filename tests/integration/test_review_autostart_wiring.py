"""Integration tests — review-runtime autostart wiring (C8 F39).

Drives the orchestrator's ``TaskDispatcher._build_review_autostart_request``
against the real Postgres:

  * a plan that just reached ``pending_human_validation`` yields a
    ``compose_review_runtime`` payload with the project's resolved ``main_image``,
    the repo/slug identifiers, and the human checklist parsed from the plan spec;
  * IDEMPOTENT: once an active (``running``) review session exists for the plan, the
    builder returns ``None`` (never a second runtime) — the property the reconciler
    relies on when it re-drives the same plan transition.

NOT RUN by the implementing agent (needs a live Postgres at TEST_PG_*).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from api_server.db.domain import Plan
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


class _FakeCelery:
    def send_task(self, *_a: Any, **_k: Any) -> None:  # pragma: no cover - unused here
        raise AssertionError("the builder must not enqueue")


async def _seed(dsn: str, *, with_session: bool = False) -> dict[str, UUID]:
    ids = {"tenant": uuid4(), "project": uuid4(), "plan": uuid4(), "session": uuid4()}
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE review_sessions, plans, projects, organizations" " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'Org', 'org-c8-f39')",
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, slug, status, is_template,"
            " repository_config)"
            " VALUES ($1, $2, 'Backend', 'backend', 'active', false, $3::jsonb)",
            ids["project"],
            ids["tenant"],
            json.dumps({"review_image": "backend:plan-1"}),
        )
        spec = json.dumps({"tests_humans": [{"id": "h1", "description": "login works"}]})
        await conn.execute(
            "INSERT INTO plans (id, tenant_id, project_id, title, slug, status, specification)"
            " VALUES ($1, $2, $3, 'My plan', 'my-plan', 'pending_human_validation', $4::jsonb)",
            ids["plan"],
            ids["tenant"],
            ids["project"],
            spec,
        )
        if with_session:
            now = datetime.now(UTC)
            await conn.execute(
                "INSERT INTO review_sessions"
                " (id, tenant_id, plan_id, spec, status, container_ids, expires_at)"
                " VALUES ($1, $2, $3, '{}'::jsonb, 'running', '[]'::jsonb, $4)",
                ids["session"],
                ids["tenant"],
                ids["plan"],
                now + timedelta(hours=48),
            )
        return ids
    finally:
        await conn.close()


def _dispatcher(dsn: str):  # type: ignore[no-untyped-def]
    from orchestrator.dispatch import TaskDispatcher

    engine = create_async_engine(dsn)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    dispatcher = TaskDispatcher(
        sessionmaker=sm,
        celery_app=_FakeCelery(),  # type: ignore[arg-type]
        settings=None,  # type: ignore[arg-type]
    )
    return dispatcher, engine, sm


@pytest.mark.asyncio
async def test_builder_returns_payload_for_fresh_plan(
    _migrated: None, migrations_pg_dsn: str
) -> None:
    async_dsn = migrations_pg_dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    ids = await _seed(migrations_pg_dsn)
    dispatcher, engine, sm = _dispatcher(async_dsn)
    try:
        async with sm() as session:
            plan = await session.get(Plan, ids["plan"])
            assert plan is not None
            payload = await dispatcher._build_review_autostart_request(
                session, plan=plan, tenant_id=ids["tenant"]
            )
    finally:
        await engine.dispose()

    assert payload is not None
    assert payload["plan_id"] == str(ids["plan"])
    assert payload["main_image"] == "backend:plan-1"  # repository_config.review_image
    assert payload["repo_name"] == "backend"
    assert payload["tenant_slug"] == "org-c8-f39"
    assert payload["project_slug"] == "backend"
    assert payload["plan_slug"] == "my-plan"
    assert payload["human_checklist"] == [{"id": "h1", "description": "login works"}]


@pytest.mark.asyncio
async def test_builder_is_idempotent_with_active_session(
    _migrated: None, migrations_pg_dsn: str
) -> None:
    async_dsn = migrations_pg_dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    ids = await _seed(migrations_pg_dsn, with_session=True)
    dispatcher, engine, sm = _dispatcher(async_dsn)
    try:
        async with sm() as session:
            plan = await session.get(Plan, ids["plan"])
            assert plan is not None
            payload = await dispatcher._build_review_autostart_request(
                session, plan=plan, tenant_id=ids["tenant"]
            )
    finally:
        await engine.dispose()

    # An active session already exists ⇒ no second runtime.
    assert payload is None
