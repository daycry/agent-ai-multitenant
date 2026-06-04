"""Validación de ``model_config`` contra el catálogo cerrado (ADR 0055 / ADR 0021,
Plan 06.17 task_06_17_10).

El ``model_config`` (la pata SER del modelo de un agente: proveedor/modelo/
temperatura) nacía sin validación y a menudo ``{}`` (ningún diálogo de la UI lo
enviaba), de modo que un proveedor inexistente o un spec vacío solo fallaban —
tarde y opaco — al arrancar el run. Esta suite pin-ea los cuatro contratos de la
decisión M-B del ADR 0055:

  1. **422 fuera de catálogo**. ``create``/``update`` rechazan un ``provider``
     fuera de ``{claude_sdk, copilot, azure_foundry, ollama}`` (ADR 0021), un
     ``model`` vacío y una ``temperature`` fuera de rango con ``422``.
  2. **Un agente nuevo NO nace ``{}``**. ``POST /agents`` sin ``model_config``
     rellena el default EXPLÍCITO operator-configurable (platform_settings), de
     modo que la fila persiste un spec completo del catálogo, no ``{}``.
  3. **Dispatch de legacy ``{}`` usa el default sin fallar**. El orquestador
     resuelve un ``model_config = {}`` legacy al default seguro (sin fallo de
     arranque, SIN auto-retry) y lo threadea al payload del worker.
  4. **Migración 0081 reversible**. Sanea las filas ``agents`` con
     ``model_config = {}`` asignándoles el default; el downgrade restaura ``{}``.

Reutiliza el patrón de fixtures de ``test_agents_endpoints`` (dos tenants
sembrados vía rol BYPASSRLS + JWT) y el del dispatcher de
``test_orchestrator_dispatch``.
"""

from __future__ import annotations

import asyncio
import base64
import json
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration

TEST_REDIS_URL = "redis://localhost:6379/15"

# Default seguro de código (anclado al catálogo cerrado del ADR 0021) que tanto
# el endpoint como el dispatch usan como fallback cuando platform_settings no
# tiene un default configurado. Lo importamos del módulo fuente para que el test
# siga el valor canónico y no lo duplique.


async def _seed(dsn: str) -> dict[str, UUID]:
    tenant_a = uuid4()
    tenant_b = uuid4()
    user_a = uuid4()
    user_b = uuid4()
    project_a = uuid4()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE executions, tasks, agents, projects, user_org_memberships,"
            " organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            tenant_a,
            "Tenant A",
            "tenant-a-mc",
            tenant_b,
            "Tenant B",
            "tenant-b-mc",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3), ($4, $5, $6)",
            user_a,
            "alice@a-mc.test",
            "argon2-placeholder",
            user_b,
            "bob@b-mc.test",
            "argon2-placeholder",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, $4), ($5, $6, $7, $8)",
            uuid4(),
            tenant_a,
            user_a,
            "tenant_admin",
            uuid4(),
            tenant_b,
            user_b,
            "tenant_admin",
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, $3)",
            project_a,
            tenant_a,
            "Project A",
        )
    finally:
        await conn.close()

    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "user_a": user_a,
        "user_b": user_b,
        "project_a": project_a,
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


async def _mint_token(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


def _payload(**overrides) -> dict:
    base = {
        "name": "Backend Senior",
        "role": "backend_dev",
        "system_prompt": "You are a senior backend engineer.",
        "scope": "global_tenant_template",
    }
    base.update(overrides)
    return base


def _valid_model_config() -> dict:
    return {"provider": "claude_sdk", "model": "claude-sonnet-4", "temperature": 0.2}


# ---------------------------------------------------------------------------
# 1. 422 fuera de catálogo (create + update)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_rejects_provider_outside_catalogue(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/agents",
            json=_payload(
                model_config={"provider": "openai", "model": "gpt-4o", "temperature": 0.2}
            ),
            headers=headers,
        )
    assert resp.status_code == 422, resp.text
    assert "provider" in resp.text


@pytest.mark.asyncio
async def test_create_rejects_empty_model(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/agents",
            json=_payload(model_config={"provider": "ollama", "model": "", "temperature": 0.2}),
            headers=headers,
        )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_create_rejects_temperature_out_of_range(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/agents",
            json=_payload(
                model_config={"provider": "claude_sdk", "model": "claude-x", "temperature": 9.0}
            ),
            headers=headers,
        )
    assert resp.status_code == 422, resp.text
    assert "temperature" in resp.text


