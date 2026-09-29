"""`GET /agents` devuelve `last_activity_at` (task_ui_30, gap de la revisión).

Es la fecha del último run del agente, calculada en UNA consulta agrupada sobre
`executions` con `tenant_id` y una ventana en `created_at` (la tabla está
particionada por mes: sin ventana no se podan particiones). Aditivo: el resto de
la respuesta no cambia.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

from ._partitions import ensure_partition_for

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


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


async def _seed_run(dsn: str, *, tenant: UUID, task: UUID, agent: UUID, at: datetime) -> None:
    await ensure_partition_for(dsn, "executions", at)
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO executions (id, tenant_id, task_id, agent_id, status, steps_log,"
            " started_at, created_at) VALUES ($1, $2, $3, $4, 'done', $5::jsonb, $6, $6)",
            uuid4(),
            tenant,
            task,
            agent,
            json.dumps([]),
            at,
        )
    finally:
        await conn.close()


async def _seed(dsn: str) -> dict[str, UUID]:
    keys = ("ta", "tb", "ua", "pa", "pb", "task_a", "task_b", "busy", "idle", "stale")
    ids = {k: uuid4() for k in (*keys, "builtin", "other")}
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE executions, tasks, agents, projects, user_org_memberships,"
            " organizations, users RESTART IDENTITY CASCADE"
        )
        for tid, slug in ((ids["ta"], "a"), (ids["tb"], "b"), (_PLATFORM_TENANT_ID, "platform")):
            await conn.execute(
                "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)", tid, slug, slug
            )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, 'a@a.test', 'x')",
            ids["ua"],
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin')",
            uuid4(),
            ids["ta"],
            ids["ua"],
        )
        for pk, tk in (("pa", "ta"), ("pb", "tb")):
            await conn.execute(
                "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, 'P')",
                ids[pk],
                ids[tk],
            )
        for task, proj, ten in (("task_a", "pa", "ta"), ("task_b", "pb", "tb")):
            await conn.execute(
                "INSERT INTO tasks (id, tenant_id, project_id, plan_id, title, status,"
                " retry_count) VALUES ($1, $2, $3, NULL, 'T', 'done', 0)",
                ids[task],
                ids[ten],
                ids[proj],
            )
        for key, ten in (("busy", "ta"), ("idle", "ta"), ("stale", "ta"), ("other", "tb")):
            await conn.execute(
                "INSERT INTO agents (id, tenant_id, name, role, system_prompt, scope)"
                " VALUES ($1, $2, $3, 'r', 'p', 'global_tenant_template')",
                ids[key],
                ids[ten],
                key,
            )
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, name, role, system_prompt, scope)"
            " VALUES ($1, $2, 'builtin', 'r', 'p', 'global_builtin')",
            ids["builtin"],
            _PLATFORM_TENANT_ID,
        )
    finally:
        await conn.close()
    return ids


@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_list_agents_reports_last_activity_within_window_and_tenant(
    configured_app, migrations_pg_dsn: str
) -> None:
    ids = await _seed(migrations_pg_dsn)
    dsn = migrations_pg_dsn
    now = datetime.now(tz=UTC)
    older, newest = now - timedelta(days=5), now - timedelta(days=1)
    task_a, task_b = ids["task_a"], ids["task_b"]
    await _seed_run(dsn, tenant=ids["ta"], task=task_a, agent=ids["busy"], at=older)
    await _seed_run(dsn, tenant=ids["ta"], task=task_a, agent=ids["busy"], at=newest)
    # Fuera de la ventana de 90 días: no cuenta.
    stale_at = now - timedelta(days=200)
    await _seed_run(dsn, tenant=ids["ta"], task=task_a, agent=ids["stale"], at=stale_at)
    # Un built-in usado por el tenant A cuenta para A.
    await _seed_run(dsn, tenant=ids["ta"], task=task_a, agent=ids["builtin"], at=older)
    # Runs del OTRO tenant sobre el mismo built-in y sobre `idle`: no cuentan para A.
    await _seed_run(dsn, tenant=ids["tb"], task=task_b, agent=ids["builtin"], at=now)
    await _seed_run(dsn, tenant=ids["tb"], task=task_b, agent=ids["idle"], at=now)

    token = await _mint_token(ids["ua"], ids["ta"])
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get("/agents", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    by_name = {a["name"]: a for a in resp.json()}

    def when(name: str) -> datetime | None:
        raw = by_name[name]["last_activity_at"]
        return None if raw is None else datetime.fromisoformat(raw)

    busy = when("busy")
    assert busy is not None
    assert abs((busy - newest).total_seconds()) < 1
    assert when("idle") is None  # sólo tiene un run de otro tenant
    assert when("stale") is None  # fuera de la ventana
    builtin = when("builtin")
    assert builtin is not None
    assert abs((builtin - older).total_seconds()) < 1
