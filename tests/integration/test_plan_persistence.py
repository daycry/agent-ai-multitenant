"""Integration tests for plan persistence (Plan 03 task_03_14).

Exercises POST/GET/PUT against `/projects/{project_id}/plans` and
`/plans/{plan_id}` end-to-end with a real DB. Verifies the canonical
template round-trips through the JSONB column intact, that the chat
bootstrap path (POST with only `conversation_id`) creates a draft
back-linked to the conversation, and that the DAG check refuses a
cycle at persistence time.
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

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------
async def _seed(dsn: str) -> dict[str, UUID]:
    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE messages, conversations, plans, projects, agents,"
            " user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            tenant_id,
            "Tenant Persist",
            "tenant-persist",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-persist",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3)",
            user_id,
            "alice@persist.test",
            "argon2-placeholder",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, $4)",
            uuid4(),
            tenant_id,
            user_id,
            "tenant_admin",
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, $3)",
            project_id,
            tenant_id,
            "Persist Project",
        )
    finally:
        await conn.close()
    return {"tenant_id": tenant_id, "user_id": user_id, "project_id": project_id}


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


# ---------------------------------------------------------------------------
# Spec fixtures
# ---------------------------------------------------------------------------
def _full_specification() -> dict:
    return {
        "summary": {
            "title": "API de inventario con auth",
            "description": "MVP de una API REST con JWT y persistencia.",
            "scope_in": ["registro", "login", "CRUD de items"],
            "scope_out": ["mobile", "ML"],
            "decisions": ["Postgres en vez de Mongo"],
            "risks": [{"name": "JWT mal rotado", "mitigation": "Rotación semanal"}],
        },
        "phases": [
            {"name": "Auth", "description": "JWT", "tasks": ["t1", "t2"]},
            {"name": "Inventario", "description": "CRUD", "tasks": ["t3"]},
        ],
        "tasks": [
            {
                "id": "t1",
                "title": "Modelar usuarios",
                "complexity": "m",
                "depends_on": [],
                "role": "backend_dev",
            },
            {
                "id": "t2",
                "title": "Implementar /login",
                "complexity": "m",
                "depends_on": ["t1"],
                "role": "backend_dev",
            },
            {
                "id": "t3",
                "title": "CRUD de items",
                "complexity": "l",
                "depends_on": ["t2"],
                "role": "backend_dev",
            },
        ],
        "estimates": {
            "duration_calendar": "3 semanas",
            "effort_person_days": 12,
            "cost_human_eur": [4800, 6000],
            "cost_ai_eur": [40, 80],
        },
        "metadata": {"template_version": "1.0"},
    }


# ===========================================================================
# Tests
# ===========================================================================
@pytest.mark.asyncio
async def test_inline_specification_persists_and_reads_back(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}
    spec = _full_specification()

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        create = await client.post(
            f"/projects/{seeded['project_id']}/plans",
            json={
                "title": "Inventory API",
                "description": "Primera iteración",
                "specification": spec,
            },
            headers=headers,
        )
        assert create.status_code == 201, create.text
        body = create.json()
        plan_id = body["id"]

        # Header fields landed.
        assert body["title"] == "Inventory API"
        assert body["description"] == "Primera iteración"
        assert body["status"] == "draft"

        # Specification round-trips through JSONB intact.
        assert body["specification"]["summary"]["title"] == spec["summary"]["title"]
        assert len(body["specification"]["tasks"]) == 3

        # GET returns the same row.
        fetched = await client.get(f"/plans/{plan_id}", headers=headers)
        assert fetched.status_code == 200
        assert fetched.json()["specification"] == body["specification"]


@pytest.mark.asyncio
async def test_bootstrap_from_conversation_creates_draft_and_back_links(
    configured_app, migrations_pg_dsn: str
) -> None:
    """When the operator clicks 'Generar Plan' in the chat, the
    frontend POSTs `{conversation_id}` only. The backend creates a
    draft plan tied to that conversation and back-links the
    conversation's `related_plan_id`."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        # First create a conversation.
        conv_resp = await client.post(
            f"/projects/{seeded['project_id']}/conversations",
            json={"title": "Planning chat"},
            headers=headers,
        )
        conv_id = conv_resp.json()["id"]

        # Now bootstrap a plan from it.
        plan_resp = await client.post(
            f"/projects/{seeded['project_id']}/plans",
            json={"conversation_id": conv_id},
            headers=headers,
        )
        assert plan_resp.status_code == 201, plan_resp.text
        plan_body = plan_resp.json()
        assert plan_body["status"] == "draft"
        assert plan_body["conversation_id"] == conv_id
        assert plan_body["title"] == "Borrador del plan"
        assert plan_body["specification"] == {}

        # The conversation now references the plan it produced.
        refreshed = await client.get(f"/conversations/{conv_id}", headers=headers)
        assert refreshed.json()["related_plan_id"] == plan_body["id"]


