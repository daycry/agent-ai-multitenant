"""Integration tests for owner-pointer isolation on memory merge +
similar (Plan 06.14 task_06_14_08, finding rag-memory-ingestion-2).

RLS fences `memory_entries` by `tenant_id` only. Within a tenant, two
rows of the *same scope* can still belong to *different owners*
(project A vs project B, team X vs team Y, user U vs user V). The merge
endpoint previously checked only that source + target shared a scope,
so it would fold one owner's content into another — a cross-owner leak.
`GET /memories/{id}/similar` had the same hole: it filtered candidates
by scope + tenant but not by owner pointer.

These tests drive both endpoints end-to-end against real Postgres:

  - merging two project_shared memories of the SAME project → 200,
  - merging two project_shared memories of DIFFERENT projects → 422,
  - team_shared / private owner mismatch → 422,
  - global (no owner pointer) → may merge,
  - `similar` never surfaces a different-owner candidate,
  - cross-tenant: a tenant B member cannot even see (404) tenant A's
    memory, so it can never be a merge source/target.
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
# Seed — two tenants, two projects/teams/users in tenant A, memories with
# embeddings so the `similar` path has something to rank.
# ---------------------------------------------------------------------------
def _vec(seed: int, dim: int = 768) -> str:
    """Deterministic unit-ish pgvector literal keyed by `seed`. Same seed
    = same vector, so we can make two rows near-identical on purpose."""
    import random

    rng = random.Random(seed)
    raw = [rng.uniform(-1.0, 1.0) for _ in range(dim)]
    norm = sum(x * x for x in raw) ** 0.5 or 1.0
    return "[" + ",".join(f"{x / norm:.6f}" for x in raw) + "]"


async def _seed(dsn: str) -> dict[str, UUID]:
    ids = {
        "tenant_a": uuid4(),
        "tenant_b": uuid4(),
        "user_a": uuid4(),
        "user_a2": uuid4(),
        "user_b": uuid4(),
        "team_a1": uuid4(),
        "team_a2": uuid4(),
        "project_a1": uuid4(),
        "project_a2": uuid4(),
        # project_shared memories
        "proj1_mem_a": uuid4(),
        "proj1_mem_b": uuid4(),
        "proj2_mem": uuid4(),
        # team_shared memories
        "team1_mem": uuid4(),
        "team2_mem": uuid4(),
        # private memories (both owned by user_a / user_a2)
        "priv_a_mem": uuid4(),
        "priv_a2_mem": uuid4(),
        # global memories (no owner pointer)
        "global_mem_1": uuid4(),
        "global_mem_2": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE memory_entries, plans, conversations, projects, agents,"
            " teams, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES"
            " ($1, $2, $3), ($4, $5, $6), ($7, $8, $9)",
            ids["tenant_a"],
            "Tenant A",
            "tenant-a-merge",
            ids["tenant_b"],
            "Tenant B",
            "tenant-b-merge",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-merge",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES"
            " ($1, $2, $3), ($4, $5, $6), ($7, $8, $9)",
            ids["user_a"],
            "alice@merge.test",
            "h",
            ids["user_a2"],
            "amy@merge.test",
            "h",
            ids["user_b"],
            "bob@merge.test",
            "h",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES"
            " ($1, $2, $3, 'tenant_member'), ($4, $5, $6, 'tenant_member'),"
            " ($7, $8, $9, 'tenant_member')",
            uuid4(),
            ids["tenant_a"],
            ids["user_a"],
            uuid4(),
            ids["tenant_a"],
            ids["user_a2"],
            uuid4(),
            ids["tenant_b"],
            ids["user_b"],
        )
        await conn.execute(
            "INSERT INTO teams (id, tenant_id, name) VALUES ($1, $2, $3), ($4, $5, $6)",
            ids["team_a1"],
            ids["tenant_a"],
            "Team A1",
            ids["team_a2"],
            ids["tenant_a"],
            "Team A2",
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, $3), ($4, $5, $6)",
            ids["project_a1"],
            ids["tenant_a"],
            "Project A1",
            ids["project_a2"],
            ids["tenant_a"],
            "Project A2",
        )

        # --- project_shared: two in project_a1 (near-identical vectors so
        #     they rank as each other's top similar), one in project_a2 ---
        await conn.execute(
            "INSERT INTO memory_entries"
            " (id, tenant_id, scope, type, content, project_id, embedding)"
            " VALUES ($1, $2, 'project_shared', 'semantic', $3, $4, $5::vector)",
            ids["proj1_mem_a"],
            ids["tenant_a"],
            "Project A1 uses asyncpg.",
            ids["project_a1"],
            _vec(1),
        )
        await conn.execute(
            "INSERT INTO memory_entries"
            " (id, tenant_id, scope, type, content, project_id, embedding)"
            " VALUES ($1, $2, 'project_shared', 'semantic', $3, $4, $5::vector)",
            ids["proj1_mem_b"],
            ids["tenant_a"],
            "Project A1 prefers asyncpg over psycopg.",
            ids["project_a1"],
            _vec(1),
        )
        await conn.execute(
            "INSERT INTO memory_entries"
            " (id, tenant_id, scope, type, content, project_id, embedding)"
            " VALUES ($1, $2, 'project_shared', 'semantic', $3, $4, $5::vector)",
            ids["proj2_mem"],
            ids["tenant_a"],
            "Project A2 secret roadmap detail.",
            ids["project_a2"],
            _vec(1),  # same vector → would be a top 'similar' hit absent owner filter
        )

        # --- team_shared: one per team ---
        await conn.execute(
            "INSERT INTO memory_entries"
            " (id, tenant_id, scope, type, content, team_id, embedding)"
            " VALUES ($1, $2, 'team_shared', 'episodic', $3, $4, $5::vector)",
            ids["team1_mem"],
            ids["tenant_a"],
            "Team A1 standup note.",
            ids["team_a1"],
            _vec(2),
        )
        await conn.execute(
            "INSERT INTO memory_entries"
            " (id, tenant_id, scope, type, content, team_id, embedding)"
            " VALUES ($1, $2, 'team_shared', 'episodic', $3, $4, $5::vector)",
            ids["team2_mem"],
            ids["tenant_a"],
            "Team A2 standup note.",
            ids["team_a2"],
            _vec(2),
        )

        # --- private: one per user ---
        await conn.execute(
            "INSERT INTO memory_entries"
            " (id, tenant_id, scope, type, content, user_id, embedding)"
            " VALUES ($1, $2, 'private', 'semantic', $3, $4, $5::vector)",
            ids["priv_a_mem"],
            ids["tenant_a"],
            "Alice's private note.",
            ids["user_a"],
            _vec(3),
        )
        await conn.execute(
            "INSERT INTO memory_entries"
            " (id, tenant_id, scope, type, content, user_id, embedding)"
            " VALUES ($1, $2, 'private', 'semantic', $3, $4, $5::vector)",
            ids["priv_a2_mem"],
            ids["tenant_a"],
            "Amy's private note.",
            ids["user_a2"],
            _vec(3),
        )

        # --- global: two rows, no owner pointer (may merge) ---
        await conn.execute(
            "INSERT INTO memory_entries"
            " (id, tenant_id, scope, type, content, embedding)"
            " VALUES ($1, $2, 'global', 'semantic', $3, $4::vector)",
            ids["global_mem_1"],
            ids["tenant_a"],
            "Tenant-wide rule one.",
            _vec(4),
        )
        await conn.execute(
            "INSERT INTO memory_entries"
            " (id, tenant_id, scope, type, content, embedding)"
            " VALUES ($1, $2, 'global', 'semantic', $3, $4::vector)",
            ids["global_mem_2"],
            ids["tenant_a"],
            "Tenant-wide rule two.",
            _vec(4),
        )
    finally:
        await conn.close()
    return ids


# ---------------------------------------------------------------------------
# App fixture (mirrors test_memory_store.py)
# ---------------------------------------------------------------------------
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


async def _merge(client: AsyncClient, source: UUID, target: UUID, headers: dict[str, str]):
    return await client.post(
        f"/memories/{source}/merge-into",
        json={"target_id": str(target)},
        headers=headers,
    )


# ---------------------------------------------------------------------------
# project_shared owner pointer
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_merge_same_project_succeeds(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await _merge(client, seeded["proj1_mem_a"], seeded["proj1_mem_b"], headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Target keeps its project_id; the source's content is folded in; the
    # source id is recorded in metadata.merged_from.
    assert body["project_id"] == str(seeded["project_a1"])
    assert "Project A1 uses asyncpg." in body["content"]
    assert "Project A1 prefers asyncpg over psycopg." in body["content"]


@pytest.mark.asyncio
@pytest.mark.cross_tenant
async def test_merge_different_projects_rejected(configured_app, migrations_pg_dsn: str) -> None:
    """Two project_shared memories of DIFFERENT projects (same tenant,
    same scope) must NOT merge — that would leak project A2's content
    into project A1."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await _merge(client, seeded["proj1_mem_a"], seeded["proj2_mem"], headers)
        assert resp.status_code == 422, resp.text
        assert "project_id" in resp.json()["detail"]

        # And the reverse direction is equally rejected.
        resp_rev = await _merge(client, seeded["proj2_mem"], seeded["proj1_mem_a"], headers)
        assert resp_rev.status_code == 422, resp_rev.text

        # The target (proj1_mem_a) must be untouched — still its own content,
        # no folded-in foreign content.
        listed = await client.get(
            f"/memories?scope=project_shared&project_id={seeded['project_a1']}",
            headers=headers,
        )
        rows = {r["id"]: r for r in listed.json()}
        assert (
            "Project A2 secret roadmap detail." not in rows[str(seeded["proj1_mem_a"])]["content"]
        )


