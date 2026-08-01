"""prod-03 task_prod03_14 — el motor corre en la RUTA, no solo en la función.

`run_planning_chat_guardrails` y `gate_generate_plan` existían desde el Plan 11
(task_11_22) con su test de integración… que los llamaba directamente. Fuera de
ese test **no tenían un solo llamante**: ni `routers/conversations.py` ni
`routers/plans.py` importaban nada de `api_server.guardrails`, y el roadmap del
Plan 11 daba `task_11_22` por cableada (hallazgo guardrails-9). El texto que un
humano escribe en el chat de planning entraba al modelo sin pasar por el motor.

Por eso este fichero entra por HTTP y no por la función: un test que llama a
`run_planning_chat_guardrails` habría seguido pasando en verde durante los dos
meses en que nadie la llamaba. Es exactamente el modo de fallo que el plan
señala.

Sobre la ubicación: el plan lo nombra `tests/e2e/test_planning_guardrails_route.py`.
Vive en `tests/integration/` porque lo que necesita es la app ASGI + Postgres +
Redis, que es lo que hay aquí; `tests/e2e/` exige runner Docker y stack
completo, y CI no lo corre. Poner el fichero allí lo habría dejado sin ejecutar,
que es cómo un test deja de vigilar.
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

# Un turno que dispara el `topic_restriction` del baseline de planning: nada que
# ver con proyecto, plan, tareas, ingeniería ni estimaciones.
_OFF_TOPIC = "mi abuela hacía una paella con conejo y caracoles que era gloria"
_ON_TOPIC = "planificamos las tareas del proyecto y su estimación"


async def _seed(dsn: str) -> dict[str, UUID]:
    ids = {
        "tenant": uuid4(),
        "admin": uuid4(),
        "project": uuid4(),
        "conversation": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE guardrail_configs, guardrail_events, messages, conversations, plans,"
            " projects, user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            ids["tenant"],
            "Route GR",
            f"route-gr-{ids['tenant'].hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, 'h')",
            ids["admin"],
            f"route-{ids['admin'].hex[:8]}@gr.test",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin')",
            uuid4(),
            ids["tenant"],
            ids["admin"],
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status, is_template)"
            " VALUES ($1, $2, 'Proyecto ruta', 'active', false)",
            ids["project"],
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO conversations (id, tenant_id, project_id, title, current_mode)"
            " VALUES ($1, $2, $3, 'Hilo', 'planning')",
            ids["conversation"],
            ids["tenant"],
            ids["project"],
        )
        # Un mensaje `agent` exige autor (`ck_messages_author_kind_consistency`),
        # y el borrador de «Generar Plan» lo firma el equipo, no el humano.
        ids["agent"] = uuid4()
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, name, role, agent_type, scope, system_prompt)"
            " VALUES ($1, $2, 'PM', 'project_manager', 'ai', 'global_tenant_template',"
            " 'eres el PM')",
            ids["agent"],
            ids["tenant"],
        )
    finally:
        await conn.close()
    return ids


async def _events(dsn: str, tenant_id: UUID) -> list[tuple[str, str, str]]:
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT guardrail_type, hook_point, agent_label FROM guardrail_events"
            " WHERE tenant_id = $1 ORDER BY created_at",
            tenant_id,
        )
        return [(r["guardrail_type"], r["hook_point"], r["agent_label"]) for r in rows]
    finally:
        await conn.close()


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


@pytest.fixture(autouse=True)
def _no_llm_reply(monkeypatch: pytest.MonkeyPatch):
    """El equipo no contesta: aquí se mide el gate, no al modelo.

    `post_message` programa la respuesta del equipo tras commitear. Dejarla
    correr metería una llamada LLM real en un test de guardrails.
    """

    def _noop(**kwargs):
        async def _run() -> None:
            return None

        return _run

    monkeypatch.setattr("api_server.routers.conversations.schedule_reply", _noop)


async def _mint_token(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    await SessionStore(get_redis()).create(
        sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600
    )
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ===========================================================================
# El chat de planning
# ===========================================================================
@pytest.mark.asyncio
async def test_posting_a_message_runs_the_engine_and_records_the_event(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Un turno fuera de tema deja evento — POR LA RUTA, sin tocar la función."""
    ids = await _seed(migrations_pg_dsn)
    token = await _mint_token(ids["admin"], ids["tenant"])

    async with _client(configured_app) as client:
        resp = await client.post(
            f"/conversations/{ids['conversation']}/messages",
            json={"content": _OFF_TOPIC, "author_kind": "user"},
            headers={"Authorization": f"Bearer {token}"},
        )

    # `warn` es advisory: el mensaje se publica igual…
    assert resp.status_code == 201, resp.text
    # …pero queda registrado, que es lo que el dashboard enseña.
    events = await _events(migrations_pg_dsn, ids["tenant"])
    assert ("topic_restriction", "pre_llm", "planning_chat") in events


