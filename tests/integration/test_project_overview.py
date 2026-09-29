"""GET /projects/{id}/overview — el Resumen del proyecto (`task_ui_10`).

Lo mismo que el dashboard del tenant pero acotado a UN proyecto, más sus avisos. Lo que
estos tests fijan porque se rompe sin avisar:

* **Los avisos se calculan del dominio**, no se inventan: MCP OAuth sin conectar, MCP sin
  tools importadas (mismo criterio derivado que `count_imported_tools`), egress bloqueado
  (misma función que la ficha y el dashboard), repo sin sincronizar (`last_git_sync` que
  escribe el worker) y pausa por presupuesto.
* **Cross-tenant.** Un proyecto de otro tenant es 404, y nada suyo asoma en el Resumen.
* **Acotado al proyecto**: el otro proyecto del mismo tenant no cuenta.
"""

from __future__ import annotations

import asyncio
import json
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")

_SERVERS = [
    {
        "name": "atlassian",
        "transport": "streamable_http",
        "url": "https://mcp.atlassian.com/v1/mcp",
    },
    {"name": "docs", "transport": "http", "url": "https://docs.example.com/mcp"},
]


async def _seed_tenant(conn: asyncpg.Connection, *, slug: str) -> dict[str, UUID]:
    tenant, user, admin = uuid4(), uuid4(), uuid4()
    project, other = uuid4(), uuid4()
    plan, other_plan = uuid4(), uuid4()
    await conn.execute(
        "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $2)", tenant, slug
    )
    for uid, mail, role in (
        (user, f"{slug}@t.test", "tenant_member"),
        (admin, f"{slug}-admin@t.test", "tenant_admin"),
    ):
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, 'h')", uid, mail
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, $4)",
            uuid4(),
            tenant,
            uid,
            role,
        )
    await conn.execute(
        "INSERT INTO projects (id, tenant_id, name, mcp_servers, git_config)"
        " VALUES ($1, $2, $3, $4::jsonb, $5::jsonb)",
        project,
        tenant,
        f"Proyecto {slug}",
        json.dumps(_SERVERS),
        json.dumps({"remote_url": "https://git.example.com/x.git"}),
    )
    await conn.execute(
        "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, $3)",
        other,
        tenant,
        f"Otro {slug}",
    )
    # Una tool importada del servidor `docs` (el criterio derivado del ADR 0166 D4).
    await conn.execute(
        "INSERT INTO tools (id, tenant_id, name, description, category,"
        " implementation_type, security_level, is_builtin)"
        " VALUES ($1, $2, 'docs.search', 'x', 'mcp', 'mcp_tool', 'safe', false)",
        uuid4(),
        tenant,
    )
    for pid, pl, title in ((project, plan, "Plan activo"), (other, other_plan, "Plan ajeno")):
        await conn.execute(
            "INSERT INTO plans (id, tenant_id, project_id, title, status, specification)"
            " VALUES ($1, $2, $3, $4, 'in_progress', '{}'::jsonb)",
            pl,
            tenant,
            pid,
            title,
        )
    tasks = [uuid4(), uuid4()]
    for task, status in zip(tasks, ("done", "in_progress"), strict=True):
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, plan_id, title, status)"
            " VALUES ($1, $2, $3, $4, $5, $6)",
            task,
            tenant,
            project,
            plan,
            f"Tarea {status}",
            status,
        )
    other_task = uuid4()
    await conn.execute(
        "INSERT INTO tasks (id, tenant_id, project_id, plan_id, title, status)"
        " VALUES ($1, $2, $3, $4, 'Tarea ajena', 'in_progress')",
        other_task,
        tenant,
        other,
        other_plan,
    )
    for task, status in ((tasks[1], "running"), (tasks[0], "failed"), (other_task, "running")):
        await conn.execute(
            "INSERT INTO executions (id, tenant_id, task_id, status, steps_log)"
            " VALUES ($1, $2, $3, $4, '[]'::jsonb)",
            uuid4(),
            tenant,
            task,
            status,
        )
    return {
        "tenant": tenant,
        "user": user,
        "admin": admin,
        "project": project,
        "other": other,
    }


async def _seed(dsn: str) -> dict[str, dict[str, UUID]]:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE approval_requests, executions, tasks, plans, tools, projects,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'P', 'ov-p')",
            _PLATFORM_TENANT_ID,
        )
        a = await _seed_tenant(conn, slug="ov-a")
        b = await _seed_tenant(conn, slug="ov-b")
    finally:
        await conn.close()
    return {"a": a, "b": b}


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


