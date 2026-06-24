"""`GET /{entity}/{id}/capabilities` — Hub de Capacidad honesto (06.17 task_06_17_08).

Plan 06.17 Fase C. El endpoint por agente/proyecto/equipo devuelve el **set
efectivo REAL** de capacidad por las cuatro vías del modelo unificado
(SABER/RECORDAR/SER/HACER) y avisos honestos. La sección HACER **delega/compone**
con la pieza pura ``compute_effective_tools`` de 06.18 (NO recalcula la
intersección).

Lo que estos tests blindan (contrato):

  * **SABER**: KBs visibles con etiqueta de NIVEL (``rol`` vía
    ``agent_knowledge_bases`` / ``stack`` vía ``kb_projects`` / ``plataforma``
    para built-ins).
  * **RECORDAR**: conteo de memoria por scope + (en agente) el ``memory_scope``
    configurado y el aviso de ``private`` silencioso.
  * **SER** (solo agente): si el modelo está configurado (``provider``/``model``)
    o el aviso "modelo no configurado".
  * **HACER**: el set efectivo de tools (compuesto con ``compute_effective_tools``);
    para EQUIPO se AGREGA read-only las capacidades de los miembros (ADR 0053).
  * **Avisos honestos**: "agente global no ve conocimiento de proyecto",
    "modelo no configurado".
  * **Aislamiento multi-tenant**: tenant B → 404.
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


# ---------------------------------------------------------------------------
# Seed: dos tenants.
#
# Tenant A:
#   - project_a con allowed_commands no vacío + un equipo + memoria por scope.
#   - kb_role (granteada al agente template), kb_stack (granteada al proyecto),
#     kb_builtin (platform built-in, granteada al proyecto -> nivel plataforma).
#   - agent_a (project_local, con model_config + tools asignadas).
#   - agent_global (global_tenant_template, SIN project_id -> aviso honesto,
#     model_config={} -> aviso "modelo no configurado").
#   - team_a con dos miembros (agent_a + agent_global) para la agregación.
#
# Tenant B: su propio agente/proyecto/equipo para el aislamiento cross-tenant.
# ---------------------------------------------------------------------------
async def _seed(dsn: str) -> dict[str, UUID]:
    tenant_a = uuid4()
    tenant_b = uuid4()
    admin_a = uuid4()
    admin_b = uuid4()
    project_a = uuid4()
    project_b = uuid4()
    team_a = uuid4()
    team_b = uuid4()
    agent_a = uuid4()
    agent_global = uuid4()
    agent_b = uuid4()

    kb_role = uuid4()
    kb_stack = uuid4()
    kb_builtin = uuid4()

    read_file = uuid4()
    shell_exec = uuid4()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE memory_entries, agent_knowledge_bases, kb_projects, chunks,"
            " documents, knowledge_bases, agent_tools, tools, team_members, teams,"
            " agents, projects, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug)"
            " VALUES ($1, $2, $3), ($4, $5, $6), ($7, $8, $9)",
            tenant_a,
            "Acme",
            "acme-cap",
            tenant_b,
            "Globex",
            "globex-cap",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-cap",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash)"
            " VALUES ($1, 'a@acme.test', 'h'), ($2, 'b@globex.test', 'h')",
            admin_a,
            admin_b,
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin'), ($4, $5, $6, 'tenant_admin')",
            uuid4(),
            tenant_a,
            admin_a,
            uuid4(),
            tenant_b,
            admin_b,
        )
        # Project A: allowed_commands no vacío (shell_exec efectivo) + team.
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, allowed_commands)"
            " VALUES ($1, $2, 'Webapp', $3)",
            project_a,
            tenant_a,
            ["pytest", "ruff"],
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, allowed_commands)"
            " VALUES ($1, $2, 'B-app', $3)",
            project_b,
            tenant_b,
            [],
        )
        await conn.execute(
            "INSERT INTO teams (id, tenant_id, name)"
            " VALUES ($1, $2, 'Squad A'), ($3, $4, 'Squad B')",
            team_a,
            tenant_a,
            team_b,
            tenant_b,
        )
        # agent_a: project_local con model_config válido.
        # agent_global: global_tenant_template SIN project_id y model_config={}.
        await conn.execute(
            "INSERT INTO agents"
            " (id, tenant_id, name, role, scope, agent_type, system_prompt,"
            "  project_id, memory_scope, model_config)"
            " VALUES"
            " ($1, $2, 'backend-dev', 'backend_dev', 'project_local', 'ai', 'p',"
            "  $3, 'project_shared', $4),"
            " ($5, $2, 'pm-template', 'project_manager', 'global_tenant_template', 'ai', 'p',"
            "  NULL, 'private', '{}'),"
            " ($6, $7, 'b-dev', 'backend_dev', 'project_local', 'ai', 'p',"
            "  $8, 'private', '{}')",
            agent_a,
            tenant_a,
            project_a,
            '{"provider": "ollama", "model": "qwen2.5-coder", "temperature": 0.2}',
            agent_global,
            agent_b,
            tenant_b,
            project_b,
        )
        # Team A: agent_a + agent_global como miembros.
        await conn.execute(
            "INSERT INTO team_members (team_id, agent_id) VALUES ($1, $2), ($1, $3)",
            team_a,
            agent_a,
            agent_global,
        )
        # KBs: role (tenant A) / stack (tenant A) / builtin (platform).
        await conn.execute(
            "INSERT INTO knowledge_bases (id, tenant_id, name, is_builtin)"
            " VALUES ($1, $2, 'Role KB', false),"
            "        ($3, $2, 'Stack KB', false),"
            "        ($4, $5, 'Builtin KB', true)",
            kb_role,
            tenant_a,
            kb_stack,
            kb_builtin,
            _PLATFORM_TENANT_ID,
        )
        # kb_role -> granteada al agente template (nivel rol).
        await conn.execute(
            "INSERT INTO agent_knowledge_bases (agent_id, kb_id, tenant_id)" " VALUES ($1, $2, $3)",
            agent_a,
            kb_role,
            tenant_a,
        )
        # kb_stack + kb_builtin -> granteadas al proyecto (stack / plataforma).
        await conn.execute(
            "INSERT INTO kb_projects (kb_id, project_id, tenant_id)"
            " VALUES ($1, $2, $3), ($4, $2, $3)",
            kb_stack,
            project_a,
            tenant_a,
            kb_builtin,
        )
        # Tools built-in.
        await conn.execute(
            "INSERT INTO tools"
            " (id, tenant_id, name, description, category,"
            "  implementation_type, security_level, is_builtin)"
            " VALUES"
            " ($1, $2, 'read_file', 'read', 'file', 'builtin', 'safe', true),"
            " ($3, $2, 'shell_exec', 'shell', 'command', 'builtin', 'privileged', true)",
            read_file,
            _PLATFORM_TENANT_ID,
            shell_exec,
        )
        # agent_a: read_file + shell_exec asignadas.
        for tool_id in (read_file, shell_exec):
            await conn.execute(
                "INSERT INTO agent_tools (agent_id, tool_id) VALUES ($1, $2)",
                agent_a,
                tool_id,
            )
        # Memoria por scope para project_a / global (RECORDAR del proyecto).
        await conn.execute(
            "INSERT INTO memory_entries (id, tenant_id, scope, type, content, project_id)"
            " VALUES ($1, $2, 'project_shared', 'semantic', 'usa asyncpg', $3),"
            "        ($4, $2, 'project_shared', 'episodic', 'el build falló', $3)",
            uuid4(),
            tenant_a,
            project_a,
            uuid4(),
        )
        await conn.execute(
            "INSERT INTO memory_entries (id, tenant_id, scope, type, content)"
            " VALUES ($1, $2, 'global', 'semantic', 'convención de commits')",
            uuid4(),
            tenant_a,
        )
    finally:
        await conn.close()
    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "admin_a": admin_a,
        "admin_b": admin_b,
        "project_a": project_a,
        "project_b": project_b,
        "team_a": team_a,
        "team_b": team_b,
        "agent_a": agent_a,
        "agent_global": agent_global,
        "agent_b": agent_b,
        "kb_role": kb_role,
        "kb_stack": kb_stack,
        "kb_builtin": kb_builtin,
        "read_file": read_file,
        "shell_exec": shell_exec,
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


# ===========================================================================
# AGENTE — SABER/RECORDAR/SER/HACER del set efectivo real.
# ===========================================================================
@pytest.mark.asyncio
async def test_agent_capabilities_full(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/agents/{seeded['agent_a']}/capabilities", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()

        assert body["entity_type"] == "agent"
        assert body["entity_id"] == str(seeded["agent_a"])

        # SABER: tres KBs visibles, una por cada nivel.
        kbs = {k["kb_id"]: k for k in body["saber"]["knowledge_bases"]}
        assert kbs[str(seeded["kb_role"])]["level"] == "rol"
        assert kbs[str(seeded["kb_stack"])]["level"] == "stack"
        assert kbs[str(seeded["kb_builtin"])]["level"] == "plataforma"

        # RECORDAR: memory_scope del agente + conteo por scope.
        assert body["recordar"]["memory_scope"] == "project_shared"
        by_scope = {m["scope"]: m["count"] for m in body["recordar"]["memory"]}
        assert by_scope.get("project_shared") == 2
        assert by_scope.get("global") == 1

        # SER: modelo configurado.
        assert body["ser"]["model_configured"] is True
        assert body["ser"]["provider"] == "ollama"
        assert body["ser"]["model"] == "qwen2.5-coder"
        # Ola D / ADR 0065: el agente pinea su propio modelo → origen "agent".
        assert body["ser"]["model_origin"] == "agent"

        # HACER: set efectivo real (read_file + shell_exec, allowed_commands no vacío).
        effective = set(body["hacer"]["effective"])
        assert "read_file" in effective
        assert "shell_exec" in effective
        assert body["hacer"]["shell_exec_effective"] is True


# ===========================================================================
# AGENTE GLOBAL — aviso honesto + "modelo no configurado".
# ===========================================================================
@pytest.mark.asyncio
async def test_global_agent_capabilities_warnings(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/agents/{seeded['agent_global']}/capabilities", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()

        # Modelo no configurado (model_config={}).
        assert body["ser"]["model_configured"] is False

        # Cada warning es bilingüe estructurado: {code, es, en}.
        codes = {w["code"] for w in body["warnings"]}
        assert "global_agent_no_project_context" in codes
        assert "model_not_configured" in codes
        for warning in body["warnings"]:
            assert set(warning) == {"code", "es", "en"}
            assert warning["es"] and warning["en"]

        es_text = " ".join(w["es"] for w in body["warnings"]).lower()
        en_text = " ".join(w["en"] for w in body["warnings"]).lower()
        # Aviso honesto bilingüe: agente global no ve conocimiento de proyecto.
        assert "global" in es_text
        assert "proyecto" in es_text
        assert "global" in en_text
        assert "project" in en_text
        # Aviso honesto bilingüe: modelo no configurado.
        assert "modelo" in es_text
        assert "model" in en_text


# ===========================================================================
# AGENTE — memory_scope=private silencioso -> aviso honesto.
# ===========================================================================
@pytest.mark.asyncio
async def test_private_memory_scope_warning(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/agents/{seeded['agent_global']}/capabilities", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["recordar"]["memory_scope"] == "private"
        codes = {w["code"] for w in body["warnings"]}
        assert "private_memory_scope" in codes
        es_text = " ".join(w["es"] for w in body["warnings"]).lower()
        en_text = " ".join(w["en"] for w in body["warnings"]).lower()
        assert "private" in es_text
        assert "private" in en_text


# ===========================================================================
# PROYECTO — capacidad efectiva (KBs de stack + memoria del proyecto).
# ===========================================================================
@pytest.mark.asyncio
async def test_project_capabilities(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/projects/{seeded['project_a']}/capabilities", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()

        assert body["entity_type"] == "project"
        assert body["entity_id"] == str(seeded["project_a"])

        # SABER: KBs granteadas al proyecto (stack + plataforma); NO la de rol.
        kbs = {k["kb_id"]: k for k in body["saber"]["knowledge_bases"]}
        assert str(seeded["kb_stack"]) in kbs
        assert str(seeded["kb_builtin"]) in kbs
        assert str(seeded["kb_role"]) not in kbs
        assert kbs[str(seeded["kb_stack"])]["level"] == "stack"
        assert kbs[str(seeded["kb_builtin"])]["level"] == "plataforma"

        # RECORDAR: memoria project_shared del proyecto.
        by_scope = {m["scope"]: m["count"] for m in body["recordar"]["memory"]}
        assert by_scope.get("project_shared") == 2


# ===========================================================================
# EQUIPO — agregación read-only de capacidades de miembros (ADR 0053).
# ===========================================================================
@pytest.mark.asyncio
async def test_team_capabilities_aggregates_members(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/teams/{seeded['team_a']}/capabilities", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()

        assert body["entity_type"] == "team"
        assert body["entity_id"] == str(seeded["team_a"])

        # La capacidad del equipo es la UNIÓN de la de sus miembros.
        kb_ids = {k["kb_id"] for k in body["saber"]["knowledge_bases"]}
        # agent_a aporta kb_role (rol) + kb_stack/kb_builtin (de su proyecto).
        assert str(seeded["kb_role"]) in kb_ids
        assert str(seeded["kb_stack"]) in kb_ids

        # HACER agregado: read_file + shell_exec del agent_a.
        effective = set(body["hacer"]["effective"])
        assert "read_file" in effective


# ===========================================================================
# AISLAMIENTO MULTI-TENANT: tenant B no ve las entidades de A → 404.
# ===========================================================================
@pytest.mark.asyncio
async def test_capabilities_cross_tenant_404(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token_b = await _mint(seeded["admin_b"], seeded["tenant_b"])
    headers = {"Authorization": f"Bearer {token_b}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        for path in (
            f"/agents/{seeded['agent_a']}/capabilities",
            f"/projects/{seeded['project_a']}/capabilities",
            f"/teams/{seeded['team_a']}/capabilities",
        ):
            resp = await client.get(path, headers=headers)
            assert resp.status_code == 404, f"{path}: {resp.text}"


# ===========================================================================
# Entidad inexistente → 404.
# ===========================================================================
@pytest.mark.asyncio
async def test_capabilities_unknown_entity_404(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        for entity in ("agents", "projects", "teams"):
            resp = await client.get(f"/{entity}/{uuid4()}/capabilities", headers=headers)
            assert resp.status_code == 404, resp.text
