"""Integration tests for /projects/{id}/tasks endpoints (task_01_08).

Covers:
  - CRUD + status moves via PUT.
  - Hard-delete (Task has no soft-delete).
  - Dependencies: create with deps, update deps, replace wholesale,
    clear with empty list, untouched when omitted, cross-project deps
    rejected, self-loop rejected by DB CHECK.
  - Cross-tenant isolation: tenant B can't see / mutate tenant A's tasks.
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


async def _seed(dsn: str) -> dict[str, UUID]:
    tenant_a = uuid4()
    tenant_b = uuid4()
    user_a = uuid4()
    user_b = uuid4()
    project_a = uuid4()
    project_a_other = uuid4()  # second project in tenant A (for cross-project dep tests)
    project_b = uuid4()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE task_dependencies, tasks, team_members, teams,"
            " projects, agents, user_org_memberships, organizations,"
            " users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES" " ($1, $2, $3), ($4, $5, $6)",
            tenant_a,
            "Tenant A",
            "tenant-a",
            tenant_b,
            "Tenant B",
            "tenant-b",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES" " ($1, $2, $3), ($4, $5, $6)",
            user_a,
            "alice@a.test",
            "argon2-placeholder",
            user_b,
            "bob@b.test",
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
            "INSERT INTO projects (id, tenant_id, name) VALUES"
            " ($1, $2, $3), ($4, $5, $6), ($7, $8, $9)",
            project_a,
            tenant_a,
            "Project A1",
            project_a_other,
            tenant_a,
            "Project A2",
            project_b,
            tenant_b,
            "Project B",
        )
    finally:
        await conn.close()

    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "user_a": user_a,
        "user_b": user_b,
        "project_a": project_a,
        "project_a_other": project_a_other,
        "project_b": project_b,
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


# ===========================================================================
# Tests
# ===========================================================================
@pytest.mark.asyncio
async def test_tasks_unauthenticated_is_401(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/projects/{seeded['project_a']}/tasks")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_task_crud_with_status_moves(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}
    base = f"/projects/{seeded['project_a']}/tasks"

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        create = await client.post(
            base,
            json={
                "title": "Write API spec",
                "priority": "high",
                "acceptance_criteria": ["OpenAPI 3.1 file checked in"],
                "estimated_complexity": "m",
            },
            headers=headers,
        )
        assert create.status_code == 201, create.text
        body = create.json()
        assert body["status"] == "backlog"  # default
        assert body["depends_on"] == []
        task_id = body["id"]

        listed = await client.get(base, headers=headers)
        assert {t["id"] for t in listed.json()} == {task_id}

        # Status move through the LEGAL pipeline (c1/T2: PUT enforces the state
        # machine — backlog→in_progress is now a 409, must go via ready/in_review).
        for new_status in ("ready", "in_progress", "in_review", "done"):
            upd = await client.put(
                f"{base}/{task_id}",
                json={"status": new_status},
                headers=headers,
            )
            assert upd.status_code == 200
            assert upd.json()["status"] == new_status

        # Filter by status
        done = await client.get(f"{base}?status=done", headers=headers)
        assert {t["id"] for t in done.json()} == {task_id}

        # DELETE is hard-delete.
        dele = await client.delete(f"{base}/{task_id}", headers=headers)
        assert dele.status_code == 204
        gone = await client.get(f"{base}/{task_id}", headers=headers)
        assert gone.status_code == 404


@pytest.mark.asyncio
async def test_task_dependencies_lifecycle(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}
    base = f"/projects/{seeded['project_a']}/tasks"

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        # Two prerequisite tasks.
        prereq_1 = (await client.post(base, json={"title": "Prereq 1"}, headers=headers)).json()[
            "id"
        ]
        prereq_2 = (await client.post(base, json={"title": "Prereq 2"}, headers=headers)).json()[
            "id"
        ]

        # Main task depending on both.
        main = await client.post(
            base,
            json={"title": "Main", "depends_on": [prereq_1, prereq_2]},
            headers=headers,
        )
        assert main.status_code == 201, main.text
        assert set(main.json()["depends_on"]) == {prereq_1, prereq_2}
        main_id = main.json()["id"]

        # PUT with new dep list -> replaces wholesale.
        upd = await client.put(
            f"{base}/{main_id}",
            json={"depends_on": [prereq_1]},
            headers=headers,
        )
        assert upd.status_code == 200
        assert upd.json()["depends_on"] == [prereq_1]

        # PUT without `depends_on` key -> deps untouched.
        upd2 = await client.put(
            f"{base}/{main_id}",
            json={"priority": "critical"},
            headers=headers,
        )
        assert upd2.status_code == 200
        assert upd2.json()["depends_on"] == [prereq_1]
        assert upd2.json()["priority"] == "critical"

        # PUT with empty list -> clear.
        upd3 = await client.put(
            f"{base}/{main_id}",
            json={"depends_on": []},
            headers=headers,
        )
        assert upd3.status_code == 200
        assert upd3.json()["depends_on"] == []


@pytest.mark.asyncio
async def test_delete_task_blocked_when_others_depend_on_it(
    configured_app, migrations_pg_dsn: str
) -> None:
    """HARDDEP: borrar un prerequisito del que otra tarea depende dejaría al
    dependiente vacuamente elegible (task_dependencies CASCADEa) → promocionaría a
    ready como si el prerequisito hubiera terminado. El DELETE debe dar 409 hasta
    que se retire la arista; borrar el DEPENDIENTE (hoja) sí se permite."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}
    base = f"/projects/{seeded['project_a']}/tasks"

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        prereq = (await client.post(base, json={"title": "Prereq"}, headers=headers)).json()["id"]
        dependent = await client.post(
            base, json={"title": "Dependent", "depends_on": [prereq]}, headers=headers
        )
        dependent_id = dependent.json()["id"]

        # Borrar el prerequisito con un dependiente vivo → 409 (no corromper el DAG).
        blocked = await client.delete(f"{base}/{prereq}", headers=headers)
        assert blocked.status_code == 409, blocked.text
        # Sigue existiendo (no se borró).
        assert (await client.get(f"{base}/{prereq}", headers=headers)).status_code == 200

        # Borrar la hoja (nadie depende de ella) sí se permite.
        leaf = await client.delete(f"{base}/{dependent_id}", headers=headers)
        assert leaf.status_code == 204
        # Y ahora el prerequisito ya se puede borrar.
        now_ok = await client.delete(f"{base}/{prereq}", headers=headers)
        assert now_ok.status_code == 204


