"""Vista de capacidad efectiva del proyecto + sección de memoria
(Plan 06.17 task_06_17_14).

La pantalla "¿con qué cuenta el proyecto?" (`projects/[id]/page.tsx` +
`projects/[id]/memories/page.tsx`) consume `GET /projects/{id}/capabilities`
(task_06_17_08). Estos tests blindan el contrato del backend que esa vista
necesita:

  * SABER: las KBs granteadas al stack del proyecto (kb_projects), con su
    etiqueta de NIVEL (`stack` / `plataforma`), y NUNCA las de rol del agente.
  * RECORDAR: la sección de memoria del proyecto agrupa memory_entries por
    scope (project_shared del proyecto + global del tenant); el contrato la
    expone para que la sub-página de memoria la pinte.
  * HACER: a nivel de proyecto no restringe (las tools son del agente) →
    `unrestricted=true`; SER es `None`.
  * El default_runtime_template del proyecto (que el selector de runtime de
    06.18 muestra como label, no slug) viaja en el ProjectResponse.

Conducido por la app ASGI con la sesión RLS real; un proyecto inexistente
(o de otro tenant) → 404.
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

PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


# ---------------------------------------------------------------------------
# Seed: un tenant con un proyecto que tiene KBs de stack + built-in granteadas,
# memoria project_shared + global, un default_runtime_template, y un agente con
# una KB de rol (que NO debe aparecer en la capacidad del proyecto).
# ---------------------------------------------------------------------------
async def _seed(dsn: str) -> dict[str, UUID]:
    tenant = uuid4()
    admin = uuid4()
    project = uuid4()
    agent = uuid4()
    kb_stack = uuid4()
    kb_builtin = uuid4()
    kb_role = uuid4()
    nonce = uuid4().hex[:8]

    conn = await asyncpg.connect(dsn)
    try:
        # Reset the relevant tables so re-running within the same session-scoped
        # DB never collides on unique (tenant_id, name) — mirrors the pattern in
        # test_capabilities_endpoint.py.
        await conn.execute(
            "TRUNCATE memory_entries, agent_knowledge_bases, kb_projects, chunks,"
            " documents, knowledge_bases, agent_tools, tools, team_members, teams,"
            " agents, projects, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'Acme', $2)",
            tenant,
            f"acme-{nonce}",
        )
        # Platform tenant for the built-in KB.
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'Platform', $2)",
            PLATFORM_TENANT_ID,
            f"platform-{nonce}",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, 'h')",
            admin,
            f"admin-{nonce}@acme.test",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin')",
            uuid4(),
            tenant,
            admin,
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, default_runtime_template)"
            " VALUES ($1, $2, 'Webapp', 'python-pytest')",
            project,
            tenant,
        )
        await conn.execute(
            "INSERT INTO agents"
            " (id, tenant_id, name, role, scope, agent_type, system_prompt,"
            "  project_id, memory_scope, model_config)"
            " VALUES ($1, $2, 'backend-dev', 'backend_dev', 'project_local', 'ai', 'p',"
            "  $3, 'project_shared', '{}')",
            agent,
            tenant,
            project,
        )
        await conn.execute(
            "INSERT INTO knowledge_bases (id, tenant_id, name, is_builtin)"
            " VALUES ($1, $2, 'Stack KB', false),"
            "        ($3, $4, 'Builtin KB', true),"
            "        ($5, $2, 'Role KB', false)",
            kb_stack,
            tenant,
            kb_builtin,
            PLATFORM_TENANT_ID,
            kb_role,
        )
        # kb_stack + kb_builtin granteadas al proyecto (stack / plataforma).
        await conn.execute(
            "INSERT INTO kb_projects (kb_id, project_id, tenant_id)"
            " VALUES ($1, $2, $3), ($4, $2, $3)",
            kb_stack,
            project,
            tenant,
            kb_builtin,
        )
        # kb_role granteada al AGENTE (rol): no debe verse en el proyecto.
        await conn.execute(
            "INSERT INTO agent_knowledge_bases (agent_id, kb_id, tenant_id) VALUES ($1, $2, $3)",
            agent,
            kb_role,
            tenant,
        )
        # Memoria: 2 project_shared del proyecto + 1 global del tenant.
        await conn.execute(
            "INSERT INTO memory_entries (id, tenant_id, scope, type, content, project_id)"
            " VALUES ($1, $2, 'project_shared', 'semantic', 'usa asyncpg', $3),"
            "        ($4, $2, 'project_shared', 'episodic', 'el build falló', $3)",
            uuid4(),
            tenant,
            project,
            uuid4(),
        )
        await conn.execute(
            "INSERT INTO memory_entries (id, tenant_id, scope, type, content)"
            " VALUES ($1, $2, 'global', 'semantic', 'convención de commits')",
            uuid4(),
            tenant,
        )
    finally:
        await conn.close()
    return {
        "tenant": tenant,
        "admin": admin,
        "project": project,
        "agent": agent,
        "kb_stack": kb_stack,
        "kb_builtin": kb_builtin,
        "kb_role": kb_role,
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


async def _mint(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


# ---------------------------------------------------------------------------
# Capacidad del proyecto: SABER (stack/plataforma) + RECORDAR (memoria) +
# HACER unrestricted + SER None.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_project_capability_view(configured_app, migrations_pg_dsn: str) -> None:
    seed = await _seed(migrations_pg_dsn)
    token = await _mint(seed["admin"], seed["tenant"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/projects/{seed['project']}/capabilities", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()

        assert body["entity_type"] == "project"
        assert body["entity_id"] == str(seed["project"])

        # SABER: KBs del stack del proyecto, con nivel; NO la de rol del agente.
        kbs = {k["kb_id"]: k for k in body["saber"]["knowledge_bases"]}
        assert kbs[str(seed["kb_stack"])]["level"] == "stack"
        assert kbs[str(seed["kb_builtin"])]["level"] == "plataforma"
        assert str(seed["kb_role"]) not in kbs

        # RECORDAR: sección de memoria del proyecto por scope.
        by_scope = {m["scope"]: m["count"] for m in body["recordar"]["memory"]}
        assert by_scope.get("project_shared") == 2
        assert by_scope.get("global") == 1

        # HACER: no restringe a nivel de proyecto; SER no aplica.
        assert body["hacer"]["unrestricted"] is True
        assert body["ser"] is None


# ---------------------------------------------------------------------------
# El ProjectResponse expone default_runtime_template (que el selector de
# runtime de 06.18 muestra como label, no slug).
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_project_response_carries_runtime_template(
    configured_app, migrations_pg_dsn: str
) -> None:
    seed = await _seed(migrations_pg_dsn)
    token = await _mint(seed["admin"], seed["tenant"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/projects/{seed['project']}", headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["default_runtime_template"] == "python-pytest"


# ---------------------------------------------------------------------------
# Proyecto inexistente → 404.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_project_capability_unknown_404(configured_app, migrations_pg_dsn: str) -> None:
    seed = await _seed(migrations_pg_dsn)
    token = await _mint(seed["admin"], seed["tenant"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/projects/{uuid4()}/capabilities", headers=headers)
        assert resp.status_code == 404, resp.text
