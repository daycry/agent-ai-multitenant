"""Capacidad de equipo + metadata de miembro (06.17 task_06_17_15, ADR 0053).

Plan 06.17 Fase E. Según el **ADR 0053 (Opción B)** NO hay subsistema
``TeamKnowledgeBase``: la capacidad de equipo es la **UNIÓN AGREGADA read-only**
de lo que ya saben/pueden sus MIEMBROS (consume ``GET /teams/{id}/capabilities``
de ``task_06_17_08``). La única escritura nueva de la UI a nivel de equipo es la
**metadata de miembro** vía el ``PUT /teams/{id}/members/{agent_id}`` ya
existente; y se **retira** el campo muerto ``teams.shared_memory_namespace``.

Lo que estos tests blindan (contrato del ADR 0053):

  * **Capacidad agregada**: ``GET /teams/{id}/capabilities`` devuelve la UNIÓN de
    las KBs (rol + stack de cada miembro) y de las tools efectivas de los
    agentes que componen el equipo, marcando ``entity_type == "team"``.
  * **Honestidad de estado**: un equipo sin miembros lo avisa (no finge capacidad).
  * **Metadata de miembro editable**: ``PUT /teams/{id}/members/{agent_id}`` con
    ``is_team_leader`` / ``role_in_team`` / ``assignment_priority`` PERSISTE
    (verificado releyendo el equipo).
  * **Aislamiento multi-tenant**: tenant B → 404 en capabilities y en el PUT de
    metadata; un fork NUNCA cruza tenants (la query es tenant-scoped por RLS).
  * **``shared_memory_namespace`` retirado**: ni el modelo ORM ni el contrato de
    ``/teams`` exponen ya el campo muerto.
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
#   - project_a con allowed_commands no vacío + un equipo (team_a) con dos
#     miembros (agent_a project_local + agent_global).
#   - kb_role (granteada al agente template), kb_stack (granteada al proyecto).
#   - read_file/shell_exec asignadas a agent_a.
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
    team_empty = uuid4()
    agent_a = uuid4()
    agent_global = uuid4()
    agent_b = uuid4()

    kb_role = uuid4()
    kb_stack = uuid4()

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
            "acme-teamcap",
            tenant_b,
            "Globex",
            "globex-teamcap",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-teamcap",
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
            " VALUES ($1, $2, 'Squad A'), ($3, $4, 'Squad B'), ($5, $2, 'Empty Squad')",
            team_a,
            tenant_a,
            team_b,
            tenant_b,
            team_empty,
        )
        # agent_a: project_local con model_config válido.
        # agent_global: global_tenant_template SIN project_id.
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
        # Team A: agent_a + agent_global. Team B: agent_b.
        await conn.execute(
            "INSERT INTO team_members (team_id, agent_id) VALUES ($1, $2), ($1, $3), ($4, $5)",
            team_a,
            agent_a,
            agent_global,
            team_b,
            agent_b,
        )
        # KBs: role (tenant A) / stack (tenant A).
        await conn.execute(
            "INSERT INTO knowledge_bases (id, tenant_id, name, is_builtin)"
            " VALUES ($1, $2, 'Role KB', false),"
            "        ($3, $2, 'Stack KB', false)",
            kb_role,
            tenant_a,
            kb_stack,
        )
        await conn.execute(
            "INSERT INTO agent_knowledge_bases (agent_id, kb_id, tenant_id) VALUES ($1, $2, $3)",
            agent_a,
            kb_role,
            tenant_a,
        )
        await conn.execute(
            "INSERT INTO kb_projects (kb_id, project_id, tenant_id) VALUES ($1, $2, $3)",
            kb_stack,
            project_a,
            tenant_a,
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
        for tool_id in (read_file, shell_exec):
            await conn.execute(
                "INSERT INTO agent_tools (agent_id, tool_id) VALUES ($1, $2)",
                agent_a,
                tool_id,
            )
    finally:
        await conn.close()
    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "admin_a": admin_a,
        "admin_b": admin_b,
        "project_a": project_a,
        "team_a": team_a,
        "team_b": team_b,
        "team_empty": team_empty,
        "agent_a": agent_a,
        "agent_global": agent_global,
        "agent_b": agent_b,
        "kb_role": kb_role,
        "kb_stack": kb_stack,
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
# CAPACIDAD DE EQUIPO — agregación read-only de capacidades de miembros.
# ===========================================================================
@pytest.mark.asyncio
async def test_team_capability_aggregates_members(configured_app, migrations_pg_dsn: str) -> None:
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

        # SABER: UNIÓN de las KBs de los miembros (rol de agent_a + stack de su
        # proyecto). No hay subsistema TeamKB: es lectura agregada (ADR 0053).
        kb_ids = {k["kb_id"] for k in body["saber"]["knowledge_bases"]}
        assert str(seeded["kb_role"]) in kb_ids
        assert str(seeded["kb_stack"]) in kb_ids

        # HACER: UNIÓN de las tools efectivas de los miembros (de agent_a).
        effective = set(body["hacer"]["effective"])
        assert "read_file" in effective
        assert "shell_exec" in effective


@pytest.mark.asyncio
async def test_empty_team_capability_is_honest(configured_app, migrations_pg_dsn: str) -> None:
    """Un equipo sin miembros AVISA explícitamente (no finge capacidad)."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/teams/{seeded['team_empty']}/capabilities", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()

        assert body["saber"]["knowledge_bases"] == []
        assert body["hacer"]["effective"] == []
        # Aviso bilingüe estructurado: {code, es, en}.
        codes = {w["code"] for w in body["warnings"]}
        assert "team_no_members" in codes
        es_text = " ".join(w["es"] for w in body["warnings"]).lower()
        en_text = " ".join(w["en"] for w in body["warnings"]).lower()
        assert "sin miembros" in es_text
        assert "no members" in en_text


