"""Honest runtime availability for tools (Plan 06.18 task_06_18_06, ADR 0049).

Three invariants land here:

  1. **``is_runtime_wired`` reflects what the runtime can really register.**
     ``ToolResponse`` (``GET /tools`` and ``GET /tools/{id}``) carries a derived
     ``is_runtime_wired`` flag = the tool's canonical name is in the runtime's
     registrable set (or its ``implementation_type`` is one the runtime wires
     from a serialised spec). A builtin with no executor (``apply_patch`` /
     ``search_code`` / ``summarize_text``) is ``False``; a wired family member
     (``read_file``) and an executable custom tool (``http_endpoint``) are
     ``True``.

  2. **``PUT /agents/{id}/tools`` rejects a non-executable builtin with 422.**
     Assigning a builtin tool that ends up a silent ``unknown tool`` is refused
     up front rather than failing at run time.

  3. **The ``git`` family is gone from the seed and ``semantic_search``
     reconciles onto the runtime's ``rag_search``.** No ``git_*`` row is seeded;
     ``semantic_search`` (catalog) resolves to ``rag_search`` (runtime) so it is
     wired, not orphaned.

The first invariant is also cross-checked against what the agent-runtime boot
actually registers (``register_builtin_families``) so the shared-domain set
cannot silently drift from the executor.
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
# Seed: one tenant_admin, a project, a project_local agent, several built-in
# tools (wired + not wired) and one executable custom tool.
# ---------------------------------------------------------------------------
async def _seed(dsn: str) -> dict[str, UUID]:
    tenant = uuid4()
    admin_user = uuid4()
    project = uuid4()
    agent = uuid4()
    read_file = uuid4()
    apply_patch = uuid4()
    semantic_search = uuid4()
    custom_http = uuid4()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE agent_tools, tools, agents, projects,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            tenant,
            "Acme",
            "acme-avail",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, 'admin@acme.test', 'h')",
            admin_user,
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin')",
            uuid4(),
            tenant,
            admin_user,
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, 'Webapp')",
            project,
            tenant,
        )
        await conn.execute(
            "INSERT INTO agents"
            " (id, tenant_id, name, role, scope, agent_type, system_prompt, project_id)"
            " VALUES ($1, $2, 'backend-dev', 'backend_dev',"
            "         'project_local', 'ai', 'You are a backend dev.', $3)",
            agent,
            tenant,
            project,
        )
        # Built-in tools (platform-owned, visible via the read-through policy).
        await conn.execute(
            "INSERT INTO tools"
            " (id, tenant_id, name, description, category,"
            "  implementation_type, security_level, is_builtin)"
            " VALUES"
            " ($1, $2, 'read_file', 'read', 'file', 'builtin', 'safe', true),"
            " ($3, $2, 'apply_patch', 'patch', 'file', 'builtin', 'sandboxed', true),"
            " ($4, $2, 'semantic_search', 'sem', 'knowledge', 'builtin', 'safe', true)",
            read_file,
            _PLATFORM_TENANT_ID,
            apply_patch,
            semantic_search,
        )
        # An executable custom tool (http_endpoint) owned by the tenant.
        await conn.execute(
            "INSERT INTO tools"
            " (id, tenant_id, name, description, category,"
            "  implementation_type, security_level, is_builtin)"
            " VALUES ($1, $2, 'acme_deploy', 'deploy', 'custom',"
            "         'http_endpoint', 'sandboxed', false)",
            custom_http,
            tenant,
        )
    finally:
        await conn.close()
    return {
        "tenant": tenant,
        "admin_user": admin_user,
        "agent": agent,
        "read_file": read_file,
        "apply_patch": apply_patch,
        "semantic_search": semantic_search,
        "custom_http": custom_http,
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
# 1. is_runtime_wired reflects the registrable set
# ===========================================================================
@pytest.mark.asyncio
async def test_is_runtime_wired_flag_on_tool_response(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint(seeded["admin_user"], seeded["tenant"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        listed = await client.get("/tools", headers=headers)
        assert listed.status_code == 200, listed.text
        by_name = {t["name"]: t for t in listed.json()}

        # Every row exposes the derived flag.
        for tool in by_name.values():
            assert "is_runtime_wired" in tool

        # A wired family member.
        assert by_name["read_file"]["is_runtime_wired"] is True
        # A builtin with NO runtime executor.
        assert by_name["apply_patch"]["is_runtime_wired"] is False
        # semantic_search reconciles onto rag_search -> wired.
        assert by_name["semantic_search"]["is_runtime_wired"] is True
        # An executable custom tool (http_endpoint) -> wired.
        assert by_name["acme_deploy"]["is_runtime_wired"] is True


@pytest.mark.asyncio
async def test_get_single_tool_carries_flag(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint(seeded["admin_user"], seeded["tenant"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/tools/{seeded['apply_patch']}", headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["is_runtime_wired"] is False


def test_runtime_wired_set_matches_runtime_executor() -> None:
    """Cross-check the shared-domain set against what the agent-runtime boot
    actually registers (the builtin families) + the run_* tools + shell_exec.
    Guards against the shared set drifting from the real executor."""
    from agent_runtime.builtin_families import register_builtin_families
    from agent_runtime.internal_api import InternalAgentAPI
    from agent_runtime.orchestration_tools import OrchestrationSink
    from agent_runtime.tools import ToolRegistry
    from shared_domain.tool_names import RUNTIME_WIRED_TOOL_NAMES

    class _FakeAPI(InternalAgentAPI):
        def __init__(self) -> None:  # bypass real config
            pass

    registry = ToolRegistry()
    registered = set(
        register_builtin_families(
            registry,
            api=_FakeAPI(),
            sink=OrchestrationSink(),
            # A task id is needed for the `stack` family (stack_exec) — the
            # worker-mediated toolchain exec (ADR 0093) targets the task's
            # worktree. A real run always carries one.
            task_id="task-contract",
        )
    )
    # The run_* docker_command tools (wired from serialised specs) + the
    # per-project shell are registered elsewhere; add them to the expected set.
    expected = registered | {
        "run_pytest",
        "run_lint",
        "run_typecheck",
        "run_build",
        "shell_exec",
    }
    assert expected == RUNTIME_WIRED_TOOL_NAMES


# ===========================================================================
# 2. PUT /agents/{id}/tools rejects a non-executable builtin
# ===========================================================================
@pytest.mark.asyncio
async def test_put_agent_tools_rejects_non_executable_builtin(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint(seeded["admin_user"], seeded["tenant"])
    headers = {"Authorization": f"Bearer {token}"}
    agent = seeded["agent"]

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        # apply_patch is a builtin with no runtime executor -> 422.
        bad = await client.put(
            f"/agents/{agent}/tools",
            json={"tools": [{"tool_id": str(seeded["apply_patch"])}]},
            headers=headers,
        )
        assert bad.status_code == 422, bad.text
        assert "apply_patch" in bad.text

        # A wired tool is accepted.
        good = await client.put(
            f"/agents/{agent}/tools",
            json={"tools": [{"tool_id": str(seeded["read_file"])}]},
            headers=headers,
        )
        assert good.status_code == 200, good.text
        assert {t["name"] for t in good.json()} == {"read_file"}


@pytest.mark.asyncio
async def test_put_agent_tools_accepts_semantic_search(
    configured_app, migrations_pg_dsn: str
) -> None:
    """semantic_search reconciles onto rag_search, so it is assignable."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint(seeded["admin_user"], seeded["tenant"])
    headers = {"Authorization": f"Bearer {token}"}
    agent = seeded["agent"]

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.put(
            f"/agents/{agent}/tools",
            json={"tools": [{"tool_id": str(seeded["semantic_search"])}]},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text


# ===========================================================================
# 3. git family retired from the seed
# ===========================================================================
def test_git_family_absent_from_seed() -> None:
    from api_server.seeds.builtin_tools import BUILTIN_TOOLS

    names = {t.name for t in BUILTIN_TOOLS}
    assert not any(n.startswith("git_") for n in names), sorted(n for n in names if "git" in n)
    # No row carries the retired `git` category either.
    assert all(t.category != "git" for t in BUILTIN_TOOLS)