@pytest.mark.asyncio
async def test_generate_plan_twice_from_one_conversation_is_idempotent(
    configured_app, migrations_pg_dsn: str
) -> None:
    """A-05: «Generar Plan» dos veces NO crea dos planes.

    `create_plan` no comprobaba si la conversación ya había producido uno: el
    attachment seguía ahí, `_draft_from_conversation` lo volvía a levantar y
    `related_plan_id` se SOBRESCRIBÍA. El primer plan quedaba huérfano del
    back-link pero vivo, sincronizable y ejecutable — dos planes con las mismas
    tareas sobre el mismo proyecto compitiendo por el mismo worktree.

    El segundo POST devuelve el plan que ya existe, y con 200 (no 201): decir
    «created» de algo que no se ha creado es mentirle al cliente, y es la señal
    que la UI necesita para avisar en vez de quedarse muda."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        conv_id = (
            await client.post(
                f"/projects/{seeded['project_id']}/conversations",
                json={"title": "Planning chat"},
                headers=headers,
            )
        ).json()["id"]

        first = await client.post(
            f"/projects/{seeded['project_id']}/plans",
            json={"conversation_id": conv_id},
            headers=headers,
        )
        assert first.status_code == 201, first.text

        second = await client.post(
            f"/projects/{seeded['project_id']}/plans",
            json={"conversation_id": conv_id},
            headers=headers,
        )
        assert second.status_code == 200, second.text
        assert second.json()["id"] == first.json()["id"]

        listed = await client.get(f"/projects/{seeded['project_id']}/plans", headers=headers)
        assert len(listed.json()) == 1, "la conversación no debe producir un plan gemelo"


@pytest.mark.asyncio
async def test_specification_with_dag_cycle_returns_422(
    configured_app, migrations_pg_dsn: str
) -> None:
    """A cycle (a depends on b, b depends on a) must be rejected at
    persistence time so the orchestrator never sees a broken graph."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    cyclic_spec = {
        "tasks": [
            {"id": "a", "title": "A", "depends_on": ["b"]},
            {"id": "b", "title": "B", "depends_on": ["a"]},
        ],
    }

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/projects/{seeded['project_id']}/plans",
            json={"title": "broken", "specification": cyclic_spec},
            headers=headers,
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "dag_cycle"
        assert set(detail["cycle"]) >= {"a", "b"}


@pytest.mark.asyncio
async def test_unknown_dependency_returns_422_via_pydantic(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Pydantic catches a depends_on referencing a non-existent task
    BEFORE the DAG validator runs — keeps the 422 path readable."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    spec = {
        "tasks": [
            {"id": "a", "title": "A", "depends_on": ["ghost"]},
        ],
    }

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/projects/{seeded['project_id']}/plans",
            json={"specification": spec},
            headers=headers,
        )
        assert resp.status_code == 422


@pytest.mark.asyncio
async def test_list_plans_filters_by_status_and_project(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        for title in ("p1", "p2", "p3"):
            await client.post(
                f"/projects/{seeded['project_id']}/plans",
                json={"title": title},
                headers=headers,
            )
        listed = await client.get(f"/projects/{seeded['project_id']}/plans", headers=headers)
        assert listed.status_code == 200
        assert {p["title"] for p in listed.json()} == {"p1", "p2", "p3"}

        # Status filter on the default draft.
        drafts = await client.get(
            f"/projects/{seeded['project_id']}/plans?status=draft", headers=headers
        )
        assert len(drafts.json()) == 3

        approved = await client.get(
            f"/projects/{seeded['project_id']}/plans?status=approved", headers=headers
        )
        assert approved.json() == []


@pytest.mark.asyncio
async def test_bootstrap_from_conversation_lifts_plan_draft_attachment(
    configured_app, migrations_pg_dsn: str
) -> None:
    """chat→plan materialisation (task_03_14): when the planning chat finished with a
    `{kind: planning_directive, intent: finish_planning, specification}` attachment,
    POSTing `/plans` with only `conversation_id` lifts that spec so the Plan is born
    with its tasks (not an empty draft)."""
    import json

    seeded = await _seed(migrations_pg_dsn)
    agent_id = uuid4()
    conv_id = uuid4()
    spec = {
        "summary": "Landing CI4 sin BD",
        "tasks": [
            {"id": "t1", "title": "Controlador Home", "description": "GET /", "depends_on": []},
            {"id": "t2", "title": "Vista Twig", "description": "saludo", "depends_on": ["t1"]},
        ],
    }
    attachment = {
        "kind": "planning_directive",
        "intent": "finish_planning",
        "title": "Landing CI4",
        "specification": spec,
    }
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, name, role, system_prompt, scope)"
            " VALUES ($1, $2, $3, $4, $5, 'global_tenant_template')",
            agent_id,
            seeded["tenant_id"],
            "PM",
            "project_manager",
            "Eres el PM.",
        )
        await conn.execute(
            "INSERT INTO conversations (id, tenant_id, project_id, title, current_mode)"
            " VALUES ($1, $2, $3, $4, 'planning')",
            conv_id,
            seeded["tenant_id"],
            seeded["project_id"],
            "Chat",
        )
        await conn.execute(
            "INSERT INTO messages (id, tenant_id, conversation_id, author_kind,"
            " author_agent_id, content, mode, attachments)"
            " VALUES ($1, $2, $3, 'agent', $4, $5, 'planning', $6::jsonb)",
            uuid4(),
            seeded["tenant_id"],
            conv_id,
            agent_id,
            "Plan listo para insertar.",
            json.dumps([attachment]),
        )
    finally:
        await conn.close()

    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/projects/{seeded['project_id']}/plans",
            json={"conversation_id": str(conv_id)},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["title"] == "Landing CI4"
        tasks = body["specification"]["tasks"]
        assert [t["id"] for t in tasks] == ["t1", "t2"]
        assert tasks[1]["depends_on"] == ["t1"]
        assert body["conversation_id"] == str(conv_id)
