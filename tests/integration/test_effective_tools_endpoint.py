"""`GET /agents/{id}/effective-tools` — contrato de frontera con 06.17.

Plan 06.18 task_06_18_07 (ADR 0048/0049). Este endpoint es la **única** fuente
honesta de "qué tools ejecuta de verdad un agente". Lo consumirá el Hub de
Capacidad de 06.17, que NO recalcula la intersección.

El contrato que aquí se fija (y que estos tests blindan):

  * ``assigned`` — las tools ASIGNADAS al agente (canónicas), cada una con un
    flag ``executable_in_runtime`` (= ``is_runtime_wired``).
  * ``effective`` — el conjunto que el runtime ejecuta de verdad: la
    intersección de ``assigned`` con el allowlist del modo (vía el punto único
    ``combine_tool_allowlists``) MÁS ``shell_exec`` SOLO si el agente lo tiene
    asignado Y ``project.allowed_commands`` no está vacío.
  * ``warnings`` — avisos explícitos legibles:
      - "set efectivo vacío en modo X" cuando la intersección con el modo es ∅;
      - "tool asignada pero no ejecutable en runtime" por cada tool no cableada;
      - "shell_exec asignado pero allowed_commands vacío".

Invariantes verificadas:
  1. tenant B → 404 (aislamiento multi-tenant).
  2. el set efectivo coincide con lo registrable (no inventa disponibilidad).
  3. ``shell_exec`` aparece SOLO si ``allowed_commands`` no vacío.
  4. avisos correctos en cada caso.
  5. agente SIN asignaciones → comportamiento honesto (no restringido).
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
# Seed: dos tenants. Tenant A: proyecto con allowed_commands, agente con varias
# tools asignadas (wired + no wired + shell_exec). Tenant B: su propio agente
# (para el aislamiento cross-tenant).
# ---------------------------------------------------------------------------
async def _seed(dsn: str) -> dict[str, UUID]:
    tenant_a = uuid4()
    tenant_b = uuid4()
    admin_a = uuid4()
    admin_b = uuid4()
    project_a = uuid4()
    project_a_empty = uuid4()
    project_b = uuid4()
    agent_a = uuid4()
    agent_a_empty = uuid4()
    agent_b = uuid4()

    read_file = uuid4()
    write_file = uuid4()
    apply_patch = uuid4()
    shell_exec = uuid4()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE agent_tools, tools, agents, projects,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug)"
            " VALUES ($1, $2, $3), ($4, $5, $6), ($7, $8, $9)",
            tenant_a,
            "Acme",
            "acme-eff",
            tenant_b,
            "Globex",
            "globex-eff",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform",
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
        # Project A: allowed_commands NO vacío (shell_exec efectivo).
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, allowed_commands)"
            " VALUES ($1, $2, 'Webapp', $3)",
            project_a,
            tenant_a,
            ["pytest", "ruff"],
        )
        # Project A-empty: allowed_commands vacío (shell_exec NO efectivo).
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, allowed_commands)"
            " VALUES ($1, $2, 'Webapp-2', $3)",
            project_a_empty,
            tenant_a,
            [],
        )
        # Project B (tenant B), para su agente project_local.
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, allowed_commands)"
            " VALUES ($1, $2, 'B-app', $3)",
            project_b,
            tenant_b,
            [],
        )
        await conn.execute(
            "INSERT INTO agents"
            " (id, tenant_id, name, role, scope, agent_type, system_prompt, project_id)"
            " VALUES"
            " ($1, $2, 'backend-dev', 'backend_dev', 'project_local', 'ai', 'p', $3),"
            " ($4, $2, 'no-tools', 'backend_dev', 'project_local', 'ai', 'p', $3),"
            " ($5, $6, 'b-dev', 'backend_dev', 'project_local', 'ai', 'p', $7)",
            agent_a,
            tenant_a,
            project_a,
            agent_a_empty,
            agent_b,
            tenant_b,
            project_b,
        )
        # Built-in tools (platform-owned, visible via read-through policy).
        await conn.execute(
            "INSERT INTO tools"
            " (id, tenant_id, name, description, category,"
            "  implementation_type, security_level, is_builtin)"
            " VALUES"
            " ($1, $2, 'read_file', 'read', 'file', 'builtin', 'safe', true),"
            " ($3, $2, 'write_file', 'write', 'file', 'builtin', 'sandboxed', true),"
            " ($4, $2, 'apply_patch', 'patch', 'file', 'builtin', 'sandboxed', true),"
            " ($5, $2, 'shell_exec', 'shell', 'command', 'builtin', 'privileged', true)",
            read_file,
            _PLATFORM_TENANT_ID,
            write_file,
            apply_patch,
            shell_exec,
        )
        # Asignación de agent_a: read_file (wired) + write_file (wired) +
        # apply_patch (NO wired) + shell_exec (wired, condicionado a commands).
        for tool_id in (read_file, write_file, apply_patch, shell_exec):
            await conn.execute(
                "INSERT INTO agent_tools (agent_id, tool_id) VALUES ($1, $2)",
                agent_a,
                tool_id,
            )
    finally:
        await conn.close()
    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "admin_a": admin_a,
        "admin_b": admin_b,
        "project_a": project_a,
        "project_a_empty": project_a_empty,
        "agent_a": agent_a,
        "agent_a_empty": agent_a_empty,
        "agent_b": agent_b,
        "read_file": read_file,
        "write_file": write_file,
        "apply_patch": apply_patch,
        "shell_exec": shell_exec,
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
# 1. Aislamiento multi-tenant: tenant B no ve el agente de A.
# ===========================================================================
@pytest.mark.asyncio
async def test_effective_tools_cross_tenant_404(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token_b = await _mint(seeded["admin_b"], seeded["tenant_b"])
    headers = {"Authorization": f"Bearer {token_b}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/agents/{seeded['agent_a']}/effective-tools", headers=headers)
        assert resp.status_code == 404, resp.text


# ===========================================================================
# 2. Set efectivo coincide con lo registrable (sin modo). shell_exec presente
#    porque allowed_commands no vacío; apply_patch marcado no-ejecutable.
# ===========================================================================
@pytest.mark.asyncio
async def test_effective_set_matches_runtime_without_mode(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/agents/{seeded['agent_a']}/effective-tools", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()

        assert body["agent_id"] == str(seeded["agent_a"])
        assert body["mode"] is None

        # assigned: las cuatro asignadas, con flag executable_in_runtime.
        assigned = {t["name"]: t for t in body["assigned"]}
        assert set(assigned) == {"read_file", "write_file", "apply_patch", "shell_exec"}
        assert assigned["read_file"]["executable_in_runtime"] is True
        assert assigned["write_file"]["executable_in_runtime"] is True
        assert assigned["apply_patch"]["executable_in_runtime"] is False
        assert assigned["shell_exec"]["executable_in_runtime"] is True

        # effective: sin modo, intersección = asignadas wired; shell_exec
        # presente (allowed_commands no vacío); apply_patch NO (no ejecutable).
        effective = set(body["effective"])
        assert effective == {"read_file", "write_file", "shell_exec"}
        assert "apply_patch" not in effective

        # shell_exec efectivo reportado explícitamente.
        assert body["shell_exec_effective"] is True

        # aviso: apply_patch asignada pero no ejecutable.
        warnings_text = " ".join(body["warnings"])
        assert "apply_patch" in warnings_text


# ===========================================================================
# 3. shell_exec NO aparece si allowed_commands vacío + aviso explícito.
# ===========================================================================
@pytest.mark.asyncio
async def test_shell_exec_excluded_when_no_allowed_commands(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    # Mover el agente al proyecto con allowed_commands vacío.
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "UPDATE agents SET project_id = $1 WHERE id = $2",
            seeded["project_a_empty"],
            seeded["agent_a"],
        )
    finally:
        await conn.close()

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/agents/{seeded['agent_a']}/effective-tools", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()

        assert "shell_exec" not in set(body["effective"])
        assert body["shell_exec_effective"] is False
        warnings_text = " ".join(body["warnings"])
        assert "shell_exec" in warnings_text
        assert "allowed_commands" in warnings_text


# ===========================================================================
# 4. Con un modo cuyo allowlist no comparte tools → set efectivo vacío + aviso.
# ===========================================================================
@pytest.mark.asyncio
async def test_empty_effective_set_in_discussion_mode(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        # discussion mode allowlist is empty → intersección vacía.
        resp = await client.get(
            f"/agents/{seeded['agent_a']}/effective-tools?mode=discussion",
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["mode"] == "discussion"
        assert body["effective"] == []
        warnings_text = " ".join(body["warnings"]).lower()
        assert "discussion" in warnings_text


# ===========================================================================
# 5. Con execution mode (allowlist amplio) la intersección incluye file_read/
#    file_write (alias) → read_file/write_file + shell_exec.
# ===========================================================================
@pytest.mark.asyncio
async def test_effective_set_with_execution_mode(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get(
            f"/agents/{seeded['agent_a']}/effective-tools?mode=execution",
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        effective = set(body["effective"])
        # execution allows file_read/file_write/shell_exec (alias→read/write).
        assert "read_file" in effective
        assert "write_file" in effective
        assert "shell_exec" in effective
        assert "apply_patch" not in effective


# ===========================================================================
# 6. Agente SIN asignaciones → honesto: no restringido (effective None/sentinel),
#    sin avisos de "no ejecutable".
# ===========================================================================
@pytest.mark.asyncio
async def test_agent_without_assignments_is_unrestricted(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get(
            f"/agents/{seeded['agent_a_empty']}/effective-tools", headers=headers
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Sin asignaciones: assigned vacío y unrestricted=True (no per-agent
        # restriction — comportamiento backward-compat de 06.15).
        assert body["assigned"] == []
        assert body["unrestricted"] is True


# ===========================================================================
# 7. Agente inexistente → 404.
# ===========================================================================
@pytest.mark.asyncio
async def test_unknown_agent_404(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/agents/{uuid4()}/effective-tools", headers=headers)
        assert resp.status_code == 404, resp.text