# ===========================================================================
# METADATA DE MIEMBRO — PUT persiste (líder/prioridad/rol).
# ===========================================================================
@pytest.mark.asyncio
async def test_member_metadata_put_persists(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        upd = await client.put(
            f"/teams/{seeded['team_a']}/members/{seeded['agent_a']}",
            json={
                "is_team_leader": True,
                "role_in_team": "Tech Lead",
                "assignment_priority": 5,
            },
            headers=headers,
        )
        assert upd.status_code == 200, upd.text

        # Releer el equipo: la metadata PERSISTE.
        got = await client.get(f"/teams/{seeded['team_a']}", headers=headers)
        assert got.status_code == 200, got.text
        member = next(m for m in got.json()["members"] if m["agent_id"] == str(seeded["agent_a"]))
        assert member["is_team_leader"] is True
        assert member["role_in_team"] == "Tech Lead"
        assert member["assignment_priority"] == 5


# ===========================================================================
# shared_memory_namespace RETIRADO — ni el modelo ni el contrato lo exponen.
# ===========================================================================
def test_shared_memory_namespace_removed_from_model() -> None:
    """El campo muerto ``teams.shared_memory_namespace`` se retira (ADR 0053)."""
    from api_server.db.domain import Team
    from api_server.schemas.teams import (
        TeamCreateRequest,
        TeamResponse,
        TeamUpdateRequest,
    )

    assert not hasattr(Team, "shared_memory_namespace")
    assert "shared_memory_namespace" not in TeamResponse.model_fields
    assert "shared_memory_namespace" not in TeamCreateRequest.model_fields
    assert "shared_memory_namespace" not in TeamUpdateRequest.model_fields


@pytest.mark.asyncio
async def test_create_team_ignores_removed_namespace(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Crear/leer equipo ya no expone ``shared_memory_namespace`` en el contrato."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        create = await client.post("/teams", json={"name": "Fresh Team"}, headers=headers)
        assert create.status_code == 201, create.text
        assert "shared_memory_namespace" not in create.json()


# ===========================================================================
# AISLAMIENTO MULTI-TENANT: tenant B no ve las entidades de A → 404.
# ===========================================================================
@pytest.mark.asyncio
async def test_team_capability_cross_tenant_404(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token_b = await _mint(seeded["admin_b"], seeded["tenant_b"])
    headers = {"Authorization": f"Bearer {token_b}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        # Capabilities de un equipo de tenant A → 404 para tenant B.
        cap = await client.get(f"/teams/{seeded['team_a']}/capabilities", headers=headers)
        assert cap.status_code == 404, cap.text

        # PUT de metadata sobre un equipo de tenant A → 404 (RLS oculta el team).
        put = await client.put(
            f"/teams/{seeded['team_a']}/members/{seeded['agent_a']}",
            json={"is_team_leader": True},
            headers=headers,
        )
        assert put.status_code == 404, put.text


# ===========================================================================
# Migración 0082: dropea `teams.shared_memory_namespace` y es REVERSIBLE.
#
# Test SÍNCRONO a propósito: `command.upgrade/downgrade` corren su propio
# `asyncio.run` (run_async_migrations), que choca con el event loop de un test
# async. Mismo patrón que `test_ingestion_state_honesty` (0080).
# ===========================================================================
async def _team_column_exists(dsn: str) -> bool:
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT 1 FROM information_schema.columns"
            " WHERE table_name = 'teams' AND column_name = 'shared_memory_namespace'"
        )
        return row is not None
    finally:
        await conn.close()


def test_migration_0082_drops_namespace_and_is_reversible(
    alembic_config, migrations_pg_dsn: str
) -> None:
    # En head (0082) la columna NO existe.
    command.upgrade(alembic_config, "head")
    assert asyncio.run(_team_column_exists(migrations_pg_dsn)) is False

    # Reversibilidad: bajar a la revisión ANTERIOR (0081) recrea la columna.
    # Apuntamos a la revisión nombrada (no "-1") para seguir siendo correctos
    # cuando se apilen migraciones posteriores sobre 0082.
    command.downgrade(alembic_config, "0081_model_config_sanitize")
    assert asyncio.run(_team_column_exists(migrations_pg_dsn)) is True

    # Volver a head la vuelve a dropear (idempotente en ambas direcciones).
    command.upgrade(alembic_config, "head")
    assert asyncio.run(_team_column_exists(migrations_pg_dsn)) is False