# ---------------------------------------------------------------------------
# team_shared owner pointer
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.cross_tenant
async def test_merge_different_teams_rejected(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await _merge(client, seeded["team1_mem"], seeded["team2_mem"], headers)
        assert resp.status_code == 422, resp.text
        assert "team_id" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# private owner pointer
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.cross_tenant
async def test_merge_different_users_rejected(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await _merge(client, seeded["priv_a_mem"], seeded["priv_a2_mem"], headers)
    # Owner isolation (H1/M3): another user's private memory is invisible, so the
    # cross-user merge is rejected as 404 (hidden) — stricter than the previous
    # 422, which revealed the target existed.
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# global scope — no owner pointer, merge allowed
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_merge_two_global_memories_succeeds(configured_app, migrations_pg_dsn: str) -> None:
    """`global` rows carry no owner pointer (they are tenant-wide), so the
    owner-pointer guard must not block them — only the scope guard applies."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await _merge(client, seeded["global_mem_1"], seeded["global_mem_2"], headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["scope"] == "global"
    assert "Tenant-wide rule one." in body["content"]


# ---------------------------------------------------------------------------
# scope mismatch still trumps (pre-existing behaviour, kept green)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_merge_across_scopes_still_rejected(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await _merge(client, seeded["proj1_mem_a"], seeded["team1_mem"], headers)
    assert resp.status_code == 422, resp.text
    assert "scope" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# similar — owner pointer filter
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.cross_tenant
async def test_similar_does_not_cross_owner(configured_app, migrations_pg_dsn: str) -> None:
    """`GET /memories/{id}/similar` for a project A1 memory must NOT
    return project A2's memory, even though they share scope + tenant +
    an identical embedding (top cosine hit absent the owner filter)."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get(
            f"/memories/{seeded['proj1_mem_a']}/similar?threshold=0.0&limit=50",
            headers=headers,
        )
    assert resp.status_code == 200, resp.text
    returned = {item["memory"]["id"] for item in resp.json()}
    # Same-owner sibling surfaces…
    assert str(seeded["proj1_mem_b"]) in returned
    # …but the different-project row never does.
    assert str(seeded["proj2_mem"]) not in returned


@pytest.mark.asyncio
async def test_similar_global_returns_other_global(configured_app, migrations_pg_dsn: str) -> None:
    """No owner pointer on `global`, so the owner filter must be absent —
    the other global row still surfaces."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get(
            f"/memories/{seeded['global_mem_1']}/similar?threshold=0.0&limit=50",
            headers=headers,
        )
    assert resp.status_code == 200, resp.text
    returned = {item["memory"]["id"] for item in resp.json()}
    assert str(seeded["global_mem_2"]) in returned


# ---------------------------------------------------------------------------
# cross-tenant: B cannot reach A's memory at all (RLS → 404)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.cross_tenant
async def test_cross_tenant_merge_source_not_found(configured_app, migrations_pg_dsn: str) -> None:
    """A tenant B member cannot use tenant A's memory as a merge source or
    target — RLS hides the row, so the lookup 404s before any merge."""
    seeded = await _seed(migrations_pg_dsn)
    token_b = await _mint_token(seeded["user_b"], seeded["tenant_b"])
    headers_b = {"Authorization": f"Bearer {token_b}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        # B as source → both A rows invisible → 404.
        resp = await _merge(client, seeded["proj1_mem_a"], seeded["proj1_mem_b"], headers_b)
        assert resp.status_code == 404, resp.text

        # B asking for similar of A's memory → 404 (row not visible).
        sim = await client.get(f"/memories/{seeded['proj1_mem_a']}/similar", headers=headers_b)
        assert sim.status_code == 404, sim.text