async def _token(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    await SessionStore(get_redis()).create(
        sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600
    )
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


async def _overview(app, project: UUID, token: str, *, expect: int = 200) -> dict:  # type: ignore[no-untyped-def]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/projects/{project}/overview", headers={"Authorization": f"Bearer {token}"}
        )
    assert resp.status_code == expect, resp.text
    return resp.json()


def _by_kind(body: dict) -> dict[str, dict]:
    return {w["kind"]: w for w in body["warnings"]}


@pytest.mark.asyncio
async def test_sections_come_in_one_response(configured_app, migrations_pg_dsn: str) -> None:
    a = (await _seed(migrations_pg_dsn))["a"]
    body = await _overview(configured_app, a["project"], await _token(a["admin"], a["tenant"]))
    assert list(body) == [
        "project",
        "plans",
        "runs",
        "spend",
        "warnings",
        "activity",
        "generated_at",
    ]
    assert body["project"]["id"] == str(a["project"])


@pytest.mark.asyncio
async def test_plans_and_runs_are_scoped_to_this_project(
    configured_app, migrations_pg_dsn: str
) -> None:
    a = (await _seed(migrations_pg_dsn))["a"]
    body = await _overview(configured_app, a["project"], await _token(a["user"], a["tenant"]))
    assert [(p["title"], p["tasks_done"], p["tasks_total"]) for p in body["plans"]] == [
        ("Plan activo", 1, 2)
    ]
    assert body["runs"]["running"] == 1
    assert body["runs"]["last_failure"]["task_title"] == "Tarea done"
    assert {r["task_title"] for r in body["activity"]} == {"Tarea in_progress", "Tarea done"}
    assert "Plan ajeno" not in json.dumps(body)


@pytest.mark.asyncio
async def test_mcp_and_repo_warnings_are_computed(configured_app, migrations_pg_dsn: str) -> None:
    a = (await _seed(migrations_pg_dsn))["a"]
    body = await _overview(configured_app, a["project"], await _token(a["user"], a["tenant"]))
    warnings = _by_kind(body)
    # `atlassian` usa OAuth y no hay token en Vault; `docs` no es OAuth.
    assert warnings["mcp_not_connected"]["names"] == ["atlassian"]
    # `docs` tiene la tool `docs.search` importada; `atlassian` ninguna.
    assert warnings["mcp_no_tools"]["names"] == ["atlassian"]
    assert warnings["mcp_egress_blocked"]["count"] == 2
    assert warnings["repo_not_synced"]["count"] == 1
    for w in body["warnings"]:
        assert w["url_path"].startswith(f"/admin/projects/{a['project']}")


@pytest.mark.asyncio
async def test_repo_warning_follows_last_git_sync(configured_app, migrations_pg_dsn: str) -> None:
    a = (await _seed(migrations_pg_dsn))["a"]
    token = await _token(a["user"], a["tenant"])

    async def set_sync(payload: dict | None) -> None:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            await conn.execute(
                "UPDATE projects SET repository_config = $2::jsonb WHERE id = $1",
                a["project"],
                json.dumps({"last_git_sync": payload} if payload else {}),
            )
        finally:
            await conn.close()

    await set_sync({"at": "2026-09-29T10:00:00+00:00", "status": "ok"})
    assert "repo_not_synced" not in _by_kind(await _overview(configured_app, a["project"], token))
    await _bust_overview_cache()
    await set_sync({"at": "2026-09-29T10:00:00+00:00", "status": "error", "error": "boom"})
    assert "repo_not_synced" in _by_kind(await _overview(configured_app, a["project"], token))


@pytest.mark.asyncio
async def test_project_without_git_or_mcp_has_no_such_warnings(
    configured_app, migrations_pg_dsn: str
) -> None:
    a = (await _seed(migrations_pg_dsn))["a"]
    body = await _overview(configured_app, a["other"], await _token(a["user"], a["tenant"]))
    assert body["warnings"] == []


