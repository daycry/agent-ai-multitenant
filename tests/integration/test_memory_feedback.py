"""task_mem_10 — `memory_feedback` y el ranking que aprende, contra Postgres real.

Cubre:
  - la migración 0153 (columnas de utilidad/estado y las dos tablas nuevas): aditiva,
    up→down→up sin rastro, RLS literal en las dos tablas;
  - `POST /internal/agent/memory-feedback`: `wrong` marca `disputed` sin borrar; un voto por
    ejecución y memoria (repetir no suma, cambiar de opinión reescribe); votos de ejecuciones
    distintas suman; nunca sobre `private`, otro proyecto ni otro tenant (404 y NADA escrito);
  - RLS de `memory_feedback` y `memory_compaction_proposals` con el rol de aplicación;
  - el recall: una memoria marcada `wrong` cae, una con votos `useful` sube, y con los pesos a
    cero el orden vuelve al de la fusión RRF.
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
PREVIOUS = "0152_memory_entries_kind"

# Tres memorias del proyecto que casan con la consulta "asyncpg"; el BM25 las ordena por
# cuántas veces aparece el término: top > mid > low, sin empates.
_TOP = "asyncpg asyncpg asyncpg asyncpg driver de la base de datos"
_MID = "asyncpg asyncpg driver secundario del servicio"
_LOW = "asyncpg nota breve"
_KEYS = (
    "ta", "tb", "alice", "p1", "p2", "pb", "agent", "task1", "task2", "e1", "e2",
    "m_top", "m_mid", "m_low", "m_priv", "m_p2", "m_other",
)  # fmt: skip


async def _seed(dsn: str) -> dict[str, UUID]:
    ids = {k: uuid4() for k in _KEYS}
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE memory_feedback, memory_compaction_proposals, memory_recall_log,"
            " memory_entries, executions, tasks, plans, conversations, projects, agents, teams,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1,'A','fb-a'),"
            " ($2,'B','fb-b'), ($3,'P','fb-p')",
            ids["ta"], ids["tb"], _PLATFORM_TENANT_ID,
        )  # fmt: skip
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, full_name)"
            " VALUES ($1,'alice@fb.test','h','Alice')",
            ids["alice"],
        )
        for key, tenant in (("p1", "ta"), ("p2", "ta"), ("pb", "tb")):
            await conn.execute(
                "INSERT INTO projects (id, tenant_id, name) VALUES ($1,$2,$3)",
                ids[key], ids[tenant], f"Proyecto {key}",
            )  # fmt: skip
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, project_id, name, role, system_prompt,"
            " memory_scope, scope) VALUES ($1,$2,$3,'Bot','backend_dev','x',"
            " 'project_shared','project_local')",
            ids["agent"], ids["ta"], ids["p1"],
        )  # fmt: skip
        for key, proj in (("task1", "p1"), ("task2", "p1")):
            await conn.execute(
                "INSERT INTO tasks (id, tenant_id, project_id, title) VALUES ($1,$2,$3,'T')",
                ids[key], ids["ta"], ids[proj],
            )  # fmt: skip
        for key, task in (("e1", "task1"), ("e2", "task2")):
            await conn.execute(
                "INSERT INTO executions (id, tenant_id, task_id, status, output, steps_log)"
                " VALUES ($1,$2,$3,'running','o','[]'::jsonb)",
                ids[key], ids["ta"], ids[task],
            )  # fmt: skip

        async def mem(key, tenant, scope, content, *, project=None, user=None, mtype="semantic"):
            await conn.execute(
                "INSERT INTO memory_entries (id, tenant_id, scope, type, content, project_id,"
                " user_id) VALUES ($1,$2,$3,$4,$5,$6,$7)",
                ids[key], ids[tenant], scope, mtype, content,
                ids[project] if project else None, ids[user] if user else None,
            )  # fmt: skip

        await mem("m_top", "ta", "project_shared", _TOP, project="p1")
        await mem("m_mid", "ta", "project_shared", _MID, project="p1")
        await mem("m_low", "ta", "project_shared", _LOW, project="p1")
        await mem("m_priv", "ta", "private", "asyncpg privada de alice", user="alice")
        await mem("m_p2", "ta", "project_shared", "asyncpg de otro proyecto", project="p2")
        await mem("m_other", "tb", "project_shared", "asyncpg de otro tenant", project="pb")
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

    async def _no_embedder():
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


def _agent_headers(ids: dict[str, UUID], task: str = "task1") -> dict[str, str]:
    from api_server.auth.internal_agent import mint_agent_token

    token = mint_agent_token(agent_id=ids["agent"], tenant_id=ids["ta"], task_id=ids[task])
    return {"Authorization": f"Bearer {token}"}


async def _vote(app, ids, key: str, verdict: str, *, task: str = "task1", note: str | None = None):
    body = {"memory_id": str(ids[key]) if key in ids else key, "verdict": verdict}
    if note:
        body["note"] = note
    async with _client(app) as c:
        return await c.post(
            "/internal/agent/memory-feedback", json=body, headers=_agent_headers(ids, task)
        )


async def _row(dsn: str, memory_id: UUID) -> asyncpg.Record:
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT useful_count, harmful_count, disputed_at, deleted_at FROM memory_entries"
            " WHERE id=$1",
            memory_id,
        )
        assert row is not None
        return row
    finally:
        await conn.close()


async def _recall_order(app, ids) -> list[str]:
    async with _client(app) as c:
        r = await c.post(
            "/internal/agent/memory-recall",
            json={"query": "asyncpg", "limit": 10},
            headers=_agent_headers(ids),
        )
    assert r.status_code == 200, r.text
    inv = {str(v): k for k, v in ids.items()}
    return [inv[h["id"]] for h in r.json()["hits"]]


# ---------------------------------------------------------------------------
# Migración 0153
# ---------------------------------------------------------------------------
async def _schema_state(dsn: str) -> dict[str, bool]:
    conn = await asyncpg.connect(dsn)
    try:
        cols = {
            r["column_name"]
            for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name='memory_entries'"
            )
        }
        tables = {
            r["tablename"]
            for r in await conn.fetch(
                "SELECT tablename FROM pg_tables WHERE tablename IN"
                " ('memory_feedback','memory_compaction_proposals')"
            )
        }
        return {
            "cols": {"useful_count", "harmful_count", "disputed_at", "archived_at",
                     "archive_reason"} <= cols,
            "feedback": "memory_feedback" in tables,
            "proposals": "memory_compaction_proposals" in tables,
        }  # fmt: skip
    finally:
        await conn.close()


def test_migration_is_additive_and_round_trips(alembic_config, migrations_pg_dsn: str) -> None:
    command.upgrade(alembic_config, PREVIOUS)

    async def _old_row() -> UUID:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            t, mid = uuid4(), uuid4()
            await conn.execute(
                "INSERT INTO organizations (id, name, slug) VALUES ($1,'M','fb-mig')", t
            )
            await conn.execute(
                "INSERT INTO memory_entries (id, tenant_id, scope, type, content)"
                " VALUES ($1,$2,'global','semantic','anterior a la 0153')",
                mid, t,
            )  # fmt: skip
            return mid
        finally:
            await conn.close()

    old = asyncio.run(_old_row())
    command.upgrade(alembic_config, "head")
    assert asyncio.run(_schema_state(migrations_pg_dsn)) == {
        "cols": True, "feedback": True, "proposals": True,
    }  # fmt: skip
    row = asyncio.run(_row(migrations_pg_dsn, old))
    assert (row["useful_count"], row["harmful_count"], row["disputed_at"]) == (0, 0, None)
    command.downgrade(alembic_config, PREVIOUS)
    assert asyncio.run(_schema_state(migrations_pg_dsn)) == {
        "cols": False, "feedback": False, "proposals": False,
    }  # fmt: skip
    command.upgrade(alembic_config, "head")
    assert asyncio.run(_schema_state(migrations_pg_dsn)) == {
        "cols": True, "feedback": True, "proposals": True,
    }  # fmt: skip


# ---------------------------------------------------------------------------
# memory_feedback
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_wrong_marks_the_memory_disputed_without_deleting_it(
    configured_app, migrations_pg_dsn
) -> None:
    ids = await _seed(migrations_pg_dsn)
    r = await _vote(configured_app, ids, "m_top", "wrong", note="la versión ya cambió")
    assert r.status_code == 200, r.text
    assert r.json() == {
        "memory_id": str(ids["m_top"]),
        "verdict": "wrong",
        "useful_count": 0,
        "harmful_count": 1,
        "disputed": True,
    }
    row = await _row(migrations_pg_dsn, ids["m_top"])
    assert (row["harmful_count"], row["useful_count"]) == (1, 0)
    assert row["disputed_at"] is not None
    assert row["deleted_at"] is None  # NO se borra
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        fb = await conn.fetchrow(
            "SELECT tenant_id, verdict, note, execution_id, agent_id FROM memory_feedback"
            " WHERE memory_id=$1",
            ids["m_top"],
        )
    finally:
        await conn.close()
    assert fb is not None
    assert fb["tenant_id"] == ids["ta"]
    assert (fb["verdict"], fb["note"]) == ("wrong", "la versión ya cambió")
    assert fb["execution_id"] == ids["e1"]  # el run en marcha de la tarea del token
    assert fb["agent_id"] == ids["agent"]


@pytest.mark.asyncio
async def test_outdated_also_disputes_and_useful_does_not(
    configured_app, migrations_pg_dsn
) -> None:
    ids = await _seed(migrations_pg_dsn)
    assert (await _vote(configured_app, ids, "m_mid", "outdated")).json()["disputed"] is True
    ok = await _vote(configured_app, ids, "m_low", "useful")
    assert ok.json() == {
        "memory_id": str(ids["m_low"]), "verdict": "useful",
        "useful_count": 1, "harmful_count": 0, "disputed": False,
    }  # fmt: skip
    assert (await _row(migrations_pg_dsn, ids["m_low"]))["disputed_at"] is None


@pytest.mark.asyncio
async def test_one_vote_per_run_repeating_does_not_add_and_changing_rewrites(
    configured_app, migrations_pg_dsn
) -> None:
    ids = await _seed(migrations_pg_dsn)
    for _ in range(3):
        r = await _vote(configured_app, ids, "m_top", "wrong")
    assert (r.json()["harmful_count"], r.json()["useful_count"]) == (1, 0)
    # cambia de opinión (mismo run): el voto se reescribe, la disputa se levanta
    r = await _vote(configured_app, ids, "m_top", "useful")
    assert r.json() == {
        "memory_id": str(ids["m_top"]), "verdict": "useful",
        "useful_count": 1, "harmful_count": 0, "disputed": False,
    }  # fmt: skip
    assert (await _row(migrations_pg_dsn, ids["m_top"]))["disputed_at"] is None


@pytest.mark.asyncio
async def test_votes_from_different_runs_add_up(configured_app, migrations_pg_dsn) -> None:
    ids = await _seed(migrations_pg_dsn)
    await _vote(configured_app, ids, "m_top", "useful", task="task1")
    r = await _vote(configured_app, ids, "m_top", "useful", task="task2")
    assert r.json()["useful_count"] == 2


@pytest.mark.asyncio
async def test_a_memory_the_agent_cannot_read_is_404_and_writes_nothing(
    configured_app, migrations_pg_dsn
) -> None:
    ids = await _seed(migrations_pg_dsn)
    for key in ("m_priv", "m_p2", "m_other", str(uuid4())):
        r = await _vote(configured_app, ids, key, "wrong")
        assert r.status_code == 404, (key, r.text)
    bad = await _vote(configured_app, ids, "m_top", "genial")
    assert bad.status_code == 422
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        assert await conn.fetchval("SELECT count(*) FROM memory_feedback") == 0
        assert (
            await conn.fetchval("SELECT count(*) FROM memory_entries WHERE disputed_at IS NOT NULL")
            == 0
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_an_archived_memory_cannot_be_voted(configured_app, migrations_pg_dsn) -> None:
    ids = await _seed(migrations_pg_dsn)
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "UPDATE memory_entries SET archived_at = now(), archive_reason='expired_unused'"
            " WHERE id=$1",
            ids["m_mid"],
        )
    finally:
        await conn.close()
    assert (await _vote(configured_app, ids, "m_mid", "wrong")).status_code == 404


# ---------------------------------------------------------------------------
# RLS con el rol de aplicación
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_rls_isolates_feedback_and_proposals_by_tenant(
    configured_app, migrations_pg_dsn, app_database_url: str
) -> None:
    ids = await _seed(migrations_pg_dsn)
    await _vote(configured_app, ids, "m_top", "wrong")
    admin = await asyncpg.connect(migrations_pg_dsn)
    try:
        await admin.execute(
            "INSERT INTO memory_compaction_proposals (tenant_id, project_id, memory_ids, summary)"
            " VALUES ($1,$2,'[]'::jsonb,'s')",
            ids["ta"], ids["p1"],
        )  # fmt: skip
    finally:
        await admin.close()

    dsn = app_database_url.replace("postgresql+asyncpg://", "postgresql://")
    app = await asyncpg.connect(dsn)
    try:
        for table in ("memory_feedback", "memory_compaction_proposals"):
            await app.execute("SELECT set_config('app.tenant_id', $1, false)", str(ids["ta"]))
            assert await app.fetchval(f"SELECT count(*) FROM {table}") == 1, table
            await app.execute("SELECT set_config('app.tenant_id', $1, false)", str(ids["tb"]))
            assert await app.fetchval(f"SELECT count(*) FROM {table}") == 0, table
            await app.execute("SELECT set_config('app.tenant_id', '', false)")
            assert await app.fetchval(f"SELECT count(*) FROM {table}") == 0, table
        # WITH CHECK: el tenant B no puede escribir filas del A
        await app.execute("SELECT set_config('app.tenant_id', $1, false)", str(ids["tb"]))
        with pytest.raises(asyncpg.PostgresError):
            await app.execute(
                "INSERT INTO memory_feedback (tenant_id, memory_id, voter_key, verdict)"
                " VALUES ($1,$2,'x','useful')",
                ids["ta"], ids["m_top"],
            )  # fmt: skip
    finally:
        await app.close()


# ---------------------------------------------------------------------------
# El ranking a través del recall
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_recall_baseline_order_is_the_fusion_order(configured_app, migrations_pg_dsn) -> None:
    ids = await _seed(migrations_pg_dsn)
    assert await _recall_order(configured_app, ids) == ["m_top", "m_mid", "m_low"]


@pytest.mark.asyncio
async def test_a_memory_marked_wrong_falls_in_the_recall(configured_app, migrations_pg_dsn) -> None:
    ids = await _seed(migrations_pg_dsn)
    await _vote(configured_app, ids, "m_top", "wrong")
    order = await _recall_order(configured_app, ids)
    assert order[0] != "m_top"
    assert order.index("m_top") > order.index("m_mid")  # cae, pero NO desaparece
    assert "m_top" in order


@pytest.mark.asyncio
async def test_a_useful_memory_rises_in_the_recall(configured_app, migrations_pg_dsn) -> None:
    ids = await _seed(migrations_pg_dsn)
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute("UPDATE memory_entries SET useful_count = 6 WHERE id=$1", ids["m_low"])
    finally:
        await conn.close()
    order = await _recall_order(configured_app, ids)
    assert order.index("m_low") < order.index("m_mid")


@pytest.mark.asyncio
async def test_weights_at_zero_restore_the_fusion_order(
    configured_app, migrations_pg_dsn, app_database_url: str
) -> None:
    """Los pesos viven en `platform_settings` (`memory.rank_*`): a cero, ni `wrong` mueve nada."""
    ids = await _seed(migrations_pg_dsn)
    await _vote(configured_app, ids, "m_top", "wrong")
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        for key in (
            "memory.rank_utility_weight",
            "memory.rank_freshness_weight",
            "memory.rank_disputed_penalty",
        ):
            await conn.execute(
                "INSERT INTO platform_settings (key, value) VALUES ($1, '0'::jsonb)"
                " ON CONFLICT (key) DO UPDATE SET value = '0'::jsonb",
                key,
            )
    finally:
        await conn.close()
    from api_server.db.platform_settings import invalidate_platform_setting_cache

    for key in (
        "memory.rank_utility_weight",
        "memory.rank_freshness_weight",
        "memory.rank_disputed_penalty",
    ):
        await invalidate_platform_setting_cache(key)
    assert await _recall_order(configured_app, ids) == ["m_top", "m_mid", "m_low"]
