"""task_mem_03 - linea de tiempo de una memoria (`memory_timeline`).

Contra Postgres real, con tres ejecuciones sembradas en orden (e1 < e2 < e3) del
mismo proyecto, mas una ejecucion de OTRO proyecto entre e2 y e3 y otra de otro
tenant. Cubre: orden cronologico, ventana, ambito (proyecto ajeno, `private`) y
cross-tenant, por el endpoint interno del agente y por el publico.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
_KEYS = [
    "ta", "tb", "alice", "amy", "bob", "team", "p1", "p2", "pb", "agent", "task1", "task2",
    "taskb", "e1", "e2", "e3", "e_p2", "e_b", "m1a", "m1b", "m2a", "m2_priv", "m3a", "m_p2",
    "m_b", "m_glob",
]  # fmt: skip


async def _seed(dsn: str) -> dict[str, UUID]:
    ids = {k: uuid4() for k in _KEYS}
    base = datetime.now(tz=UTC) - timedelta(days=10)
    when = {
        "e1": base,
        "e2": base + timedelta(days=1),
        "e_p2": base + timedelta(days=1, hours=12),
        "e3": base + timedelta(days=2),
        "e_b": base + timedelta(days=1, hours=6),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE memory_entries, executions, tasks, plans, conversations, projects,"
            " agents, teams, user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1,'A','tl-a'),"
            " ($2,'B','tl-b'), ($3,'P','tl-p')",
            ids["ta"], ids["tb"], _PLATFORM_TENANT_ID,
        )  # fmt: skip
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, full_name) VALUES"
            " ($1,'alice@tl.test','h','Alice'), ($2,'amy@tl.test','h','Amy'),"
            " ($3,'bob@tl.test','h','Bob')",
            ids["alice"], ids["amy"], ids["bob"],
        )  # fmt: skip
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES"
            " ($1,$2,$3,'tenant_member'), ($4,$2,$5,'tenant_member'),"
            " ($6,$7,$8,'tenant_member')",
            uuid4(), ids["ta"], ids["alice"], uuid4(), ids["amy"], uuid4(), ids["tb"], ids["bob"],
        )  # fmt: skip
        await conn.execute(
            "INSERT INTO teams (id, tenant_id, name) VALUES ($1,$2,'Equipo')",
            ids["team"], ids["ta"],
        )  # fmt: skip
        for key, tenant in (("p1", "ta"), ("p2", "ta"), ("pb", "tb")):
            await conn.execute(
                "INSERT INTO projects (id, tenant_id, name, team_id) VALUES ($1,$2,$3,$4)",
                ids[key], ids[tenant], f"Proyecto {key}", ids["team"] if tenant == "ta" else None,
            )  # fmt: skip
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, project_id, name, role, system_prompt,"
            " memory_scope, scope) VALUES ($1,$2,$3,'Bot','backend_dev','x',"
            " 'project_shared','project_local')",
            ids["agent"], ids["ta"], ids["p1"],
        )  # fmt: skip
        for key, tenant, proj in (
            ("task1", "ta", "p1"),
            ("task2", "ta", "p2"),
            ("taskb", "tb", "pb"),
        ):
            await conn.execute(
                "INSERT INTO tasks (id, tenant_id, project_id, title) VALUES ($1,$2,$3,'T')",
                ids[key], ids[tenant], ids[proj],
            )  # fmt: skip
        for key, tenant, task in (
            ("e1", "ta", "task1"),
            ("e2", "ta", "task1"),
            ("e3", "ta", "task1"),
            ("e_p2", "ta", "task2"),
            ("e_b", "tb", "taskb"),
        ):
            await conn.execute(
                "INSERT INTO executions (id, tenant_id, task_id, status, output, steps_log,"
                " created_at) VALUES ($1,$2,$3,'done','o','[]'::jsonb,$4)",
                ids[key], ids[tenant], ids[task], when[key],
            )  # fmt: skip

        async def mem(key, tenant, scope, content, *, execution=None, project=None, user=None,
                      offset=0):  # fmt: skip
            created = (when[execution] if execution else base) + timedelta(minutes=offset)
            await conn.execute(
                "INSERT INTO memory_entries (id, tenant_id, scope, type, content, project_id,"
                " user_id, agent_id, source_execution_id, created_at, updated_at)"
                " VALUES ($1,$2,$3,'semantic',$4,$5,$6,$7,$8,$9,$9)",
                ids[key], ids[tenant], scope, content,
                ids[project] if project else None, ids[user] if user else None,
                ids["agent"] if tenant == "ta" and scope != "private" else None,
                ids[execution] if execution else None, created,
            )  # fmt: skip

        await mem("m1a", "ta", "project_shared", "Uno A " + "x" * 200, execution="e1", project="p1")
        await mem("m1b", "ta", "project_shared", "Uno B", execution="e1", project="p1", offset=1)
        await mem("m2a", "ta", "project_shared", "Dos A", execution="e2", project="p1")
        await mem("m2_priv", "ta", "private", "Dos privada", execution="e2", user="alice",
                  offset=1)  # fmt: skip
        await mem("m3a", "ta", "project_shared", "Tres A", execution="e3", project="p1")
        await mem("m_p2", "ta", "project_shared", "Otro proyecto", execution="e_p2", project="p2")
        await mem("m_b", "tb", "project_shared", "Otro tenant", execution="e_b", project="pb")
        await mem("m_glob", "ta", "global", "Global sin ejecucion")
    finally:
        await conn.close()
    return ids


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

    try:
        yield create_app()
    finally:
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()


async def _headers(user_id: UUID, tenant_id: UUID) -> dict[str, str]:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    await SessionStore(get_redis()).create(
        sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600
    )
    token = encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)
    return {"Authorization": f"Bearer {token}"}


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _agent_headers(ids: dict[str, UUID]) -> dict[str, str]:
    from api_server.auth.internal_agent import mint_agent_token

    token = mint_agent_token(agent_id=ids["agent"], tenant_id=ids["ta"])
    return {"Authorization": f"Bearer {token}"}


def _shape(body: dict) -> list[tuple[int, list[str]]]:
    return [(e["position"], [m["id"] for m in e["memories"]]) for e in body["executions"]]


@pytest.mark.asyncio
async def test_internal_by_memory_orders_chronologically(configured_app, migrations_pg_dsn) -> None:
    ids = await _seed(migrations_pg_dsn)
    async with _client(configured_app) as c:
        r = await c.post(
            "/internal/agent/memory-timeline",
            json={"memory_id": str(ids["m2a"])},
            headers=_agent_headers(ids),
        )
    assert r.status_code == 200, r.text
    body = r.json()
    # e_p2 (otro proyecto) no aparece; la privada de alice tampoco.
    assert _shape(body) == [
        (-1, [str(ids["m1a"]), str(ids["m1b"])]),
        (0, [str(ids["m2a"])]),
        (1, [str(ids["m3a"])]),
    ]
    hit = body["executions"][0]["memories"][0]
    assert set(hit) >= {
        "id", "headline", "type", "scope", "created_at", "agent", "project", "execution_id"
    }  # fmt: skip
    assert "content" not in hit
    assert len(hit["headline"]) <= 121
    assert hit["agent"] == "Bot"
    assert hit["project"] == "Proyecto p1"
    assert hit["execution_id"] == str(ids["e1"])


@pytest.mark.asyncio
async def test_internal_window_limits_neighbouring_executions(
    configured_app, migrations_pg_dsn
) -> None:
    ids = await _seed(migrations_pg_dsn)
    async with _client(configured_app) as c:
        r1 = await c.post(
            "/internal/agent/memory-timeline",
            json={"memory_id": str(ids["m3a"]), "window": 1},
            headers=_agent_headers(ids),
        )
        r5 = await c.post(
            "/internal/agent/memory-timeline",
            json={"execution_id": str(ids["e3"]), "window": 5},
            headers=_agent_headers(ids),
        )
    assert [p for p, _ in _shape(r1.json())] == [-1, 0]
    assert [p for p, _ in _shape(r5.json())] == [-2, -1, 0]


@pytest.mark.asyncio
async def test_internal_scope_and_cross_tenant_are_404(configured_app, migrations_pg_dsn) -> None:
    ids = await _seed(migrations_pg_dsn)
    async with _client(configured_app) as c:
        h = _agent_headers(ids)
        for payload in (
            {"memory_id": str(ids["m2_priv"])},  # private: un agente nunca la ve
            {"memory_id": str(ids["m_p2"])},  # otro proyecto
            {"memory_id": str(ids["m_b"])},  # otro tenant
            {"execution_id": str(ids["e_p2"])},
            {"execution_id": str(ids["e_b"])},
            {"memory_id": str(uuid4())},
        ):
            r = await c.post("/internal/agent/memory-timeline", json=payload, headers=h)
            assert r.status_code == 404, (payload, r.text)
        bad = await c.post("/internal/agent/memory-timeline", json={}, headers=h)
        both = await c.post(
            "/internal/agent/memory-timeline",
            json={"memory_id": str(ids["m2a"]), "execution_id": str(ids["e2"])},
            headers=h,
        )
    assert bad.status_code == 422
    assert both.status_code == 422


@pytest.mark.asyncio
async def test_public_timeline_private_only_owner_and_no_cross_tenant(
    configured_app, migrations_pg_dsn
) -> None:
    ids = await _seed(migrations_pg_dsn)
    async with _client(configured_app) as c:
        alice = await _headers(ids["alice"], ids["ta"])
        amy = await _headers(ids["amy"], ids["ta"])
        bob = await _headers(ids["bob"], ids["tb"])
        ok = await c.get(f"/memories/{ids['m2a']}/timeline", headers=amy)
        own = await c.get(f"/memories/{ids['m2_priv']}/timeline", headers=alice)
        other = await c.get(f"/memories/{ids['m2_priv']}/timeline", headers=amy)
        cross = await c.get(f"/memories/{ids['m2a']}/timeline", headers=bob)
        narrow = await c.get(f"/memories/{ids['m2a']}/timeline?window=1", headers=amy)
        glob = await c.get(f"/memories/{ids['m_glob']}/timeline", headers=amy)
    assert ok.status_code == 200
    # la privada de alice no sale para amy aunque sea de la misma ejecucion
    assert [m for _, ms in _shape(ok.json()) for m in ms] == [
        str(ids["m1a"]), str(ids["m1b"]), str(ids["m2a"]), str(ids["m3a"]),
    ]  # fmt: skip
    assert own.status_code == 200
    assert _shape(own.json()) == [(0, [str(ids["m2_priv"])])]
    assert other.status_code == 404
    assert cross.status_code == 404
    assert [p for p, _ in _shape(narrow.json())] == [-1, 0, 1]
    # una memoria sin ejecucion de origen devuelve solo ella misma
    assert glob.status_code == 200
    assert _shape(glob.json()) == [(0, [str(ids["m_glob"])])]