@pytest.mark.asyncio
async def test_budget_pause_and_spend_are_admin_only(
    configured_app, migrations_pg_dsn: str
) -> None:
    a = (await _seed(migrations_pg_dsn))["a"]
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "UPDATE projects SET paused_by_budget = true WHERE id = $1", a["project"]
        )
    finally:
        await conn.close()
    admin = await _overview(configured_app, a["project"], await _token(a["admin"], a["tenant"]))
    member = await _overview(configured_app, a["project"], await _token(a["user"], a["tenant"]))
    assert "budget_paused_project" in _by_kind(admin)
    assert admin["spend"].get("reason") != "forbidden"
    assert "budget_paused_project" not in _by_kind(member)
    assert member["spend"] == {"available": False, "reason": "forbidden"}


@pytest.mark.asyncio
async def test_another_tenants_project_is_404(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    a, b = seeded["a"], seeded["b"]
    await _overview(configured_app, b["project"], await _token(a["admin"], a["tenant"]), expect=404)
    # Y su Resumen propio no filtra nada del otro.
    body = await _overview(configured_app, a["project"], await _token(a["admin"], a["tenant"]))
    text = json.dumps(body)
    assert "ov-b" not in text
    assert str(b["project"]) not in text


@pytest.mark.asyncio
async def test_requires_authentication(configured_app, migrations_pg_dsn: str) -> None:
    a = (await _seed(migrations_pg_dsn))["a"]
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/projects/{a['project']}/overview")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# task_ui_12 — el aviso `role_without_agent` y las pestañas Equipo y Costes.
# ---------------------------------------------------------------------------
_SPEC_QA = {"tasks": [{"title": "t1", "role": "backend_dev"}, {"title": "t2", "role": "qa"}]}


async def _set_plan_spec(
    dsn: str, project: UUID, spec: dict, *, status: str = "in_progress"
) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "UPDATE plans SET specification = $2::jsonb, status = $3 WHERE project_id = $1",
            project,
            json.dumps(spec),
            status,
        )
    finally:
        await conn.close()


async def _add_team(
    dsn: str, seeded: dict[str, UUID], roles: list[tuple[str, str]], *, project_key: str = "project"
) -> dict[str, UUID]:
    """Un equipo asignado al proyecto con un agente por (rol, agent_type)."""
    conn = await asyncpg.connect(dsn)
    team = uuid4()
    ids: dict[str, UUID] = {}
    try:
        await conn.execute(
            "INSERT INTO teams (id, tenant_id, name) VALUES ($1, $2, 'Equipo')",
            team,
            seeded["tenant"],
        )
        for role, kind in roles:
            agent = uuid4()
            ids[role] = agent
            await conn.execute(
                "INSERT INTO agents (id, tenant_id, name, agent_type, role, system_prompt, scope)"
                " VALUES ($1, $2, $3, $4, $5, 'p', 'global_tenant_template')",
                agent,
                seeded["tenant"],
                f"Agente {role}",
                kind,
                role,
            )
            await conn.execute(
                "INSERT INTO team_members (team_id, agent_id, tenant_id) VALUES ($1, $2, $3)",
                team,
                agent,
                seeded["tenant"],
            )
        await conn.execute(
            "UPDATE projects SET team_id = $2 WHERE id = $1", seeded[project_key], team
        )
    finally:
        await conn.close()
    return ids


@pytest.mark.asyncio
async def test_role_without_agent_warning_follows_the_team(
    configured_app, migrations_pg_dsn: str
) -> None:
    a = (await _seed(migrations_pg_dsn))["a"]
    token = await _token(a["user"], a["tenant"])
    await _set_plan_spec(migrations_pg_dsn, a["project"], _SPEC_QA)
    # Sin equipo: los dos roles pedidos son hueco.
    warning = _by_kind(await _overview(configured_app, a["project"], token))["role_without_agent"]
    assert warning["names"] == ["backend_dev", "qa"]
    assert warning["count"] == 2
    assert warning["url_path"] == f"/admin/projects/{a['project']}/team"
    # Equipo con backend_dev pero sin qa: sólo qa.
    await _bust_overview_cache()
    await _add_team(migrations_pg_dsn, a, [("backend_dev", "ai")])
    warning = _by_kind(await _overview(configured_app, a["project"], token))["role_without_agent"]
    assert warning["names"] == ["qa"]


