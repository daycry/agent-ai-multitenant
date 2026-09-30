"""Gaps de la revisión adversarial (intento 1) del carril backend del memorizer.

 1. el primer archivado respeta `memory.usage_tracking_since` (no archivar el histórico);
 2. el feedback de un solo run no dispara `disputed` (CWE-863), el rechazo limpia la disputa,
    un `useful` humano des-disputa y la compactación agrupa team_shared/global, nunca private;
 5. el uso no cuenta archivadas y es idempotente por run con llamadas concurrentes;
 6. confirmar archiva lo que el admin vio (409 si difiere) y las huérfanas se auditan;
 7. el job archiva en tandas con auditoría en bloque;
 8. migración 0154 (aditiva, up→down→up) e índices usados por el planificador;
 9. `POST /memories` con todo `<private>` es 422, no 500;
10. `memory-get` tolera ids mal formados.
"""

from __future__ import annotations

import asyncio
import json
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = [pytest.mark.integration, pytest.mark.cross_tenant]

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
_KEYS = (
    "ta", "admin", "member", "p1", "agent", "task1", "task2", "e1", "e2", "team",
    "old", "proj_a", "proj_b", "team_a", "team_b", "glob_a", "glob_b", "priv",
)  # fmt: skip


async def _seed(dsn: str, *, usage_since: str | None = "2020-01-01T00:00:00+00:00") -> dict:
    ids = {k: uuid4() for k in _KEYS}
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE memory_feedback, memory_compaction_proposals, memory_recall_log,"
            " memory_entries, executions, tasks, plans, conversations, projects, agents, teams,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute("DELETE FROM platform_settings WHERE key LIKE 'memory.%'")
        if usage_since is not None:
            await conn.execute(
                "INSERT INTO platform_settings (key, value) VALUES"
                " ('memory.usage_tracking_since', to_jsonb($1::text))",
                usage_since,
            )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1,'A','rf-a'), ($2,'P','rf-p')",
            ids["ta"], _PLATFORM_TENANT_ID,
        )  # fmt: skip
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, full_name) VALUES"
            " ($1,'admin@rf.test','h','Admin'), ($2,'member@rf.test','h','Member')",
            ids["admin"], ids["member"],
        )  # fmt: skip
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES"
            " ($1,$2,$3,'tenant_admin'), ($4,$2,$5,'tenant_member')",
            uuid4(), ids["ta"], ids["admin"], uuid4(), ids["member"],
        )  # fmt: skip
        await conn.execute(
            "INSERT INTO teams (id, tenant_id, name) VALUES ($1,$2,'Equipo')",
            ids["team"], ids["ta"],
        )  # fmt: skip
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, team_id) VALUES ($1,$2,'P1',$3)",
            ids["p1"], ids["ta"], ids["team"],
        )  # fmt: skip
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, project_id, name, role, system_prompt,"
            " memory_scope, scope) VALUES ($1,$2,$3,'Bot','backend_dev','x',"
            " 'project_shared','project_local')",
            ids["agent"], ids["ta"], ids["p1"],
        )  # fmt: skip
        for tkey, ekey in (("task1", "e1"), ("task2", "e2")):
            await conn.execute(
                "INSERT INTO tasks (id, tenant_id, project_id, title) VALUES ($1,$2,$3,'T')",
                ids[tkey], ids["ta"], ids["p1"],
            )  # fmt: skip
            await conn.execute(
                "INSERT INTO executions (id, tenant_id, task_id, status, output, steps_log)"
                " VALUES ($1,$2,$3,'running','o','[]'::jsonb)",
                ids[ekey], ids["ta"], ids[tkey],
            )  # fmt: skip
    finally:
        await conn.close()
    return ids


async def _mem(dsn, ids, key, *, scope="project_shared", days=1, recalled=0, disputed=False,
               mtype="episodic", user=None):  # fmt: skip
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO memory_entries (id, tenant_id, scope, type, content, project_id,"
            " team_id, user_id, recall_count, created_at, updated_at, disputed_at)"
            " VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9, now() - make_interval(days => $10),"
            " now() - make_interval(days => $10), CASE WHEN $11 THEN now() ELSE NULL END)",
            ids[key], ids["ta"], scope, mtype, f"memoria {key}",
            ids["p1"] if scope == "project_shared" else None,
            ids["team"] if scope == "team_shared" else None,
            ids[user] if user else None, recalled, days, disputed,
        )  # fmt: skip
    finally:
        await conn.close()


