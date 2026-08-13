"""El servicio de despliegue contra BD real — `task_mkt2_03`.

Corre sobre `open_tenant_session` (el `app_user` NOBYPASSRLS, con la RLS
puesta), no como `migrations_user`: probar el aislamiento cross-tenant con un
rol que se salta la RLS no probaría nada.

Los cuatro nodos que el plan declara irrenunciables, y por qué cada uno:

1. **Cross-tenant** — la instalación del tenant A no se despliega en un proyecto
   del B (404, no 403: un 403 confirmaría que el proyecto existe) ni aparece en
   sus lecturas.
2. **Retirada EXACTA** — se siembra una tool asignada A MANO al mismo agente que
   el despliegue toca; al retirar, la del operador SIGUE ahí. Es el test que
   justifica que exista `created_refs`.
3. **Idempotencia** — segundo despliegue activo del mismo par: no-op con aviso.
4. **Config inválida ⇒ nada escrito** — la transacción entera fuera, sin media
   entrada MCP en el proyecto.

Más el reparto por tipo (`mcp_server` → `mcp_servers` + `mcp_tool_roles`;
`tool`/`skill` → `agent_tools`/`agent_skills`) y la regla de no-política-paralela
del ADR 0142 §4: un `mcp_server` **NO** escribe `agent_tools`.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from uuid6 import uuid7

pytestmark = [pytest.mark.integration, pytest.mark.cross_tenant]

_ACTOR = "user:test"


# ---------------------------------------------------------------------------
# App/engine configurada contra la BD de test
# ---------------------------------------------------------------------------
@pytest.fixture()
def configured_env(
    alembic_config: object,
    app_database_url: str,
    admin_database_url: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
):
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
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
    try:
        yield
    finally:
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()


def _principal(user_id: UUID, tenant_id: UUID) -> Any:
    from api_server.auth.deps import AuthPrincipal

    return AuthPrincipal(user_id=user_id, session_id=uuid7(), tenant_id=tenant_id)


# ---------------------------------------------------------------------------
# Siembra: dos tenants, cada uno con equipo, agentes y proyecto
# ---------------------------------------------------------------------------
_MCP_MANIFEST = {
    "implementation_type": "mcp_tool",
    "implementation_ref": "https://jira.example.test/mcp",
    "targets": ["backend_dev"],
    "mcp_server": {"name": "jira", "transport": "streamable_http"},
    "config_schema": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "widget": "text"},
            "auth_ref": {"type": "string", "widget": "text", "secret": True},
        },
        "required": ["url"],
    },
}

_TOOL_MANIFEST = {
    "implementation_type": "http_endpoint",
    "implementation_ref": "https://status.example.test/api",
    "targets": ["qa"],
    "config_schema": {
        "type": "object",
        "properties": {"base_url": {"type": "string"}},
        "required": ["base_url"],
    },
}

_SKILL_MANIFEST = {
    "prompt_fragment": "Documenta SIEMPRE los contratos públicos.",
    "category": "docs",
    "targets": ["technical_writer"],
}


async def _seed(dsn: str) -> dict[str, UUID]:
    ids: dict[str, UUID] = {
        k: uuid4()
        for k in (
            "tenant_a",
            "tenant_b",
            "user_a",
            "user_b",
            "source",
            "listing_mcp",
            "listing_tool",
            "listing_skill",
            "team_a",
            "team_b",
            "agent_backend",
            "agent_qa",
            "agent_writer",
            "agent_b",
            "project_a",
            "project_a2",
            "project_b",
        )
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE marketplace_deployments, marketplace_audit_entries,"
            " marketplace_installations, marketplace_listing_versions,"
            " marketplace_listings, marketplace_sources, agent_tools, agent_skills,"
            " team_members, tools, skills, projects, agents, teams,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1,$2,$3),($4,$5,$6)",
            ids["tenant_a"],
            "Deploy A",
            "deploy-a",
            ids["tenant_b"],
            "Deploy B",
            "deploy-b",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1,$2,$3),($4,$5,$6)",
            ids["user_a"],
            "dep-a@test.test",
            "argon2-placeholder",
            ids["user_b"],
            "dep-b@test.test",
            "argon2-placeholder",
        )
        for key, tenant, user in (
            ("a", ids["tenant_a"], ids["user_a"]),
            ("b", ids["tenant_b"], ids["user_b"]),
        ):
            await conn.execute(
                "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
                " VALUES ($1,$2,$3,'tenant_admin')",
                uuid4(),
                tenant,
                user,
            )
            _ = key
        await conn.execute(
            "INSERT INTO marketplace_sources (id, name, source_type, is_trusted)"
            " VALUES ($1,'oficial-deploy','official',true)",
            ids["source"],
        )

        async def _listing(lid: UUID, kind: str, name: str, manifest: dict[str, Any]) -> None:
            await conn.execute(
                "INSERT INTO marketplace_listings"
                " (id, source_id, tenant_id, kind, name, version, trust_level, manifest,"
                "  requested_permissions)"
                " VALUES ($1,$2,NULL,$3,$4,'1.0.0','verified',$5::jsonb,'[]'::jsonb)",
                lid,
                ids["source"],
                kind,
                name,
                json.dumps(manifest),
            )

        await _listing(ids["listing_mcp"], "mcp_server", "jira-mcp", _MCP_MANIFEST)
        await _listing(ids["listing_tool"], "tool", "status-checker", _TOOL_MANIFEST)
        await _listing(ids["listing_skill"], "skill", "doc-contracts", _SKILL_MANIFEST)

        # Equipos + agentes.
        for team, tenant, name in (
            (ids["team_a"], ids["tenant_a"], "Equipo A"),
            (ids["team_b"], ids["tenant_b"], "Equipo B"),
        ):
            await conn.execute(
                "INSERT INTO teams (id, tenant_id, name) VALUES ($1,$2,$3)", team, tenant, name
            )
        for agent, tenant, name, role in (
            (ids["agent_backend"], ids["tenant_a"], "Bea Backend", "backend_dev"),
            (ids["agent_qa"], ids["tenant_a"], "Quim QA", "qa"),
            (ids["agent_writer"], ids["tenant_a"], "Wanda Writer", "technical_writer"),
            (ids["agent_b"], ids["tenant_b"], "Berto B", "backend_dev"),
        ):
            await conn.execute(
                "INSERT INTO agents (id, tenant_id, name, role, scope, system_prompt)"
                " VALUES ($1,$2,$3,$4,'global_tenant_template',$5)",
                agent,
                tenant,
                name,
                role,
                f"Eres {name}.",
            )
        for team, agent in (
            (ids["team_a"], ids["agent_backend"]),
            (ids["team_a"], ids["agent_qa"]),
            (ids["team_a"], ids["agent_writer"]),
            (ids["team_b"], ids["agent_b"]),
        ):
            await conn.execute(
                "INSERT INTO team_members (team_id, agent_id) VALUES ($1,$2)", team, agent
            )
        for project, tenant, team, name, slug in (
            (ids["project_a"], ids["tenant_a"], ids["team_a"], "Proyecto A1", "proj-a1"),
            (ids["project_a2"], ids["tenant_a"], ids["team_a"], "Proyecto A2", "proj-a2"),
            (ids["project_b"], ids["tenant_b"], ids["team_b"], "Proyecto B", "proj-b"),
        ):
            await conn.execute(
                "INSERT INTO projects (id, tenant_id, team_id, name, slug) VALUES ($1,$2,$3,$4,$5)",
                project,
                tenant,
                team,
                name,
                slug,
            )
    finally:
        await conn.close()
    return ids


async def _install(dsn: str, *, tenant: UUID, listing: UUID, user: UUID) -> UUID:
    """Instalación ENABLED + su fila materializada (lo que hace el ADR 0100).

    Se siembra a mano en vez de pasar por el endpoint porque este fichero prueba
    el SERVICIO de despliegue; la cadena completa (endpoint incluido) es
    `test_marketplace_v2_chain.py`.
    """
    installation = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT kind, name, manifest FROM marketplace_listings WHERE id = $1", listing
        )
        assert row is not None
        manifest = json.loads(row["manifest"])
        await conn.execute(
            "INSERT INTO marketplace_installations"
            " (id, tenant_id, listing_id, version, status, installed_by)"
            " VALUES ($1,$2,$3,'1.0.0','enabled',$4)",
            installation,
            tenant,
            listing,
            user,
        )
        if row["kind"] == "skill":
            await conn.execute(
                "INSERT INTO skills"
                " (id, tenant_id, name, category, prompt_fragment, source_listing_id,"
                "  source_installation_id, source_version)"
                " VALUES ($1,$2,$3,$4,$5,$6,$7,'1.0.0')",
                uuid4(),
                tenant,
                row["name"],
                manifest.get("category", "research"),
                manifest["prompt_fragment"],
                listing,
                installation,
            )
        else:
            await conn.execute(
                "INSERT INTO tools"
                " (id, tenant_id, name, category, implementation_type, implementation_ref,"
                "  security_level, source_listing_id, source_installation_id, source_version)"
                " VALUES ($1,$2,$3,'network',$4,$5,'sandboxed',$6,$7,'1.0.0')",
                uuid4(),
                tenant,
                row["name"],
                manifest["implementation_type"],
                manifest["implementation_ref"],
                listing,
                installation,
            )
    finally:
        await conn.close()
    return installation


async def _seed_namespaced_mcp_tools(dsn: str, tenant: UUID, names: list[str]) -> None:
    """Las filas `<server>.<tool>` que el import MCP (ADR 0052) crea.

    El despliegue escribe la política `mcp_tool_roles` sobre ESTAS claves, que
    son las que el runtime consulta. Sin ellas no hay nada que restringir — y el
    servicio lo avisa en vez de fingir.
    """
    conn = await asyncpg.connect(dsn)
    try:
        for name in names:
            await conn.execute(
                "INSERT INTO tools (id, tenant_id, name, category, implementation_type,"
                " implementation_ref, security_level)"
                " VALUES ($1,$2,$3,'mcp','mcp_tool',$4,'sandboxed')",
                uuid4(),
                tenant,
                name,
                f"mcp://{name}",
            )
    finally:
        await conn.close()


async def _project_row(dsn: str, project_id: UUID) -> asyncpg.Record:
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT mcp_servers, mcp_tool_roles FROM projects WHERE id = $1", project_id
        )
        assert row is not None
        return row
    finally:
        await conn.close()


# ===========================================================================
# mcp_server: mcp_servers + mcp_tool_roles, y NADA de agent_tools
# ===========================================================================
@pytest.mark.asyncio
async def test_mcp_deploy_writes_the_project_config_and_the_role_policy(
    configured_env: None, migrations_pg_dsn: str
) -> None:
    from api_server.auth.deps import open_tenant_session
    from api_server.marketplace.deploy import deploy_installation

    ids = await _seed(migrations_pg_dsn)
    installation = await _install(
        migrations_pg_dsn,
        tenant=ids["tenant_a"],
        listing=ids["listing_mcp"],
        user=ids["user_a"],
    )
    await _seed_namespaced_mcp_tools(
        migrations_pg_dsn, ids["tenant_a"], ["jira.create_issue", "jira.search"]
    )

    principal = _principal(ids["user_a"], ids["tenant_a"])
    async with open_tenant_session(principal) as session:
        result = await deploy_installation(
            session,
            installation_id=installation,
            project_id=ids["project_a"],
            config={"url": "https://jira-a.example.test/mcp"},
            role_map=None,  # cae a `targets: [backend_dev]` del manifest
            actor=_ACTOR,
            actor_user_id=ids["user_a"],
        )
        await session.commit()

    assert result.already_deployed is False
    assert result.kind == "mcp_server"

    row = await _project_row(migrations_pg_dsn, ids["project_a"])
    servers = json.loads(row["mcp_servers"])
    assert [s["name"] for s in servers] == ["jira"]
    assert servers[0]["url"] == "https://jira-a.example.test/mcp"
    assert servers[0]["transport"] == "streamable_http"

    policy = json.loads(row["mcp_tool_roles"])
    assert policy == {
        "jira.create_issue": ["backend_dev"],
        "jira.search": ["backend_dev"],
    }, policy

    # ADR 0142 §4 / ADR 0128: un mcp_server NO reparte grants por agente.
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        grants = await conn.fetchval("SELECT count(*) FROM agent_tools")
    finally:
        await conn.close()
    assert grants == 0, (
        "un despliegue mcp_server escribió agent_tools: eso es la política"
        " paralela que el ADR 0142 §4 prohíbe"
    )

    assert result.created_refs["mcp_servers"] == ["jira"]
    assert sorted(result.created_refs["mcp_tool_roles"]) == ["jira.create_issue", "jira.search"]


@pytest.mark.asyncio
async def test_two_projects_get_different_urls_for_the_same_installation(
    configured_env: None, migrations_pg_dsn: str
) -> None:
    """El caso que el modelo viejo no podía expresar (§1 del diseño)."""
    from api_server.auth.deps import open_tenant_session
    from api_server.marketplace.deploy import deploy_installation

    ids = await _seed(migrations_pg_dsn)
    installation = await _install(
        migrations_pg_dsn, tenant=ids["tenant_a"], listing=ids["listing_mcp"], user=ids["user_a"]
    )
    principal = _principal(ids["user_a"], ids["tenant_a"])

    for project, url in (
        (ids["project_a"], "https://a.example.test/mcp"),
        (ids["project_a2"], "https://a2.example.test/mcp"),
    ):
        async with open_tenant_session(principal) as session:
            await deploy_installation(
                session,
                installation_id=installation,
                project_id=project,
                config={"url": url},
                actor=_ACTOR,
                actor_user_id=ids["user_a"],
            )
            await session.commit()

    first = json.loads((await _project_row(migrations_pg_dsn, ids["project_a"]))["mcp_servers"])
    second = json.loads((await _project_row(migrations_pg_dsn, ids["project_a2"]))["mcp_servers"])
    assert first[0]["url"] == "https://a.example.test/mcp"
    assert second[0]["url"] == "https://a2.example.test/mcp"


# ===========================================================================
# tool / skill: agent_tools / agent_skills por rol
# ===========================================================================
@pytest.mark.asyncio
async def test_tool_deploy_grants_only_the_targeted_role(
    configured_env: None, migrations_pg_dsn: str
) -> None:
    from api_server.auth.deps import open_tenant_session
    from api_server.marketplace.deploy import deploy_installation

    ids = await _seed(migrations_pg_dsn)
    installation = await _install(
        migrations_pg_dsn, tenant=ids["tenant_a"], listing=ids["listing_tool"], user=ids["user_a"]
    )
    principal = _principal(ids["user_a"], ids["tenant_a"])
    async with open_tenant_session(principal) as session:
        result = await deploy_installation(
            session,
            installation_id=installation,
            project_id=ids["project_a"],
            config={"base_url": "https://app-a.example"},
            actor=_ACTOR,
            actor_user_id=ids["user_a"],
        )
        await session.commit()

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        rows = await conn.fetch(
            "SELECT at.agent_id, a.role, at.tenant_id FROM agent_tools at"
            " JOIN agents a ON a.id = at.agent_id"
        )
    finally:
        await conn.close()
    assert [r["role"] for r in rows] == ["qa"], (
        f"el `targets: [qa]` del manifest tiene que limitar el reparto: {[r['role'] for r in rows]}"
    )
    # El trigger de la 0124 estampa el tenant desde el agente propietario.
    assert rows[0]["tenant_id"] == ids["tenant_a"]
    assert len(result.created_refs["agent_tools"]) == 1


@pytest.mark.asyncio
async def test_skill_deploy_grants_agent_skills(
    configured_env: None, migrations_pg_dsn: str
) -> None:
    from api_server.auth.deps import open_tenant_session
    from api_server.marketplace.deploy import deploy_installation

    ids = await _seed(migrations_pg_dsn)
    installation = await _install(
        migrations_pg_dsn, tenant=ids["tenant_a"], listing=ids["listing_skill"], user=ids["user_a"]
    )
    principal = _principal(ids["user_a"], ids["tenant_a"])
    async with open_tenant_session(principal) as session:
        await deploy_installation(
            session,
            installation_id=installation,
            project_id=ids["project_a"],
            actor=_ACTOR,
            actor_user_id=ids["user_a"],
        )
        await session.commit()

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        roles = await conn.fetch(
            "SELECT a.role FROM agent_skills s JOIN agents a ON a.id = s.agent_id"
        )
    finally:
        await conn.close()
    assert [r["role"] for r in roles] == ["technical_writer"]


@pytest.mark.asyncio
async def test_explicit_role_map_overrides_the_manifest_targets(
    configured_env: None, migrations_pg_dsn: str
) -> None:
    """D5: el manifest sugiere, quien despliega decide."""
    from api_server.auth.deps import open_tenant_session
    from api_server.marketplace.deploy import deploy_installation

    ids = await _seed(migrations_pg_dsn)
    installation = await _install(
        migrations_pg_dsn, tenant=ids["tenant_a"], listing=ids["listing_tool"], user=ids["user_a"]
    )
    principal = _principal(ids["user_a"], ids["tenant_a"])
    async with open_tenant_session(principal) as session:
        await deploy_installation(
            session,
            installation_id=installation,
            project_id=ids["project_a"],
            config={"base_url": "https://app-a.example"},
            role_map=["backend_dev"],  # el manifest sugería `qa`
            actor=_ACTOR,
            actor_user_id=ids["user_a"],
        )
        await session.commit()

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        roles = await conn.fetch(
            "SELECT a.role FROM agent_tools t JOIN agents a ON a.id = t.agent_id"
        )
    finally:
        await conn.close()
    assert [r["role"] for r in roles] == ["backend_dev"]


# ===========================================================================
# Nodo 2: la retirada es EXACTA
# ===========================================================================
@pytest.mark.asyncio
async def test_retire_does_not_take_the_very_same_tool_the_operator_granted_by_hand(
    configured_env: None, migrations_pg_dsn: str
) -> None:
    """EL caso del plan, en su versión que muerde: **la MISMA tool**.

    El hermano de abajo siembra una tool *distinta* en el mismo agente, y eso
    solo caza el fallo «retirar limpia todas las tools del agente». El fallo
    fino es otro y es el que el plan nombra: el operador ya había asignado a
    mano **la tool del listing** a ese agente; el despliegue se la encuentra
    puesta, y si la ANOTA en `created_refs` como suya, al retirar se la lleva.

    (Comprobado con el ciclo RED-GREEN: al hacer que el despliegue anotase
    también las asignaciones preexistentes, el hermano de abajo seguía verde y
    ESTE se ponía rojo. Un test que no puede fallar no vale nada.)
    """
    from api_server.auth.deps import open_tenant_session
    from api_server.marketplace.deploy import deploy_installation, retire_deployment

    ids = await _seed(migrations_pg_dsn)
    installation = await _install(
        migrations_pg_dsn, tenant=ids["tenant_a"], listing=ids["listing_tool"], user=ids["user_a"]
    )

    # El operador se adelanta: asigna LA MISMA tool materializada al MISMO agente.
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        tool_id = await conn.fetchval(
            "SELECT id FROM tools WHERE source_installation_id = $1", installation
        )
        await conn.execute(
            "INSERT INTO agent_tools (agent_id, tool_id) VALUES ($1,$2)", ids["agent_qa"], tool_id
        )
    finally:
        await conn.close()

    principal = _principal(ids["user_a"], ids["tenant_a"])
    async with open_tenant_session(principal) as session:
        result = await deploy_installation(
            session,
            installation_id=installation,
            project_id=ids["project_a"],
            config={"base_url": "https://app-a.example"},
            actor=_ACTOR,
            actor_user_id=ids["user_a"],
        )
        await session.commit()

    assert "agent_tools" not in result.created_refs, (
        f"el despliegue se apuntó como suya una asignación que ya existía: {result.created_refs}"
    )
    assert any("ya tenía la tool" in w for w in result.warnings), result.warnings

    async with open_tenant_session(principal) as session:
        removed = await retire_deployment(
            session, deployment_id=result.deployment_id, actor=_ACTOR, actor_user_id=ids["user_a"]
        )
        await session.commit()
    assert removed == 0

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        survivors = await conn.fetch("SELECT agent_id, tool_id FROM agent_tools")
    finally:
        await conn.close()
    assert [(r["agent_id"], r["tool_id"]) for r in survivors] == [(ids["agent_qa"], tool_id)], (
        "la retirada se llevó la asignación que el OPERADOR había hecho a mano"
    )


@pytest.mark.asyncio
async def test_retire_removes_exactly_what_it_created_and_nothing_else(
    configured_env: None, migrations_pg_dsn: str
) -> None:
    """El test que justifica `created_refs`.

    Se siembra una tool asignada A MANO al agente `qa` (una tool distinta, del
    catálogo propio del tenant) y se comprueba que sobrevive a la retirada del
    despliegue. Sin `created_refs`, la retirada «limpia las tools del agente» y
    se lleva por delante el trabajo del operador.
    """
    from api_server.auth.deps import open_tenant_session
    from api_server.marketplace.deploy import deploy_installation, retire_deployment

    ids = await _seed(migrations_pg_dsn)
    installation = await _install(
        migrations_pg_dsn, tenant=ids["tenant_a"], listing=ids["listing_tool"], user=ids["user_a"]
    )

    manual_tool = uuid4()
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "INSERT INTO tools (id, tenant_id, name, category, implementation_type,"
            " implementation_ref, security_level)"
            " VALUES ($1,$2,'tool-del-operador','network','http_endpoint','https://x','sandboxed')",
            manual_tool,
            ids["tenant_a"],
        )
        await conn.execute(
            "INSERT INTO agent_tools (agent_id, tool_id) VALUES ($1,$2)",
            ids["agent_qa"],
            manual_tool,
        )
    finally:
        await conn.close()

    principal = _principal(ids["user_a"], ids["tenant_a"])
    async with open_tenant_session(principal) as session:
        result = await deploy_installation(
            session,
            installation_id=installation,
            project_id=ids["project_a"],
            config={"base_url": "https://app-a.example"},
            actor=_ACTOR,
            actor_user_id=ids["user_a"],
        )
        await session.commit()

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        assert await conn.fetchval("SELECT count(*) FROM agent_tools") == 2
    finally:
        await conn.close()

    async with open_tenant_session(principal) as session:
        removed = await retire_deployment(
            session,
            deployment_id=result.deployment_id,
            actor=_ACTOR,
            actor_user_id=ids["user_a"],
        )
        await session.commit()
    assert removed == 1

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        survivors = await conn.fetch("SELECT tool_id FROM agent_tools")
        status = await conn.fetchrow(
            "SELECT status, retired_at, created_refs FROM marketplace_deployments WHERE id = $1",
            result.deployment_id,
        )
        audit = await conn.fetch("SELECT action FROM marketplace_audit_entries ORDER BY created_at")
    finally:
        await conn.close()

    assert [r["tool_id"] for r in survivors] == [manual_tool], (
        "la retirada se llevó la tool que el OPERADOR había asignado a mano"
    )
    assert status is not None
    assert status["status"] == "retired" and status["retired_at"] is not None
    # La fila retirada conserva su rastro: se puede auditar qué creó.
    assert json.loads(status["created_refs"])["agent_tools"]
    assert [a["action"] for a in audit] == ["deploy", "retire"]


@pytest.mark.asyncio
async def test_retire_leaves_an_mcp_server_the_project_already_declared(
    configured_env: None, migrations_pg_dsn: str
) -> None:
    """La misma exactitud, del lado MCP: lo que ya estaba no se anota ni se quita."""
    from api_server.auth.deps import open_tenant_session
    from api_server.marketplace.deploy import deploy_installation, retire_deployment

    ids = await _seed(migrations_pg_dsn)
    installation = await _install(
        migrations_pg_dsn, tenant=ids["tenant_a"], listing=ids["listing_mcp"], user=ids["user_a"]
    )
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "UPDATE projects SET mcp_servers = $2::jsonb WHERE id = $1",
            ids["project_a"],
            json.dumps(
                [
                    {
                        "name": "jira",
                        "transport": "streamable_http",
                        "url": "https://el-jira-del-operador.test/mcp",
                    }
                ]
            ),
        )
    finally:
        await conn.close()

    principal = _principal(ids["user_a"], ids["tenant_a"])
    async with open_tenant_session(principal) as session:
        result = await deploy_installation(
            session,
            installation_id=installation,
            project_id=ids["project_a"],
            config={"url": "https://jira-del-marketplace.test/mcp"},
            actor=_ACTOR,
            actor_user_id=ids["user_a"],
        )
        await session.commit()

    assert "mcp_servers" not in result.created_refs
    assert any("ya declaraba" in w for w in result.warnings), result.warnings

    async with open_tenant_session(principal) as session:
        await retire_deployment(
            session, deployment_id=result.deployment_id, actor=_ACTOR, actor_user_id=ids["user_a"]
        )
        await session.commit()

    servers = json.loads((await _project_row(migrations_pg_dsn, ids["project_a"]))["mcp_servers"])
    assert servers[0]["url"] == "https://el-jira-del-operador.test/mcp", (
        "la retirada se llevó (o pisó) el servidor MCP que el operador ya tenía"
    )


# ===========================================================================
# Nodo 3: idempotencia
# ===========================================================================
@pytest.mark.asyncio
async def test_second_deploy_is_a_noop_with_a_warning(
    configured_env: None, migrations_pg_dsn: str
) -> None:
    from api_server.auth.deps import open_tenant_session
    from api_server.marketplace.deploy import deploy_installation

    ids = await _seed(migrations_pg_dsn)
    installation = await _install(
        migrations_pg_dsn, tenant=ids["tenant_a"], listing=ids["listing_tool"], user=ids["user_a"]
    )
    principal = _principal(ids["user_a"], ids["tenant_a"])

    async with open_tenant_session(principal) as session:
        first = await deploy_installation(
            session,
            installation_id=installation,
            project_id=ids["project_a"],
            config={"base_url": "https://app-a.example"},
            actor=_ACTOR,
        )
        await session.commit()

    async with open_tenant_session(principal) as session:
        second = await deploy_installation(
            session,
            installation_id=installation,
            project_id=ids["project_a"],
            config={"base_url": "https://OTRA.example"},
            actor=_ACTOR,
        )
        await session.commit()

    assert second.already_deployed is True
    assert second.deployment_id == first.deployment_id
    assert second.warnings

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        count = await conn.fetchval("SELECT count(*) FROM marketplace_deployments")
        config = await conn.fetchval(
            "SELECT config FROM marketplace_deployments WHERE id = $1", first.deployment_id
        )
        grants = await conn.fetchval("SELECT count(*) FROM agent_tools")
    finally:
        await conn.close()
    assert count == 1
    assert json.loads(config)["base_url"] == "https://app-a.example", (
        "el segundo despliegue pisó la config del primero: no era un no-op"
    )
    assert grants == 1


# ===========================================================================
# Nodo 4: config inválida ⇒ nada escrito
# ===========================================================================
@pytest.mark.asyncio
async def test_invalid_config_writes_nothing_at_all(
    configured_env: None, migrations_pg_dsn: str
) -> None:
    from api_server.auth.deps import open_tenant_session
    from api_server.marketplace.deploy import DeployError, deploy_installation

    ids = await _seed(migrations_pg_dsn)
    installation = await _install(
        migrations_pg_dsn, tenant=ids["tenant_a"], listing=ids["listing_mcp"], user=ids["user_a"]
    )
    principal = _principal(ids["user_a"], ids["tenant_a"])

    with pytest.raises(DeployError) as excinfo:
        async with open_tenant_session(principal) as session:
            await deploy_installation(
                session,
                installation_id=installation,
                project_id=ids["project_a"],
                # `url` requerido ausente + secreto en claro en `auth_ref`.
                config={"auth_ref": "ghp_en_claro"},
                actor=_ACTOR,
            )
            await session.commit()

    assert excinfo.value.status_code == 422
    joined = " ".join(excinfo.value.errors)
    assert "url" in joined
    assert "ghp_en_claro" not in joined, "el error de validación filtró el secreto"

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        deployments = await conn.fetchval("SELECT count(*) FROM marketplace_deployments")
        servers = await conn.fetchval(
            "SELECT mcp_servers FROM projects WHERE id = $1", ids["project_a"]
        )
    finally:
        await conn.close()
    assert deployments == 0
    assert json.loads(servers) == [], "quedó media entrada MCP escrita tras una config inválida"


# ===========================================================================
# Nodo 1: cross-tenant
# ===========================================================================
@pytest.mark.asyncio
async def test_installation_of_tenant_a_cannot_be_deployed_into_a_project_of_tenant_b(
    configured_env: None, migrations_pg_dsn: str
) -> None:
    from api_server.auth.deps import open_tenant_session
    from api_server.marketplace.deploy import DeployNotFoundError, deploy_installation

    ids = await _seed(migrations_pg_dsn)
    installation = await _install(
        migrations_pg_dsn, tenant=ids["tenant_a"], listing=ids["listing_mcp"], user=ids["user_a"]
    )

    principal_a = _principal(ids["user_a"], ids["tenant_a"])
    with pytest.raises(DeployNotFoundError):
        async with open_tenant_session(principal_a) as session:
            await deploy_installation(
                session,
                installation_id=installation,
                project_id=ids["project_b"],  # ¡del OTRO tenant!
                config={"url": "https://x.test/mcp"},
                actor=_ACTOR,
            )

    # Y al revés: el tenant B no puede ni ver la instalación de A.
    principal_b = _principal(ids["user_b"], ids["tenant_b"])
    with pytest.raises(DeployNotFoundError):
        async with open_tenant_session(principal_b) as session:
            await deploy_installation(
                session,
                installation_id=installation,
                project_id=ids["project_b"],
                config={"url": "https://x.test/mcp"},
                actor=_ACTOR,
            )

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        assert await conn.fetchval("SELECT count(*) FROM marketplace_deployments") == 0
        servers = await conn.fetchval(
            "SELECT mcp_servers FROM projects WHERE id = $1", ids["project_b"]
        )
    finally:
        await conn.close()
    assert json.loads(servers) == []


@pytest.mark.asyncio
async def test_tenant_b_cannot_see_the_deployment_of_tenant_a(
    configured_env: None, migrations_pg_dsn: str
) -> None:
    from api_server.auth.deps import open_tenant_session
    from api_server.db.marketplace import MarketplaceDeployment
    from api_server.marketplace.deploy import deploy_installation
    from sqlalchemy import select

    ids = await _seed(migrations_pg_dsn)
    installation = await _install(
        migrations_pg_dsn, tenant=ids["tenant_a"], listing=ids["listing_tool"], user=ids["user_a"]
    )
    async with open_tenant_session(_principal(ids["user_a"], ids["tenant_a"])) as session:
        await deploy_installation(
            session,
            installation_id=installation,
            project_id=ids["project_a"],
            config={"base_url": "https://app-a.example"},
            actor=_ACTOR,
        )
        await session.commit()

    async with open_tenant_session(_principal(ids["user_b"], ids["tenant_b"])) as session:
        rows = (await session.execute(select(MarketplaceDeployment))).scalars().all()
    assert rows == [], "FUGA CROSS-TENANT: el tenant B lee los despliegues del A"


# ===========================================================================
# Estado de la instalación + el pin
# ===========================================================================
@pytest.mark.asyncio
async def test_a_disabled_installation_cannot_be_deployed(
    configured_env: None, migrations_pg_dsn: str
) -> None:
    from api_server.auth.deps import open_tenant_session
    from api_server.marketplace.deploy import DeployConflictError, deploy_installation

    ids = await _seed(migrations_pg_dsn)
    installation = await _install(
        migrations_pg_dsn, tenant=ids["tenant_a"], listing=ids["listing_tool"], user=ids["user_a"]
    )
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "UPDATE marketplace_installations SET status='disabled' WHERE id=$1", installation
        )
    finally:
        await conn.close()

    with pytest.raises(DeployConflictError):
        async with open_tenant_session(_principal(ids["user_a"], ids["tenant_a"])) as session:
            await deploy_installation(
                session,
                installation_id=installation,
                project_id=ids["project_a"],
                config={"base_url": "https://app-a.example"},
                actor=_ACTOR,
            )


async def _private_listing(dsn: str, tenant: UUID, listing_id: UUID) -> None:
    """Un listing PRIVADO del tenant (el caso en el que sí puede versionar)."""
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO marketplace_listings"
            " (id, source_id, tenant_id, kind, name, version, trust_level, manifest,"
            "  requested_permissions)"
            " SELECT $1, source_id, $2, kind, 'tool-privada', version, trust_level, manifest,"
            "        requested_permissions"
            "   FROM marketplace_listings WHERE id = $3",
            listing_id,
            tenant,
            # se clona el listing `tool` para heredar su manifest/config_schema
            await conn.fetchval(
                "SELECT id FROM marketplace_listings WHERE name = 'status-checker'"
            ),
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_deploy_pins_the_version_of_an_unpinned_private_installation(
    configured_env: None, migrations_pg_dsn: str
) -> None:
    """El hueco que `ensure_listing_version` tapa mientras la fase 3 no exista.

    Una instalación creada DESPUÉS de la 0128 nace sin pin (su escritor es la
    fase 3/4). Para un listing PRIVADO del tenant, el primer despliegue crea la
    fila de versión y la pina, así que el pin no falta donde importa.
    """
    from api_server.auth.deps import open_tenant_session
    from api_server.marketplace.deploy import deploy_installation

    ids = await _seed(migrations_pg_dsn)
    private_listing = uuid4()
    await _private_listing(migrations_pg_dsn, ids["tenant_a"], private_listing)
    installation = await _install(
        migrations_pg_dsn, tenant=ids["tenant_a"], listing=private_listing, user=ids["user_a"]
    )
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        assert (
            await conn.fetchval(
                "SELECT pinned_version_id FROM marketplace_installations WHERE id=$1", installation
            )
            is None
        )
    finally:
        await conn.close()

    async with open_tenant_session(_principal(ids["user_a"], ids["tenant_a"])) as session:
        await deploy_installation(
            session,
            installation_id=installation,
            project_id=ids["project_a"],
            config={"base_url": "https://app-a.example"},
            actor=_ACTOR,
        )
        await session.commit()

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        pinned = await conn.fetchrow(
            "SELECT v.version, v.listing_id, v.tenant_id FROM marketplace_installations i"
            " JOIN marketplace_listing_versions v ON v.id = i.pinned_version_id"
            " WHERE i.id = $1",
            installation,
        )
    finally:
        await conn.close()
    assert pinned is not None
    assert pinned["version"] == "1.0.0"
    assert pinned["listing_id"] == private_listing
    assert pinned["tenant_id"] == ids["tenant_a"]


@pytest.mark.asyncio
async def test_a_tenant_cannot_author_the_version_history_of_the_global_catalog(
    configured_env: None, migrations_pg_dsn: str
) -> None:
    """Hallazgo real de esta implementación, fijado como contrato.

    Un listing GLOBAL (`tenant_id IS NULL`) publicado DESPUÉS de la migración no
    tiene fila de versión. La primera versión del servicio intentaba crearla
    desde la sesión del tenant que despliega, y la RLS lo rechazaba con
    `InsufficientPrivilegeError` — correctamente: el histórico del catálogo
    oficial es de la plataforma, no de quien lo instala.

    Lo que se exige ahora: el despliegue **funciona igual** (leyendo el manifest
    vivo), **no** se fabrica ninguna fila de versión global, y el pin se queda
    como estaba. Si alguien "arregla" esto insertando la fila, este test se pone
    rojo y hace bien.
    """
    from api_server.auth.deps import open_tenant_session
    from api_server.marketplace.deploy import deploy_installation

    ids = await _seed(migrations_pg_dsn)
    installation = await _install(
        migrations_pg_dsn, tenant=ids["tenant_a"], listing=ids["listing_tool"], user=ids["user_a"]
    )

    async with open_tenant_session(_principal(ids["user_a"], ids["tenant_a"])) as session:
        result = await deploy_installation(
            session,
            installation_id=installation,
            project_id=ids["project_a"],
            config={"base_url": "https://app-a.example"},
            actor=_ACTOR,
        )
        await session.commit()
    assert result.already_deployed is False

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        rows = await conn.fetchval(
            "SELECT count(*) FROM marketplace_listing_versions WHERE listing_id = $1",
            ids["listing_tool"],
        )
        pin = await conn.fetchval(
            "SELECT pinned_version_id FROM marketplace_installations WHERE id = $1", installation
        )
        grants = await conn.fetchval("SELECT count(*) FROM agent_tools")
    finally:
        await conn.close()
    assert rows == 0, "un tenant fabricó una fila de versión del catálogo GLOBAL"
    assert pin is None
    assert grants == 1, "el despliegue tenía que funcionar igual sin fila de versión"