@pytest.mark.asyncio
async def test_role_without_agent_warning_disappears_when_covered(
    configured_app, migrations_pg_dsn: str
) -> None:
    a = (await _seed(migrations_pg_dsn))["a"]
    token = await _token(a["user"], a["tenant"])
    await _set_plan_spec(migrations_pg_dsn, a["project"], _SPEC_QA)
    await _add_team(migrations_pg_dsn, a, [("backend_dev", "ai"), ("qa", "ai")])
    assert "role_without_agent" not in _by_kind(
        await _overview(configured_app, a["project"], token)
    )


@pytest.mark.asyncio
async def test_role_without_agent_ignores_inactive_plans_and_other_projects(
    configured_app, migrations_pg_dsn: str
) -> None:
    a = (await _seed(migrations_pg_dsn))["a"]
    token = await _token(a["user"], a["tenant"])
    # Plan completado que pide qa: no cuenta. El plan del otro proyecto pide qa: tampoco.
    await _set_plan_spec(migrations_pg_dsn, a["project"], _SPEC_QA, status="completed")
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "UPDATE plans SET specification = $2::jsonb WHERE project_id = $1",
            a["other"],
            json.dumps(_SPEC_QA),
        )
    finally:
        await conn.close()
    assert "role_without_agent" not in _by_kind(
        await _overview(configured_app, a["project"], token)
    )


async def _api_get(app, path: str, token: str, *, expect: int = 200):  # type: ignore[no-untyped-def]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(path, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == expect, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_team_tab_lists_members_humans_and_the_same_role_gaps(
    configured_app, migrations_pg_dsn: str
) -> None:
    a = (await _seed(migrations_pg_dsn))["a"]
    token = await _token(a["user"], a["tenant"])
    await _set_plan_spec(migrations_pg_dsn, a["project"], _SPEC_QA)
    ids = await _add_team(migrations_pg_dsn, a, [("backend_dev", "ai"), ("reviewer", "human")])
    body = await _api_get(configured_app, f"/projects/{a['project']}/team", token)
    assert body["team"]["name"] == "Equipo"
    members = {m["role"]: m for m in body["members"]}
    assert members["backend_dev"]["agent_type"] == "ai"
    assert members["reviewer"]["agent_type"] == "human"
    assert members["backend_dev"]["url_path"] == f"/admin/agents/{ids['backend_dev']}"
    assert [g["role"] for g in body["role_gaps"]] == ["qa"]
    # El hueco y el aviso del Resumen salen del mismo cálculo.
    overview = await _overview(configured_app, a["project"], token)
    assert _by_kind(overview)["role_without_agent"]["names"] == ["qa"]


@pytest.mark.asyncio
async def test_team_tab_offers_tenant_agents_that_fill_the_gap(
    configured_app, migrations_pg_dsn: str
) -> None:
    a = (await _seed(migrations_pg_dsn))["a"]
    token = await _token(a["user"], a["tenant"])
    await _set_plan_spec(migrations_pg_dsn, a["project"], _SPEC_QA)
    conn = await asyncpg.connect(migrations_pg_dsn)
    qa_agent = uuid4()
    try:
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, name, role, system_prompt, scope)"
            " VALUES ($1, $2, 'Quique QA', 'qa', 'p', 'global_tenant_template')",
            qa_agent,
            a["tenant"],
        )
    finally:
        await conn.close()
    body = await _api_get(configured_app, f"/projects/{a['project']}/team", token)
    gap = next(g for g in body["role_gaps"] if g["role"] == "qa")
    assert gap["candidates"] == [
        {"agent_id": str(qa_agent), "name": "Quique QA", "url_path": f"/admin/agents/{qa_agent}"}
    ]