async def _sql(dsn: str, query: str, *args):
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetch(query, *args)
    finally:
        await conn.close()


@pytest.fixture(autouse=True)
def _redis_client_bound_to_this_loop():
    from api_server.auth.deps import reset_redis_cache

    reset_redis_cache()
    yield
    reset_redis_cache()


@pytest.fixture()
def workers_settings(monkeypatch: pytest.MonkeyPatch, migrations_pg_dsn: str):
    async_dsn = migrations_pg_dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    monkeypatch.setenv("WORKERS_DATABASE_URL", async_dsn)
    from workers.config import get_settings, reset_settings_cache

    reset_settings_cache()
    yield get_settings()
    reset_settings_cache()


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


def _agent(ids, task="task1") -> dict[str, str]:
    from api_server.auth.internal_agent import mint_agent_token

    token = mint_agent_token(agent_id=ids["agent"], tenant_id=ids["ta"], task_id=ids[task])
    return {"Authorization": f"Bearer {token}"}


async def _job(settings) -> dict:
    from workers.maintenance import _maintain_memory_lifecycle_async

    return await _maintain_memory_lifecycle_async(settings)


async def _set_setting(dsn: str, key: str, value_json: str) -> None:
    await _sql(
        dsn,
        "INSERT INTO platform_settings (key, value) VALUES ($1, $2::jsonb)"
        " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        key, value_json,
    )  # fmt: skip
    from api_server.db.platform_settings import invalidate_platform_setting_cache

    await invalidate_platform_setting_cache(key)


# ---------------------------------------------------------------------------
# 1. El primer archivado respeta el inicio de la medición del uso
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_first_pass_does_not_archive_history_older_than_the_usage_tracking_start(
    configured_app, migrations_pg_dsn, workers_settings
) -> None:
    from datetime import UTC, datetime, timedelta

    ids = await _seed(migrations_pg_dsn, usage_since=None)  # default: la fecha de la 0151
    await _mem(migrations_pg_dsn, ids, "old", days=200)  # recall_count = 0 por la migración
    out = await _job(workers_settings)
    assert out["archived"] == 0, out  # nada previo a la medición caduca...
    ten_days_ago = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    await _set_setting(migrations_pg_dsn, "memory.usage_tracking_since", json.dumps(ten_days_ago))
    assert (await _job(workers_settings))["archived"] == 0  # ...ni con 10 días de medición
    hundred = (datetime.now(UTC) - timedelta(days=100)).isoformat()
    await _set_setting(migrations_pg_dsn, "memory.usage_tracking_since", json.dumps(hundred))
    out = await _job(workers_settings)  # 100 > 90 días de medición real: ahora sí
    assert out["archived"] == 1, out
    rows = await _sql(migrations_pg_dsn, "SELECT archive_reason FROM memory_entries")
    assert rows[0]["archive_reason"] == "expired_unused"


# ---------------------------------------------------------------------------
# 2. Feedback: un run no dispara la disputa
# ---------------------------------------------------------------------------
async def _vote(app, ids, key, verdict, *, task="task1"):
    async with _client(app) as c:
        return await c.post(
            "/internal/agent/memory-feedback",
            json={"memory_id": str(ids[key]), "verdict": verdict},
            headers=_agent(ids, task),
        )


async def _human_vote(app, ids, key, verdict, who="admin"):
    h = await _headers(ids[who], ids["ta"])
    async with _client(app) as c:
        return await c.post(f"/memories/{ids[key]}/feedback", json={"verdict": verdict}, headers=h)


async def _disputed(dsn, ids, key) -> bool:
    rows = await _sql(dsn, "SELECT disputed_at FROM memory_entries WHERE id=$1", ids[key])
    return rows[0]["disputed_at"] is not None


