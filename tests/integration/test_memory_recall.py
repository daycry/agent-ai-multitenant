"""Integration tests for the hybrid memory recall (Plan 04 task_04_04).

Exercises :func:`api_server.memorizer.recall.recall` against real
Postgres + pgvector. Seeds a handful of `memory_entries` (some with
embeddings, some without; some in scope, some out of scope) and
asserts both the BM25-only path, the vector-only path, the hybrid
RRF path and the scope+owner filter.
"""

from __future__ import annotations

import random
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from api_server.memorizer.recall import recall
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture()
def schema_at_head(alembic_config) -> None:
    command.upgrade(alembic_config, "head")


def _vec(seed: int, dim: int = 768) -> list[float]:
    """Deterministic pseudo-random unit-ish vector keyed by `seed`.

    Same seed = same vector — handy for asserting which memory ranks
    near a given query in the vector path."""
    rng = random.Random(seed)
    raw = [rng.uniform(-1.0, 1.0) for _ in range(dim)]
    # Loose normalisation: divide by L2 norm so cosine similarity is
    # well-behaved against the index.
    norm = sum(x * x for x in raw) ** 0.5 or 1.0
    return [x / norm for x in raw]


async def _seed(dsn: str) -> dict[str, UUID]:
    """Seed two tenants, four memories covering each scope and the
    presence/absence of an embedding."""
    tenant_id = uuid4()
    other_tenant = uuid4()
    user_id = uuid4()
    team_id = uuid4()
    project_id = uuid4()
    # Four memories — descriptive ids so failures are readable.
    asyncpg_sql = uuid4()
    pgvector_local = uuid4()
    rest_endpoints_team = uuid4()
    private_secret = uuid4()
    other_tenant_mem = uuid4()
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
            tenant_id,
            "Tenant A",
            "tenant-a-recall",
            other_tenant,
            "Tenant B",
            "tenant-b-recall",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-recall",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3)",
            user_id,
            "alice@recall.test",
            "h",
        )
        await conn.execute(
            "INSERT INTO teams (id, tenant_id, name) VALUES ($1, $2, $3)",
            team_id,
            tenant_id,
            "Team A",
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, $3)",
            project_id,
            tenant_id,
            "Recall Project",
        )

        # ---- Four memories in tenant A, two scopes that share owners ----
        await conn.execute(
            "INSERT INTO memory_entries"
            " (id, tenant_id, scope, type, content, project_id, embedding)"
            " VALUES ($1, $2, 'project_shared', 'semantic', $3, $4, $5::vector)",
            asyncpg_sql,
            tenant_id,
            "Project uses asyncpg and SQLAlchemy 2.x async; never psycopg3.",
            project_id,
            "[" + ",".join(f"{x:.6f}" for x in _vec(1)) + "]",
        )
        await conn.execute(
            "INSERT INTO memory_entries"
            " (id, tenant_id, scope, type, content, project_id, embedding)"
            " VALUES ($1, $2, 'project_shared', 'semantic', $3, $4, NULL)",
            pgvector_local,
            tenant_id,
            "Vector search runs locally via pgvector HNSW — no external service.",
            project_id,
        )
        await conn.execute(
            "INSERT INTO memory_entries"
            " (id, tenant_id, scope, type, content, team_id, embedding)"
            " VALUES ($1, $2, 'team_shared', 'episodic', $3, $4, $5::vector)",
            rest_endpoints_team,
            tenant_id,
            "Team prefers REST endpoints over GraphQL for internal services.",
            team_id,
            "[" + ",".join(f"{x:.6f}" for x in _vec(2)) + "]",
        )
        await conn.execute(
            "INSERT INTO memory_entries"
            " (id, tenant_id, scope, type, content, user_id, embedding)"
            " VALUES ($1, $2, 'private', 'semantic', $3, $4, NULL)",
            private_secret,
            tenant_id,
            "Alice's private memory: avoid Friday deploys.",
            user_id,
        )

        # ---- One memory in tenant B (must never surface for tenant A) ----
        await conn.execute(
            "INSERT INTO memory_entries"
            " (id, tenant_id, scope, type, content, embedding)"
            " VALUES ($1, $2, 'global', 'semantic',"
            " 'asyncpg is the canonical async driver everywhere.', NULL)",
            other_tenant_mem,
            other_tenant,
        )
    finally:
        await conn.close()
    return {
        "tenant_id": tenant_id,
        "other_tenant": other_tenant,
        "user_id": user_id,
        "team_id": team_id,
        "project_id": project_id,
        "asyncpg_sql": asyncpg_sql,
        "pgvector_local": pgvector_local,
        "rest_endpoints_team": rest_endpoints_team,
        "private_secret": private_secret,
        "other_tenant_mem": other_tenant_mem,
    }


