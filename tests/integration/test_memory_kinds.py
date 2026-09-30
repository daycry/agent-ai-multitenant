"""task_mem_04 — `memory_entries.kind`: migración 0152, filtro por `kinds` y persistencia.

Contra PostgreSQL real:
  - la migración es aditiva (las filas existentes quedan `NULL`), el CHECK cierra el
    vocabulario y hace up→down→up sin dejar rastro;
  - `memory-recall` con `kinds` sólo devuelve esos tipos (y jamás de otro tenant), un
    filtro inválido no rompe el recall, y el hit trae el `kind` real;
  - `memory-get`, `GET /memories` (+ filtro `kind`) y la línea de tiempo lo devuelven;
  - `persist_memory_candidates` escribe el `kind` y no memoriza texto `<private>`.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = [pytest.mark.integration, pytest.mark.cross_tenant]

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
PREVIOUS = "0151_memory_recall_usage"

_DECISION = "Decidimos fijar asyncpg como único driver de la base de datos."
_GOTCHA = "Cuidado: asyncpg falla en silencio si el HOME no es escribible."
_LEGACY = "Nota antigua sobre asyncpg sin clasificar."
_SUMMARY = "Resumen del run: se fijó asyncpg como driver y se corrigió el import."
_OTHER_TENANT = "Decisión de OTRO tenant sobre asyncpg."


async def _seed(dsn: str) -> dict[str, UUID]:
    ids = {
        k: uuid4()
        for k in (
            "ta",
            "tb",
            "alice",
            "project",
            "pb",
            "agent",
            "task",
            "m_decision",
            "m_gotcha",
            "m_legacy",
            "m_summary",
            "m_other",
        )
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE memory_recall_log, memory_entries, executions, tasks, plans,"
            " conversations, projects, agents, teams, user_org_memberships,"
            " organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1,'A','kind-a'),"
            " ($2,'B','kind-b'), ($3,'P','kind-p')",
            ids["ta"],
            ids["tb"],
            _PLATFORM_TENANT_ID,
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, full_name)"
            " VALUES ($1,'alice@kind.test','h','Alice')",
            ids["alice"],
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1,$2,$3,'tenant_member')",
            uuid4(),
            ids["ta"],
            ids["alice"],
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1,$2,'Alfa'), ($3,$4,'Beta')",
            ids["project"],
            ids["ta"],
            ids["pb"],
            ids["tb"],
        )
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, project_id, name, role, system_prompt,"
            " memory_scope, scope) VALUES ($1,$2,$3,'Backend Bot','backend_dev','x',"
            " 'project_shared','project_local')",
            ids["agent"],
            ids["ta"],
            ids["project"],
        )
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, title) VALUES ($1,$2,$3,'T')",
            ids["task"],
            ids["ta"],
            ids["project"],
        )
        rows = [
            (ids["m_decision"], ids["ta"], "semantic", _DECISION, ids["project"], "decision"),
            (ids["m_gotcha"], ids["ta"], "semantic", _GOTCHA, ids["project"], "gotcha"),
            (ids["m_legacy"], ids["ta"], "semantic", _LEGACY, ids["project"], None),
            (ids["m_summary"], ids["ta"], "episodic", _SUMMARY, ids["project"], "summary"),
            (ids["m_other"], ids["tb"], "semantic", _OTHER_TENANT, ids["pb"], "decision"),
        ]
        for mid, tid, typ, content, pid, kind in rows:
            await conn.execute(
                "INSERT INTO memory_entries (id, tenant_id, scope, type, content, project_id,"
                " kind) VALUES ($1,$2,'project_shared',$3,$4,$5,$6)",
                mid,
                tid,
                typ,
                content,
                pid,
                kind,
            )
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
    from api_server.routers.docs_viewer import get_query_embedder

    app = create_app()

    async def _no_embedder():  # BM25 basta: el test es del filtro, no del ranking
        yield None

    app.dependency_overrides[get_query_embedder] = _no_embedder
    try:
        yield app
    finally:
        app.dependency_overrides.clear()
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _agent_headers(ids: dict[str, UUID]) -> dict[str, str]:
    from api_server.auth.internal_agent import mint_agent_token

    token = mint_agent_token(agent_id=ids["agent"], tenant_id=ids["ta"], task_id=ids["task"])
    return {"Authorization": f"Bearer {token}"}


async def _user_headers(ids: dict[str, UUID]) -> dict[str, str]:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    await SessionStore(get_redis()).create(
        sid, user_id=ids["alice"], tenant_id=ids["ta"], ttl_seconds=3600
    )
    token = encode_jwt(user_id=ids["alice"], session_id=sid, tenant_id=ids["ta"])
    return {"Authorization": f"Bearer {token}"}


async def _recall(app, ids: dict[str, UUID], **body) -> list[dict]:
    async with _client(app) as c:
        resp = await c.post(
            "/internal/agent/memory-recall",
            json={"query": "asyncpg", "limit": 20, **body},
            headers=_agent_headers(ids),
        )
    assert resp.status_code == 200, resp.text
    return resp.json()["hits"]


# ---------------------------------------------------------------------------
# Migración 0152
# ---------------------------------------------------------------------------
def test_migration_adds_a_nullable_kind_with_a_closed_check(
    alembic_config, migrations_pg_dsn: str
) -> None:
    command.upgrade(alembic_config, PREVIOUS)

    async def _seed_old_row() -> UUID:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            t, mid = uuid4(), uuid4()
            await conn.execute(
                "INSERT INTO organizations (id, name, slug) VALUES ($1,'M','kind-mig')", t
            )
            await conn.execute(
                "INSERT INTO memory_entries (id, tenant_id, scope, type, content)"
                " VALUES ($1,$2,'global','semantic','memoria anterior a la 0152')",
                mid,
                t,
            )
            return mid
        finally:
            await conn.close()

    old_id = asyncio.run(_seed_old_row())
    command.upgrade(alembic_config, "head")

    async def _check() -> None:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            col = await conn.fetchrow(
                "SELECT is_nullable, character_maximum_length FROM information_schema.columns"
                " WHERE table_name='memory_entries' AND column_name='kind'"
            )
            assert col is not None and col["is_nullable"] == "YES"
            assert (
                await conn.fetchval("SELECT kind FROM memory_entries WHERE id=$1", old_id) is None
            )
            t = await conn.fetchval("SELECT tenant_id FROM memory_entries WHERE id=$1", old_id)
            for ok in ("summary", "decision", "bugfix", "pattern", "gotcha", "preference", "fact"):
                await conn.execute(
                    "INSERT INTO memory_entries (id, tenant_id, scope, type, content, kind)"
                    " VALUES ($1,$2,'global','semantic','x',$3)",
                    uuid4(),
                    t,
                    ok,
                )
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(
                    "INSERT INTO memory_entries (id, tenant_id, scope, type, content, kind)"
                    " VALUES ($1,$2,'global','semantic','x','raro')",
                    uuid4(),
                    t,
                )
        finally:
            await conn.close()

    asyncio.run(_check())


def test_migration_up_down_up_round_trip(alembic_config, migrations_pg_dsn: str) -> None:
    async def _state() -> tuple[bool, bool]:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            col = await conn.fetchval(
                "SELECT count(*) FROM information_schema.columns"
                " WHERE table_name='memory_entries' AND column_name='kind'"
            )
            chk = await conn.fetchval(
                "SELECT count(*) FROM pg_constraint WHERE conname='ck_memory_entries_kind'"
            )
            return bool(col), bool(chk)
        finally:
            await conn.close()

    command.upgrade(alembic_config, "head")
    assert asyncio.run(_state()) == (True, True)
    command.downgrade(alembic_config, PREVIOUS)
    assert asyncio.run(_state()) == (False, False)
    command.upgrade(alembic_config, "head")
    assert asyncio.run(_state()) == (True, True)


# ---------------------------------------------------------------------------
# Filtro `kinds` del recall
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_recall_without_kinds_returns_every_kind_with_the_real_value(
    configured_app, migrations_pg_dsn: str
) -> None:
    ids = await _seed(migrations_pg_dsn)
    hits = await _recall(configured_app, ids)
    by_id = {h["id"]: h["kind"] for h in hits}
    assert by_id == {
        str(ids["m_decision"]): "decision",
        str(ids["m_gotcha"]): "gotcha",
        str(ids["m_legacy"]): None,
        str(ids["m_summary"]): "summary",
    }


@pytest.mark.asyncio
async def test_recall_kinds_filter_returns_only_those_kinds_and_never_another_tenant(
    configured_app, migrations_pg_dsn: str
) -> None:
    ids = await _seed(migrations_pg_dsn)
    only_decisions = await _recall(configured_app, ids, kinds=["decision"])
    assert [h["id"] for h in only_decisions] == [str(ids["m_decision"])]  # no la de tenant B
    two = await _recall(configured_app, ids, kinds=["decision", "gotcha"])
    assert {h["id"] for h in two} == {str(ids["m_decision"]), str(ids["m_gotcha"])}
    summaries = await _recall(configured_app, ids, kinds=["summary"])
    assert [h["id"] for h in summaries] == [str(ids["m_summary"])]


@pytest.mark.asyncio
async def test_recall_tolerates_a_sloppy_kinds_filter(
    configured_app, migrations_pg_dsn: str
) -> None:
    ids = await _seed(migrations_pg_dsn)
    sloppy = await _recall(configured_app, ids, kinds=["Decisión", "banana"])
    assert [h["id"] for h in sloppy] == [str(ids["m_decision"])]
    nothing_valid = await _recall(configured_app, ids, kinds=["banana"])
    assert len(nothing_valid) == 4  # sin nada válido = sin filtro, nunca un 422


@pytest.mark.asyncio
async def test_recall_function_filters_by_kinds_under_the_tenant(
    configured_app, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from api_server.memorizer.recall import recall
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    ids = await _seed(migrations_pg_dsn)
    engine = create_async_engine(app_database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            await session.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(ids["ta"])}
            )
            hits = await recall(
                session,
                query="asyncpg",
                tenant_id=ids["ta"],
                scopes=["project_shared"],
                project_id=ids["project"],
                kinds=["gotcha"],
            )
    finally:
        await engine.dispose()
    assert [h.memory_id for h in hits] == [ids["m_gotcha"]]
    assert hits[0].kind == "gotcha"


# ---------------------------------------------------------------------------
# memory-get, GET /memories, timeline
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_memory_get_returns_the_kind(configured_app, migrations_pg_dsn: str) -> None:
    ids = await _seed(migrations_pg_dsn)
    async with _client(configured_app) as c:
        resp = await c.post(
            "/internal/agent/memory-get",
            json={"ids": [str(ids["m_decision"]), str(ids["m_legacy"])]},
            headers=_agent_headers(ids),
        )
    assert resp.status_code == 200, resp.text
    assert {m["id"]: m["kind"] for m in resp.json()["memories"]} == {
        str(ids["m_decision"]): "decision",
        str(ids["m_legacy"]): None,
    }


@pytest.mark.asyncio
async def test_list_memories_returns_kind_and_filters_by_it(
    configured_app, migrations_pg_dsn: str
) -> None:
    ids = await _seed(migrations_pg_dsn)
    h = await _user_headers(ids)
    async with _client(configured_app) as c:
        everything = (await c.get("/memories", headers=h)).json()
        gotchas = (await c.get("/memories", params={"kind": "gotcha"}, headers=h)).json()
        invalid = await c.get("/memories", params={"kind": "raro"}, headers=h)
    by_id = {r["id"]: r["kind"] for r in everything}
    assert by_id[str(ids["m_decision"])] == "decision"
    assert by_id[str(ids["m_legacy"])] is None  # «sin clasificar»
    assert str(ids["m_other"]) not in by_id  # otro tenant
    assert [r["id"] for r in gotchas] == [str(ids["m_gotcha"])]
    assert invalid.status_code == 422


# ---------------------------------------------------------------------------
# Persistencia
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_persist_writes_the_kind_and_drops_private_text(
    configured_app, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    from api_server.memorizer import MemoryCandidate, persist_memory_candidates
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    ids = await _seed(migrations_pg_dsn)
    engine = create_async_engine(admin_database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session, session.begin():
            rows = await persist_memory_candidates(
                session,
                [
                    MemoryCandidate("Resumen nuevo del run.", "episodic", kind="summary"),
                    MemoryCandidate(
                        "Regla útil <private>dato interno</private>", "semantic", kind="pattern"
                    ),
                    MemoryCandidate("<private>todo privado</private>", "semantic", kind="fact"),
                    MemoryCandidate("Sin clasificar.", "semantic"),
                ],
                tenant_id=ids["ta"],
                scope="project_shared",
                project_id=ids["project"],
            )
    finally:
        await engine.dispose()
    assert sorted((r.kind or "", r.content) for r in rows) == sorted(
        [
            ("summary", "Resumen nuevo del run."),
            ("pattern", "Regla útil"),
            ("", "Sin clasificar."),
        ]
    )