@pytest.mark.asyncio
async def test_one_agent_run_marking_wrong_does_not_dispute_but_two_runs_do(
    configured_app, migrations_pg_dsn
) -> None:
    ids = await _seed(migrations_pg_dsn)
    await _mem(migrations_pg_dsn, ids, "proj_a", scope="global", mtype="semantic")
    r = await _vote(configured_app, ids, "proj_a", "wrong", task="task1")
    assert r.status_code == 200 and r.json()["harmful_count"] == 1
    assert r.json()["disputed"] is False
    assert not await _disputed(migrations_pg_dsn, ids, "proj_a")
    r = await _vote(configured_app, ids, "proj_a", "outdated", task="task2")
    assert r.json()["harmful_count"] == 2 and r.json()["disputed"] is True


@pytest.mark.asyncio
async def test_the_threshold_is_a_bounded_platform_setting(
    configured_app, migrations_pg_dsn, workers_settings
) -> None:
    ids = await _seed(migrations_pg_dsn)
    await _mem(migrations_pg_dsn, ids, "proj_a")
    await _set_setting(migrations_pg_dsn, "memory.dispute_min_agent_voters", "1")
    r = await _vote(configured_app, ids, "proj_a", "wrong")
    assert r.json()["disputed"] is True

    from api_server.db.platform_settings import get_memory_dispute_min_agent_voters
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from workers.db import worker_engine

    engine = worker_engine(workers_settings)
    try:
        async with async_sessionmaker(engine)() as s:
            assert await get_memory_dispute_min_agent_voters(s) == 1
            await _set_setting(migrations_pg_dsn, "memory.dispute_min_agent_voters", "9999")
            assert await get_memory_dispute_min_agent_voters(s) == 10  # acotado
            await _set_setting(migrations_pg_dsn, "memory.dispute_min_agent_voters", "0")
            assert await get_memory_dispute_min_agent_voters(s) == 1
            await _set_setting(migrations_pg_dsn, "memory.dispute_min_agent_voters", '"x"')
            assert await get_memory_dispute_min_agent_voters(s) == 2  # ilegible: default
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_human_negative_vote_disputes_by_itself_and_a_human_useful_undisputes_agents(
    configured_app, migrations_pg_dsn
) -> None:
    ids = await _seed(migrations_pg_dsn)
    await _mem(migrations_pg_dsn, ids, "proj_a")
    await _mem(migrations_pg_dsn, ids, "proj_b")
    r = await _human_vote(configured_app, ids, "proj_a", "wrong")
    assert r.status_code == 200 and r.json()["disputed"] is True
    # proj_b: disputada por DOS runs de agentes; un `useful` humano la des-disputa
    await _vote(configured_app, ids, "proj_b", "wrong", task="task1")
    await _vote(configured_app, ids, "proj_b", "wrong", task="task2")
    assert await _disputed(migrations_pg_dsn, ids, "proj_b")
    r = await _human_vote(configured_app, ids, "proj_b", "useful")
    assert r.json()["disputed"] is False and r.json()["harmful_count"] == 2
    # un voto de agente posterior y aislado no la vuelve a disputar (los previos: revisados)
    await _vote(configured_app, ids, "proj_b", "wrong", task="task1")
    assert not await _disputed(migrations_pg_dsn, ids, "proj_b")


@pytest.mark.asyncio
async def test_rejecting_a_compaction_clears_the_dispute_and_only_later_votes_reopen_it(
    configured_app, migrations_pg_dsn, workers_settings
) -> None:
    ids = await _seed(migrations_pg_dsn)
    await _mem(migrations_pg_dsn, ids, "proj_a")
    await _vote(configured_app, ids, "proj_a", "wrong", task="task1")
    await _vote(configured_app, ids, "proj_a", "wrong", task="task2")
    assert (await _job(workers_settings))["proposals"] == 1
    pid = (await _sql(migrations_pg_dsn, "SELECT id FROM memory_compaction_proposals"))[0]["id"]
    admin = await _headers(ids["admin"], ids["ta"])
    async with _client(configured_app) as c:
        r = await c.post(f"/memories/compactions/{pid}/reject", headers=admin)
    assert r.status_code == 200
    assert not await _disputed(migrations_pg_dsn, ids, "proj_a")
    reviewed = await _sql(
        migrations_pg_dsn,
        "SELECT count(*) AS n FROM memory_feedback WHERE reviewed_at IS NOT NULL",
    )
    assert reviewed[0]["n"] == 2
    # un voto posterior aislado no alcanza (los dos anteriores ya están revisados)...
    await _vote(configured_app, ids, "proj_a", "wrong", task="task1")
    assert not await _disputed(migrations_pg_dsn, ids, "proj_a")
    # ...pero dos runs posteriores, sí
    await _vote(configured_app, ids, "proj_a", "wrong", task="task2")
    assert await _disputed(migrations_pg_dsn, ids, "proj_a")


