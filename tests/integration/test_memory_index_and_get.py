"""task_mem_01 / task_mem_02 — índice de memorias, `memory_get` y uso registrado.

Endpoints `POST /internal/agent/memory-recall` (ahora índice) y
`POST /internal/agent/memory-get` (detalle por ids), contra PostgreSQL real con
el rol `app_user` (RLS). Cubre:

  - el hit del índice no lleva contenido y sí `{id, headline, ..., tokens}`;
  - `memory-get` NO devuelve ids de otro tenant, de otro proyecto ni `private`
    de una persona (un agente ni las lee ni las escribe: CLAUDE.md);
  - cada hit / cada `memory_get` incrementa `recall_count` (una vez por run) y
    escribe `memory_recall_log`, bajo RLS;
  - un fallo al escribir el uso NO rompe el recall (best-effort).
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient

pytestmark = [pytest.mark.integration, pytest.mark.cross_tenant]

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")

_MINE = "El proyecto Alfa fija asyncpg como único driver de base de datos."
_OTHER_PROJECT = "El proyecto Otro fija asyncpg como driver secreto de otro proyecto."
_PRIVATE = "Nota privada de Alice sobre el driver asyncpg del proyecto."
_OTHER_TENANT = "Memoria de OTRO tenant sobre el driver asyncpg."


async def _seed(dsn: str) -> dict[str, UUID]:
    ids = {
        k: uuid4()
        for k in (
            "ta",
            "tb",
            "alice",
            "team",
            "project",
            "other_project",
            "agent",
            "task",
            "execution",
            "m_mine",
            "m_other_project",
            "m_private",
            "m_other_tenant",
            "pb",
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
            "INSERT INTO organizations (id, name, slug) VALUES ($1,'A','mig-a'),"
            " ($2,'B','mig-b'), ($3,'P','mig-p')",
            ids["ta"],
            ids["tb"],
            _PLATFORM_TENANT_ID,
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, full_name)"
            " VALUES ($1,'alice@mig.test','h','Alice')",
            ids["alice"],
        )
        await conn.execute(
            "INSERT INTO teams (id, tenant_id, name) VALUES ($1,$2,'Equipo')",
            ids["team"],
            ids["ta"],
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, team_id) VALUES"
            " ($1,$2,'Alfa',$3), ($4,$2,'Otro',NULL), ($5,$6,'Beta',NULL)",
            ids["project"],
            ids["ta"],
            ids["team"],
            ids["other_project"],
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
        await conn.execute(
            "INSERT INTO executions (id, tenant_id, task_id, agent_id, status, steps_log)"
            " VALUES ($1,$2,$3,$4,'running','[]'::jsonb)",
            ids["execution"],
            ids["ta"],
            ids["task"],
            ids["agent"],
        )
        await conn.execute(
            "INSERT INTO memory_entries (id, tenant_id, scope, type, content, project_id)"
            " VALUES ($1,$2,'project_shared','semantic',$3,$4)",
            ids["m_mine"],
            ids["ta"],
            _MINE,
            ids["project"],
        )
        await conn.execute(
            "INSERT INTO memory_entries (id, tenant_id, scope, type, content, project_id)"
            " VALUES ($1,$2,'project_shared','semantic',$3,$4)",
            ids["m_other_project"],
            ids["ta"],
            _OTHER_PROJECT,
            ids["other_project"],
        )
        await conn.execute(
            "INSERT INTO memory_entries (id, tenant_id, scope, type, content, user_id)"
            " VALUES ($1,$2,'private','semantic',$3,$4)",
            ids["m_private"],
            ids["ta"],
            _PRIVATE,
            ids["alice"],
        )
        await conn.execute(
            "INSERT INTO memory_entries (id, tenant_id, scope, type, content, project_id)"
            " VALUES ($1,$2,'project_shared','semantic',$3,$4)",
            ids["m_other_tenant"],
            ids["tb"],
            _OTHER_TENANT,
            ids["pb"],
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

    async def _no_embedder():  # BM25 basta: el test es de forma y de aislamiento
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


def _headers(ids: dict[str, UUID], *, with_task: bool = True) -> dict[str, str]:
    from api_server.auth.internal_agent import mint_agent_token

    token = mint_agent_token(
        agent_id=ids["agent"],
        tenant_id=ids["ta"],
        task_id=ids["task"] if with_task else None,
    )
    return {"Authorization": f"Bearer {token}"}


async def _fetch(dsn: str, sql: str, *args) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(dsn)
    try:
        return list(await conn.fetch(sql, *args))
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# task_mem_01 — el índice
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_recall_returns_an_index_without_the_content(
    configured_app, migrations_pg_dsn: str
) -> None:
    ids = await _seed(migrations_pg_dsn)
    async with _client(configured_app) as c:
        resp = await c.post(
            "/internal/agent/memory-recall",
            json={"query": "asyncpg driver", "limit": 10},
            headers=_headers(ids),
        )
    assert resp.status_code == 200, resp.text
    hits = resp.json()["hits"]
    assert [h["id"] for h in hits] == [str(ids["m_mine"])], hits
    hit = hits[0]
    assert set(hit) >= {
        "id",
        "headline",
        "type",
        "kind",
        "scope",
        "created_at",
        "agent",
        "project",
        "score",
        "tokens",
    }
    assert "content" not in hit
    assert hit["headline"] == _MINE[:120]
    assert hit["kind"] is None  # la columna `kind` la crea task_mem_04
    assert hit["project"] == "Alfa"
    assert hit["tokens"] == -(-len(_MINE) // 4)
    assert hit["score"] > 0


# ---------------------------------------------------------------------------
# task_mem_01 — memory_get respeta tenant, ámbito y RLS
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_memory_get_returns_the_full_content_of_visible_ids_only(
    configured_app, migrations_pg_dsn: str
) -> None:
    ids = await _seed(migrations_pg_dsn)
    wanted = [
        ids["m_mine"],
        ids["m_other_project"],  # otro proyecto del mismo tenant
        ids["m_private"],  # private de una persona: un agente NO la lee
        ids["m_other_tenant"],  # otro tenant
        uuid4(),  # inexistente
    ]
    async with _client(configured_app) as c:
        resp = await c.post(
            "/internal/agent/memory-get",
            json={"ids": [str(i) for i in wanted]},
            headers=_headers(ids),
        )
    assert resp.status_code == 200, resp.text
    got = resp.json()["memories"]
    assert [m["id"] for m in got] == [str(ids["m_mine"])], got
    assert got[0]["content"] == _MINE
    assert got[0]["scope"] == "project_shared"


@pytest.mark.asyncio
async def test_memory_get_caps_the_ids_at_twenty(configured_app, migrations_pg_dsn: str) -> None:
    ids = await _seed(migrations_pg_dsn)
    async with _client(configured_app) as c:
        resp = await c.post(
            "/internal/agent/memory-get",
            json={"ids": [str(uuid4()) for _ in range(21)]},
            headers=_headers(ids),
        )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# task_mem_02 — uso registrado, idempotente por run, bajo RLS
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_usage_is_counted_once_per_run_and_logged_under_rls(
    configured_app, migrations_pg_dsn: str, app_database_url: str
) -> None:
    ids = await _seed(migrations_pg_dsn)
    async with _client(configured_app) as c:
        for _ in range(2):  # el agente repite la búsqueda dentro del mismo run
            r = await c.post(
                "/internal/agent/memory-recall",
                json={"query": "asyncpg driver"},
                headers=_headers(ids),
            )
            assert r.status_code == 200
        r = await c.post(
            "/internal/agent/memory-get",
            json={"ids": [str(ids["m_mine"])]},
            headers=_headers(ids),
        )
        assert r.status_code == 200

    row = (
        await _fetch(
            migrations_pg_dsn,
            "SELECT recall_count, last_recalled_at FROM memory_entries WHERE id = $1",
            ids["m_mine"],
        )
    )[0]
    assert row["recall_count"] == 1, "un run cuenta una vez, aunque repita o use las dos vías"
    assert row["last_recalled_at"] is not None
    untouched = await _fetch(
        migrations_pg_dsn,
        "SELECT recall_count FROM memory_entries WHERE id = ANY($1)",
        [ids["m_other_project"], ids["m_private"], ids["m_other_tenant"]],
    )
    assert [r["recall_count"] for r in untouched] == [0, 0, 0]

    log = await _fetch(
        migrations_pg_dsn,
        "SELECT kind, execution_id, agent_id, tenant_id FROM memory_recall_log"
        " WHERE memory_id = $1 ORDER BY kind",
        ids["m_mine"],
    )
    assert [r["kind"] for r in log] == ["detail", "index"], "una fila por vía y run"
    assert all(r["execution_id"] == ids["execution"] for r in log)
    assert all(r["agent_id"] == ids["agent"] and r["tenant_id"] == ids["ta"] for r in log)

    # RLS: el tenant B no ve el log del A; el A sí.
    dsn = app_database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(ids["tb"]))
        assert await conn.fetchval("SELECT count(*) FROM memory_recall_log") == 0
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(ids["ta"]))
        assert await conn.fetchval("SELECT count(*) FROM memory_recall_log") == 2
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_a_second_run_counts_again(configured_app, migrations_pg_dsn: str) -> None:
    ids = await _seed(migrations_pg_dsn)
    async with _client(configured_app) as c:
        await c.post(
            "/internal/agent/memory-get",
            json={"ids": [str(ids["m_mine"])]},
            headers=_headers(ids),
        )
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute("UPDATE executions SET status = 'done' WHERE id = $1", ids["execution"])
        await conn.execute(
            "INSERT INTO executions (id, tenant_id, task_id, agent_id, status, steps_log)"
            " VALUES ($1,$2,$3,$4,'running','[]'::jsonb)",
            uuid4(),
            ids["ta"],
            ids["task"],
            ids["agent"],
        )
    finally:
        await conn.close()
    async with _client(configured_app) as c:
        await c.post(
            "/internal/agent/memory-get",
            json={"ids": [str(ids["m_mine"])]},
            headers=_headers(ids),
        )
    rows = await _fetch(
        migrations_pg_dsn, "SELECT recall_count FROM memory_entries WHERE id = $1", ids["m_mine"]
    )
    assert rows[0]["recall_count"] == 2


@pytest.mark.asyncio
async def test_a_failing_usage_write_never_breaks_the_recall(
    configured_app, migrations_pg_dsn: str
) -> None:
    ids = await _seed(migrations_pg_dsn)
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        # Sin la tabla del log, el registro de uso falla: el recall debe salir igual.
        await conn.execute("ALTER TABLE memory_recall_log RENAME TO memory_recall_log_x")
    finally:
        await conn.close()
    try:
        async with _client(configured_app) as c:
            r1 = await c.post(
                "/internal/agent/memory-recall",
                json={"query": "asyncpg driver"},
                headers=_headers(ids),
            )
            r2 = await c.post(
                "/internal/agent/memory-get",
                json={"ids": [str(ids["m_mine"])]},
                headers=_headers(ids),
            )
        assert r1.status_code == 200 and len(r1.json()["hits"]) == 1
        assert r2.status_code == 200 and len(r2.json()["memories"]) == 1
    finally:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            await conn.execute("ALTER TABLE memory_recall_log_x RENAME TO memory_recall_log")
        finally:
            await conn.close()


@pytest.mark.asyncio
async def test_without_a_task_in_the_token_usage_is_still_logged_without_run(
    configured_app, migrations_pg_dsn: str
) -> None:
    ids = await _seed(migrations_pg_dsn)
    async with _client(configured_app) as c:
        for _ in range(2):
            await c.post(
                "/internal/agent/memory-get",
                json={"ids": [str(ids["m_mine"])]},
                headers=_headers(ids, with_task=False),
            )
    rows = await _fetch(
        migrations_pg_dsn,
        "SELECT execution_id FROM memory_recall_log WHERE memory_id = $1",
        ids["m_mine"],
    )
    assert [r["execution_id"] for r in rows] == [None, None]