@pytest.mark.asyncio
async def test_team_tab_cross_tenant_is_404_and_does_not_leak(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    a, b = seeded["a"], seeded["b"]
    await _add_team(migrations_pg_dsn, b, [("qa", "ai")])
    await _api_get(
        configured_app,
        f"/projects/{b['project']}/team",
        await _token(a["admin"], a["tenant"]),
        expect=404,
    )
    body = await _api_get(
        configured_app,
        f"/projects/{a['project']}/team",
        await _token(a["admin"], a["tenant"]),
    )
    assert body["team"] is None
    assert body["members"] == []
    assert "Agente qa" not in json.dumps(body)


async def _seed_costs(dsn: str, a: dict[str, UUID]) -> None:
    """Dos runs con coste en el proyecto y uno en el otro proyecto del mismo tenant."""
    conn = await asyncpg.connect(dsn)
    try:
        agent = uuid4()
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, name, role, system_prompt, scope)"
            " VALUES ($1, $2, 'Ana', 'backend_dev', 'p', 'global_tenant_template')",
            agent,
            a["tenant"],
        )
        rows = await conn.fetch("SELECT id FROM tasks WHERE tenant_id = $1", a["tenant"])
        for i, row in enumerate(rows):
            await conn.execute(
                "UPDATE executions SET agent_id = $2, total_cost_usd = $3, total_tokens = 100,"
                " last_model = $4 WHERE task_id = $1",
                row["id"],
                agent,
                1 + i,
                "m-a" if i == 0 else "m-b",
            )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_costs_tab_breaks_down_spend_of_this_project_only(
    configured_app, migrations_pg_dsn: str
) -> None:
    a = (await _seed(migrations_pg_dsn))["a"]
    await _seed_costs(migrations_pg_dsn, a)
    body = await _api_get(
        configured_app,
        f"/projects/{a['project']}/costs",
        await _token(a["admin"], a["tenant"]),
    )
    assert body["run_count"] == 2
    assert float(body["total_cost_usd"]) == pytest.approx(
        sum(float(r["cost_usd"]) for r in body["by_plan"])
    )
    assert [p["title"] for p in body["by_plan"]] == ["Plan activo"]
    assert [x["name"] for x in body["by_agent"]] == ["Ana"]
    assert {m["model"] for m in body["by_model"]} <= {"m-a", "m-b"}
    assert body["budget"]["available"] is False
    assert body["paused_by_budget"] is False
    assert "Plan ajeno" not in json.dumps(body)


@pytest.mark.asyncio
async def test_costs_tab_is_tenant_admin_only(configured_app, migrations_pg_dsn: str) -> None:
    a = (await _seed(migrations_pg_dsn))["a"]
    await _api_get(
        configured_app,
        f"/projects/{a['project']}/costs",
        await _token(a["user"], a["tenant"]),
        expect=403,
    )


