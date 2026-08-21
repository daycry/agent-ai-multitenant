"""Fork "Personalizar (crear copia)" copia las capacidades (06.17 task_06_17_12).

Plan 06.17 Fase D. Hasta ahora ``POST /agents/{id}/fork`` clonaba solo la fila
``agents`` (persona + model_config) pero NO las capacidades asignadas: el fork
nacía sin las KBs (SABER), sin las tools (HACER) y sin las skills (SER) del
origen. Esta tarea hace que el fork **herede** las tres junctions del origen:

  * ``agent_knowledge_bases`` (KBs granteadas al rol del agente).
  * ``agent_tools`` (tools asignadas, con su ``config_override``).
  * ``agent_skills`` (skills asignadas, con su ``proficiency``).

Lo que estos tests blindan (contrato):

  * El fork de un built-in/template **hereda** las KBs, tools y skills del origen.
  * El ``config_override`` de las tools y la ``proficiency`` de las skills se
    copian fielmente.
  * **Aislamiento multi-tenant** (``cross_tenant``): el origen y todas sus
    capacidades viven en el tenant del que forkea; un fork NUNCA copia recursos
    de otro tenant. Las filas clonadas llevan el ``tenant_id`` del que forkea.
  * El **badge Linked/Forked** es coherente con ``forked_from_agent_id`` (el
    fork lo trae no nulo; el origen lo trae nulo).
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
#   - project_a (destino del fork).
#   - source_agent: global_tenant_template con KBs/tools/skills asignadas.
#       * kb_role (tenant A)  -> agent_knowledge_bases
#       * tool_builtin (platform) + tool_custom (tenant A) -> agent_tools
#           (tool_custom con config_override no nulo)
#       * skill_builtin (platform) + skill_custom (tenant A) -> agent_skills
#           (skill_custom con proficiency != default)
#
# Tenant B: su propio agente + KB/tool/skill, para el aislamiento cross-tenant.
# ---------------------------------------------------------------------------
async def _seed(dsn: str) -> dict[str, UUID]:
    tenant_a = uuid4()
    tenant_b = uuid4()
    admin_a = uuid4()
    admin_b = uuid4()
    project_a = uuid4()
    project_b = uuid4()
    source_agent = uuid4()
    agent_b = uuid4()
    builtin_agent = uuid4()

    kb_role = uuid4()
    kb_b = uuid4()
    tool_builtin = uuid4()
    tool_custom = uuid4()
    tool_b = uuid4()
    skill_builtin = uuid4()
    skill_custom = uuid4()
    skill_b = uuid4()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE agent_skills, agent_tools, agent_knowledge_bases, kb_projects,"
            " skills, tools, knowledge_bases, team_members, teams, agents, projects,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug)"
            " VALUES ($1, $2, $3), ($4, $5, $6), ($7, $8, $9)",
            tenant_a,
            "Acme",
            "acme-fork",
            tenant_b,
            "Globex",
            "globex-fork",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-fork",
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
            "INSERT INTO projects (id, tenant_id, name)"
            " VALUES ($1, $2, 'Webapp'), ($3, $4, 'B-app')",
            project_a,
            tenant_a,
            project_b,
            tenant_b,
        )
        # source_agent: template del tenant A (forkeable). agent_b: del tenant B.
        await conn.execute(
            "INSERT INTO agents"
            " (id, tenant_id, name, role, scope, agent_type, system_prompt,"
            "  project_id, memory_scope, model_config)"
            " VALUES"
            " ($1, $2, 'backend-template', 'backend_dev', 'global_tenant_template', 'ai', 'p',"
            "  NULL, 'project_shared', $3),"
            " ($4, $5, 'b-dev', 'backend_dev', 'project_local', 'ai', 'p',"
            "  $6, 'private', '{}')",
            source_agent,
            tenant_a,
            '{"provider": "ollama", "model": "qwen2.5-coder", "temperature": 0.2}',
            agent_b,
            tenant_b,
            project_b,
        )
        # Built-in de PLATAFORMA (scope=global_builtin, tenant de plataforma):
        # visible a todos los tenants por la policy `agents_global_builtin_read`
        # (migración 0004) y forkeable, pero NO granteable en sí mismo.
        # Necesario para human_06_9_04: "Si forkeas el built-in → la copia SÍ
        # permite grant".
        await conn.execute(
            "INSERT INTO agents"
            " (id, tenant_id, name, role, scope, agent_type, system_prompt, model_config)"
            " VALUES ($1, $2, 'builtin-pm', 'project_manager', 'global_builtin', 'ai', 'p', '{}')",
            builtin_agent,
            _PLATFORM_TENANT_ID,
        )
        # KBs: role (tenant A) / b (tenant B).
        await conn.execute(
            "INSERT INTO knowledge_bases (id, tenant_id, name, is_builtin)"
            " VALUES ($1, $2, 'Role KB', false), ($3, $4, 'B KB', false)",
            kb_role,
            tenant_a,
            kb_b,
            tenant_b,
        )
        # Tools: builtin (platform) / custom (tenant A) / b (tenant B).
        await conn.execute(
            "INSERT INTO tools"
            " (id, tenant_id, name, description, category,"
            "  implementation_type, security_level, is_builtin)"
            " VALUES"
            " ($1, $2, 'read_file', 'read', 'file', 'builtin', 'safe', true),"
            " ($3, $4, 'custom_lint', 'lint', 'custom', 'python_function', 'safe', false),"
            " ($5, $6, 'b_tool', 'b', 'custom', 'python_function', 'safe', false)",
            tool_builtin,
            _PLATFORM_TENANT_ID,
            tool_custom,
            tenant_a,
            tool_b,
            tenant_b,
        )
        # Skills: builtin (platform) / custom (tenant A) / b (tenant B).
        await conn.execute(
            "INSERT INTO skills"
            " (id, tenant_id, name, category, prompt_fragment, is_builtin)"
            " VALUES"
            " ($1, $2, 'tdd', 'qa', 'Practica TDD.', true),"
            " ($3, $4, 'house-style', 'backend', 'Sigue el estilo de la casa.', false),"
            " ($5, $6, 'b-skill', 'backend', 'B.', false)",
            skill_builtin,
            _PLATFORM_TENANT_ID,
            skill_custom,
            tenant_a,
            skill_b,
            tenant_b,
        )
        # source_agent capabilities (tenant A).
        await conn.execute(
            "INSERT INTO agent_knowledge_bases (agent_id, kb_id, tenant_id) VALUES ($1, $2, $3)",
            source_agent,
            kb_role,
            tenant_a,
        )
        await conn.execute(
            "INSERT INTO agent_tools (agent_id, tool_id, config_override)"
            " VALUES ($1, $2, NULL), ($1, $3, $4::jsonb)",
            source_agent,
            tool_builtin,
            tool_custom,
            '{"max_lines": 200}',
        )
        await conn.execute(
            "INSERT INTO agent_skills (agent_id, skill_id, proficiency)"
            " VALUES ($1, $2, 'standard'), ($1, $3, 'expert')",
            source_agent,
            skill_builtin,
            skill_custom,
        )
        # agent_b capabilities (tenant B) — para confirmar aislamiento.
        await conn.execute(
            "INSERT INTO agent_knowledge_bases (agent_id, kb_id, tenant_id) VALUES ($1, $2, $3)",
            agent_b,
            kb_b,
            tenant_b,
        )
        await conn.execute(
            "INSERT INTO agent_tools (agent_id, tool_id) VALUES ($1, $2)",
            agent_b,
            tool_b,
        )
        await conn.execute(
            "INSERT INTO agent_skills (agent_id, skill_id) VALUES ($1, $2)",
            agent_b,
            skill_b,
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
        "source_agent": source_agent,
        "agent_b": agent_b,
        "builtin_agent": builtin_agent,
        "kb_role": kb_role,
        "kb_b": kb_b,
        "tool_builtin": tool_builtin,
        "tool_custom": tool_custom,
        "tool_b": tool_b,
        "skill_builtin": skill_builtin,
        "skill_custom": skill_custom,
        "skill_b": skill_b,
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
# El fork hereda KBs / tools / skills del origen.
# ===========================================================================
@pytest.mark.asyncio
async def test_fork_inherits_capabilities(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        fork_resp = await client.post(
            f"/agents/{seeded['source_agent']}/fork",
            json={"project_id": str(seeded["project_a"])},
            headers=headers,
        )
        assert fork_resp.status_code == 201, fork_resp.text
        fork = fork_resp.json()
        fork_id = fork["id"]

        # Badge derivado de forked_from_agent_id (no del scope).
        assert fork["forked_from_agent_id"] == str(seeded["source_agent"])
        assert UUID(fork["tenant_id"]) == seeded["tenant_a"]

        # SABER: la KB de rol se heredó.
        kbs = (await client.get(f"/agents/{fork_id}/knowledge-bases", headers=headers)).json()
        assert {row["kb_id"] for row in kbs} == {str(seeded["kb_role"])}

        # HACER: las dos tools (builtin + custom) se heredaron con su override.
        tools = (await client.get(f"/agents/{fork_id}/tools", headers=headers)).json()
        by_id = {t["tool_id"]: t for t in tools}
        assert set(by_id) == {str(seeded["tool_builtin"]), str(seeded["tool_custom"])}
        assert by_id[str(seeded["tool_builtin"])]["config_override"] is None
        assert by_id[str(seeded["tool_custom"])]["config_override"] == {"max_lines": 200}

        # SER: las dos skills (builtin + custom) se heredaron.
        skills = (await client.get(f"/agents/{fork_id}/skills", headers=headers)).json()
        assert {s["skill_id"] for s in skills} == {
            str(seeded["skill_builtin"]),
            str(seeded["skill_custom"]),
        }


@pytest.mark.asyncio
async def test_fork_proficiency_is_preserved(configured_app, migrations_pg_dsn: str) -> None:
    """La ``proficiency`` de cada agent_skill viaja al fork (no se aplana a default)."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        fork = (
            await client.post(
                f"/agents/{seeded['source_agent']}/fork",
                json={"project_id": str(seeded["project_a"])},
                headers=headers,
            )
        ).json()

    # Verificación directa en BD: la fila clonada conserva proficiency='expert'.
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        prof = await conn.fetchval(
            "SELECT proficiency FROM agent_skills WHERE agent_id = $1 AND skill_id = $2",
            UUID(fork["id"]),
            seeded["skill_custom"],
        )
    finally:
        await conn.close()
    assert prof == "expert"