@pytest.mark.asyncio
async def test_compaction_groups_team_and_global_and_never_private(
    configured_app, migrations_pg_dsn, workers_settings
) -> None:
    ids = await _seed(migrations_pg_dsn)
    await _mem(migrations_pg_dsn, ids, "proj_a", disputed=True)
    await _mem(migrations_pg_dsn, ids, "team_a", scope="team_shared", disputed=True)
    await _mem(migrations_pg_dsn, ids, "team_b", scope="team_shared", disputed=True)
    await _mem(migrations_pg_dsn, ids, "glob_a", scope="global", disputed=True)
    await _mem(migrations_pg_dsn, ids, "glob_b", scope="global", disputed=True)
    await _mem(migrations_pg_dsn, ids, "priv", scope="private", disputed=True, user="admin")
    assert (await _job(workers_settings))["proposals"] == 3
    rows = await _sql(
        migrations_pg_dsn,
        "SELECT id, scope, project_id, team_id, memory_ids FROM memory_compaction_proposals",
    )
    by_scope = {r["scope"]: r for r in rows}
    assert set(by_scope) == {"project_shared", "team_shared", "global"}
    assert by_scope["team_shared"]["team_id"] == ids["team"]
    assert by_scope["global"]["project_id"] is None and by_scope["global"]["team_id"] is None
    assert sorted(json.loads(by_scope["team_shared"]["memory_ids"])) == sorted(
        [str(ids["team_a"]), str(ids["team_b"])]
    )
    everything = {i for r in rows for i in json.loads(r["memory_ids"])}
    assert str(ids["priv"]) not in everything
    # confirmar la de equipo archiva sólo sus dos memorias
    admin = await _headers(ids["admin"], ids["ta"])
    async with _client(configured_app) as c:
        ok = await c.post(
            f"/memories/compactions/{by_scope['team_shared']['id']}/confirm",
            json={"memory_ids": [str(ids["team_a"]), str(ids["team_b"])]},
            headers=admin,
        )
    assert ok.status_code == 200 and ok.json()["archived"] == 2
    archived = await _sql(
        migrations_pg_dsn, "SELECT id FROM memory_entries WHERE archived_at IS NOT NULL"
    )
    assert {r["id"] for r in archived} == {ids["team_a"], ids["team_b"]}


# ---------------------------------------------------------------------------
# 5. Uso: archivadas no cuentan; idempotente con llamadas concurrentes
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_usage_skips_archived_and_is_idempotent_under_concurrency(
    configured_app, migrations_pg_dsn, workers_settings
) -> None:
    from api_server.memorizer.usage import record_recall_usage
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from workers.db import worker_engine

    ids = await _seed(migrations_pg_dsn)
    await _mem(migrations_pg_dsn, ids, "proj_a")
    await _mem(migrations_pg_dsn, ids, "proj_b")
    await _sql(
        migrations_pg_dsn,
        "UPDATE memory_entries SET archived_at = now() WHERE id=$1",
        ids["proj_b"],
    )

    async def get_one():
        async with _client(configured_app) as c:
            return await c.post(
                "/internal/agent/memory-get",
                json={"ids": [str(ids["proj_a"])]},
                headers=_agent(ids, "task1"),
            )

    results = await asyncio.gather(*[get_one() for _ in range(6)])
    assert all(r.status_code == 200 for r in results)
    rows = await _sql(migrations_pg_dsn, "SELECT id, recall_count FROM memory_entries")
    counts = {r["id"]: r["recall_count"] for r in rows}
    assert counts[ids["proj_a"]] == 1, counts  # un solo run: una sola cuenta

    engine = worker_engine(workers_settings)
    try:
        async with async_sessionmaker(engine)() as s, s.begin():
            await record_recall_usage(
                s, tenant_id=ids["ta"], memory_ids=[ids["proj_b"]], kind="index",
                agent_id=ids["agent"], execution_id=uuid4(),
            )  # fmt: skip
    finally:
        await engine.dispose()
    rows = await _sql(
        migrations_pg_dsn, "SELECT recall_count FROM memory_entries WHERE id=$1", ids["proj_b"]
    )
    assert rows[0]["recall_count"] == 0  # la archivada no cuenta


