"""El panel «Asignaciones» de una KB, de punta a punta (Plan 06.9 human_06_9_03).

`GET /knowledge-bases/{kb_id}/projects` y `GET /knowledge-bases/{kb_id}/agents`
son los DOS listados inversos que alimenta el diálogo «Asignaciones» del detalle
de KB en el admin-panel (`routers/knowledge_bases.py`, task_06_9_05). Existían
desde el plan 06.9 y **ningún test los invocaba**: el mecanismo estaba entregado
y sin llamantes en la suite — exactamente el patrón que
`docs/03-guides/verificar-antes-de-implementar.md` §5 describe.

Lo que estos tests fijan como contrato (las cuatro líneas del checklist humano):

  * «panel Asignaciones lista proyectos y agentes» → tras un grant a un
    proyecto y otro a un agente, cada GET inverso devuelve SU fila con el
    nombre legible (y, en el caso del agente, el `scope` que la UI usa para el
    badge Linked/Forked);
  * «Click Revoke en una fila de proyecto → desaparece de la lista»;
  * «El proyecto afectado ya no ve la KB al refrescar» → se comprueba por el
    listado directo `GET /projects/{id}/knowledge-bases`, no solo por el
    inverso;
  * «Mismo flow para revoke desde un agente».

Y el que el checklist humano no puede comprobar a ojo: **aislamiento
multi-tenant**. Un tenant no ve —ni por asomo— las asignaciones del otro, y su
propio panel no arrastra el proyecto/agente del vecino.
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


# ---------------------------------------------------------------------------
# Seed: dos tenants simétricos, cada uno con su KB, su proyecto y su agente
# (template, forkeable/granteable). La simetría es deliberada: el test
# cross-tenant compara los DOS paneles, no solo comprueba un 404.
# ---------------------------------------------------------------------------
async def _seed(dsn: str) -> dict[str, UUID]:
    ids = {
        "tenant_a": uuid4(),
        "tenant_b": uuid4(),
        "admin_a": uuid4(),
        "member_a": uuid4(),
        "admin_b": uuid4(),
        "project_a": uuid4(),
        "project_b": uuid4(),
        "agent_a": uuid4(),
        "agent_b": uuid4(),
        "kb_a": uuid4(),
        "kb_b": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE agent_knowledge_bases, kb_projects, chunks, documents,"
            " knowledge_bases, agents, projects, user_org_memberships,"
            " organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            ids["tenant_a"],
            "Acme",
            "acme-kbassign",
            ids["tenant_b"],
            "Globex",
            "globex-kbassign",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES"
            " ($1, 'admin-a@kbassign.test', 'h'),"
            " ($2, 'member-a@kbassign.test', 'h'),"
            " ($3, 'admin-b@kbassign.test', 'h')",
            ids["admin_a"],
            ids["member_a"],
            ids["admin_b"],
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES"
            " ($1, $2, $3, 'tenant_admin'),"
            " ($4, $5, $6, 'tenant_user'),"
            " ($7, $8, $9, 'tenant_admin')",
            uuid4(),
            ids["tenant_a"],
            ids["admin_a"],
            uuid4(),
            ids["tenant_a"],
            ids["member_a"],
            uuid4(),
            ids["tenant_b"],
            ids["admin_b"],
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status) VALUES"
            " ($1, $2, 'Webapp A', 'active'), ($3, $4, 'Webapp B', 'active')",
            ids["project_a"],
            ids["tenant_a"],
            ids["project_b"],
            ids["tenant_b"],
        )
        await conn.execute(
            "INSERT INTO agents"
            " (id, tenant_id, name, role, scope, agent_type, system_prompt) VALUES"
            " ($1, $2, 'backend-dev-A', 'backend_dev',"
            "  'global_tenant_template', 'ai', 'p'),"
            " ($3, $4, 'backend-dev-B', 'backend_dev',"
            "  'global_tenant_template', 'ai', 'p')",
            ids["agent_a"],
            ids["tenant_a"],
            ids["agent_b"],
            ids["tenant_b"],
        )
        await conn.execute(
            "INSERT INTO knowledge_bases (id, tenant_id, name) VALUES"
            " ($1, $2, 'API REST design principles'), ($3, $4, 'KB del vecino')",
            ids["kb_a"],
            ids["tenant_a"],
            ids["kb_b"],
            ids["tenant_b"],
        )
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


async def _mint(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ===========================================================================
# Panel «Asignaciones»: grant a proyecto + a agente, y los DOS GET inversos
# ===========================================================================
@pytest.mark.asyncio
async def test_assignments_panel_lists_projects_and_agents(
    configured_app, migrations_pg_dsn: str
) -> None:
    ids = await _seed(migrations_pg_dsn)
    token = await _mint(ids["admin_a"], ids["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}
    kb = ids["kb_a"]

    async with _client(configured_app) as client:
        # Panel vacío antes de cualquier grant (el 200 con [] es la señal de
        # que la KB existe pero no está asignada — distinto del 404).
        assert (await client.get(f"/knowledge-bases/{kb}/projects", headers=headers)).json() == []
        assert (await client.get(f"/knowledge-bases/{kb}/agents", headers=headers)).json() == []

        # Grant a un proyecto y a un agente.
        r = await client.post(
            f"/knowledge-bases/{kb}/projects",
            headers=headers,
            json={"project_id": str(ids["project_a"])},
        )
        assert r.status_code == 201, r.text
        r = await client.post(
            f"/agents/{ids['agent_a']}/knowledge-bases",
            headers=headers,
            json={"kb_id": str(kb)},
        )
        assert r.status_code == 201, r.text

        # GET inverso #1: los proyectos que tienen esta KB.
        projects = (await client.get(f"/knowledge-bases/{kb}/projects", headers=headers)).json()
        assert [p["project_id"] for p in projects] == [str(ids["project_a"])]
        # El panel muestra el NOMBRE, no el UUID, y quién concedió el grant.
        assert projects[0]["name"] == "Webapp A"
        assert projects[0]["granted_by"] == str(ids["admin_a"])
        assert projects[0]["granted_at"] is not None

        # GET inverso #2: los agentes que tienen esta KB.
        agents = (await client.get(f"/knowledge-bases/{kb}/agents", headers=headers)).json()
        assert [a["agent_id"] for a in agents] == [str(ids["agent_a"])]
        assert agents[0]["name"] == "backend-dev-A"
        # `scope` + `role` son lo que la UI usa para marcar built-in/fork.
        assert agents[0]["scope"] == "global_tenant_template"
        assert agents[0]["role"] == "backend_dev"
        assert agents[0]["granted_by"] == str(ids["admin_a"])


# ===========================================================================
# «Click Revoke en una fila de proyecto → desaparece de la lista»
# ===========================================================================
@pytest.mark.asyncio
async def test_revoke_project_row_disappears_from_the_panel_and_from_the_project(
    configured_app, migrations_pg_dsn: str
) -> None:
    ids = await _seed(migrations_pg_dsn)
    token = await _mint(ids["admin_a"], ids["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}
    kb, project = ids["kb_a"], ids["project_a"]

    async with _client(configured_app) as client:
        await client.post(
            f"/knowledge-bases/{kb}/projects",
            headers=headers,
            json={"project_id": str(project)},
        )
        # El proyecto SÍ ve la KB antes del revoke (listado directo — el que
        # refresca la pestaña KBs del proyecto).
        before = (await client.get(f"/projects/{project}/knowledge-bases", headers=headers)).json()
        assert [kb_row["id"] for kb_row in before] == [str(kb)]

        # Revoke por fila.
        r = await client.delete(f"/knowledge-bases/{kb}/projects/{project}", headers=headers)
        assert r.status_code == 204, r.text

        # Desaparece del panel inverso...
        assert (await client.get(f"/knowledge-bases/{kb}/projects", headers=headers)).json() == []
        # ...y el proyecto afectado ya no la ve al refrescar.
        after = (await client.get(f"/projects/{project}/knowledge-bases", headers=headers)).json()
        assert after == []


# ===========================================================================
# «Mismo flow para revoke desde un agente»
# ===========================================================================
@pytest.mark.asyncio
async def test_revoke_agent_row_disappears_from_the_panel_and_from_the_agent(
    configured_app, migrations_pg_dsn: str
) -> None:
    ids = await _seed(migrations_pg_dsn)
    token = await _mint(ids["admin_a"], ids["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}
    kb, agent = ids["kb_a"], ids["agent_a"]

    async with _client(configured_app) as client:
        await client.post(
            f"/agents/{agent}/knowledge-bases", headers=headers, json={"kb_id": str(kb)}
        )
        before = (await client.get(f"/agents/{agent}/knowledge-bases", headers=headers)).json()
        assert [row["kb_id"] for row in before] == [str(kb)]

        r = await client.delete(f"/agents/{agent}/knowledge-bases/{kb}", headers=headers)
        assert r.status_code == 204, r.text

        assert (await client.get(f"/knowledge-bases/{kb}/agents", headers=headers)).json() == []
        assert (await client.get(f"/agents/{agent}/knowledge-bases", headers=headers)).json() == []


# ===========================================================================
# El panel es de LECTURA para cualquier member; el revoke exige tenant_admin
# ===========================================================================
@pytest.mark.asyncio
async def test_member_reads_the_panel_but_cannot_revoke(
    configured_app, migrations_pg_dsn: str
) -> None:
    ids = await _seed(migrations_pg_dsn)
    admin_headers = {"Authorization": f"Bearer {await _mint(ids['admin_a'], ids['tenant_a'])}"}
    member_headers = {"Authorization": f"Bearer {await _mint(ids['member_a'], ids['tenant_a'])}"}
    kb, project = ids["kb_a"], ids["project_a"]

    async with _client(configured_app) as client:
        await client.post(
            f"/knowledge-bases/{kb}/projects",
            headers=admin_headers,
            json={"project_id": str(project)},
        )
        # Lee el panel...
        listed = await client.get(f"/knowledge-bases/{kb}/projects", headers=member_headers)
        assert listed.status_code == 200, listed.text
        assert len(listed.json()) == 1
        # ...pero no puede revocar ni conceder.
        revoke = await client.delete(
            f"/knowledge-bases/{kb}/projects/{project}", headers=member_headers
        )
        assert revoke.status_code == 403, revoke.text
        grant = await client.post(
            f"/knowledge-bases/{kb}/projects",
            headers=member_headers,
            json={"project_id": str(project)},
        )
        assert grant.status_code == 403, grant.text
        # Y el grant sigue vivo (el 403 no fue un borrado silencioso).
        still = (await client.get(f"/knowledge-bases/{kb}/projects", headers=admin_headers)).json()
        assert [p["project_id"] for p in still] == [str(project)]


# ===========================================================================
# Aislamiento multi-tenant: un tenant no ve las asignaciones del otro
# ===========================================================================
@pytest.mark.asyncio
async def test_tenant_b_cannot_see_tenant_a_assignments(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Tenant A asigna su KB a su proyecto y a su agente. Tenant B:

    * recibe 404 en los DOS GET inversos de la KB de A (RLS la oculta y
      `_load_kb` lo convierte en un 404 limpio, sin filtrar que existe);
    * ve su PROPIO panel con solo sus filas — ni el proyecto ni el agente de
      A se cuelan en el listado de B.
    """
    ids = await _seed(migrations_pg_dsn)
    headers_a = {"Authorization": f"Bearer {await _mint(ids['admin_a'], ids['tenant_a'])}"}
    headers_b = {"Authorization": f"Bearer {await _mint(ids['admin_b'], ids['tenant_b'])}"}

    async with _client(configured_app) as client:
        # Tenant A asigna lo suyo.
        await client.post(
            f"/knowledge-bases/{ids['kb_a']}/projects",
            headers=headers_a,
            json={"project_id": str(ids["project_a"])},
        )
        await client.post(
            f"/agents/{ids['agent_a']}/knowledge-bases",
            headers=headers_a,
            json={"kb_id": str(ids["kb_a"])},
        )
        # Tenant B asigna lo suyo (panel no vacío en los dos lados: sin esto
        # el "no veo nada" de B podría ser un falso verde por panel vacío).
        await client.post(
            f"/knowledge-bases/{ids['kb_b']}/projects",
            headers=headers_b,
            json={"project_id": str(ids["project_b"])},
        )
        await client.post(
            f"/agents/{ids['agent_b']}/knowledge-bases",
            headers=headers_b,
            json={"kb_id": str(ids["kb_b"])},
        )

        # B no alcanza la KB de A por ninguno de los dos GET inversos.
        for path in ("projects", "agents"):
            resp = await client.get(f"/knowledge-bases/{ids['kb_a']}/{path}", headers=headers_b)
            assert resp.status_code == 404, f"{path}: {resp.status_code} {resp.text}"

        # El panel de B trae SOLO lo de B.
        b_projects = (
            await client.get(f"/knowledge-bases/{ids['kb_b']}/projects", headers=headers_b)
        ).json()
        assert [p["project_id"] for p in b_projects] == [str(ids["project_b"])]
        assert str(ids["project_a"]) not in {p["project_id"] for p in b_projects}
        assert "Webapp A" not in {p["name"] for p in b_projects}

        b_agents = (
            await client.get(f"/knowledge-bases/{ids['kb_b']}/agents", headers=headers_b)
        ).json()
        assert [a["agent_id"] for a in b_agents] == [str(ids["agent_b"])]
        assert str(ids["agent_a"]) not in {a["agent_id"] for a in b_agents}
        assert "backend-dev-A" not in {a["name"] for a in b_agents}

        # Y el panel de A sigue intacto (simetría: nadie se pisó al vecino).
        a_projects = (
            await client.get(f"/knowledge-bases/{ids['kb_a']}/projects", headers=headers_a)
        ).json()
        assert [p["project_id"] for p in a_projects] == [str(ids["project_a"])]
