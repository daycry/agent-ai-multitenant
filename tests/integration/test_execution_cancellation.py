"""POST /executions/{id}/cancel — cooperative cancellation control plane
(auditoría zona 'ejecuciones', hallazgo high/gap; diseño docs/roadmap/fixes-pesados-auditoria.md).

Covers the API-server slice: the endpoint flags a running execution, revokes the
Celery job (mocked), is idempotent, 409s a terminal execution and 404s cross-tenant.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


async def _seed(dsn: str) -> dict[str, UUID]:
    """Seed two tenants (A with a running execution, B for the cross-tenant test)."""
    a = {"tenant": uuid4(), "user": uuid4(), "project": uuid4(), "task": uuid4(), "exec": uuid4()}
    b_tenant, b_user = uuid4(), uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE executions, tasks, plans, conversations, projects, agents,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1,$2,$3),($4,$5,$6),($7,$8,$9)",
            a["tenant"],
            "Tenant A",
            "tenant-a-cancel",
            b_tenant,
            "Tenant B",
            "tenant-b-cancel",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-cancel",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1,$2,$3),($4,$5,$6)",
            a["user"],
            "a@cancel.test",
            "h",
            b_user,
            "b@cancel.test",
            "h",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1,$2,$3,$4),($5,$6,$7,$8)",
            uuid4(),
            a["tenant"],
            a["user"],
            "tenant_admin",
            uuid4(),
            b_tenant,
            b_user,
            "tenant_admin",
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1,$2,$3)",
            a["project"],
            a["tenant"],
            "Cancel Project",
        )
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, title, status) VALUES ($1,$2,$3,$4,$5)",
            a["task"],
            a["tenant"],
            a["project"],
            "Run me",
            "in_progress",
        )
        await conn.execute(
            "INSERT INTO executions (id, tenant_id, task_id, status, celery_task_id,"
            " started_at) VALUES ($1,$2,$3,$4,$5, now())",
            a["exec"],
            a["tenant"],
            a["task"],
            "running",
            "celery-job-123",
        )
    finally:
        await conn.close()
    return {**a, "b_tenant": b_tenant, "b_user": b_user}


@pytest.fixture()
def configured_app(
    alembic_config, app_database_url, admin_database_url, test_redis_url, monkeypatch
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
    await SessionStore(get_redis()).create(
        sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600
    )
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


async def _flag(dsn: str, execution_id: UUID) -> object:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval(
            "SELECT cancel_requested_at FROM executions WHERE id = $1", execution_id
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_cancel_running_execution_flags_and_revokes(
    configured_app, migrations_pg_dsn: str, monkeypatch
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user"], seeded["tenant"])
    headers = {"Authorization": f"Bearer {token}"}

    revoked: list[str] = []

    async def _fake_revoke(job_id: str) -> bool:
        revoked.append(job_id)
        return True

    monkeypatch.setattr("api_server.routers.executions.revoke_execution_job", _fake_revoke)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/executions/{seeded['exec']}/cancel", headers=headers)
        assert resp.status_code == 202, resp.text
        assert resp.json()["status"] == "cancel_requested"

    assert await _flag(migrations_pg_dsn, seeded["exec"]) is not None  # flag stamped
    assert revoked == ["celery-job-123"]  # job revoked with the stored id


@pytest.mark.asyncio
async def test_cancel_is_idempotent(configured_app, migrations_pg_dsn: str, monkeypatch) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user"], seeded["tenant"])
    headers = {"Authorization": f"Bearer {token}"}
    monkeypatch.setattr(
        "api_server.routers.executions.revoke_execution_job",
        lambda job_id: asyncio.sleep(0, result=True),
    )

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        first = await client.post(f"/executions/{seeded['exec']}/cancel", headers=headers)
        flag1 = await _flag(migrations_pg_dsn, seeded["exec"])
        second = await client.post(f"/executions/{seeded['exec']}/cancel", headers=headers)
        flag2 = await _flag(migrations_pg_dsn, seeded["exec"])

    assert first.status_code == 202 and second.status_code == 202
    assert flag1 == flag2  # the second cancel does NOT bump the timestamp


@pytest.mark.asyncio
async def test_cancel_terminal_execution_is_409(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "UPDATE executions SET status='done', completed_at=now() WHERE id=$1", seeded["exec"]
        )
    finally:
        await conn.close()
    token = await _mint_token(seeded["user"], seeded["tenant"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/executions/{seeded['exec']}/cancel", headers=headers)

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["status"] == "done"


@pytest.mark.asyncio
async def test_cancel_cross_tenant_is_404(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["b_user"], seeded["b_tenant"])  # tenant B
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/executions/{seeded['exec']}/cancel", headers=headers)

    assert resp.status_code == 404, resp.text
    assert await _flag(migrations_pg_dsn, seeded["exec"]) is None  # flag NOT set cross-tenant
