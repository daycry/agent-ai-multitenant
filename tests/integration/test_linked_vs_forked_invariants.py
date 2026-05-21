"""End-to-end invariants of the linked-vs-forked model (task_01_18).

These are integration regression tests for the load-bearing property
that justifies the whole pattern (spec §5.7):

  *Forks are isolated.* Editing a fork never alters the source. Editing
  the source never alters the fork. Once the user customizes a copy,
  that copy is theirs.

  *Linked references see updates.* When a tenant adds the global agent
  to a team by id (no fork), looking that agent up later shows the
  global's current state. The team carries no copy of the agent's
  fields, just a reference.

The corollaries proved here:
  - Multiple forks of the same source are independent.
  - A team_member row carrying a fork's id never reflects the source's
    edits (it has its own copy by definition).
  - Soft-deleting the source leaves the fork accessible and editable.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


async def _seed(dsn: str) -> dict[str, UUID]:
    tenant_a = uuid4()
    user_a = uuid4()
    project_a = uuid4()
    builtin_agent = uuid4()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE agents, projects, team_members, teams,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            tenant_a,
            "Tenant A",
            "tenant-a",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3)",
            user_a,
            "a@x.test",
            "x",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, $4)",
            uuid4(),
            tenant_a,
            user_a,
            "tenant_admin",
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, $3)",
            project_a,
            tenant_a,
            "Project A",
        )
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, name, role, system_prompt,"
            " model_config, scope, project_id, max_concurrent_tasks)"
            " VALUES ($1, $2, $3, 'project_manager', $4, $5::jsonb,"
            " 'global_builtin', NULL, 2)",
            builtin_agent,
            _PLATFORM_TENANT_ID,
            "Built-in PM",
            "Prompt v1.",
            '{"provider": "anthropic", "model": "claude-sonnet"}',
        )
    finally:
        await conn.close()

    return {
        "tenant_a": tenant_a,
        "user_a": user_a,
        "project_a": project_a,
        "builtin_agent": builtin_agent,
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


async def _patch_source_via_sql(
    dsn: str,
    source_id: UUID,
    *,
    name: str | None = None,
    system_prompt: str | None = None,
    soft_delete: bool = False,
) -> None:
    """Mutate a platform-owned agent. The tenant API can't write to it
    (RLS), but the migrations role bypasses RLS."""
    sets: list[str] = ["updated_at = $2"]
    params: list = [source_id, datetime.now(tz=UTC)]
    if name is not None:
        sets.append(f"name = ${len(params) + 1}")
        params.append(name)
    if system_prompt is not None:
        sets.append(f"system_prompt = ${len(params) + 1}")
        params.append(system_prompt)
    if soft_delete:
        sets.append("deleted_at = now()")

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(f"UPDATE agents SET {', '.join(sets)} WHERE id = $1", *params)
    finally:
        await conn.close()


# ===========================================================================
# Invariants
# ===========================================================================
@pytest.mark.asyncio
async def test_edit_fork_never_alters_source(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        fork = (
            await client.post(
                f"/agents/{seeded['builtin_agent']}/fork",
                json={"project_id": str(seeded["project_a"])},
                headers=headers,
            )
        ).json()
        await client.put(
            f"/agents/{fork['id']}",
            json={
                "name": "Fork-only edit",
                "system_prompt": "Fork-only prompt.",
                "max_concurrent_tasks": 9,
            },
            headers=headers,
        )

        source = await client.get(f"/agents/{seeded['builtin_agent']}", headers=headers)

    body = source.json()
    assert body["name"] == "Built-in PM"
    assert body["system_prompt"] == "Prompt v1."
    assert body["max_concurrent_tasks"] == 2


@pytest.mark.asyncio
async def test_edit_source_never_alters_fork(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        fork = (
            await client.post(
                f"/agents/{seeded['builtin_agent']}/fork",
                json={"project_id": str(seeded["project_a"])},
                headers=headers,
            )
        ).json()

        await _patch_source_via_sql(
            migrations_pg_dsn,
            seeded["builtin_agent"],
            name="Source moved on",
            system_prompt="Brand new prompt.",
        )

        fork_after = await client.get(f"/agents/{fork['id']}", headers=headers)

    body = fork_after.json()
    # Fork stayed pinned to the values at fork time.
    assert body["name"] == "Built-in PM"
    assert body["system_prompt"] == "Prompt v1."


@pytest.mark.asyncio
async def test_multiple_forks_of_same_source_are_independent(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Two forks created from the same global should not see each
    other's edits -- they're siblings, not copies of each other."""
    seeded = await _seed(migrations_pg_dsn)
    # Add a second project so each fork lives in its own scope.
    project_b = uuid4()
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, $3)",
            project_b,
            seeded["tenant_a"],
            "Project B",
        )
    finally:
        await conn.close()

    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        fork1 = (
            await client.post(
                f"/agents/{seeded['builtin_agent']}/fork",
                json={"project_id": str(seeded["project_a"]), "name": "Fork 1"},
                headers=headers,
            )
        ).json()
        fork2 = (
            await client.post(
                f"/agents/{seeded['builtin_agent']}/fork",
                json={"project_id": str(project_b), "name": "Fork 2"},
                headers=headers,
            )
        ).json()

        await client.put(
            f"/agents/{fork1['id']}",
            json={"name": "Fork 1 mutated"},
            headers=headers,
        )

        fork2_after = (await client.get(f"/agents/{fork2['id']}", headers=headers)).json()
        source_after = (
            await client.get(f"/agents/{seeded['builtin_agent']}", headers=headers)
        ).json()

    assert fork2_after["name"] == "Fork 2"
    assert source_after["name"] == "Built-in PM"


