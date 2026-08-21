"""Integration tests for POST /projects/{id}/tasks/{id}/generate-acceptance-criteria.

The endpoint proposes acceptance criteria for one task via the project's chat LLM
(ADR 0021). It NEVER persists — it returns the proposal so the operator can review
(and, when the task already had criteria, confirm against a comparison) before
saving with the normal PUT. Covers: happy path (proposal returned, DB untouched),
409 when no provider is configured, and 404 cross-tenant.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


async def _seed(dsn: str) -> dict[str, UUID]:
    """Two tenants: A owns a project + task; B is a separate tenant (cross-tenant)."""
    a_tenant, a_user, a_project, a_task = uuid4(), uuid4(), uuid4(), uuid4()
    b_tenant, b_user = uuid4(), uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE task_dependencies, tasks, plan_comments, plans, conversations,"
            " projects, agents, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1,$2,$3), ($4,$5,$6), ($7,$8,$9)",
            a_tenant,
            "Tenant A",
            "tenant-a-crit",
            b_tenant,
            "Tenant B",
            "tenant-b-crit",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-crit",
        )
        await conn.executemany(
            "INSERT INTO users (id, email, password_hash) VALUES ($1,$2,$3)",
            [(a_user, "a@crit.test", "h"), (b_user, "b@crit.test", "h")],
        )
        await conn.executemany(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES ($1,$2,$3,$4)",
            [
                (uuid4(), a_tenant, a_user, "tenant_admin"),
                (uuid4(), b_tenant, b_user, "tenant_admin"),
            ],
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1,$2,$3)",
            a_project,
            a_tenant,
            "Crit Project",
        )
        # Task starts with NO acceptance criteria (server_default '[]').
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, title, description)"
            " VALUES ($1,$2,$3,$4,$5)",
            a_task,
            a_tenant,
            a_project,
            "Auditar dependencias",
            "Revisar composer.lock y vulnerabilidades",
        )
    finally:
        await conn.close()
    return {
        "a_tenant": a_tenant,
        "a_user": a_user,
        "a_project": a_project,
        "a_task": a_task,
        "b_user": b_user,
        "b_tenant": b_tenant,
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


class _StubProvider:
    async def aclose(self) -> None:  # pragma: no cover - trivial
        return None


@pytest.mark.asyncio
async def test_generate_returns_proposal_without_persisting(
    configured_app, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["a_user"], seeded["a_tenant"])
    headers = {"Authorization": f"Bearer {token}"}

    closed: list[bool] = []

    class _P(_StubProvider):
        async def aclose(self) -> None:
            closed.append(True)

    async def _fake_resolve(session: Any, effective: Any, vault: Any):
        return _P(), "ollama", "m"

    async def _fake_generate(provider: Any, **kwargs: Any) -> list[str]:
        return ["composer audit sin vulnerabilidades", "la suite PHPUnit pasa"]

    monkeypatch.setattr("api_server.routers.tasks._resolve_chat_provider", _fake_resolve)
    monkeypatch.setattr(
        "api_server.routers.tasks.generate_task_acceptance_criteria", _fake_generate
    )

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/projects/{seeded['a_project']}/tasks/{seeded['a_task']}"
            "/generate-acceptance-criteria",
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["acceptance_criteria"] == [
            "composer audit sin vulnerabilidades",
            "la suite PHPUnit pasa",
        ]
        assert closed == [True], "provider was not closed"

        # The proposal is NOT persisted — the task still has no criteria.
        got = await client.get(
            f"/projects/{seeded['a_project']}/tasks/{seeded['a_task']}", headers=headers
        )
        assert got.status_code == 200, got.text
        assert got.json()["acceptance_criteria"] == []


@pytest.mark.asyncio
async def test_generate_returns_409_when_no_provider(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["a_user"], seeded["a_tenant"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/projects/{seeded['a_project']}/tasks/{seeded['a_task']}"
            "/generate-acceptance-criteria",
            headers=headers,
        )
        assert resp.status_code == 409, resp.text


async def _seed_plan_with_sibling(dsn: str) -> dict[str, UUID]:
    """One plan with TWO tasks: the target + a sibling that already fixed a
    response contract in its criteria. The endpoint must feed that sibling's
    criteria to the generator (fix for the ResponseTrait-vs-contract block)."""
    t, u, p, plan, main, sib = uuid4(), uuid4(), uuid4(), uuid4(), uuid4(), uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE task_dependencies, tasks, plan_comments, plans, conversations,"
            " projects, agents, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1,$2,$3), ($4,$5,$6)",
            t,
            "Tenant S",
            "tenant-s-crit",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-s-crit",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1,$2,$3)", u, "s@crit.test", "h"
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES ($1,$2,$3,$4)",
            uuid4(),
            t,
            u,
            "tenant_admin",
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1,$2,$3)", p, t, "Sib Project"
        )
        await conn.execute(
            "INSERT INTO plans (id, tenant_id, project_id, title) VALUES ($1,$2,$3,$4)",
            plan,
            t,
            p,
            "Plan CI4",
        )
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, plan_id, title) VALUES ($1,$2,$3,$4,$5)",
            main,
            t,
            p,
            plan,
            "Implementar los controladores",
        )
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, plan_id, title, acceptance_criteria)"
            " VALUES ($1,$2,$3,$4,$5,$6::jsonb)",
            sib,
            t,
            p,
            plan,
            "Definir contrato de respuesta JSON",
            json.dumps(["el cuerpo de éxito usa {message, meta}"]),
        )
    finally:
        await conn.close()
    return {"tenant": t, "user": u, "project": p, "main": main}


@pytest.mark.asyncio
async def test_generate_threads_sibling_criteria_into_the_generator(
    configured_app, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await _seed_plan_with_sibling(migrations_pg_dsn)
    token = await _mint_token(seeded["user"], seeded["tenant"])
    headers = {"Authorization": f"Bearer {token}"}

    captured: dict[str, Any] = {}

    async def _fake_resolve(session: Any, effective: Any, vault: Any):
        return _StubProvider(), "ollama", "m"

    async def _fake_generate(provider: Any, **kwargs: Any) -> list[str]:
        captured.update(kwargs)
        return ["propuesto"]

    monkeypatch.setattr("api_server.routers.tasks._resolve_chat_provider", _fake_resolve)
    monkeypatch.setattr(
        "api_server.routers.tasks.generate_task_acceptance_criteria", _fake_generate
    )

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/projects/{seeded['project']}/tasks/{seeded['main']}/generate-acceptance-criteria",
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        # The sibling contract task's title + criterion reached the generator.
        sib_ctx = captured.get("sibling_context", "")
        assert "Definir contrato de respuesta JSON" in sib_ctx
        assert "el cuerpo de éxito usa {message, meta}" in sib_ctx


@pytest.mark.asyncio
async def test_generate_cross_tenant_returns_404(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["b_user"], seeded["b_tenant"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/projects/{seeded['a_project']}/tasks/{seeded['a_task']}"
            "/generate-acceptance-criteria",
            headers=headers,
        )
        assert resp.status_code == 404, resp.text
