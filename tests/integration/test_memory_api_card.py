"""task_mem_20 — API de memoria con la nueva forma, contra Postgres real.

- `GET /memories` acepta `min_recall_count`, `max_recall_count` (0 = «nunca recuperada») y
  `disputed`, y devuelve `last_recalled_at`, `useful_count`, `harmful_count`, `disputed_at`;
- `GET /memories/{id}` es la ficha: procedencia resuelta (nombre del agente, título de la
  ejecución, proyecto) y su línea de tiempo embebida;
- `POST /memories/{id}/feedback` es el voto humano (`useful|wrong|outdated`): un voto por
  persona, reescribible, y `wrong` deja la memoria `disputed` sin borrarla;
- aislamiento: la privada de otra persona y todo lo de otro tenant responden 404 en la
  ficha y en el voto (y NADA se escribe), y no salen en el listado con ningún filtro.
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
_KEYS = (
    "ta", "tb", "alice", "bob", "p1", "pb", "agent", "task", "e1", "e2",
    "m_hot", "m_never", "m_disputed", "m_priv_bob", "m_priv_alice", "m_archived", "m_b",
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
            "INSERT INTO organizations (id, name, slug) VALUES ($1,'A','mc-a'),"
            " ($2,'B','mc-b'), ($3,'P','mc-p')",
            ids["ta"], ids["tb"], _PLATFORM_TENANT_ID,
        )  # fmt: skip
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, full_name) VALUES"
            " ($1,'alice@mc.test','h','Alice'), ($2,'bob@mc.test','h','Bob')",
            ids["alice"], ids["bob"],
        )  # fmt: skip
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES"
            " ($1,$2,$3,'tenant_member'), ($4,$2,$5,'tenant_member')",
            uuid4(), ids["ta"], ids["alice"], uuid4(), ids["bob"],
        )  # fmt: skip
        for key, tenant in (("p1", "ta"), ("pb", "tb")):
            await conn.execute(
                "INSERT INTO projects (id, tenant_id, name) VALUES ($1,$2,$3)",
                ids[key], ids[tenant], f"Proyecto {key}",
            )  # fmt: skip
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, project_id, name, role, system_prompt,"
            " memory_scope, scope) VALUES ($1,$2,$3,'Agente Ficha','backend_dev','x',"
            " 'project_shared','project_local')",
            ids["agent"], ids["ta"], ids["p1"],
        )  # fmt: skip
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, title)"
            " VALUES ($1,$2,$3,'Migrar a asyncpg')",
            ids["task"], ids["ta"], ids["p1"],
        )  # fmt: skip
        for ex, days in (("e1", 2), ("e2", 1)):
            await conn.execute(
                "INSERT INTO executions (id, tenant_id, task_id, status, output, steps_log,"
                " created_at) VALUES ($1,$2,$3,'done','o','[]'::jsonb,"
                " now() - make_interval(days => $4))",
                ids[ex], ids["ta"], ids["task"], days,
            )  # fmt: skip

        async def mem(key, tenant, content, *, scope="project_shared", recalled=0, useful=0,
                      harmful=0, disputed=False, archived=False, project="p1", user=None,
                      execution=None, agent=None):  # fmt: skip
            owner_p = ids[project] if scope == "project_shared" and project else None
            owner_u = ids[user] if scope == "private" else None
            await conn.execute(
                "INSERT INTO memory_entries (id, tenant_id, scope, type, content, project_id,"
                " user_id, agent_id, source_execution_id, recall_count, last_recalled_at,"
                " useful_count, harmful_count, disputed_at, archived_at, archive_reason)"
                " VALUES ($1,$2,$3,'semantic',$4,$5,$6,$7,$8,$9::int,"
                " CASE WHEN $9::int > 0 THEN now() ELSE NULL END, $10,$11,"
                " CASE WHEN $12::bool THEN now() ELSE NULL END,"
                " CASE WHEN $13::bool THEN now() ELSE NULL END,"
                " CASE WHEN $13::bool THEN 'expired_unused' ELSE NULL END)",
                ids[key], ids[tenant], scope, content, owner_p, owner_u,
                ids[agent] if agent else None, ids[execution] if execution else None,
                recalled, useful, harmful, disputed, archived,
            )  # fmt: skip

        await mem("m_hot", "ta", "usada con asyncpg", recalled=3, useful=2,
                  agent="agent", execution="e1")  # fmt: skip
        await mem("m_never", "ta", "nunca recuperada", execution="e2", agent="agent")
        await mem("m_disputed", "ta", "dada por falsa", recalled=1, harmful=1, disputed=True)
        await mem("m_priv_bob", "ta", "privada de Bob", scope="private", user="bob", recalled=5)
        await mem("m_priv_alice", "ta", "privada de Alice", scope="private", user="alice")
        await mem("m_archived", "ta", "archivada", archived=True)
        await mem("m_b", "tb", "de otro tenant", recalled=9, project="pb")
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

    app = create_app()
    try:
        yield app
    finally:
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


async def _feedback_rows(dsn: str) -> list[tuple[str, str]]:
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch("SELECT voter_key, verdict FROM memory_feedback ORDER BY voter_key")
        return sorted((r["voter_key"], r["verdict"]) for r in rows)
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# GET /memories — filtros y campos nuevos
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_list_returns_usage_and_utility_fields(configured_app, migrations_pg_dsn) -> None:
    ids = await _seed(migrations_pg_dsn)
    h = await _headers(ids["alice"], ids["ta"])
    async with _client(configured_app) as c:
        rows = {r["id"]: r for r in (await c.get("/memories", headers=h)).json()}
    hot = rows[str(ids["m_hot"])]
    assert hot["recall_count"] == 3 and hot["useful_count"] == 2 and hot["harmful_count"] == 0
    assert hot["last_recalled_at"] is not None and hot["disputed_at"] is None
    never = rows[str(ids["m_never"])]
    assert never["recall_count"] == 0 and never["last_recalled_at"] is None
    bad = rows[str(ids["m_disputed"])]
    assert bad["harmful_count"] == 1 and bad["disputed"] is True and bad["disputed_at"]


@pytest.mark.asyncio
async def test_list_filters_by_recall_count_and_disputed(configured_app, migrations_pg_dsn) -> None:
    ids = await _seed(migrations_pg_dsn)
    h = await _headers(ids["alice"], ids["ta"])
    async with _client(configured_app) as c:

        async def got(qs: str) -> set[str]:
            r = await c.get(f"/memories?{qs}", headers=h)
            assert r.status_code == 200, r.text
            return {m["id"] for m in r.json()}

        # «nunca recuperada» = recall_count = 0 (las privadas ajenas y el otro tenant, fuera)
        assert await got("max_recall_count=0") == {
            str(ids["m_never"]),
            str(ids["m_priv_alice"]),
        }
        assert await got("min_recall_count=1") == {str(ids["m_hot"]), str(ids["m_disputed"])}
        assert await got("min_recall_count=2&max_recall_count=3") == {str(ids["m_hot"])}
        assert await got("disputed=true") == {str(ids["m_disputed"])}
        assert str(ids["m_disputed"]) not in await got("disputed=false")
        # la privada de Bob y la de otro tenant no salen con NINGÚN filtro
        for qs in ("min_recall_count=5", "min_recall_count=9", "min_recall_count=0&scope=private"):
            found = await got(qs)
            assert str(ids["m_priv_bob"]) not in found and str(ids["m_b"]) not in found
        assert await got("min_recall_count=5") == set()
        r = await c.get("/memories?min_recall_count=-1", headers=h)
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# GET /memories/{id} — la ficha
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_card_resolves_provenance_and_embeds_timeline(
    configured_app, migrations_pg_dsn
) -> None:
    ids = await _seed(migrations_pg_dsn)
    h = await _headers(ids["alice"], ids["ta"])
    async with _client(configured_app) as c:
        r = await c.get(f"/memories/{ids['m_hot']}", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == str(ids["m_hot"])
    assert body["agent_name"] == "Agente Ficha"
    assert body["project_name"] == "Proyecto p1"
    assert body["execution_title"] == "Migrar a asyncpg"
    assert body["useful_count"] == 2 and body["recall_count"] == 3
    assert body["my_verdict"] is None
    tl = body["timeline"]
    assert tl["anchor_memory_id"] == str(ids["m_hot"])
    assert str(ids["e1"]) in [e["execution_id"] for e in tl["executions"]]


@pytest.mark.asyncio
async def test_card_hides_foreign_private_and_other_tenant(
    configured_app, migrations_pg_dsn
) -> None:
    ids = await _seed(migrations_pg_dsn)
    h = await _headers(ids["alice"], ids["ta"])
    async with _client(configured_app) as c:
        assert (await c.get(f"/memories/{ids['m_priv_alice']}", headers=h)).status_code == 200
        for key in ("m_priv_bob", "m_b"):
            r = await c.get(f"/memories/{ids[key]}", headers=h)
            assert r.status_code == 404, (key, r.text)
        assert (await c.get(f"/memories/{uuid4()}", headers=h)).status_code == 404
        # una archivada se puede abrir (el visor la enseña con filtro)
        assert (await c.get(f"/memories/{ids['m_archived']}", headers=h)).status_code == 200


# ---------------------------------------------------------------------------
# POST /memories/{id}/feedback — el voto humano
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_human_wrong_vote_disputes_without_deleting_one_vote_per_person(
    configured_app, migrations_pg_dsn
) -> None:
    ids = await _seed(migrations_pg_dsn)
    ha = await _headers(ids["alice"], ids["ta"])
    hb = await _headers(ids["bob"], ids["ta"])
    url = f"/memories/{ids['m_never']}/feedback"
    async with _client(configured_app) as c:
        r = await c.post(url, headers=ha, json={"verdict": "wrong", "note": "obsoleto"})
        assert r.status_code == 200, r.text
        assert r.json()["disputed"] is True and r.json()["harmful_count"] == 1
        # repetir el voto no suma; cambiar de opinión lo reescribe
        r = await c.post(url, headers=ha, json={"verdict": "wrong"})
        assert r.json()["harmful_count"] == 1
        r = await c.post(url, headers=ha, json={"verdict": "useful"})
        assert r.json()["harmful_count"] == 0 and r.json()["useful_count"] == 1
        assert r.json()["disputed"] is False
        # otra persona suma su propio voto
        r = await c.post(url, headers=hb, json={"verdict": "outdated"})
        assert r.json()["useful_count"] == 1 and r.json()["harmful_count"] == 1
        assert r.json()["disputed"] is True
        card = (await c.get(f"/memories/{ids['m_never']}", headers=hb)).json()
        assert card["my_verdict"] == "outdated" and card["disputed"] is True
    assert await _feedback_rows(migrations_pg_dsn) == sorted(
        [(f"user:{ids['alice']}", "useful"), (f"user:{ids['bob']}", "outdated")]
    )


@pytest.mark.asyncio
async def test_feedback_404_on_foreign_private_other_tenant_and_writes_nothing(
    configured_app, migrations_pg_dsn
) -> None:
    ids = await _seed(migrations_pg_dsn)
    h = await _headers(ids["alice"], ids["ta"])
    async with _client(configured_app) as c:
        for key in ("m_priv_bob", "m_b", "m_archived"):
            r = await c.post(f"/memories/{ids[key]}/feedback", headers=h, json={"verdict": "wrong"})
            assert r.status_code == 404, (key, r.text)
        r = await c.post(
            f"/memories/{ids['m_never']}/feedback", headers=h, json={"verdict": "bogus"}
        )
        assert r.status_code == 422
        # la propia privada sí se puede votar
        r = await c.post(
            f"/memories/{ids['m_priv_alice']}/feedback", headers=h, json={"verdict": "useful"}
        )
        assert r.status_code == 200
    assert await _feedback_rows(migrations_pg_dsn) == [(f"user:{ids['alice']}", "useful")]