@pytest.mark.asyncio
async def test_costs_tab_cross_tenant_is_404(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    a, b = seeded["a"], seeded["b"]
    await _api_get(
        configured_app,
        f"/projects/{b['project']}/costs",
        await _token(a["admin"], a["tenant"]),
        expect=404,
    )


@pytest.mark.asyncio
async def test_runs_can_be_filtered_by_project(configured_app, migrations_pg_dsn: str) -> None:
    a = (await _seed(migrations_pg_dsn))["a"]
    token = await _token(a["user"], a["tenant"])
    mine = await _api_get(configured_app, f"/runs?project_id={a['project']}", token)
    other = await _api_get(configured_app, f"/runs?project_id={a['other']}", token)
    everything = await _api_get(configured_app, "/runs", token)
    assert len(mine) == 2 and len(other) == 1 and len(everything) == 3
    assert {r["plan_title"] for r in mine} == {"Plan activo"}


# ---------------------------------------------------------------------------
# Revisión adversarial (intento 1): sonda OAuth de tres estados, ventana y caché, enlaces.
# ---------------------------------------------------------------------------
async def _bust_overview_cache() -> None:
    """La caché de 30 s del Resumen: los tests que mutan la BD entre dos lecturas la vacían."""
    from api_server.auth.deps import get_redis

    redis = get_redis()
    async for key in redis.scan_iter(match="dashboard:project:*"):
        await redis.delete(key)


class _SealedVault:
    def resolve(self, auth_ref: str) -> dict[str, str]:
        from shared_mcp import MCPAuthError

        raise MCPAuthError("Vault read failed") from RuntimeError("Vault is sealed")

    def write(self, auth_ref: str, values: dict[str, str]) -> None: ...


@pytest.mark.asyncio
async def test_vault_down_is_unknown_not_unconnected(
    configured_app, migrations_pg_dsn: str
) -> None:
    from api_server.mcp import oauth_probe
    from api_server.routers.mcp import get_vault_resolver

    oauth_probe.clear_oauth_probe_cache()
    a = (await _seed(migrations_pg_dsn))["a"]
    configured_app.dependency_overrides[get_vault_resolver] = _SealedVault
    try:
        body = await _overview(configured_app, a["project"], await _token(a["user"], a["tenant"]))
    finally:
        configured_app.dependency_overrides.pop(get_vault_resolver, None)
    warnings = _by_kind(body)
    assert "mcp_not_connected" not in warnings
    assert warnings["vault_unavailable"]["names"] == ["atlassian"]
    assert (
        warnings["vault_unavailable"]["url_path"] == f"/admin/projects/{a['project']}/capabilities"
    )


@pytest.mark.asyncio
async def test_corrupt_vault_entry_does_not_500(configured_app, migrations_pg_dsn: str) -> None:
    from api_server.mcp import oauth_probe
    from api_server.routers.mcp import get_vault_resolver
    from shared_mcp import StaticVaultResolver, oauth_vault_path

    oauth_probe.clear_oauth_probe_cache()
    a = (await _seed(migrations_pg_dsn))["a"]
    path = oauth_vault_path(
        tenant_id=str(a["tenant"]), project_id=str(a["project"]), server_name="atlassian"
    )
    resolver = StaticVaultResolver({path: {"oauth_token": "{corrupto"}})
    configured_app.dependency_overrides[get_vault_resolver] = lambda: resolver
    try:
        body = await _overview(configured_app, a["project"], await _token(a["user"], a["tenant"]))
    finally:
        configured_app.dependency_overrides.pop(get_vault_resolver, None)
    assert "vault_unavailable" in _by_kind(body)


@pytest.mark.asyncio
async def test_overview_is_cached_per_project_and_trimmed_after(
    configured_app, migrations_pg_dsn: str
) -> None:
    from api_server.auth.deps import get_redis

    a = (await _seed(migrations_pg_dsn))["a"]
    admin_token = await _token(a["admin"], a["tenant"])
    first = await _overview(configured_app, a["project"], admin_token)
    ttl = await get_redis().ttl(f"dashboard:project:{a['tenant']}:{a['project']}")
    assert 0 < ttl <= 30
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute("UPDATE projects SET name = 'Renombrado' WHERE id = $1", a["project"])
    finally:
        await conn.close()
    again = await _overview(configured_app, a["project"], admin_token)
    assert again["project"]["name"] == first["project"]["name"], "sirve la caché de 30 s"
    # El recorte por rol es posterior a la caché: el miembro no hereda el gasto del admin.
    member = await _overview(configured_app, a["project"], await _token(a["user"], a["tenant"]))
    assert member["spend"] == {"available": False, "reason": "forbidden"}


def test_executions_queries_have_a_partition_pruning_window() -> None:
    from datetime import timedelta

    from api_server.dashboard import project

    assert timedelta(days=30) <= project.EXECUTIONS_WINDOW <= timedelta(days=180)


@pytest.mark.asyncio
async def test_old_executions_fall_outside_the_window(
    configured_app, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import timedelta

    from api_server.dashboard import project

    # Ventana de 1 h: con filas de hace 2 días (siempre hay partición) basta para fijar
    # que el corte se aplica, sin depender de qué particiones antiguas existan.
    monkeypatch.setattr(project, "EXECUTIONS_WINDOW", timedelta(hours=1))
    a = (await _seed(migrations_pg_dsn))["a"]
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "UPDATE executions SET created_at = now() - interval '2 days'"
            " WHERE tenant_id = $1 AND status = 'failed'",
            a["tenant"],
        )
    finally:
        await conn.close()
    body = await _overview(configured_app, a["project"], await _token(a["user"], a["tenant"]))
    assert body["runs"]["last_failure"] is None
    assert all(r["status"] != "failed" for r in body["activity"])


@pytest.mark.asyncio
async def test_blocks_link_to_the_project_tabs(configured_app, migrations_pg_dsn: str) -> None:
    a = (await _seed(migrations_pg_dsn))["a"]
    body = await _overview(configured_app, a["project"], await _token(a["user"], a["tenant"]))
    base = f"/admin/projects/{a['project']}"
    assert body["runs"]["url_path"] == f"{base}/runs"
    warnings = _by_kind(body)
    for kind in ("mcp_not_connected", "mcp_no_tools", "mcp_egress_blocked"):
        assert warnings[kind]["url_path"] == f"{base}/capabilities"


@pytest.mark.asyncio
async def test_team_tab_lists_builtin_members_but_not_other_tenants_agents(
    configured_app, migrations_pg_dsn: str
) -> None:
    """`/teams/{id}` enseña los agentes built-in (scope `global_builtin`, bajo el tenant de
    plataforma) que son miembros; la pestaña Equipo debe hacer lo mismo, sin abrir agentes
    de otro tenant."""
    seeded = await _seed(migrations_pg_dsn)
    a, b = seeded["a"], seeded["b"]
    token = await _token(a["user"], a["tenant"])
    await _add_team(migrations_pg_dsn, a, [("backend_dev", "ai")])
    builtin, foreign = uuid4(), uuid4()
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        team = await conn.fetchval("SELECT team_id FROM projects WHERE id = $1", a["project"])
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, name, agent_type, role, system_prompt, scope)"
            " VALUES ($1, $2, 'Built-in QA', 'ai', 'qa', 'p', 'global_builtin')",
            builtin,
            _PLATFORM_TENANT_ID,
        )
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, name, agent_type, role, system_prompt, scope)"
            " VALUES ($1, $2, 'Ajeno', 'ai', 'qa', 'p', 'global_tenant_template')",
            foreign,
            b["tenant"],
        )
        for agent, tenant in ((builtin, a["tenant"]), (foreign, a["tenant"])):
            await conn.execute(
                "INSERT INTO team_members (team_id, agent_id, tenant_id) VALUES ($1, $2, $3)",
                team,
                agent,
                tenant,
            )
    finally:
        await conn.close()
    body = await _api_get(configured_app, f"/projects/{a['project']}/team", token)
    names = {m["name"] for m in body["members"]}
    assert "Built-in QA" in names
    assert "Ajeno" not in names


