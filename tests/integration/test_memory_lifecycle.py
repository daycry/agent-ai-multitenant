"""task_mem_11 — caducidad y compactación de memorias, contra Postgres real.

El job `workers.maintain_memory_lifecycle`:
  - ARCHIVA (soft, nunca borra) las `episodic` sin `kind = summary`, con `recall_count = 0` y de
    más de N días; deja fila de `audit_log` por memoria; es idempotente y N es un ajuste;
  - PROPONE una revisión de compactación por proyecto con sus memorias `disputed`: no aplica
    nada, refresca la pendiente en vez de duplicarla y no re-propone lo que el operador rechazó.

Y sobre lo archivado: no aparece en el recall, en `memory_get` ni en la línea de tiempo del
agente, pero SÍ en el visor (`GET /memories?archived=true`). Confirmar una compactación es cosa
de `tenant_admin` (un miembro recibe 403; la de otro tenant, 404) y archiva con motivo.
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
_SETTING_KEYS = ("memory.archive_after_days",)
_KEYS = (
    "ta", "tb", "admin", "member", "p1", "p2", "pb", "agent", "task", "e1",
    "old", "old_fact", "summary", "recalled", "semantic", "young", "private", "cortex", "deleted",
    "mid_age", "b_old",
    "d1", "d2", "d3", "d_b", "d_keep",
)  # fmt: skip


@pytest.fixture(autouse=True)
def _redis_client_bound_to_this_loop():
    from api_server.auth.deps import reset_redis_cache

    reset_redis_cache()
    yield
    reset_redis_cache()


async def _seed(dsn: str) -> dict[str, UUID]:
    ids = {k: uuid4() for k in _KEYS}
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE memory_feedback, memory_compaction_proposals, memory_recall_log,"
            " memory_entries, executions, tasks, plans, conversations, projects, agents, teams,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute("DELETE FROM platform_settings WHERE key LIKE 'memory.%'")
        # El histórico sembrado es anterior a la medición del uso: para que caduque hay que
        # adelantar el inicio de la medición (por defecto es 2026-09-30, ver platform_settings).
        await conn.execute(
            "INSERT INTO platform_settings (key, value)"
            " VALUES ('memory.usage_tracking_since', '\"2020-01-01T00:00:00+00:00\"'::jsonb)"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1,'A','lc-a'),"
            " ($2,'B','lc-b'), ($3,'P','lc-p')",
            ids["ta"], ids["tb"], _PLATFORM_TENANT_ID,
        )  # fmt: skip
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, full_name) VALUES"
            " ($1,'admin@lc.test','h','Admin'), ($2,'member@lc.test','h','Member')",
            ids["admin"], ids["member"],
        )  # fmt: skip
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES"
            " ($1,$2,$3,'tenant_admin'), ($4,$2,$5,'tenant_member')",
            uuid4(), ids["ta"], ids["admin"], uuid4(), ids["member"],
        )  # fmt: skip
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
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, title) VALUES ($1,$2,$3,'T')",
            ids["task"], ids["ta"], ids["p1"],
        )  # fmt: skip
        await conn.execute(
            "INSERT INTO executions (id, tenant_id, task_id, status, output, steps_log)"
            " VALUES ($1,$2,$3,'done','o','[]'::jsonb)",
            ids["e1"], ids["ta"], ids["task"],
        )  # fmt: skip

        async def mem(key, tenant, content, *, days, scope="project_shared", mtype="episodic",
                      kind=None, recalled=0, project="p1", user=None, meta="{}",
                      deleted=False, disputed=False):  # fmt: skip
            owner = {
                "project_shared": (ids[project] if project else None, None),
                "global": (None, None),
                "private": (None, ids[user] if user else None),
            }[scope]
            await conn.execute(
                "INSERT INTO memory_entries (id, tenant_id, scope, type, kind, content,"
                " project_id, user_id, recall_count, metadata, created_at, updated_at,"
                " deleted_at, disputed_at, harmful_count)"
                " VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,"
                " now() - make_interval(days => $11), now() - make_interval(days => $11),"
                " CASE WHEN $12 THEN now() ELSE NULL END,"
                " CASE WHEN $13 THEN now() ELSE NULL END, CASE WHEN $13 THEN 1 ELSE 0 END)",
                ids[key], ids[tenant], scope, mtype, kind, content,
                owner[0], owner[1], recalled, meta, days, deleted, disputed,
            )  # fmt: skip

        # --- caducidad -----------------------------------------------------
        await mem("old", "ta", "vieja sin recuperar zafiro", days=100)
        await mem("old_fact", "ta", "vieja con kind fact zafiro", days=100, kind="fact")
        await mem("summary", "ta", "resumen viejo zafiro", days=100, kind="summary")
        await mem("recalled", "ta", "vieja pero recuperada zafiro", days=100, recalled=2)
        await mem("semantic", "ta", "regla semantica zafiro", days=100, mtype="semantic")
        await mem("young", "ta", "reciente zafiro", days=10)
        await mem("private", "ta", "privada de una persona", days=100, scope="private",
                  user="admin", project=None)  # fmt: skip
        await mem("cortex", "ta", "memoria del cortex zafiro", days=100, scope="global",
                  project=None, meta='{"cortex": true}')  # fmt: skip
        await mem("deleted", "ta", "borrada zafiro", days=100, deleted=True)
        await mem("mid_age", "ta", "cincuenta dias zafiro", days=50)
        await mem("b_old", "tb", "vieja de otro tenant zafiro", days=100, project="pb")
        # --- compactación: disputadas (recuperadas, para que la caducidad no las archive) ----
        await mem("d1", "ta", "falsa uno", days=5, recalled=1, disputed=True, project="p1")
        await mem("d2", "ta", "falsa dos", days=6, recalled=1, disputed=True, project="p1")
        await mem("d3", "ta", "falsa tres", days=7, recalled=1, disputed=True, project="p2")
        await mem("d_b", "tb", "falsa de B", days=5, recalled=1, disputed=True, project="pb")
        await mem("d_keep", "ta", "sana", days=5, recalled=1, project="p1")
    finally:
        await conn.close()
    return ids


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


def _agent_headers(ids: dict[str, UUID]) -> dict[str, str]:
    from api_server.auth.internal_agent import mint_agent_token

    token = mint_agent_token(agent_id=ids["agent"], tenant_id=ids["ta"], task_id=ids["task"])
    return {"Authorization": f"Bearer {token}"}


async def _run_job(settings) -> dict:
    from workers.maintenance import _maintain_memory_lifecycle_async

    return await _maintain_memory_lifecycle_async(settings)


async def _archived(dsn: str) -> dict[UUID, str]:
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT id, archive_reason FROM memory_entries WHERE archived_at IS NOT NULL"
        )
        return {r["id"]: r["archive_reason"] for r in rows}
    finally:
        await conn.close()


async def _set_archive_days(dsn: str, days: int) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO platform_settings (key, value)"
            " VALUES ('memory.archive_after_days', $1::jsonb)"
            " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            str(days),
        )
    finally:
        await conn.close()
    from api_server.db.platform_settings import invalidate_platform_setting_cache

    await invalidate_platform_setting_cache("memory.archive_after_days")


# ---------------------------------------------------------------------------
# Selección: qué archiva y qué no (las mismas reglas que el unit, contra SQL)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_job_archives_exactly_the_expired_episodic_memories(
    configured_app, migrations_pg_dsn, workers_settings
) -> None:
    ids = await _seed(migrations_pg_dsn)
    out = await _run_job(workers_settings)
    assert out["archived"] == 3 and out["older_than_days"] == 90, out
    archived = await _archived(migrations_pg_dsn)
    assert set(archived) == {ids["old"], ids["old_fact"], ids["b_old"]}
    assert set(archived.values()) == {"expired_unused"}
    # No archiva: resumen, recuperada, semantic, reciente, privada, del córtex, borrada, ni
    # la de 50 días (por debajo del umbral por defecto).
    for keep in ("summary", "recalled", "semantic", "young", "private", "cortex", "deleted",
                 "mid_age"):  # fmt: skip
        assert ids[keep] not in archived, keep


@pytest.mark.asyncio
async def test_job_never_deletes_and_leaves_one_audit_row_per_memory(
    configured_app, migrations_pg_dsn, workers_settings
) -> None:
    ids = await _seed(migrations_pg_dsn)
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        before = await conn.fetchval("SELECT count(*) FROM memory_entries")
        live_before = await conn.fetchval(
            "SELECT count(*) FROM memory_entries WHERE deleted_at IS NULL"
        )
    finally:
        await conn.close()
    await _run_job(workers_settings)
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        assert await conn.fetchval("SELECT count(*) FROM memory_entries") == before
        # `deleted_at` sigue como estaba: archivar no es borrar
        assert (
            await conn.fetchval("SELECT count(*) FROM memory_entries WHERE deleted_at IS NULL")
            == live_before
        )
        audit = await conn.fetch(
            "SELECT tenant_id, resource_id, changes->>'reason' AS reason"
            " FROM audit_log WHERE action = 'memory.archived'"
        )
    finally:
        await conn.close()
    assert {(r["resource_id"], r["tenant_id"]) for r in audit} == {
        (ids["old"], ids["ta"]), (ids["old_fact"], ids["ta"]), (ids["b_old"], ids["tb"]),
    }  # fmt: skip
    assert {r["reason"] for r in audit} == {"expired_unused"}


@pytest.mark.asyncio
async def test_job_is_idempotent_and_the_threshold_is_a_platform_setting(
    configured_app, migrations_pg_dsn, workers_settings
) -> None:
    ids = await _seed(migrations_pg_dsn)
    assert (await _run_job(workers_settings))["archived"] == 3
    assert (await _run_job(workers_settings))["archived"] == 0  # idempotente
    assert ids["mid_age"] not in await _archived(migrations_pg_dsn)
    await _set_archive_days(migrations_pg_dsn, 30)  # 50 días > 30: ahora sí caduca
    out = await _run_job(workers_settings)
    assert out["older_than_days"] == 30
    assert out["archived"] == 1
    assert ids["mid_age"] in await _archived(migrations_pg_dsn)
    assert ids["young"] not in await _archived(migrations_pg_dsn)  # 10 días: no


# ---------------------------------------------------------------------------
# Lo archivado: fuera del recall, dentro del visor con filtro
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_archived_is_out_of_the_recall_but_in_the_viewer_with_the_filter(
    configured_app, migrations_pg_dsn, workers_settings
) -> None:
    ids = await _seed(migrations_pg_dsn)
    # (sin recall previo: recuperar una memoria le pone `recall_count` > 0 y dejaría de caducar)
    await _run_job(workers_settings)
    admin = await _headers(ids["admin"], ids["ta"])
    async with _client(configured_app) as c:
        after = await c.post(
            "/internal/agent/memory-recall",
            json={"query": "zafiro", "limit": 20},
            headers=_agent_headers(ids),
        )
        get = await c.post(
            "/internal/agent/memory-get",
            json={"ids": [str(ids["old"]), str(ids["young"])]},
            headers=_agent_headers(ids),
        )
        tl = await c.post(
            "/internal/agent/memory-timeline",
            json={"memory_id": str(ids["old"])},
            headers=_agent_headers(ids),
        )
        listed = await c.get("/memories", params={"limit": 100}, headers=admin)
        only_archived = await c.get(
            "/memories", params={"archived": "true", "limit": 100}, headers=admin
        )
        counts = await c.get("/memories/counts", headers=admin)
        public_tl = await c.get(f"/memories/{ids['old']}/timeline", headers=admin)
        cited = await c.get("/memories", params={"id_prefix": str(ids["old"])[:8]}, headers=admin)
    recalled = {h["id"] for h in after.json()["hits"]}
    assert str(ids["old"]) not in recalled and str(ids["old_fact"]) not in recalled
    assert str(ids["young"]) in recalled and str(ids["summary"]) in recalled  # lo vivo sigue
    assert [m["id"] for m in get.json()["memories"]] == [str(ids["young"])]  # no la archivada
    assert tl.status_code == 404  # un agente no abre la línea de tiempo de una archivada

    assert str(ids["old"]) not in {m["id"] for m in listed.json()}  # oculta por defecto
    arch = {m["id"]: m for m in only_archived.json()}
    assert set(arch) == {str(ids["old"]), str(ids["old_fact"])}  # B no se cuela
    assert arch[str(ids["old"])]["archive_reason"] == "expired_unused"
    assert arch[str(ids["old"])]["archived_at"] is not None
    assert counts.json()["all"] == len(listed.json())  # el contador es el del listado
    assert public_tl.status_code == 200  # el visor humano sí abre la ficha
    assert [m["id"] for m in cited.json()] == [str(ids["old"])]  # una cita antigua resuelve


# ---------------------------------------------------------------------------
# Compactación de disputadas
# ---------------------------------------------------------------------------
async def _proposals(dsn: str, status: str = "pending") -> list[asyncpg.Record]:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetch(
            "SELECT id, tenant_id, project_id, status, memory_ids, summary"
            " FROM memory_compaction_proposals WHERE status=$1 ORDER BY project_id",
            status,
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_job_proposes_one_review_per_project_without_applying_it(
    configured_app, migrations_pg_dsn, workers_settings
) -> None:
    import json

    ids = await _seed(migrations_pg_dsn)
    out = await _run_job(workers_settings)
    assert out["proposals"] == 3, out
    pend = await _proposals(migrations_pg_dsn)
    by_project = {r["project_id"]: r for r in pend}
    assert set(by_project) == {ids["p1"], ids["p2"], ids["pb"]}
    assert sorted(json.loads(by_project[ids["p1"]]["memory_ids"])) == sorted(
        [str(ids["d1"]), str(ids["d2"])]
    )
    assert by_project[ids["p1"]]["tenant_id"] == ids["ta"]
    assert by_project[ids["pb"]]["tenant_id"] == ids["tb"]
    assert str(ids["d1"])[:8] in by_project[ids["p1"]]["summary"]
    # NO aplica nada: las disputadas siguen vivas y sin archivar
    archived = await _archived(migrations_pg_dsn)
    assert not ({ids["d1"], ids["d2"], ids["d3"], ids["d_b"]} & set(archived))
    # refresca, no duplica
    await _run_job(workers_settings)
    assert len(await _proposals(migrations_pg_dsn)) == 3


@pytest.mark.asyncio
async def test_pending_review_is_not_touched_and_new_disputes_wait_for_a_new_one(
    configured_app, migrations_pg_dsn, workers_settings
) -> None:
    """La propuesta pendiente es el conjunto que el admin mira: no se le añade nada; lo nuevo
    espera a una propuesta NUEVA tras la decisión."""
    import json

    ids = await _seed(migrations_pg_dsn)
    await _run_job(workers_settings)
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "UPDATE memory_entries SET disputed_at = now(), harmful_count = 1 WHERE id=$1",
            ids["d_keep"],
        )
    finally:
        await conn.close()
    await _run_job(workers_settings)
    p1 = [r for r in await _proposals(migrations_pg_dsn) if r["project_id"] == ids["p1"]]
    assert len(p1) == 1
    assert sorted(json.loads(p1[0]["memory_ids"])) == sorted([str(ids["d1"]), str(ids["d2"])])
    # tras confirmar, la nueva disputada tiene su propuesta
    admin = await _headers(ids["admin"], ids["ta"])
    async with _client(configured_app) as c:
        ok = await c.post(
            f"/memories/compactions/{p1[0]['id']}/confirm",
            json={"memory_ids": [str(ids["d1"]), str(ids["d2"])]},
            headers=admin,
        )
    assert ok.status_code == 200
    await _run_job(workers_settings)
    again = [r for r in await _proposals(migrations_pg_dsn) if r["project_id"] == ids["p1"]]
    assert len(again) == 1 and json.loads(again[0]["memory_ids"]) == [str(ids["d_keep"])]


@pytest.mark.asyncio
async def test_confirm_archives_with_reason_and_audit_and_needs_a_tenant_admin(
    configured_app, migrations_pg_dsn, workers_settings
) -> None:
    ids = await _seed(migrations_pg_dsn)
    await _run_job(workers_settings)
    pend = {r["project_id"]: r["id"] for r in await _proposals(migrations_pg_dsn)}
    admin = await _headers(ids["admin"], ids["ta"])
    member = await _headers(ids["member"], ids["ta"])
    async with _client(configured_app) as c:
        denied_list = await c.get("/memories/compactions", headers=member)
        body = {"memory_ids": [str(ids["d1"]), str(ids["d2"])]}
        denied = await c.post(
            f"/memories/compactions/{pend[ids['p1']]}/confirm", json=body, headers=member
        )
        listed = await c.get("/memories/compactions", headers=admin)
        cross = await c.post(
            f"/memories/compactions/{pend[ids['pb']]}/confirm",
            json={"memory_ids": [str(ids["d_b"])]},
            headers=admin,
        )
        ok = await c.post(
            f"/memories/compactions/{pend[ids['p1']]}/confirm", json=body, headers=admin
        )
        again = await c.post(
            f"/memories/compactions/{pend[ids['p1']]}/confirm", json=body, headers=admin
        )
    assert denied_list.status_code == 403 and denied.status_code == 403
    # sólo las del tenant A: la de B no se ve
    assert {p["project_id"] for p in listed.json()} == {str(ids["p1"]), str(ids["p2"])}
    assert {p["project_name"] for p in listed.json()} == {"Proyecto p1", "Proyecto p2"}
    assert cross.status_code == 404  # una propuesta de OTRO tenant no existe para A
    assert ok.status_code == 200 and ok.json()["archived"] == 2, ok.text
    assert again.status_code == 404  # ya decidida

    archived = await _archived(migrations_pg_dsn)
    assert {k for k, v in archived.items() if v == "disputed_compacted"} == {ids["d1"], ids["d2"]}
    assert (
        ids["d3"] not in archived and ids["d_b"] not in archived and ids["d_keep"] not in archived
    )
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        audit = await conn.fetch(
            "SELECT action, resource_id, user_id FROM audit_log"
            " WHERE (action = 'memory.archived' AND changes->>'reason' = 'disputed_compacted')"
            "    OR action = 'memory.compaction_confirmed'"
        )
        status_row = await conn.fetchval(
            "SELECT status FROM memory_compaction_proposals WHERE id=$1", pend[ids["p1"]]
        )
        still_there = await conn.fetchval(
            "SELECT count(*) FROM memory_entries WHERE id = ANY($1)", [ids["d1"], ids["d2"]]
        )
    finally:
        await conn.close()
    assert status_row == "confirmed"
    assert still_there == 2  # archivadas, NO borradas
    archived_rows = {r["resource_id"] for r in audit if r["action"] == "memory.archived"}
    assert archived_rows == {ids["d1"], ids["d2"]}
    confirmed = [r for r in audit if r["action"] == "memory.compaction_confirmed"]
    assert len(confirmed) == 1 and confirmed[0]["user_id"] == ids["admin"]
    # y las archivadas salen en el visor con el filtro
    async with _client(configured_app) as c:
        only = await c.get("/memories", params={"archived": "true"}, headers=admin)
    assert {m["id"] for m in only.json()} >= {str(ids["d1"]), str(ids["d2"])}


@pytest.mark.asyncio
async def test_rejected_review_keeps_the_memories_and_is_not_proposed_again(
    configured_app, migrations_pg_dsn, workers_settings
) -> None:
    ids = await _seed(migrations_pg_dsn)
    await _run_job(workers_settings)
    pend = {r["project_id"]: r["id"] for r in await _proposals(migrations_pg_dsn)}
    admin = await _headers(ids["admin"], ids["ta"])
    async with _client(configured_app) as c:
        r = await c.post(f"/memories/compactions/{pend[ids['p2']]}/reject", headers=admin)
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert ids["d3"] not in await _archived(migrations_pg_dsn)  # sigue viva
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:  # ...pero ya NO disputada: el rechazo limpia la disputa
        assert (
            await conn.fetchval("SELECT disputed_at FROM memory_entries WHERE id=$1", ids["d3"])
            is None
        )
    finally:
        await conn.close()
    await _run_job(workers_settings)
    still_pending = {r["project_id"] for r in await _proposals(migrations_pg_dsn)}
    assert ids["p2"] not in still_pending  # no se re-propone lo rechazado
    assert len(await _proposals(migrations_pg_dsn, "rejected")) == 1


# ---------------------------------------------------------------------------
# D1 (intento 1) — la cola lleva titulares vivos: el panel no pide la ficha de cada memoria
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_compaction_list_carries_live_headlines_in_one_payload(
    configured_app, migrations_pg_dsn, workers_settings
) -> None:
    ids = await _seed(migrations_pg_dsn)
    await _run_job(workers_settings)
    admin = await _headers(ids["admin"], ids["ta"])
    async with _client(configured_app) as c:
        listed = await c.get("/memories/compactions", headers=admin)
    assert listed.status_code == 200, listed.text
    by_project = {p["project_id"]: p for p in listed.json()}
    p1 = by_project[str(ids["p1"])]
    assert {m["id"]: m["headline"] for m in p1["memories"]} == {
        str(ids["d1"]): "falsa uno",
        str(ids["d2"]): "falsa dos",
    }
    # jamás una memoria de otro tenant
    everything = {m["id"] for p in listed.json() for m in p["memories"]}
    assert str(ids["d_b"]) not in everything


# ---------------------------------------------------------------------------
# B5/D5 (intento 1) — GET /memories/resolve: las citas de un informe, en UNA consulta
# ---------------------------------------------------------------------------
async def _seed_ambiguous(dsn: str, ids: dict[str, UUID]) -> tuple[UUID, UUID]:
    twin_a = UUID("abcdef12-0000-4000-8000-000000000001")
    twin_b = UUID("abcdef12-0000-4000-8000-000000000002")
    conn = await asyncpg.connect(dsn)
    try:
        for mid in (twin_a, twin_b):
            await conn.execute(
                "INSERT INTO memory_entries (id, tenant_id, scope, type, content, project_id)"
                " VALUES ($1,$2,'project_shared','episodic','gemela',$3)",
                mid, ids["ta"], ids["p1"],
            )  # fmt: skip
    finally:
        await conn.close()
    return twin_a, twin_b


@pytest.mark.asyncio
async def test_resolve_maps_each_prefix_to_ok_ambiguous_or_unknown(
    configured_app, migrations_pg_dsn
) -> None:
    ids = await _seed(migrations_pg_dsn)
    await _seed_ambiguous(migrations_pg_dsn, ids)
    admin = await _headers(ids["admin"], ids["ta"])
    member = await _headers(ids["member"], ids["ta"])
    ok_prefix = str(ids["young"])[:8]
    private_prefix = str(ids["private"])[:8]
    other_tenant_prefix = str(ids["b_old"])[:8]
    deleted_prefix = str(ids["deleted"])[:8]
    wanted = ",".join(
        [ok_prefix, "abcdef12", private_prefix, other_tenant_prefix, deleted_prefix, "0badc0de"]
    )
    async with _client(configured_app) as c:
        as_admin = await c.get("/memories/resolve", params={"prefixes": wanted}, headers=admin)
        as_member = await c.get("/memories/resolve", params={"prefixes": wanted}, headers=member)
    assert as_admin.status_code == 200, as_admin.text
    got = {r["prefix"]: r for r in as_admin.json()}
    assert got[ok_prefix]["status"] == "ok"
    assert got[ok_prefix]["memory"]["id"] == str(ids["young"])
    assert got["abcdef12"]["status"] == "ambiguous" and got["abcdef12"].get("memory") is None
    assert got[private_prefix]["status"] == "ok"  # su dueño sí la ve
    assert got[other_tenant_prefix]["status"] == "unknown"  # jamás otro tenant
    assert got[deleted_prefix]["status"] == "unknown"
    assert got["0badc0de"]["status"] == "unknown"
    seen_by_member = {r["prefix"]: r["status"] for r in as_member.json()}
    assert seen_by_member[private_prefix] == "unknown"  # jamás la privada ajena
    assert seen_by_member[ok_prefix] == "ok"


@pytest.mark.asyncio
async def test_resolve_rejects_short_malformed_or_too_many_prefixes(
    configured_app, migrations_pg_dsn
) -> None:
    ids = await _seed(migrations_pg_dsn)
    admin = await _headers(ids["admin"], ids["ta"])
    many = ",".join(f"{i:08x}" for i in range(60))
    async with _client(configured_app) as c:
        short = await c.get("/memories/resolve", params={"prefixes": "abc123"}, headers=admin)
        wild = await c.get("/memories/resolve", params={"prefixes": "abcd%efgh"}, headers=admin)
        too_many = await c.get("/memories/resolve", params={"prefixes": many}, headers=admin)
        empty = await c.get("/memories/resolve", params={"prefixes": ""}, headers=admin)
    assert short.status_code == 422 and wild.status_code == 422
    assert too_many.status_code == 422 and empty.status_code == 422