@pytest.mark.asyncio
async def test_linked_reference_in_team_member_reflects_source_updates(
    configured_app, migrations_pg_dsn: str
) -> None:
    """A team_member row carrying the global's id (linked, not forked)
    is just a pointer. Looking the agent up later shows the source's
    current state."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        team_id = (await client.post("/teams", json={"name": "Team A"}, headers=headers)).json()[
            "id"
        ]
        # Linked: we add the source's id directly, not a fork.
        await client.post(
            f"/teams/{team_id}/members",
            json={"agent_id": str(seeded["builtin_agent"])},
            headers=headers,
        )

        await _patch_source_via_sql(
            migrations_pg_dsn,
            seeded["builtin_agent"],
            name="Built-in PM v2",
        )

        agent_after = await client.get(f"/agents/{seeded['builtin_agent']}", headers=headers)

    # Team member row still references the global id.
    assert agent_after.status_code == 200
    assert agent_after.json()["name"] == "Built-in PM v2"


@pytest.mark.asyncio
async def test_forked_agent_in_team_member_does_not_reflect_source_updates(
    configured_app, migrations_pg_dsn: str
) -> None:
    """A team_member carrying a *fork's* id stays pinned to the fork's
    own values, no matter what the source upstream does."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        fork = (
            await client.post(
                f"/agents/{seeded['builtin_agent']}/fork",
                json={"project_id": str(seeded["project_a"])},
                headers=headers,
            )
        ).json()
        team_id = (await client.post("/teams", json={"name": "Team A"}, headers=headers)).json()[
            "id"
        ]
        await client.post(
            f"/teams/{team_id}/members",
            json={"agent_id": fork["id"]},
            headers=headers,
        )

        await _patch_source_via_sql(
            migrations_pg_dsn,
            seeded["builtin_agent"],
            name="Upstream renamed",
        )

        fork_after = await client.get(f"/agents/{fork['id']}", headers=headers)

    assert fork_after.json()["name"] == "Built-in PM"  # fork pinned


@pytest.mark.asyncio
async def test_fork_survives_source_soft_delete(configured_app, migrations_pg_dsn: str) -> None:
    """If the platform retires a built-in, existing forks must still be
    usable -- they're independent rows."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        fork = (
            await client.post(
                f"/agents/{seeded['builtin_agent']}/fork",
                json={"project_id": str(seeded["project_a"])},
                headers=headers,
            )
        ).json()

        await _patch_source_via_sql(migrations_pg_dsn, seeded["builtin_agent"], soft_delete=True)

        # Fork still accessible.
        get = await client.get(f"/agents/{fork['id']}", headers=headers)
        assert get.status_code == 200

        # Fork still editable.
        upd = await client.put(
            f"/agents/{fork['id']}",
            json={"name": "Still alive"},
            headers=headers,
        )
        assert upd.status_code == 200
        assert upd.json()["name"] == "Still alive"

        # The diff endpoint flags source_deleted but doesn't error.
        diff = await client.get(f"/agents/{fork['id']}/diff", headers=headers)
        assert diff.status_code == 200
        assert diff.json()["source_deleted"] is True