@pytest.mark.asyncio
async def test_task_dependency_must_be_same_project(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}
    base_a = f"/projects/{seeded['project_a']}/tasks"
    base_a2 = f"/projects/{seeded['project_a_other']}/tasks"

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        # A task in the second project.
        other = (await client.post(base_a2, json={"title": "Other"}, headers=headers)).json()["id"]

        # Attempting to make a project_a task depend on a project_a2 task
        # is rejected (different project, even within the same tenant).
        resp = await client.post(
            base_a,
            json={"title": "Wrong", "depends_on": [other]},
            headers=headers,
        )
    assert resp.status_code == 404
    assert "dependency" in resp.text.lower()


@pytest.mark.asyncio
async def test_task_self_dependency_blocked_by_db(configured_app, migrations_pg_dsn: str) -> None:
    """ck_task_dependencies_no_self_loop must fire even if the app would
    let it through. We trigger this by PUTting depends_on=[<own id>]."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}
    base = f"/projects/{seeded['project_a']}/tasks"

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        task_id = (await client.post(base, json={"title": "Lonely"}, headers=headers)).json()["id"]

        resp = await client.put(
            f"{base}/{task_id}",
            json={"depends_on": [task_id]},
            headers=headers,
        )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_task_isolation_across_tenants(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token_a = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    token_b = await _mint_token(seeded["user_b"], seeded["tenant_b"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        created = await client.post(
            f"/projects/{seeded['project_a']}/tasks",
            json={"title": "A's task"},
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert created.status_code == 201
        task_id = created.json()["id"]

        # Tenant B sees project_a as missing (RLS).
        listed_b = await client.get(
            f"/projects/{seeded['project_a']}/tasks",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert listed_b.status_code == 404

        fetch_b = await client.get(
            f"/projects/{seeded['project_a']}/tasks/{task_id}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert fetch_b.status_code == 404


@pytest.mark.asyncio
async def test_task_for_unknown_project_returns_404(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}
    bogus_project = uuid4()

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/projects/{bogus_project}/tasks",
            json={"title": "Ghost"},
            headers=headers,
        )
    assert resp.status_code == 404
    assert "project not found" in resp.text.lower()