@pytest.mark.asyncio
async def test_an_on_topic_message_leaves_no_noise(configured_app, migrations_pg_dsn: str) -> None:
    """La guarda de la guarda: si TODO turno dejara evento, el evento no diría nada."""
    ids = await _seed(migrations_pg_dsn)
    token = await _mint_token(ids["admin"], ids["tenant"])

    async with _client(configured_app) as client:
        resp = await client.post(
            f"/conversations/{ids['conversation']}/messages",
            json={"content": _ON_TOPIC, "author_kind": "user"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 201, resp.text
    assert await _events(migrations_pg_dsn, ids["tenant"]) == []


@pytest.mark.asyncio
async def test_a_blocking_tenant_guardrail_stops_the_turn_and_keeps_its_event(
    configured_app, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    """Un `block` de la capa TENANT corta el turno con 422…

    …y el evento del turno bloqueado SOBREVIVE. Es el caso que obliga a
    registrarlo en su propia transacción: el 422 hace rollback de la sesión de
    la request, y el único turno que la plataforma llegó a DETENER sería
    justamente el que no aparecería nunca en el dashboard.
    """
    from api_server.db.guardrail_config import set_layer_config
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    ids = await _seed(migrations_pg_dsn)
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as s, s.begin():
            await set_layer_config(
                s,
                "tenant",
                {
                    "guardrails": {
                        "pre_llm": [
                            {
                                "type": "keyword",
                                "action": "block",
                                "config": {"keywords": ["contraseña maestra"]},
                            }
                        ]
                    }
                },
                tenant_id=ids["tenant"],
            )
    finally:
        await engine.dispose()

    token = await _mint_token(ids["admin"], ids["tenant"])
    async with _client(configured_app) as client:
        resp = await client.post(
            f"/conversations/{ids['conversation']}/messages",
            json={
                "content": "la contraseña maestra del proyecto es esta, apúntala en el plan",
                "author_kind": "user",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error"] == "guardrail_blocked"

    events = await _events(migrations_pg_dsn, ids["tenant"])
    assert ("keyword", "pre_llm", "planning_chat") in events

    # Y el mensaje NO se publicó: bloquear después de persistir no bloquea nada.
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        count = await conn.fetchval(
            "SELECT count(*) FROM messages WHERE conversation_id = $1", ids["conversation"]
        )
    finally:
        await conn.close()
    assert count == 0


# ===========================================================================
# El gate de «Generar Plan»
# ===========================================================================
async def _attach_draft(dsn: str, ids: dict[str, UUID], specification: dict) -> None:
    """Deja en el chat el attachment `finish_planning` que materializa un plan.

    Es el camino REAL de «Generar Plan»: el sub-grafo de planning adjunta la
    `specification` y `POST /plans` con solo `conversation_id` la levanta.
    """
    import json

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO messages (id, tenant_id, conversation_id, author_kind,"
            " author_agent_id, content, mode, attachments)"
            " VALUES ($1, $2, $3, 'agent', $4, 'listo', 'planning', $5::jsonb)",
            uuid4(),
            ids["tenant"],
            ids["conversation"],
            ids["agent"],
            json.dumps(
                [
                    {
                        "kind": "planning_directive",
                        "intent": "finish_planning",
                        "title": "Plan del chat",
                        "specification": specification,
                    }
                ]
            ),
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_generating_a_plan_from_a_malformed_chat_draft_is_blocked_by_the_route(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Un borrador del equipo sin `summary` no llega a persistirse como plan."""
    ids = await _seed(migrations_pg_dsn)
    await _attach_draft(
        migrations_pg_dsn,
        ids,
        {"summary": {}, "tasks": [{"id": "t-1", "title": "Hacer algo"}]},
    )
    token = await _mint_token(ids["admin"], ids["tenant"])

    async with _client(configured_app) as client:
        resp = await client.post(
            f"/projects/{ids['project']}/plans",
            json={"conversation_id": str(ids["conversation"])},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 422, resp.text
    body = resp.json()["detail"]
    assert body["error"] == "plan_draft_rejected"
    assert body["feedback"], "un rechazo sin feedback no le dice al equipo qué arreglar"

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        count = await conn.fetchval("SELECT count(*) FROM plans")
    finally:
        await conn.close()
    assert count == 0


@pytest.mark.asyncio
async def test_a_well_formed_chat_draft_still_creates_its_plan(
    configured_app, migrations_pg_dsn: str
) -> None:
    """La guarda de la guarda: el gate no puede convertirse en «no se crean planes»."""
    ids = await _seed(migrations_pg_dsn)
    await _attach_draft(
        migrations_pg_dsn,
        ids,
        {
            "summary": {"description": "Migrar el servicio de facturación"},
            "tasks": [{"id": "t-1", "title": "Diseñar el esquema", "depends_on": []}],
        },
    )
    token = await _mint_token(ids["admin"], ids["tenant"])

    async with _client(configured_app) as client:
        resp = await client.post(
            f"/projects/{ids['project']}/plans",
            json={"conversation_id": str(ids["conversation"])},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_an_inline_specification_is_not_narrowed_by_the_gate(
    configured_app, migrations_pg_dsn: str
) -> None:
    """El contrato público de la API no se estrecha por cablear el gate.

    Un `specification` inline no viene de un LLM: lo valida Pydantic. Meterlo
    además por el esquema estructural rompía 14 tests de flujos legítimos que
    crean planes con tareas y `summary` vacío — el gate estaría bloqueando el
    producto en vez de los borradores malos del equipo.
    """
    ids = await _seed(migrations_pg_dsn)
    token = await _mint_token(ids["admin"], ids["tenant"])

    async with _client(configured_app) as client:
        resp = await client.post(
            f"/projects/{ids['project']}/plans",
            json={
                "title": "Plan por API",
                "specification": {"summary": {}, "tasks": [{"id": "t-1", "title": "Algo"}]},
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_an_empty_plan_shell_is_still_allowed(configured_app, migrations_pg_dsn: str) -> None:
    """Crear la carcasa vacía y rellenarla luego es un flujo legítimo.

    Si el gate corriera también aquí, exigiría `summary` + una tarea y dejaría
    de poderse crear un plan en blanco — el gate estaría bloqueando el producto
    en vez de los borradores malos.
    """
    ids = await _seed(migrations_pg_dsn)
    token = await _mint_token(ids["admin"], ids["tenant"])

    async with _client(configured_app) as client:
        resp = await client.post(
            f"/projects/{ids['project']}/plans",
            json={"title": "Carcasa"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 201, resp.text