# ---------------------------------------------------------------------------
# 6. Compactación: lo que vio el admin; huérfanas auditadas
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_confirm_with_a_different_set_is_409_and_archives_nothing(
    configured_app, migrations_pg_dsn, workers_settings
) -> None:
    ids = await _seed(migrations_pg_dsn)
    await _mem(migrations_pg_dsn, ids, "proj_a", disputed=True)
    await _mem(migrations_pg_dsn, ids, "proj_b", disputed=True)
    await _job(workers_settings)
    pid = (await _sql(migrations_pg_dsn, "SELECT id FROM memory_compaction_proposals"))[0]["id"]
    admin = await _headers(ids["admin"], ids["ta"])
    async with _client(configured_app) as c:
        stale = await c.post(
            f"/memories/compactions/{pid}/confirm",
            json={"memory_ids": [str(ids["proj_a"])]},
            headers=admin,
        )
        extra = await c.post(
            f"/memories/compactions/{pid}/confirm",
            json={"memory_ids": [str(ids["proj_a"]), str(ids["proj_b"]), str(uuid4())]},
            headers=admin,
        )
        missing = await c.post(f"/memories/compactions/{pid}/confirm", json={}, headers=admin)
    assert stale.status_code == 409 and extra.status_code == 409
    assert missing.status_code == 422
    assert (
        await _sql(migrations_pg_dsn, "SELECT 1 FROM memory_entries WHERE archived_at IS NOT NULL")
        == []
    )
    st = await _sql(migrations_pg_dsn, "SELECT status FROM memory_compaction_proposals")
    assert st[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_orphan_pending_proposals_are_deleted_with_an_audit_row(
    configured_app, migrations_pg_dsn, workers_settings
) -> None:
    ids = await _seed(migrations_pg_dsn)
    await _mem(migrations_pg_dsn, ids, "proj_a", disputed=True)
    await _job(workers_settings)
    await _sql(migrations_pg_dsn, "UPDATE memory_entries SET archived_at = now()")
    await _job(workers_settings)
    assert await _sql(migrations_pg_dsn, "SELECT 1 FROM memory_compaction_proposals") == []
    audit = await _sql(
        migrations_pg_dsn,
        "SELECT tenant_id FROM audit_log WHERE action = 'memory.compaction_orphan_deleted'",
    )
    assert len(audit) == 1 and audit[0]["tenant_id"] == ids["ta"]


# ---------------------------------------------------------------------------
# 7. Tandas y auditoría en bloque
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_archive_runs_in_batches_until_exhausted_with_a_total_cap(
    configured_app, migrations_pg_dsn, workers_settings
) -> None:
    from api_server.memorizer.lifecycle import archive_expired
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from workers.db import worker_engine

    ids = await _seed(migrations_pg_dsn, usage_since=None)
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "INSERT INTO memory_entries (id, tenant_id, scope, type, content, project_id,"
            " created_at) SELECT gen_random_uuid(), $1, 'project_shared', 'episodic', 'c' || g,"
            " $2, now() - interval '200 days' FROM generate_series(1, 7) g",
            ids["ta"], ids["p1"],
        )  # fmt: skip
    finally:
        await conn.close()
    engine = worker_engine(workers_settings)
    try:
        async with async_sessionmaker(engine)() as s, s.begin():
            capped = await archive_expired(s, older_than_days=90, limit=2, max_total=5)
        async with async_sessionmaker(engine)() as s, s.begin():
            rest = await archive_expired(s, older_than_days=90, limit=2)
    finally:
        await engine.dispose()
    assert (capped, rest) == (5, 2)
    audit = await _sql(
        migrations_pg_dsn, "SELECT count(*) AS n FROM audit_log WHERE action='memory.archived'"
    )
    assert audit[0]["n"] == 7


