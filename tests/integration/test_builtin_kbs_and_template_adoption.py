"""Tests for the canonical KB catalog + project template adoption
(Plan 06.9 task_06_9_06 + task_06_9_07 + task_06_9_08).

Covers:

  * `seed_builtin_kbs` inserts the 6 KBs under PLATFORM_TENANT_ID
    with deterministic UUIDs.
  * `seed_builtin_project_templates` populates `default_kb_grants`
    on the 8 templates (with the 5 stack-aware ones non-empty).
  * `apply_template_kb_grants` reads the template's grants and
    creates `kb_projects` rows for the new project.
  * Idempotent: re-adopting the same template doesn't duplicate
    grants.
  * Empty `default_kb_grants` (e.g. doc-modernization) returns [].
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


# ---------------------------------------------------------------------------
# Fixture: migrations applied + GRANT to app_user. Engine is created
# inside each test (one event loop per test) and seeded there too —
# mixing engine creation in the sync fixture with use from a different
# event loop crashes asyncpg's protocol pool with "Event loop is closed".
# ---------------------------------------------------------------------------
@pytest.fixture()
def migrated_db(alembic_config, admin_database_url: str):
    command.upgrade(alembic_config, "head")
    from tests.integration.conftest import _grant_app_user_existing_tables

    asyncio.run(_grant_app_user_existing_tables())
    yield admin_database_url


async def _seeded_engine(admin_url: str):
    """Create an async engine and run every dependency seed once.
    Caller must `await engine.dispose()` when done."""
    from api_server.seeds.builtin_agents import seed_builtin_agents
    from api_server.seeds.builtin_kb_categories import seed_builtin_kb_categories
    from api_server.seeds.builtin_kbs import seed_builtin_kbs
    from api_server.seeds.builtin_project_templates import seed_builtin_project_templates
    from api_server.seeds.builtin_teams import seed_builtin_teams
    from api_server.seeds.platform import ensure_platform_tenant

    engine = create_async_engine(admin_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session, session.begin():
        await ensure_platform_tenant(session)
        await seed_builtin_agents(session)
        await seed_builtin_teams(session)
        # Plan 06.10: categorías antes que KBs (FK).
        await seed_builtin_kb_categories(session)
        await seed_builtin_kbs(session)
        await seed_builtin_project_templates(session)
    return engine


# ---------------------------------------------------------------------------
# builtin_kbs seed
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_builtin_kbs_seeded_with_deterministic_ids(migrated_db) -> None:
    from api_server.seeds.builtin_kbs import BUILTIN_KBS, kb_id_for_slug

    engine = await _seeded_engine(migrated_db)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT id, name FROM knowledge_bases"
                        " WHERE tenant_id = :tid AND deleted_at IS NULL"
                        " ORDER BY name"
                    ),
                    {"tid": str(PLATFORM_TENANT_ID)},
                )
            ).all()
    finally:
        await engine.dispose()

    by_id = {row[0]: row[1] for row in rows}
    for kb in BUILTIN_KBS:
        assert kb.id in by_id, f"missing built-in KB {kb.slug}"
        assert by_id[kb.id] == kb.name

    # Deterministic — kb_id_for_slug must agree with what the seed
    # wrote, otherwise re-seeding would orphan grants.
    for kb in BUILTIN_KBS:
        assert kb_id_for_slug(kb.slug) == kb.id


# ---------------------------------------------------------------------------
# Project template carries default_kb_grants
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_api_rest_template_has_expected_grants(migrated_db) -> None:
    from api_server.seeds.builtin_project_templates import _project_template_id

    engine = await _seeded_engine(migrated_db)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session:
            row = (
                await session.execute(
                    text("SELECT default_kb_grants FROM projects WHERE id = :tid"),
                    {"tid": str(_project_template_id("api-rest"))},
                )
            ).first()
    finally:
        await engine.dispose()

    assert row is not None
    grants = row[0]
    assert set(grants) == {
        "python-fastapi-conventions",
        "api-rest-guidelines",
        "postgresql-best-practices",
    }


@pytest.mark.asyncio
async def test_doc_modernization_template_has_empty_grants(migrated_db) -> None:
    """Templates intentionally stack-agnostic (doc-modernization,
    research-spec, devops-bootstrap) leave the array empty."""
    from api_server.seeds.builtin_project_templates import _project_template_id

    engine = await _seeded_engine(migrated_db)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session:
            row = (
                await session.execute(
                    text("SELECT default_kb_grants FROM projects WHERE id = :tid"),
                    {"tid": str(_project_template_id("doc-modernization"))},
                )
            ).first()
    finally:
        await engine.dispose()

    assert row is not None
    assert row[0] == []


# ---------------------------------------------------------------------------
# Template adoption applies the grants
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_apply_template_grants_creates_kb_projects_rows(
    migrated_db, migrations_pg_dsn: str
) -> None:
    from api_server.seeds.builtin_kbs import kb_id_for_slug
    from api_server.seeds.builtin_project_templates import _project_template_id
    from api_server.seeds.template_adoption import apply_template_kb_grants

    # Create a fresh tenant + project that "adopted" the api-rest template.
    tenant = uuid4()
    new_project = uuid4()
    granter = uuid4()

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'Tadopt', 'tadopt')",
            tenant,
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, 'a@tadopt.test', 'h')",
            granter,
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status)"
            " VALUES ($1, $2, 'P-from-api-rest', 'active')",
            new_project,
            tenant,
        )
    finally:
        await conn.close()

    engine = await _seeded_engine(migrated_db)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session, session.begin():
            granted = await apply_template_kb_grants(
                session,
                template_id=_project_template_id("api-rest"),
                new_project_id=new_project,
                tenant_id=tenant,
                granted_by=granter,
            )

        async with sm() as session:
            rows = (
                await session.execute(
                    text("SELECT kb_id FROM kb_projects WHERE project_id = :pid ORDER BY kb_id"),
                    {"pid": str(new_project)},
                )
            ).all()
    finally:
        await engine.dispose()

    # 3 grants expected (the api-rest template above).
    assert set(granted) == {
        kb_id_for_slug("python-fastapi-conventions"),
        kb_id_for_slug("api-rest-guidelines"),
        kb_id_for_slug("postgresql-best-practices"),
    }
    assert {r[0] for r in rows} == set(granted)


@pytest.mark.asyncio
async def test_apply_template_grants_is_idempotent(migrated_db, migrations_pg_dsn: str) -> None:
    from api_server.seeds.builtin_project_templates import _project_template_id
    from api_server.seeds.template_adoption import apply_template_kb_grants

    tenant = uuid4()
    new_project = uuid4()
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'Tidem', 'tidem')",
            tenant,
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status)"
            " VALUES ($1, $2, 'P-idem', 'active')",
            new_project,
            tenant,
        )
    finally:
        await conn.close()

    template_id = _project_template_id("api-rest")
    engine = await _seeded_engine(migrated_db)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session, session.begin():
            await apply_template_kb_grants(
                session,
                template_id=template_id,
                new_project_id=new_project,
                tenant_id=tenant,
            )
            await apply_template_kb_grants(  # second call — must NOT duplicate
                session,
                template_id=template_id,
                new_project_id=new_project,
                tenant_id=tenant,
            )

        async with sm() as session:
            count = (
                await session.execute(
                    text("SELECT COUNT(*) FROM kb_projects WHERE project_id = :pid"),
                    {"pid": str(new_project)},
                )
            ).scalar_one()
    finally:
        await engine.dispose()
    assert count == 3
