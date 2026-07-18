"""PROY2-09/10/12 (auditoría proyecto 2026-07-17): robustez del sync y del
planning-chat.

- Dos syncs CONCURRENTES del mismo plan materializaban tareas duplicadas
  (read-then-insert sin lock) → advisory-lock transaccional por plan.
- Un re-sync con scope más ancho perdía para siempre las aristas DAG de las
  tareas preexistentes (solo cableaba deps de las recién creadas).
- El spec del planning-chat esquivaba Pydantic: un id duplicado del LLM
  reventaba con 500 (ValueError de validate_dag) en vez de 422.
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

_SPEC = {
    "tasks": [
        {"id": "t1", "title": "Base", "depends_on": []},
        {"id": "t2", "title": "Encima", "depends_on": ["t1"]},
    ]
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


async def _token(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    await SessionStore(get_redis()).create(
        sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600
    )
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


async def _seed(dsn: str, *, spec: dict | None = None) -> dict[str, UUID]:
    import json

    tenant, admin, proj, plan = uuid4(), uuid4(), uuid4(), uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE task_dependencies, tasks, plans, messages, conversations, agents,"
            " projects, user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T', $2)",
            tenant,
            f"sp-{tenant.hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, 'a@sp.test', 'h')", admin
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin')",
            uuid4(),
            tenant,
            admin,
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status) VALUES ($1, $2, 'P', 'active')",
            proj,
            tenant,
        )
        await conn.execute(
            "INSERT INTO plans (id, tenant_id, project_id, title, status, specification)"
            " VALUES ($1, $2, $3, 'PL', 'approved', $4::jsonb)",
            plan,
            tenant,
            proj,
            json.dumps(spec if spec is not None else _SPEC),
        )
    finally:
        await conn.close()
    return {"tenant": tenant, "admin": admin, "proj": proj, "plan": plan}


@pytest.mark.asyncio
async def test_concurrent_syncs_do_not_duplicate_tasks(
    configured_app, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    """PROY2-09: dos syncs simultáneos del MISMO plan → exactamente 2 tareas."""
    from api_server.chat.sync_to_kanban import sync_plan_to_kanban
    from api_server.db.domain import Plan
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    seeded = await _seed(migrations_pg_dsn)
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)

        async def one_sync() -> None:
            async with sm() as session, session.begin():
                plan = (
                    await session.execute(select(Plan).where(Plan.id == seeded["plan"]))
                ).scalar_one()
                await sync_plan_to_kanban(session, plan, scope="total")

        await asyncio.gather(one_sync(), one_sync())
    finally:
        await engine.dispose()

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        count = await conn.fetchval("SELECT count(*) FROM tasks WHERE plan_id = $1", seeded["plan"])
    finally:
        await conn.close()
    assert count == 2


@pytest.mark.asyncio
async def test_resync_wires_edges_of_preexisting_tasks(
    configured_app, migrations_pg_dsn: str
) -> None:
    """PROY2-10: sync de t2 (su dep t1 fuera de scope), luego sync total —
    la arista t2→t1 debe quedar cableada en el segundo sync."""
    seeded = await _seed(migrations_pg_dsn)
    headers = {"Authorization": f"Bearer {await _token(seeded['admin'], seeded['tenant'])}"}
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        first = await client.post(
            f"/plans/{seeded['plan']}/sync-to-kanban",
            json={"scope": "selection", "task_ids": ["t2"]},
            headers=headers,
        )
        assert first.status_code == 200, first.text
        second = await client.post(
            f"/plans/{seeded['plan']}/sync-to-kanban",
            json={"scope": "total"},
            headers=headers,
        )
        assert second.status_code == 200, second.text

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        edges = await conn.fetchval(
            "SELECT count(*) FROM task_dependencies td"
            " JOIN tasks t ON t.id = td.task_id WHERE t.plan_id = $1",
            seeded["plan"],
        )
    finally:
        await conn.close()
    assert edges == 1  # t2 -> t1


@pytest.mark.asyncio
async def test_chat_spec_with_duplicate_ids_yields_422(
    configured_app, migrations_pg_dsn: str
) -> None:
    """PROY2-12: el spec del planning-chat con id duplicado → 422, no 500."""
    import json

    seeded = await _seed(migrations_pg_dsn)
    conv, agent = uuid4(), uuid4()
    bad_spec = {"tasks": [{"id": "dup", "title": "A"}, {"id": "dup", "title": "B"}]}
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "INSERT INTO conversations (id, tenant_id, project_id) VALUES ($1, $2, $3)",
            conv,
            seeded["tenant"],
            seeded["proj"],
        )
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, name, role, system_prompt, agent_type, scope,"
            " project_id) VALUES ($1, $2, 'PM', 'project-manager', 'x', 'ai', 'project_local', $3)",
            agent,
            seeded["tenant"],
            seeded["proj"],
        )
        await conn.execute(
            "INSERT INTO messages (id, tenant_id, conversation_id, author_kind, author_agent_id,"
            " mode, content, attachments)"
            " VALUES ($1, $2, $3, 'agent', $4, 'planning', 'listo', $5::jsonb)",
            uuid4(),
            seeded["tenant"],
            conv,
            agent,
            json.dumps(
                [
                    {
                        "kind": "planning_directive",
                        "intent": "finish_planning",
                        "title": "Plan malo",
                        "specification": bad_spec,
                    }
                ]
            ),
        )
    finally:
        await conn.close()

    headers = {"Authorization": f"Bearer {await _token(seeded['admin'], seeded['tenant'])}"}
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/projects/{seeded['proj']}/plans",
            json={"title": "Desde chat", "conversation_id": str(conv)},
            headers=headers,
        )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error"] == "invalid_spec"