@pytest.mark.asyncio
async def test_fork_clones_rows_under_forking_tenant(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Las junctions clonadas llevan el tenant del que forkea (no fugas)."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        fork = (
            await client.post(
                f"/agents/{seeded['source_agent']}/fork",
                json={"project_id": str(seeded["project_a"])},
                headers=headers,
            )
        ).json()

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        kb_tenant = await conn.fetchval(
            "SELECT tenant_id FROM agent_knowledge_bases WHERE agent_id = $1",
            UUID(fork["id"]),
        )
    finally:
        await conn.close()
    assert kb_tenant == seeded["tenant_a"]


# ===========================================================================
# Aislamiento multi-tenant: un fork NUNCA copia recursos de otro tenant.
# ===========================================================================
@pytest.mark.asyncio
async def test_fork_source_of_other_tenant_returns_404(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Tenant A no puede forkear el agente (custom) del tenant B: RLS lo oculta
    -> 404. Ningún recurso de B termina copiado en A."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/agents/{seeded['agent_b']}/fork",
            json={"project_id": str(seeded["project_a"])},
            headers=headers,
        )
    assert resp.status_code == 404, resp.text
    assert "source agent not found" in resp.text.lower()


@pytest.mark.asyncio
async def test_fork_does_not_leak_other_tenant_capabilities(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Tenant B forkea su propio agente: el fork hereda SOLO los recursos de B;
    ninguna KB/tool/skill del tenant A aparece en el fork de B.

    El `name` explícito NO es decorativo: `agent_b` ya es `project_local` de
    `project_b`, así que forkearlo A SU PROPIO PROYECTO sin renombrar repite el
    par `(project_b, 'b-dev')` y choca con `uq_agents_tenant_project_name_live`
    (migración 0126, 2026-07-30). Este test lleva roto desde entonces —el fork
    salía como 500 y el `.json()` no traía `id`—; lo que comprueba es el
    AISLAMIENTO de capacidades, no la herencia del nombre, así que se le da uno
    libre y sigue midiendo lo suyo.
    """
    seeded = await _seed(migrations_pg_dsn)
    token_b = await _mint(seeded["admin_b"], seeded["tenant_b"])
    headers = {"Authorization": f"Bearer {token_b}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/agents/{seeded['agent_b']}/fork",
            json={"project_id": str(seeded["project_b"]), "name": "b-dev (copia)"},
            headers=headers,
        )
        assert response.status_code == 201, response.text
        fork_id = response.json()["id"]

        kbs = (await client.get(f"/agents/{fork_id}/knowledge-bases", headers=headers)).json()
        assert {row["kb_id"] for row in kbs} == {str(seeded["kb_b"])}
        assert str(seeded["kb_role"]) not in {row["kb_id"] for row in kbs}

        tools = (await client.get(f"/agents/{fork_id}/tools", headers=headers)).json()
        tool_ids = {t["tool_id"] for t in tools}
        assert tool_ids == {str(seeded["tool_b"])}
        assert str(seeded["tool_custom"]) not in tool_ids

        skills = (await client.get(f"/agents/{fork_id}/skills", headers=headers)).json()
        skill_ids = {s["skill_id"] for s in skills}
        assert skill_ids == {str(seeded["skill_b"])}
        assert str(seeded["skill_custom"]) not in skill_ids


@pytest.mark.asyncio
async def test_diff_exposes_capabilities(configured_app, migrations_pg_dsn: str) -> None:
    """El diff fork-vs-source expone los sets de KBs/tools/skills de cada lado
    (Plan 06.17 task_06_17_12): tras forkear, ambos lados coinciden."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        fork = (
            await client.post(
                f"/agents/{seeded['source_agent']}/fork",
                json={"project_id": str(seeded["project_a"])},
                headers=headers,
            )
        ).json()
        diff = (await client.get(f"/agents/{fork['id']}/diff", headers=headers)).json()

    caps = diff["capabilities"]
    assert set(caps) == {"fork", "source"}
    # Recién forkeado: el fork hereda exactamente las capacidades del origen.
    assert caps["fork"]["kb_ids"] == [str(seeded["kb_role"])]
    assert set(caps["fork"]["tool_ids"]) == {
        str(seeded["tool_builtin"]),
        str(seeded["tool_custom"]),
    }
    assert set(caps["fork"]["skill_ids"]) == {
        str(seeded["skill_builtin"]),
        str(seeded["skill_custom"]),
    }
    assert caps["fork"]["kb_ids"] == caps["source"]["kb_ids"]
    assert set(caps["fork"]["tool_ids"]) == set(caps["source"]["tool_ids"])
    assert set(caps["fork"]["skill_ids"]) == set(caps["source"]["skill_ids"])


