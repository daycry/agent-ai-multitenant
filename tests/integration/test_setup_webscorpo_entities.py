"""Integration test for the WebScorpo entity seed (task_demo_ws_02).

Exercises ``scripts/setup_webscorpo.py`` against a REAL migrated PostgreSQL
(the throwaway ``agentic_platform_test`` DB the integration conftest builds).
Asserts that after running ``seed_webscorpo`` once:

  * the tenant/org Mediapro, the team WebScorpo and the project webscorpo exist,
    all carrying the SAME ``tenant_id`` (tenant-scoped);
  * the 10 specialist agents exist (agent_type='ai', tenant-scoped) and are all
    members of the team, with exactly one team leader (the PM);
  * the project carries the Plan 06.16 fields — ``allowed_commands`` with the
    expected PHP/CI4 toolchain and ``default_runtime_template='php-phpunit'``;
  * every agent has its per-role tools assigned via the ``agent_tools`` junction
    (Plan 06.15), with ``shell_exec`` + the file family on EVERY agent and the
    ``run_*`` runtime tools on backend/dba/qa/devops (the git family was retired
    from the catalog in Plan 06.18, ADR 0049, so it is no longer assigned);
  * re-running the seed is idempotent — counts do not grow and no duplicates
    appear.

The seed itself ensures the built-in tool catalog (incl. ``shell_exec``) exists,
so the test only needs the migrated schema — no extra catalog seeding.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

# Make ``scripts/`` importable (no __init__.py there; the demo seeds run as
# top-level modules). Mirrors how setup_webscorpo.py bootstraps its own path.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import setup_webscorpo as ws  # noqa: E402  (import after sys.path tweak)

# run_* runtime tools that only backend/dba/qa/devops receive (Plan 06.15).
_RUN_SLUGS = ("run-pytest", "run-lint", "run-typecheck", "run-build")
_RUN_AGENTS = {
    "webscorpo-backend",
    "webscorpo-dba",
    "webscorpo-qa",
    "webscorpo-devops",
}


async def _truncate_domain(admin_url: str) -> None:
    """Clear the tenant-scoped domain tables so this demo seed runs on a clean
    slate (CASCADE handles the junctions). Since Plan 06.18 added
    ``UNIQUE (tenant_id, name)`` on ``tools``, a built-in ``read_file`` left by
    an earlier test in the session would otherwise collide with this seed's own
    ``seed_builtin_tools`` and abort the whole seed transaction."""
    engine = create_async_engine(admin_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "TRUNCATE tools, skills, agents, teams, projects,"
                    " knowledge_bases, organizations RESTART IDENTITY CASCADE"
                )
            )
    finally:
        await engine.dispose()


@pytest.fixture()
def migrated_db(alembic_config, admin_database_url: str):
    """Upgrade the throwaway test DB to head + grant the app role + truncate the
    domain tables. Yields the admin (BYPASSRLS) URL the seed writes through."""
    command.upgrade(alembic_config, "head")
    from tests.integration.conftest import _grant_app_user_existing_tables

    asyncio.run(_grant_app_user_existing_tables())
    asyncio.run(_truncate_domain(admin_database_url))
    yield admin_database_url


async def _run_seed(admin_url: str) -> ws.SeedResult:
    """Run the seed once inside its own engine/transaction. Caller disposes."""
    engine = create_async_engine(admin_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session, session.begin():
            return await ws.seed_webscorpo(session)
    finally:
        await engine.dispose()


async def _count(admin_url: str, sql: str, params: dict[str, object]) -> int:
    engine = create_async_engine(admin_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session:
            return int((await session.execute(text(sql), params)).scalar_one())
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Entities: tenant + team + 10 agents + project
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_tenant_team_and_project_exist_tenant_scoped(migrated_db) -> None:
    result = await _run_seed(migrated_db)
    tid = ws.tenant_id()
    assert result.tenant_id == tid

    engine = create_async_engine(migrated_db)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session:
            org = (
                await session.execute(
                    text("SELECT name, slug FROM organizations WHERE id = :id"),
                    {"id": str(tid)},
                )
            ).first()
            assert org is not None and org.slug == ws.TENANT_SLUG

            team = (
                await session.execute(
                    text(
                        "SELECT tenant_id, name FROM teams" " WHERE id = :id AND deleted_at IS NULL"
                    ),
                    {"id": str(ws.team_id())},
                )
            ).first()
            assert team is not None
            assert team.name == ws.TEAM_NAME
            # tenant-scoped: the team belongs to the Mediapro tenant.
            assert UUID(str(team.tenant_id)) == tid

            project = (
                await session.execute(
                    text(
                        "SELECT tenant_id, name, status, team_id FROM projects"
                        " WHERE id = :id AND deleted_at IS NULL"
                    ),
                    {"id": str(ws.project_id())},
                )
            ).first()
            assert project is not None
            assert project.name == ws.PROJECT_NAME
            assert project.status == "active"
            assert UUID(str(project.tenant_id)) == tid
            # project wired to the WebScorpo team.
            assert UUID(str(project.team_id)) == ws.team_id()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ten_agents_seeded_and_team_membered(migrated_db) -> None:
    result = await _run_seed(migrated_db)
    assert len(result.agent_ids) == 10

    tid = ws.tenant_id()
    engine = create_async_engine(migrated_db)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session:
            # All 10 agents exist, agent_type='ai', tenant-scoped, not deleted.
            rows = (
                await session.execute(
                    text(
                        "SELECT id, agent_type, tenant_id, scope FROM agents"
                        " WHERE id = ANY(CAST(:ids AS uuid[])) AND deleted_at IS NULL"
                    ),
                    {"ids": [str(i) for i in result.agent_ids.values()]},
                )
            ).all()
            assert len(rows) == 10
            for r in rows:
                assert r.agent_type == "ai"
                assert UUID(str(r.tenant_id)) == tid
                assert r.scope == "global_tenant_template"

            # All 10 are members of the team.
            members = (
                await session.execute(
                    text(
                        "SELECT agent_id, is_team_leader FROM team_members" " WHERE team_id = :tid"
                    ),
                    {"tid": str(ws.team_id())},
                )
            ).all()
            assert {UUID(str(m.agent_id)) for m in members} == set(result.agent_ids.values())
            # Exactly one leader, and it is the PM.
            leaders = [m for m in members if m.is_team_leader]
            assert len(leaders) == 1
            assert UUID(str(leaders[0].agent_id)) == result.agent_ids["webscorpo-pm"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_project_command_config_plan_06_16(migrated_db) -> None:
    await _run_seed(migrated_db)
    engine = create_async_engine(migrated_db)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT allowed_commands, default_runtime_template"
                        " FROM projects WHERE id = :id"
                    ),
                    {"id": str(ws.project_id())},
                )
            ).first()
            assert row is not None
            assert list(row.allowed_commands) == list(ws.PROJECT_ALLOWED_COMMANDS)
            # The PHP/CI4 toolchain (analysis §6.2) is present.
            for cmd in ("php", "composer", "vendor/bin/phpunit", "npm"):
                assert cmd in row.allowed_commands
            assert row.default_runtime_template == "php-phpunit"
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Tool assignments (agent_tools junction — Plan 06.15)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_tools_assigned_per_agent(migrated_db) -> None:
    from api_server.seeds.builtin_tools import _tool_id

    result = await _run_seed(migrated_db)

    shell_id = _tool_id("shell-exec")
    # File family on EVERY agent. The git family (git-status/diff/commit/log) was
    # RETIRED from the catalog in Plan 06.18 (task_06_18_06, ADR 0049) — it has no
    # runtime executor — so it is no longer assigned (and would FK-fail).
    file_ids = {_tool_id(s) for s in ("read-file", "write-file", "list-files", "search-code")}
    run_ids = {_tool_id(s) for s in _RUN_SLUGS}

    engine = create_async_engine(migrated_db)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session:
            for slug, aid in result.agent_ids.items():
                tool_ids = {
                    UUID(str(r.tool_id))
                    for r in (
                        await session.execute(
                            text("SELECT tool_id FROM agent_tools WHERE agent_id = :aid"),
                            {"aid": str(aid)},
                        )
                    ).all()
                }
                assert tool_ids, f"{slug} has no tools assigned"
                # shell_exec + file family on EVERY agent (git retired in 06.18).
                assert shell_id in tool_ids, f"{slug} missing shell_exec"
                assert file_ids <= tool_ids, f"{slug} missing file tools"
                # run_* only on backend/dba/qa/devops.
                if slug in _RUN_AGENTS:
                    assert run_ids <= tool_ids, f"{slug} missing run_* tools"
                else:
                    assert not (run_ids & tool_ids), f"{slug} should not have run_* tools"

            # shell_exec must really exist in the catalog (the seed ensures it).
            shell_exists = (
                await session.execute(
                    text("SELECT name FROM tools WHERE id = :id"),
                    {"id": str(shell_id)},
                )
            ).first()
            assert shell_exists is not None and shell_exists.name == "shell_exec"
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Idempotency: re-running never duplicates
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_seed_is_idempotent(migrated_db) -> None:
    tid = str(ws.tenant_id())

    # First run.
    await _run_seed(migrated_db)

    orgs_1 = await _count(
        migrated_db, "SELECT count(*) FROM organizations WHERE id = :id", {"id": tid}
    )
    teams_1 = await _count(
        migrated_db,
        "SELECT count(*) FROM teams WHERE id = :id AND deleted_at IS NULL",
        {"id": str(ws.team_id())},
    )
    agents_1 = await _count(
        migrated_db,
        "SELECT count(*) FROM agents WHERE tenant_id = :tid AND deleted_at IS NULL",
        {"tid": tid},
    )
    members_1 = await _count(
        migrated_db,
        "SELECT count(*) FROM team_members WHERE team_id = :tid",
        {"tid": str(ws.team_id())},
    )
    projects_1 = await _count(
        migrated_db,
        "SELECT count(*) FROM projects WHERE tenant_id = :tid AND deleted_at IS NULL",
        {"tid": tid},
    )
    assignments_1 = await _count(
        migrated_db,
        "SELECT count(*) FROM agent_tools at"
        " JOIN agents a ON a.id = at.agent_id WHERE a.tenant_id = :tid",
        {"tid": tid},
    )

    # Second + third run — counts must not change.
    await _run_seed(migrated_db)
    result3 = await _run_seed(migrated_db)
    assert len(result3.agent_ids) == 10

    assert (
        await _count(migrated_db, "SELECT count(*) FROM organizations WHERE id = :id", {"id": tid})
        == orgs_1
        == 1
    )
    assert (
        await _count(
            migrated_db,
            "SELECT count(*) FROM teams WHERE id = :id AND deleted_at IS NULL",
            {"id": str(ws.team_id())},
        )
        == teams_1
        == 1
    )
    assert (
        await _count(
            migrated_db,
            "SELECT count(*) FROM agents WHERE tenant_id = :tid AND deleted_at IS NULL",
            {"tid": tid},
        )
        == agents_1
        == 10
    )
    assert (
        await _count(
            migrated_db,
            "SELECT count(*) FROM team_members WHERE team_id = :tid",
            {"tid": str(ws.team_id())},
        )
        == members_1
        == 10
    )
    assert (
        await _count(
            migrated_db,
            "SELECT count(*) FROM projects WHERE tenant_id = :tid AND deleted_at IS NULL",
            {"tid": tid},
        )
        == projects_1
        == 1
    )
    assert (
        await _count(
            migrated_db,
            "SELECT count(*) FROM agent_tools at"
            " JOIN agents a ON a.id = at.agent_id WHERE a.tenant_id = :tid",
            {"tid": tid},
        )
        == assignments_1
    )