@pytest.mark.asyncio
async def test_create_accepts_valid_catalogue_config(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/agents",
            json=_payload(model_config=_valid_model_config()),
            headers=headers,
        )
    assert resp.status_code == 201, resp.text
    assert resp.json()["model_config"] == _valid_model_config()


@pytest.mark.asyncio
async def test_update_rejects_provider_outside_catalogue(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        created = await client.post(
            "/agents",
            json=_payload(model_config=_valid_model_config()),
            headers=headers,
        )
        assert created.status_code == 201, created.text
        agent_id = created.json()["id"]

        resp = await client.put(
            f"/agents/{agent_id}",
            json={"model_config": {"provider": "vertex", "model": "gemini", "temperature": 0.1}},
            headers=headers,
        )
    assert resp.status_code == 422, resp.text
    assert "provider" in resp.text


# ---------------------------------------------------------------------------
# 2. Un agente nuevo NO nace {} — el endpoint rellena el default explícito
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_new_agent_without_model_config_is_not_empty(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        # Sin enviar model_config en absoluto.
        resp = await client.post("/agents", json=_payload(), headers=headers)
    assert resp.status_code == 201, resp.text
    cfg = resp.json()["model_config"]
    assert cfg != {}, "un agente nuevo no debe nacer con model_config vacío (ADR 0055)"
    # El default explícito está anclado al catálogo cerrado del ADR 0021.
    from api_server.db.llm_providers import LLM_PROVIDER_KINDS

    assert cfg.get("provider") in LLM_PROVIDER_KINDS
    assert isinstance(cfg.get("model"), str) and cfg["model"].strip()


# ---------------------------------------------------------------------------
# 3. Dispatch de legacy {} aplica el default seguro sin fallar (sin auto-retry)
# ---------------------------------------------------------------------------
async def _seed_dispatch_legacy(sm, *, empty_model_config: bool) -> dict[str, UUID]:
    from api_server.db.domain import Agent, Project, Task
    from api_server.db.models import Organization
    from sqlalchemy import text

    ids = {"tenant": uuid4(), "project": uuid4(), "agent": uuid4(), "task": uuid4()}
    async with sm() as s, s.begin():
        await s.execute(
            text(
                "TRUNCATE executions, task_dependencies, tasks, agents, projects,"
                " organizations RESTART IDENTITY CASCADE"
            )
        )
        s.add(Organization(id=ids["tenant"], name="MC tenant", slug="mc-disp-tenant"))
        await s.flush()
        s.add(
            Project(
                id=ids["project"],
                tenant_id=ids["tenant"],
                name="MC project",
                status="active",
                is_template=False,
                worker_config={"assignment_policy": "load_balanced"},
            )
        )
        await s.flush()
        s.add(
            Agent(
                id=ids["agent"],
                tenant_id=ids["tenant"],
                name="Legacy",
                role="backend-dev",
                system_prompt="You write things.",
                agent_type="ai",
                scope="project_local",
                project_id=ids["project"],
                model_config={} if empty_model_config else _valid_model_config(),
            )
        )
        await s.flush()
        s.add(
            Task(
                id=ids["task"],
                tenant_id=ids["tenant"],
                project_id=ids["project"],
                title="Do a thing",
                description="exercise dispatch",
                status="ready",
                priority="medium",
            )
        )
    return ids


def _dispatcher(sm):
    from orchestrator.config import Settings as OrchestratorSettings
    from orchestrator.dispatch import TaskDispatcher
    from workers.celery_app import build_celery_app
    from workers.config import Settings as WorkerSettings

    celery_app = build_celery_app(WorkerSettings(broker_url=TEST_REDIS_URL))
    return TaskDispatcher(
        sessionmaker=sm,
        celery_app=celery_app,
        settings=OrchestratorSettings(redis_url=TEST_REDIS_URL),
    )


def _ready_event(ids):
    from orchestrator.events import EVENT_TASK_STATUS_CHANGED, TaskEvent

    return TaskEvent(
        stream_id="1-0",
        type=EVENT_TASK_STATUS_CHANGED,
        tenant_id=str(ids["tenant"]),
        project_id=str(ids["project"]),
        task_id=str(ids["task"]),
        occurred_at="2026-06-04T00:00:00+00:00",
        payload={"old_status": "backlog", "new_status": "ready"},
    )


@pytest.mark.asyncio
async def test_dispatch_legacy_empty_model_config_uses_safe_default(
    configured_app, admin_database_url: str
) -> None:
    from api_server.db.llm_providers import LLM_PROVIDER_KINDS
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(admin_database_url)
    redis: Redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed_dispatch_legacy(sm, empty_model_config=True)
        await redis.delete("default")

        # No debe lanzar (sin fallo de arranque, sin auto-retry).
        await _dispatcher(sm).handle(_ready_event(ids))

        raw = await redis.lrange("default", 0, -1)
        assert len(raw) == 1, "el run debe encolarse pese al model_config {} legacy"
        message = json.loads(raw[0])
        body = json.loads(base64.b64decode(message["body"]))
        _args, kwargs, _embed = body
        request = kwargs["request"]
        # El spec vacío legacy se resuelve al default seguro del catálogo.
        assert request["model"] != {}, "dispatch no debe propagar un spec vacío"
        assert request["model"].get("provider") in LLM_PROVIDER_KINDS
        assert isinstance(request["model"].get("model"), str) and request["model"]["model"].strip()
    finally:
        await redis.delete("default")
        await redis.aclose()
        await engine.dispose()


@pytest.mark.asyncio
async def test_dispatch_keeps_explicit_model_config_verbatim(
    configured_app, admin_database_url: str
) -> None:
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(admin_database_url)
    redis: Redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed_dispatch_legacy(sm, empty_model_config=False)
        await redis.delete("default")

        await _dispatcher(sm).handle(_ready_event(ids))

        raw = await redis.lrange("default", 0, -1)
        assert len(raw) == 1
        message = json.loads(raw[0])
        body = json.loads(base64.b64decode(message["body"]))
        _args, kwargs, _embed = body
        request = kwargs["request"]
        # Un spec explícito y válido se forwardea verbatim (default no pisa).
        assert request["model"] == _valid_model_config()
    finally:
        await redis.delete("default")
        await redis.aclose()
        await engine.dispose()


# ---------------------------------------------------------------------------
# 4. Migración 0081: sanea model_config {} legacy y es reversible
# ---------------------------------------------------------------------------
async def _insert_legacy_empty_agent(dsn: str) -> UUID:
    tenant_id = uuid4()
    agent_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE agents, projects, user_org_memberships, organizations,"
            " users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'Acme', 'acme-mig81')",
            tenant_id,
        )
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, name, role, system_prompt,"
            " model_config, scope, project_id)"
            " VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, NULL)",
            agent_id,
            tenant_id,
            "Legacy Empty",
            "backend_dev",
            "You write things.",
            "{}",
            "global_tenant_template",
        )
    finally:
        await conn.close()
    return agent_id


