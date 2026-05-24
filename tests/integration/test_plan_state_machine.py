"""Integration tests for the plan state machine + status endpoints
(Plan 03 task_03_16).

Covers:
  - The transition adjacency list at the function level (pure Python).
  - The router enforces it: a PUT that tries an illegal jump returns
    409 with the offending pair; a legal one updates the row.
  - approved_at / approved_by are stamped on the legal jump into
    ``approved``.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from api_server.chat.plan_state_machine import (
    PlanTransitionError,
    allowed_transitions,
    is_terminal,
    transition_plan_status,
)
from api_server.db.domain import Plan, PlanStatus
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


# ---------------------------------------------------------------------------
# Unit-style: the pure-Python state machine
# ---------------------------------------------------------------------------
def test_draft_can_advance_or_be_cancelled() -> None:
    assert allowed_transitions("draft") == frozenset({"pending_approval", "cancelled"})


def test_archived_is_terminal() -> None:
    assert is_terminal("archived") is True
    assert allowed_transitions("archived") == frozenset()


def test_completed_only_archives_next() -> None:
    assert allowed_transitions("completed") == frozenset({"archived"})


def test_transition_function_stamps_approved_metadata() -> None:
    """Going INTO `approved` records approved_at and approved_by;
    other transitions only touch `status`."""
    plan = Plan(
        tenant_id=uuid4(),
        project_id=uuid4(),
        title="t",
        status="pending_approval",
        specification={},
    )
    actor = uuid4()
    transition_plan_status(plan, PlanStatus.APPROVED.value, actor=actor)
    assert plan.status == "approved"
    assert plan.approved_at is not None
    assert plan.approved_by == actor


def test_transition_function_rejects_illegal_jump() -> None:
    plan = Plan(
        tenant_id=uuid4(),
        project_id=uuid4(),
        title="t",
        status="draft",
        specification={},
    )
    # draft -> completed is not in the table.
    with pytest.raises(PlanTransitionError) as info:
        transition_plan_status(plan, PlanStatus.COMPLETED.value)
    assert info.value.from_status == "draft"
    assert info.value.to_status == "completed"


def test_same_status_is_a_noop() -> None:
    plan = Plan(
        tenant_id=uuid4(),
        project_id=uuid4(),
        title="t",
        status="draft",
        specification={},
    )
    transition_plan_status(plan, "draft")
    assert plan.status == "draft"
    assert plan.approved_at is None


# ===========================================================================
# Router integration
# ===========================================================================
async def _seed(dsn: str) -> dict[str, UUID]:
    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE plans, conversations, projects, agents,"
            " user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug)" " VALUES ($1, $2, $3), ($4, $5, $6)",
            tenant_id,
            "Tenant SM",
            "tenant-sm",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-sm",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3)",
            user_id,
            "alice@sm.test",
            "argon2-placeholder",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, $4)",
            uuid4(),
            tenant_id,
            user_id,
            "tenant_admin",
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, $3)",
            project_id,
            tenant_id,
            "SM Project",
        )
    finally:
        await conn.close()
    return {"tenant_id": tenant_id, "user_id": user_id, "project_id": project_id}


@pytest.fixture()
def configured_app(
    alembic_config,
    app_database_url: str,
    admin_database_url: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
):
    command.upgrade(alembic_config, "head")
    from tests.integration.conftest import _flush_redis, _grant_app_user_existing_tables

    asyncio.run(_grant_app_user_existing_tables())
    asyncio.run(_flush_redis(test_redis_url))

    monkeypatch.setenv("API_SERVER_DATABASE_URL", app_database_url)
    monkeypatch.setenv("API_SERVER_ADMIN_DATABASE_URL", admin_database_url)
    monkeypatch.setenv("API_SERVER_REDIS_URL", test_redis_url)
    monkeypatch.setenv("API_SERVER_JWT_SECRET", "test-secret")

    from api_server.auth.deps import reset_redis_cache
    from api_server.config import get_settings
    from api_server.db.session import reset_engine_cache

    get_settings.cache_clear()
    reset_engine_cache()
    reset_redis_cache()

    from api_server.main import create_app

    app = create_app()
    try:
        yield app
    finally:
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()


async def _mint_token(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


@pytest.mark.asyncio
async def test_router_advances_a_legal_transition_chain(
    configured_app, migrations_pg_dsn: str
) -> None:
    """draft -> pending_approval -> approved is all-legal."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        create = await client.post(
            f"/projects/{seeded['project_id']}/plans",
            json={"title": "lifecycle plan"},
            headers=headers,
        )
        assert create.status_code == 201
        plan_id = create.json()["id"]
        assert create.json()["status"] == "draft"

        for next_status in ("pending_approval", "approved"):
            upd = await client.put(
                f"/plans/{plan_id}",
                json={"status": next_status},
                headers=headers,
            )
            assert upd.status_code == 200, upd.text
            assert upd.json()["status"] == next_status

        # `approved` stamps approved_at + approved_by.
        body = upd.json()
        assert body["approved_at"] is not None
        assert body["approved_by"] == str(seeded["user_id"])


@pytest.mark.asyncio
async def test_router_rejects_illegal_transition_with_409(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        create = await client.post(
            f"/projects/{seeded['project_id']}/plans",
            json={"title": "bad jump"},
            headers=headers,
        )
        plan_id = create.json()["id"]

        # draft -> completed is not allowed.
        bad = await client.put(
            f"/plans/{plan_id}",
            json={"status": "completed"},
            headers=headers,
        )
        assert bad.status_code == 409
        body = bad.json()
        assert body["detail"]["error"] == "invalid_plan_transition"
        assert body["detail"]["from"] == "draft"
        assert body["detail"]["to"] == "completed"

        # The plan stayed in draft despite the failed call.
        again = await client.get(f"/plans/{plan_id}", headers=headers)
        assert again.json()["status"] == "draft"
