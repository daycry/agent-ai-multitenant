"""Integration tests for the planning-chat guardrails (Plan 11 task_11_22).

Exercises ``api_server.guardrails.planning`` end to end against a real
Postgres + the tenant-isolation RLS of migration 0052: the planning chat /
plan-generation flow runs the Phase A/B guardrails engine with three
planning-specific guardrails, persisting every triggered guardrail as a
tenant-scoped ``guardrail_events`` row.

What is verified (the task's acceptance criteria):
  - **off-topic planning input is flagged/warned** — a ``pre_llm`` chat
    turn that strays off the project/planning topic triggers
    ``topic_restriction`` with action ``warn``.
  - **an unsupported number in a draft triggers the hallucination check** —
    a ``post_llm`` answer asserting a cost / date with no citation triggers
    ``factuality_citations``.
  - **a structurally invalid draft blocks "Generar Plan" with actionable
    feedback** — the structural gate returns ``allowed=False`` plus the
    JSON-Schema validation errors, and persists a ``block`` event.
  - **a valid on-topic well-formed draft passes** — both the chat turn and
    the structural gate pass with no event written.
  - **tenant-scoped** (``@pytest.mark.cross_tenant``) — events written by
    one tenant's planning chat are visible only to that tenant.

The LLM is never touched: the guardrails are pure (heuristic / JSON-Schema)
so the planning "turn" text is supplied directly, matching the engine's
host-agnostic contract. Fixture wiring mirrors ``test_guardrail_events.py``.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from uuid6 import uuid7

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Seed: two tenants, each with an admin.
# ---------------------------------------------------------------------------
async def _seed(dsn: str) -> dict[str, UUID]:
    ids = {
        "tenant_a": uuid4(),
        "tenant_b": uuid4(),
        "admin_a": uuid4(),
        "admin_b": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE guardrail_events, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            ids["tenant_a"],
            "Tenant A",
            "tenant-a-planning-gr",
            ids["tenant_b"],
            "Tenant B",
            "tenant-b-planning-gr",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3), ($4, $5, $6)",
            ids["admin_a"],
            "admin-a@planning-gr.test",
            "h",
            ids["admin_b"],
            "admin-b@planning-gr.test",
            "h",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES"
            " ($1, $2, $3, 'tenant_admin'), ($4, $5, $6, 'tenant_admin')",
            uuid4(),
            ids["tenant_a"],
            ids["admin_a"],
            uuid4(),
            ids["tenant_b"],
            ids["admin_b"],
        )
    finally:
        await conn.close()
    return ids


async def _fetch_events(dsn: str, tenant_id: UUID) -> list[dict]:
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT guardrail_type, hook_point, severity, action, agent_label,"
            " detail, detail_payload::text AS payload"
            " FROM guardrail_events WHERE tenant_id = $1 ORDER BY created_at",
            tenant_id,
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


async def _count_events(dsn: str, tenant_id: UUID) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        return int(
            await conn.fetchval(
                "SELECT count(*) FROM guardrail_events WHERE tenant_id = $1", tenant_id
            )
        )
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Fixtures (identical wiring to test_guardrail_events.configured_app)
# ---------------------------------------------------------------------------
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


def _principal(user_id: UUID, tenant_id: UUID):
    from api_server.auth.deps import AuthPrincipal

    return AuthPrincipal(user_id=user_id, session_id=uuid7(), tenant_id=tenant_id)


# A valid, on-topic, well-formed draft plan specification (the structural
# gate's happy path).
_VALID_DRAFT: dict = {
    "summary": {"goal": "Ship the new planning feature", "scope": "backend + frontend"},
    "phases": [{"id": "p1", "title": "Design"}],
    "estimates": {"effort_days": 12},
    "metadata": {"template_version": "1.0"},
    "tasks": [
        {"id": "t1", "title": "Design the API", "depends_on": []},
        {"id": "t2", "title": "Implement the backend", "depends_on": ["t1"]},
        {"id": "t3", "title": "Build the frontend", "depends_on": ["t1"]},
    ],
}


# ===========================================================================
# 1. Off-topic planning input is flagged / warned (pre_llm topic adherence)
# ===========================================================================
@pytest.mark.asyncio
async def test_off_topic_planning_input_is_warned(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    tenant_a = seeded["tenant_a"]

    from api_server.auth.deps import open_tenant_session
    from api_server.guardrails.planning import run_planning_chat_guardrails
    from shared_guardrails.types import Action

    async with open_tenant_session(_principal(seeded["admin_a"], tenant_a)) as session:
        decision = await run_planning_chat_guardrails(
            session,
            hook="pre_llm",
            text="Can you give me a recipe for lasagna and tonight's football scores?",
            tenant_id=tenant_a,
        )
        await session.commit()

    assert decision.triggered, "off-topic input should trip topic_restriction"
    assert decision.action == Action.WARN
    fired = {o.type for o in decision.triggered_outcomes}
    assert "topic_restriction" in fired

    # The warning is observable as a tenant-scoped event.
    events = await _fetch_events(migrations_pg_dsn, tenant_a)
    assert len(events) == 1
    ev = events[0]
    assert ev["guardrail_type"] == "topic_restriction"
    assert ev["hook_point"] == "pre_llm"
    assert ev["action"] == "warn"
    assert ev["agent_label"] == "planning_chat"


@pytest.mark.asyncio
async def test_on_topic_planning_input_passes(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    tenant_a = seeded["tenant_a"]

    from api_server.auth.deps import open_tenant_session
    from api_server.guardrails.planning import run_planning_chat_guardrails

    async with open_tenant_session(_principal(seeded["admin_a"], tenant_a)) as session:
        decision = await run_planning_chat_guardrails(
            session,
            hook="pre_llm",
            text="Let's plan the backend tasks for the new feature and estimate the sprint.",
            tenant_id=tenant_a,
        )
        await session.commit()

    assert not decision.triggered
    # A clean turn writes no event.
    assert await _count_events(migrations_pg_dsn, tenant_a) == 0


# ===========================================================================
# 2. An unsupported number triggers the hallucination check (post_llm)
# ===========================================================================
@pytest.mark.asyncio
async def test_unsupported_number_triggers_hallucination_check(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    tenant_a = seeded["tenant_a"]

    from api_server.auth.deps import open_tenant_session
    from api_server.guardrails.planning import run_planning_chat_guardrails

    # An answer asserting a cost + a delivery year with NO supporting citation
    # — exactly the "estimates/costs/dates asserted in the plan" the check
    # must flag. It stays on-topic so only factuality fires.
    answer = (
        "The project will cost 45000 euros and the backend tasks will be done by 2026. "
        "We will deliver the planning roadmap for the feature."
    )
    async with open_tenant_session(_principal(seeded["admin_a"], tenant_a)) as session:
        decision = await run_planning_chat_guardrails(
            session,
            hook="post_llm",
            text=answer,
            tenant_id=tenant_a,
        )
        await session.commit()

    assert decision.triggered
    fired = {o.type for o in decision.triggered_outcomes}
    assert (
        "factuality_citations" in fired
    ), "an unsupported number must trip the hallucination check"

    events = await _fetch_events(migrations_pg_dsn, tenant_a)
    types = {e["guardrail_type"] for e in events}
    assert "factuality_citations" in types
    # The persisted detail must NOT leak the raw numbers verbatim beyond the
    # masked summary — the recorder dropped the unsafe payload keys.
    for ev in events:
        assert "unsupported" not in (ev["payload"] or "")


@pytest.mark.asyncio
async def test_well_supported_answer_passes(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    tenant_a = seeded["tenant_a"]

    from api_server.auth.deps import open_tenant_session
    from api_server.guardrails.planning import run_planning_chat_guardrails

    answer = (
        "We will deliver the planning roadmap, backend tasks and frontend tasks for the "
        "project. No hard numbers are committed yet; estimates follow in the next phase."
    )
    async with open_tenant_session(_principal(seeded["admin_a"], tenant_a)) as session:
        decision = await run_planning_chat_guardrails(
            session,
            hook="post_llm",
            text=answer,
            tenant_id=tenant_a,
        )
        await session.commit()

    assert not decision.triggered
    assert await _count_events(migrations_pg_dsn, tenant_a) == 0


# ===========================================================================
# 3. A structurally invalid draft BLOCKS "Generar Plan" with feedback
# ===========================================================================
@pytest.mark.asyncio
async def test_invalid_draft_blocks_generate_plan_with_feedback(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    tenant_a = seeded["tenant_a"]

    from api_server.auth.deps import open_tenant_session
    from api_server.guardrails.planning import gate_generate_plan

    # A task missing its required `title` — structurally invalid.
    invalid_draft = {
        "summary": {"goal": "x"},
        "tasks": [{"id": "t1"}],
    }
    async with open_tenant_session(_principal(seeded["admin_a"], tenant_a)) as session:
        result = await gate_generate_plan(session, draft=invalid_draft, tenant_id=tenant_a)
        await session.commit()

    assert result.allowed is False, "an invalid draft must block plan generation"
    assert result.feedback, "the block must carry actionable feedback"
    # The feedback names the offending field + its JSON path.
    joined = " ".join(result.feedback)
    assert "title" in joined
    assert "tasks[0]" in joined

    events = await _fetch_events(migrations_pg_dsn, tenant_a)
    assert len(events) == 1
    ev = events[0]
    assert ev["guardrail_type"] == "output_structure"
    assert ev["action"] == "block"
    assert ev["agent_label"] == "plan_generation"


@pytest.mark.asyncio
async def test_empty_draft_blocks_generate_plan(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    tenant_a = seeded["tenant_a"]

    from api_server.auth.deps import open_tenant_session
    from api_server.guardrails.planning import gate_generate_plan

    # A freshly-created empty draft ({} ) has neither summary nor tasks.
    async with open_tenant_session(_principal(seeded["admin_a"], tenant_a)) as session:
        result = await gate_generate_plan(session, draft={}, tenant_id=tenant_a)
        await session.commit()

    assert result.allowed is False
    assert result.feedback
    assert await _count_events(migrations_pg_dsn, tenant_a) == 1


# ===========================================================================
# 4. A valid, on-topic, well-formed draft passes the gate
# ===========================================================================
@pytest.mark.asyncio
async def test_valid_draft_passes_generate_plan(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    tenant_a = seeded["tenant_a"]

    from api_server.auth.deps import open_tenant_session
    from api_server.guardrails.planning import gate_generate_plan

    async with open_tenant_session(_principal(seeded["admin_a"], tenant_a)) as session:
        result = await gate_generate_plan(session, draft=_VALID_DRAFT, tenant_id=tenant_a)
        await session.commit()

    assert result.allowed is True
    assert result.feedback == ()
    # A valid draft writes no guardrail event.
    assert await _count_events(migrations_pg_dsn, tenant_a) == 0


# ===========================================================================
# 5. Tenant isolation — planning-chat events are tenant-scoped
# ===========================================================================
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_planning_guardrail_events_are_tenant_isolated(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    tenant_a = seeded["tenant_a"]
    tenant_b = seeded["tenant_b"]

    from api_server.auth.deps import open_tenant_session
    from api_server.guardrails.planning import (
        gate_generate_plan,
        run_planning_chat_guardrails,
    )

    # Tenant A: an off-topic turn (1 event) + an invalid draft (1 event) = 2.
    async with open_tenant_session(_principal(seeded["admin_a"], tenant_a)) as session_a:
        await run_planning_chat_guardrails(
            session_a, hook="pre_llm", text="Tell me a joke about cats.", tenant_id=tenant_a
        )
        await gate_generate_plan(session_a, draft={"summary": {}, "tasks": []}, tenant_id=tenant_a)
        await session_a.commit()

    # Tenant B: a single unsupported-number answer = 1 event.
    async with open_tenant_session(_principal(seeded["admin_b"], tenant_b)) as session_b:
        await run_planning_chat_guardrails(
            session_b,
            hook="post_llm",
            text="The plan costs 99000 euros and ships in 2027 for the project.",
            tenant_id=tenant_b,
        )
        await session_b.commit()

    events_a = await _fetch_events(migrations_pg_dsn, tenant_a)
    events_b = await _fetch_events(migrations_pg_dsn, tenant_b)
    assert len(events_a) == 2
    assert len(events_b) == 1

    # A tenant-A RLS session sees ONLY tenant A's events (and vice versa).
    from api_server.db.guardrail_event import GuardrailEvent
    from sqlalchemy import func, select

    async with open_tenant_session(_principal(seeded["admin_a"], tenant_a)) as session_a:
        count_a = await session_a.scalar(select(func.count()).select_from(GuardrailEvent))
    async with open_tenant_session(_principal(seeded["admin_b"], tenant_b)) as session_b:
        count_b = await session_b.scalar(select(func.count()).select_from(GuardrailEvent))

    assert count_a == 2, "tenant A must see only its own 2 events"
    assert count_b == 1, "tenant B must see only its own 1 event"