async def _read_model_config(dsn: str, agent_id: UUID) -> dict:
    conn = await asyncpg.connect(dsn)
    try:
        raw = await conn.fetchval("SELECT model_config FROM agents WHERE id = $1", agent_id)
        return json.loads(raw) if isinstance(raw, str) else dict(raw or {})
    finally:
        await conn.close()


def test_migration_0081_sanitizes_empty_model_config_and_is_reversible(
    alembic_config, migrations_pg_dsn: str
) -> None:
    from api_server.db.llm_providers import LLM_PROVIDER_KINDS

    # Nos colocamos JUSTO antes de 0081 sin depender del estado que dejó otro
    # test (la DB de test es session-scoped): primero ``upgrade head`` garantiza
    # que el esquema existe, luego ``downgrade`` a 0080 deja la fila legacy {}
    # sin sanear para que el ``upgrade head`` siguiente ejecute 0081 sobre ella.
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "0080_documents_indexed_empty")
    agent_id = asyncio.run(_insert_legacy_empty_agent(migrations_pg_dsn))

    command.upgrade(alembic_config, "head")
    after_up = asyncio.run(_read_model_config(migrations_pg_dsn, agent_id))
    assert after_up != {}, "0081 debe rellenar el model_config {} con el default seguro"
    assert after_up.get("provider") in LLM_PROVIDER_KINDS
    assert isinstance(after_up.get("model"), str) and after_up["model"].strip()

    # Reversibilidad: bajamos a 0080 (la revisión ANTERIOR a 0081) para que el
    # downgrade de 0081 corra y restaure {} en las filas saneadas. Revisión
    # NOMBRADA (no "-1" relativo al head) para que el test siga siendo correcto
    # al apilar migraciones posteriores (p.ej. 0082): un "-1" solo desharía la
    # cima y dejaría intacto el saneo de 0081.
    command.downgrade(alembic_config, "0080_documents_indexed_empty")
    after_down = asyncio.run(_read_model_config(migrations_pg_dsn, agent_id))
    assert after_down == {}, "el downgrade debe restaurar el model_config {} saneado"
    command.upgrade(alembic_config, "head")