@pytest.mark.asyncio
async def test_source_badge_is_not_a_fork(configured_app, migrations_pg_dsn: str) -> None:
    """El badge Linked/Forked se deriva de forked_from_agent_id: el origen
    (un template) lo trae nulo -> NO es un fork."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        src = (await client.get(f"/agents/{seeded['source_agent']}", headers=headers)).json()
    assert src["forked_from_agent_id"] is None


# ===========================================================================
# human_06_9_04 — "Si forkeas el built-in → la copia SÍ permite grant"
#
# Este fichero clonaba capacidades pero NUNCA hacía un grant NUEVO sobre el
# fork, así que la mitad afirmativa del test humano (el built-in se cierra,
# su fork se abre) no la sostenía ningún test. Las dos mitades van juntas a
# propósito: un 201 sobre el fork solo significa algo si el 403 sobre el
# built-in ocurre en la MISMA corrida y con el MISMO token.
# ===========================================================================
@pytest.mark.asyncio
async def test_builtin_rejects_grant_but_its_fork_accepts_one(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        # (1) Grant DIRECTO sobre el built-in de PLATAFORMA → rechazado.
        #
        # DIVERGENCIA, documentada en vez de maquillada: el checklist humano
        # dice "403 con mensaje claro" y `_load_writable_agent_for_kb`
        # (routers/agents.py) tiene esa rama, pero `get_writable_or_404` filtra
        # ANTES por `tenant_id == principal.tenant_id`. Los built-ins REALES
        # viven en el tenant de plataforma (seeds/builtin_agents.py,
        # ci4_team.py, qa_e2e_automator.py), así que lo que devuelven de verdad
        # es 404 "agent not found". La rama 403 solo se alcanza con un built-in
        # cuyo tenant_id coincide con el del llamante — configuración que los
        # seeds NUNCA producen y que es la que monta
        # test_agent_kb_grants.py::test_grant_on_builtin_agent_is_403.
        # Para el test humano el fondo se sostiene igual (el built-in no acepta
        # el grant, su fork sí); lo que no se sostiene es el código 403.
        denied = await client.post(
            f"/agents/{seeded['builtin_agent']}/knowledge-bases",
            headers=headers,
            json={"kb_id": str(seeded["kb_role"])},
        )
        assert denied.status_code == 404, denied.text
        assert denied.json()["detail"] == "agent not found"
        # El rechazo no escribió nada: el built-in sigue sin la KB.
        builtin_kbs = (
            await client.get(f"/agents/{seeded['builtin_agent']}/knowledge-bases", headers=headers)
        ).json()
        assert builtin_kbs == []

        # (2) "Personalizar (crear copia)" del built-in.
        fork_resp = await client.post(
            f"/agents/{seeded['builtin_agent']}/fork",
            json={"project_id": str(seeded["project_a"])},
            headers=headers,
        )
        assert fork_resp.status_code == 201, fork_resp.text
        fork = fork_resp.json()
        fork_id = fork["id"]
        # El fork es del tenant que forkea y deja de ser global_builtin (es lo
        # que lo hace granteable).
        assert UUID(fork["tenant_id"]) == seeded["tenant_a"]
        assert fork["scope"] != "global_builtin"
        assert fork["forked_from_agent_id"] == str(seeded["builtin_agent"])

        # El fork de un built-in de PLATAFORMA nace sin KBs: las del built-in
        # viven en el tenant de plataforma y RLS no las hace visibles (ADR 0026
        # — el tenant grantea las suyas al fork).
        before = (await client.get(f"/agents/{fork_id}/knowledge-bases", headers=headers)).json()
        assert before == []

        # (3) Grant NUEVO sobre el fork → 201.
        granted = await client.post(
            f"/agents/{fork_id}/knowledge-bases",
            headers=headers,
            json={"kb_id": str(seeded["kb_role"])},
        )
        assert granted.status_code == 201, granted.text
        assert granted.json()["kb_id"] == str(seeded["kb_role"])

        # Y el grant es REAL: aparece en el listado del fork...
        after = (await client.get(f"/agents/{fork_id}/knowledge-bases", headers=headers)).json()
        assert [row["kb_id"] for row in after] == [str(seeded["kb_role"])]
        # ...y en el panel «Asignaciones» inverso de la KB (Plan 06.9).
        assigned = (
            await client.get(f"/knowledge-bases/{seeded['kb_role']}/agents", headers=headers)
        ).json()
        assert fork_id in {row["agent_id"] for row in assigned}

    # La fila clonada lleva el tenant del que forkea, nunca el de plataforma.
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        kb_tenant = await conn.fetchval(
            "SELECT tenant_id FROM agent_knowledge_bases WHERE agent_id = $1",
            UUID(fork_id),
        )
    finally:
        await conn.close()
    assert kb_tenant == seeded["tenant_a"]