# ---------------------------------------------------------------------------
# Revisión adversarial (intento 2): un estado OAuth transitorio no se cachea y «Conectar»
# invalida el Resumen.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_vault_unavailable_payload_is_not_cached_in_redis(
    configured_app, migrations_pg_dsn: str
) -> None:
    from api_server.auth.deps import get_redis
    from api_server.mcp import oauth_probe
    from api_server.routers.mcp import get_vault_resolver

    oauth_probe.clear_oauth_probe_cache()
    a = (await _seed(migrations_pg_dsn))["a"]
    await _bust_overview_cache()
    configured_app.dependency_overrides[get_vault_resolver] = _SealedVault
    try:
        body = await _overview(configured_app, a["project"], await _token(a["user"], a["tenant"]))
    finally:
        configured_app.dependency_overrides.pop(get_vault_resolver, None)
    assert "vault_unavailable" in _by_kind(body)
    key = f"dashboard:project:{a['tenant']}:{a['project']}"
    assert await get_redis().get(key) is None, "un Vault sellado es transitorio: no dura 30 s"


@pytest.mark.asyncio
async def test_invalidate_project_oauth_state_drops_the_overview_and_the_probe(
    configured_app, migrations_pg_dsn: str
) -> None:
    from api_server.auth.deps import get_redis
    from api_server.mcp import oauth_probe
    from api_server.routers.mcp import get_vault_resolver
    from api_server.routers.project_overview import invalidate_project_oauth_state
    from shared_mcp import StaticVaultResolver, oauth_vault_path

    oauth_probe.clear_oauth_probe_cache()
    seeded = await _seed(migrations_pg_dsn)
    a, b = seeded["a"], seeded["b"]
    await _bust_overview_cache()
    data: dict[str, dict[str, str]] = {}
    configured_app.dependency_overrides[get_vault_resolver] = lambda: StaticVaultResolver(data)
    try:
        token = await _token(a["user"], a["tenant"])
        before = await _overview(configured_app, a["project"], token)
        assert "mcp_not_connected" in _by_kind(before)
        key_a = f"dashboard:project:{a['tenant']}:{a['project']}"
        assert await get_redis().get(key_a) is not None
        # «Conectar» termina: aparece el token y se invalida ESE proyecto.
        path = oauth_vault_path(
            tenant_id=str(a["tenant"]), project_id=str(a["project"]), server_name="atlassian"
        )
        data[path] = {"oauth_token": json.dumps({"access_token": "x", "token_type": "Bearer"})}
        # Una clave de OTRO proyecto/tenant no se toca.
        other_key = f"dashboard:project:{b['tenant']}:{b['project']}"
        await get_redis().setex(other_key, 30, "{}")
        await invalidate_project_oauth_state(a["tenant"], a["project"], "atlassian")
        assert await get_redis().get(key_a) is None
        assert await get_redis().get(other_key) is not None
        after = await _overview(configured_app, a["project"], token)
        assert "mcp_not_connected" not in _by_kind(after)
    finally:
        configured_app.dependency_overrides.pop(get_vault_resolver, None)