# ---------------------------------------------------------------------------
# 8. Migración 0154 e índices
# ---------------------------------------------------------------------------
async def _schema(dsn: str) -> dict:
    idx = {
        r["indexname"]
        for r in await _sql(
            dsn,
            "SELECT indexname FROM pg_indexes"
            " WHERE tablename IN ('memory_entries','memory_compaction_proposals')",
        )
    }
    cols = {
        r["column_name"]
        for r in await _sql(
            dsn,
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name IN ('memory_feedback','memory_compaction_proposals')",
        )
    }
    return {
        "idx": {
            "ix_memory_entries_tenant_source_exec",
            "ix_memory_entries_archive_candidates",
            "uq_memory_compaction_pending_owner",
        }
        <= idx,
        "cols": {"reviewed_at", "scope", "team_id"} <= cols,
    }


def test_migration_0154_is_additive_and_round_trips(alembic_config, migrations_pg_dsn: str) -> None:
    command.upgrade(alembic_config, "head")
    assert asyncio.run(_schema(migrations_pg_dsn)) == {"idx": True, "cols": True}
    command.downgrade(alembic_config, "0153_memory_feedback_archive")
    assert asyncio.run(_schema(migrations_pg_dsn)) == {"idx": False, "cols": False}
    command.upgrade(alembic_config, "head")
    assert asyncio.run(_schema(migrations_pg_dsn)) == {"idx": True, "cols": True}


@pytest.mark.asyncio
async def test_the_queries_can_use_the_new_indexes(configured_app, migrations_pg_dsn) -> None:
    from api_server.memorizer.lifecycle import _ARCHIVE_SQL_WHERE

    where = _ARCHIVE_SQL_WHERE.replace("CAST(:days AS integer)", "90").replace(
        "CAST(:since AS timestamptz)", "'2020-01-01'::timestamptz"
    )
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        async with conn.transaction():
            await conn.execute("SET LOCAL enable_seqscan = off")
            plan = await conn.fetch(
                f"EXPLAIN SELECT id FROM memory_entries WHERE {where} ORDER BY created_at LIMIT 10"
            )
            src = await conn.fetch(
                "EXPLAIN SELECT id FROM memory_entries WHERE tenant_id = gen_random_uuid()"
                " AND source_execution_id = gen_random_uuid()"
            )
    finally:
        await conn.close()
    assert "ix_memory_entries_archive_candidates" in "\n".join(r[0] for r in plan)
    assert "ix_memory_entries_tenant_source_exec" in "\n".join(r[0] for r in src)


# ---------------------------------------------------------------------------
# 9 y 10. 422 sin 500; memory-get tolerante
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_storing_content_that_is_all_private_is_422_not_500(
    configured_app, migrations_pg_dsn
) -> None:
    ids = await _seed(migrations_pg_dsn)
    admin = await _headers(ids["admin"], ids["ta"])
    async with _client(configured_app) as c:
        r = await c.post(
            "/memories",
            json={"content": "<private>mi clave</private>", "scope": "global", "type": "semantic"},
            headers=admin,
        )
        ok = await c.post(
            "/memories",
            json={"content": "algo publico", "scope": "global", "type": "semantic"},
            headers=admin,
        )
    assert r.status_code == 422 and "private" in r.text.lower()
    assert ok.status_code == 201, ok.text


@pytest.mark.asyncio
async def test_memory_get_ignores_malformed_ids_and_reports_them(
    configured_app, migrations_pg_dsn
) -> None:
    ids = await _seed(migrations_pg_dsn)
    await _mem(migrations_pg_dsn, ids, "proj_a")
    async with _client(configured_app) as c:
        good = await c.post(
            "/internal/agent/memory-get",
            json={"ids": ["no-es-uuid", str(ids["proj_a"]), "123"]},
            headers=_agent(ids),
        )
        none_valid = await c.post(
            "/internal/agent/memory-get", json={"ids": ["x", "y"]}, headers=_agent(ids)
        )
        too_many = await c.post(
            "/internal/agent/memory-get",
            json={"ids": [str(uuid4()) for _ in range(21)]},
            headers=_agent(ids),
        )
    assert good.status_code == 200, good.text
    body = good.json()
    assert [m["id"] for m in body["memories"]] == [str(ids["proj_a"])]
    assert body["ignored_ids"] == ["no-es-uuid", "123"]
    assert none_valid.status_code == 422 and too_many.status_code == 422
