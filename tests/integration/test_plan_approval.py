"""Integration tests for the plan approval flow + double signature
(Plan 03 task_03_25).

Drives the single and double firma paths end-to-end through
`POST /plans/{id}/approve`. The double-firma path is gated by the
platform setting `plan_approval_double_signature_threshold`; we set
it directly on the table to keep these tests self-contained (the
admin endpoint for editing platform settings is wired in Plan 00).
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from api_server.chat.cost import compute_ai_cost
from api_server.chat.plan_state_machine import (
    SameSignerError,
    transition_plan_status,
)
from api_server.db.domain import Plan, PlanStatus
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


# ---------------------------------------------------------------------------
# Pure-state-machine assertions (no DB)
# ---------------------------------------------------------------------------
def test_double_firma_first_signature_stamps_first_approved_metadata() -> None:
    plan = Plan(
        tenant_id=uuid4(),
        project_id=uuid4(),
        title="t",
        status="pending_approval",
        specification={},
    )
    actor = uuid4()
    transition_plan_status(plan, PlanStatus.PENDING_SECOND_APPROVAL.value, actor=actor)
    assert plan.status == "pending_second_approval"
    assert plan.first_approved_by == actor
    assert plan.first_approved_at is not None
    # The final approved_* slots remain empty until the second signer.
    assert plan.approved_by is None
    assert plan.approved_at is None


def test_double_firma_second_signature_rejects_same_signer() -> None:
    signer = uuid4()
    plan = Plan(
        tenant_id=uuid4(),
        project_id=uuid4(),
        title="t",
        status="pending_approval",
        specification={},
    )
    transition_plan_status(plan, PlanStatus.PENDING_SECOND_APPROVAL.value, actor=signer)
    with pytest.raises(SameSignerError) as info:
        transition_plan_status(plan, PlanStatus.APPROVED.value, actor=signer)
    assert info.value.signer == signer


def test_double_firma_second_signature_distinct_signer_approves() -> None:
    first = uuid4()
    second = uuid4()
    plan = Plan(
        tenant_id=uuid4(),
        project_id=uuid4(),
        title="t",
        status="pending_approval",
        specification={},
    )
    transition_plan_status(plan, PlanStatus.PENDING_SECOND_APPROVAL.value, actor=first)
    transition_plan_status(plan, PlanStatus.APPROVED.value, actor=second)
    assert plan.status == "approved"
    assert plan.first_approved_by == first
    assert plan.approved_by == second


# ===========================================================================
# Router integration
# ===========================================================================
async def _seed(dsn: str, *, threshold: str = "0") -> dict[str, UUID]:
    """Seed a tenant + two users (approvers) + a project."""
    tenant_id = uuid4()
    alice_id = uuid4()
    bob_id = uuid4()
    project_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE plan_comments, plans, conversations, projects, agents,"
            " user_org_memberships, organizations, users, platform_settings"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES" " ($1, $2, $3), ($4, $5, $6)",
            tenant_id,
            "Tenant Approval",
            "tenant-approval",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-approval",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES" " ($1, $2, $3), ($4, $5, $6)",
            alice_id,
            "alice@approve.test",
            "h",
            bob_id,
            "bob@approve.test",
            "h",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, $4), ($5, $6, $7, $8)",
            uuid4(),
            tenant_id,
            alice_id,
            "tenant_admin",
            uuid4(),
            tenant_id,
            bob_id,
            "tenant_admin",
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, $3)",
            project_id,
            tenant_id,
            "Approval Project",
        )
        # platform_settings stores the JSON-encoded threshold value.
        await conn.execute(
            "INSERT INTO platform_settings (key, value) VALUES ($1, $2::jsonb)",
            "plan_approval_double_signature_threshold",
            f'"{threshold}"',
        )
    finally:
        await conn.close()
    return {
        "tenant_id": tenant_id,
        "alice_id": alice_id,
        "bob_id": bob_id,
        "project_id": project_id,
    }


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


# A small, cheap plan (well under any reasonable threshold).
_CHEAP_SPEC = {
    "tasks": [{"id": "t1", "title": "Tiny", "complexity": "xs"}],
}
# An expensive plan (many big tasks on Opus) — easily above 1 USD.
_EXPENSIVE_SPEC = {
    "metadata": {"default_model_id": "claude-opus-4-7"},
    "tasks": [
        {"id": "t1", "title": "A", "complexity": "xl", "model": "claude-opus-4-7"},
        {"id": "t2", "title": "B", "complexity": "xl", "model": "claude-opus-4-7"},
        {"id": "t3", "title": "C", "complexity": "xl", "model": "claude-opus-4-7"},
    ],
}


async def _create_and_open(client: AsyncClient, seeded: dict, spec: dict, headers: dict) -> str:
    """Create a plan, move it to pending_approval, return its id."""
    create = await client.post(
        f"/projects/{seeded['project_id']}/plans",
        json={"title": "Plan", "specification": spec},
        headers=headers,
    )
    assert create.status_code == 201, create.text
    plan_id = create.json()["id"]
    move = await client.put(
        f"/plans/{plan_id}",
        json={"status": "pending_approval"},
        headers=headers,
    )
    assert move.status_code == 200, move.text
    return plan_id


@pytest.mark.asyncio
async def test_draft_plan_cannot_sync_to_kanban(configured_app, migrations_pg_dsn: str) -> None:
    """Un borrador NO debe materializar tareas: sync-to-kanban en draft -> 409."""
    seeded = await _seed(migrations_pg_dsn, threshold="1000")
    token = await _mint_token(seeded["alice_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        create = await client.post(
            f"/projects/{seeded['project_id']}/plans",
            json={"title": "Plan", "specification": _CHEAP_SPEC},
            headers=headers,
        )
        plan_id = create.json()["id"]  # status defaults to draft

        resp = await client.post(
            f"/plans/{plan_id}/sync-to-kanban", json={"scope": "total"}, headers=headers
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["error"] == "plan_not_approved"


@pytest.mark.asyncio
async def test_approved_plan_can_sync_and_start_execution_creates_tasks(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Aprobado -> sync permitido; start-execution lo pone in_progress y crea las
    tareas en el Kanban (revisa si están y si no las crea)."""
    seeded = await _seed(migrations_pg_dsn, threshold="1000")
    token = await _mint_token(seeded["alice_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        plan_id = await _create_and_open(client, seeded, _CHEAP_SPEC, headers)
        approve = await client.post(f"/plans/{plan_id}/approve", headers=headers)
        assert approve.json()["status"] == "approved"

        # start-execution: approved -> in_progress AND materialises the tasks.
        started = await client.post(f"/plans/{plan_id}/start-execution", headers=headers)
        assert started.status_code == 200, started.text
        assert started.json()["status"] == "in_progress"

        # The single spec task is now in the Kanban (idempotent re-sync returns it as skipped).
        resync = await client.post(
            f"/plans/{plan_id}/sync-to-kanban", json={"scope": "total"}, headers=headers
        )
        assert resync.status_code == 200, resync.text
        assert resync.json()["skipped_task_ids"]  # already materialised by start-execution


@pytest.mark.asyncio
async def test_start_execution_on_unapproved_plan_returns_409(
    configured_app, migrations_pg_dsn: str
) -> None:
    """start-execution solo es legal desde approved: en draft -> 409."""
    seeded = await _seed(migrations_pg_dsn, threshold="1000")
    token = await _mint_token(seeded["alice_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        create = await client.post(
            f"/projects/{seeded['project_id']}/plans",
            json={"title": "Plan", "specification": _CHEAP_SPEC},
            headers=headers,
        )
        plan_id = create.json()["id"]  # draft

        resp = await client.post(f"/plans/{plan_id}/start-execution", headers=headers)
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["error"] == "invalid_plan_transition"


@pytest.mark.asyncio
async def test_cheap_plan_below_threshold_takes_single_signature(
    configured_app, migrations_pg_dsn: str
) -> None:
    """With threshold = 1000 USD, a cheap plan approves on one click."""
    seeded = await _seed(migrations_pg_dsn, threshold="1000")
    token = await _mint_token(seeded["alice_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        plan_id = await _create_and_open(client, seeded, _CHEAP_SPEC, headers)

        resp = await client.post(f"/plans/{plan_id}/approve", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "approved"
        assert body["approved_by"] == str(seeded["alice_id"])


@pytest.mark.asyncio
async def test_expensive_plan_above_threshold_needs_two_signatures(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Threshold = 0.01 USD ensures every expensive plan triggers the
    double-firma path. First signer parks it in pending_second_approval;
    a different signer closes it."""
    seeded = await _seed(migrations_pg_dsn, threshold="0.01")

    # Sanity: our fixture spec actually exceeds the threshold.
    ai = compute_ai_cost(_EXPENSIVE_SPEC, default_model_id="claude-opus-4-7")
    assert ai.cost_max > Decimal("0.01")

    alice_token = await _mint_token(seeded["alice_id"], seeded["tenant_id"])
    bob_token = await _mint_token(seeded["bob_id"], seeded["tenant_id"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        plan_id = await _create_and_open(
            client,
            seeded,
            _EXPENSIVE_SPEC,
            {"Authorization": f"Bearer {alice_token}"},
        )

        # Alice signs first → pending_second_approval.
        first = await client.post(
            f"/plans/{plan_id}/approve",
            headers={"Authorization": f"Bearer {alice_token}"},
        )
        assert first.status_code == 200, first.text
        assert first.json()["status"] == "pending_second_approval"
        assert first.json()["first_approved_by"] == str(seeded["alice_id"])

        # Alice trying again is rejected as same signer.
        bad = await client.post(
            f"/plans/{plan_id}/approve",
            headers={"Authorization": f"Bearer {alice_token}"},
        )
        assert bad.status_code == 409
        assert bad.json()["detail"]["error"] == "same_signer"

        # Bob (a different user) closes the plan.
        second = await client.post(
            f"/plans/{plan_id}/approve",
            headers={"Authorization": f"Bearer {bob_token}"},
        )
        assert second.status_code == 200, second.text
        body = second.json()
        assert body["status"] == "approved"
        assert body["approved_by"] == str(seeded["bob_id"])
        assert body["first_approved_by"] == str(seeded["alice_id"])


@pytest.mark.asyncio
async def test_approve_from_wrong_state_returns_409(configured_app, migrations_pg_dsn: str) -> None:
    """POST /approve only makes sense from pending_approval / pending_second_approval."""
    seeded = await _seed(migrations_pg_dsn, threshold="0")
    token = await _mint_token(seeded["alice_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        # Create a plan but leave it in `draft`.
        create = await client.post(
            f"/projects/{seeded['project_id']}/plans",
            json={"title": "Draft plan"},
            headers=headers,
        )
        plan_id = create.json()["id"]

        resp = await client.post(f"/plans/{plan_id}/approve", headers=headers)
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["error"] == "invalid_plan_transition"
        assert detail["from"] == "draft"