async def _open_session(app_database_url: str, tenant_id: UUID):
    engine = create_async_engine(app_database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    session = session_factory()
    await session.execute(
        sa_text("SELECT set_config('app.tenant_id', :tid, false)"),
        {"tid": str(tenant_id)},
    )
    return engine, session


# ---------------------------------------------------------------------------
# BM25-only path
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_bm25_only_returns_text_matches(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await _seed(migrations_pg_dsn)

    engine, session = await _open_session(app_database_url, seeded["tenant_id"])
    try:
        hits = await recall(
            session,
            query="asyncpg sqlalchemy",
            tenant_id=seeded["tenant_id"],
            scopes=["project_shared", "team_shared", "global"],
            project_id=seeded["project_id"],
            team_id=seeded["team_id"],
            user_id=seeded["user_id"],
            # No query embedding → vector path is empty.
        )
    finally:
        await session.close()
        await engine.dispose()

    ids = [h.memory_id for h in hits]
    assert seeded["asyncpg_sql"] in ids, ids
    # All hits ranked by bm25 only (vector path empty).
    for hit in hits:
        assert hit.vector_rank is None
        assert hit.bm25_rank is not None


# ---------------------------------------------------------------------------
# Vector-only path
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_vector_only_returns_nearest_embedding(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await _seed(migrations_pg_dsn)

    # Query vector identical to the one we stored for `asyncpg_sql` →
    # cosine distance 0 → top hit.
    engine, session = await _open_session(app_database_url, seeded["tenant_id"])
    try:
        hits = await recall(
            session,
            query="",  # blank text → BM25 path returns nothing
            tenant_id=seeded["tenant_id"],
            scopes=["project_shared", "team_shared", "global"],
            project_id=seeded["project_id"],
            team_id=seeded["team_id"],
            user_id=seeded["user_id"],
            query_embedding=_vec(1),
        )
    finally:
        await session.close()
        await engine.dispose()

    ids = [h.memory_id for h in hits]
    assert ids, "vector path returned nothing"
    assert ids[0] == seeded["asyncpg_sql"]
    # Memories without an embedding never surface from the vector path.
    assert seeded["pgvector_local"] not in ids


# ---------------------------------------------------------------------------
# Hybrid path
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_hybrid_combines_both_paths_with_rrf(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await _seed(migrations_pg_dsn)

    engine, session = await _open_session(app_database_url, seeded["tenant_id"])
    try:
        hits = await recall(
            session,
            query="pgvector vector search",
            tenant_id=seeded["tenant_id"],
            scopes=["project_shared", "team_shared", "global"],
            project_id=seeded["project_id"],
            team_id=seeded["team_id"],
            user_id=seeded["user_id"],
            query_embedding=_vec(1),
        )
    finally:
        await session.close()
        await engine.dispose()

    # We expect at least:
    #  - `pgvector_local` via BM25 (it mentions "pgvector" + "vector"),
    #  - `asyncpg_sql` via the vector path (its stored vector is the query),
    # so both must surface from at least one of the two paths.
    ids = [h.memory_id for h in hits]
    assert seeded["asyncpg_sql"] in ids
    assert seeded["pgvector_local"] in ids


# ---------------------------------------------------------------------------
# Scope + cross-tenant filtering
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_private_scope_requires_matching_user_id(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await _seed(migrations_pg_dsn)

    engine, session = await _open_session(app_database_url, seeded["tenant_id"])
    try:
        # Same alice → her private memory surfaces.
        hits_owner = await recall(
            session,
            query="Friday deploys",
            tenant_id=seeded["tenant_id"],
            scopes=["private", "project_shared", "team_shared", "global"],
            project_id=seeded["project_id"],
            team_id=seeded["team_id"],
            user_id=seeded["user_id"],
        )
        # Different user → no row leaks.
        hits_stranger = await recall(
            session,
            query="Friday deploys",
            tenant_id=seeded["tenant_id"],
            scopes=["private", "project_shared", "team_shared", "global"],
            project_id=seeded["project_id"],
            team_id=seeded["team_id"],
            user_id=uuid4(),
        )
    finally:
        await session.close()
        await engine.dispose()

    assert seeded["private_secret"] in [h.memory_id for h in hits_owner]
    assert seeded["private_secret"] not in [h.memory_id for h in hits_stranger]


@pytest.mark.asyncio
async def test_cross_tenant_memory_never_leaks(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    """Tenant A's recall must never surface tenant B's memories, even
    when the text matches perfectly and the scope is 'global'."""
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await _seed(migrations_pg_dsn)

    engine, session = await _open_session(app_database_url, seeded["tenant_id"])
    try:
        hits = await recall(
            session,
            query="asyncpg canonical async driver",
            tenant_id=seeded["tenant_id"],
            scopes=["project_shared", "team_shared", "global", "private"],
            project_id=seeded["project_id"],
            team_id=seeded["team_id"],
            user_id=seeded["user_id"],
        )
    finally:
        await session.close()
        await engine.dispose()

    assert seeded["other_tenant_mem"] not in [h.memory_id for h in hits]


@pytest.mark.asyncio
async def test_scope_filter_drops_team_when_not_in_list(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    """If the caller does not include 'team_shared' in the scopes
    list the team-shared memory never surfaces."""
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await _seed(migrations_pg_dsn)

    engine, session = await _open_session(app_database_url, seeded["tenant_id"])
    try:
        hits = await recall(
            session,
            query="REST endpoints",
            tenant_id=seeded["tenant_id"],
            scopes=["project_shared", "global"],  # team_shared OMITTED
            project_id=seeded["project_id"],
            team_id=seeded["team_id"],
            user_id=seeded["user_id"],
        )
    finally:
        await session.close()
        await engine.dispose()

    assert seeded["rest_endpoints_team"] not in [h.memory_id for h in hits]
