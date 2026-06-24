"""Integration tests for /projects endpoints (task_01_07).

Covers CRUD with the dense field set, the budget-invariants validator,
cross-tenant isolation, and team_id 404-vs-FK-error translation.
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
    tenant_a = uuid4()
    tenant_b = uuid4()
    user_a = uuid4()
    user_b = uuid4()
    team_a = uuid4()
    team_b = uuid4()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE team_members, teams, projects, agents,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES"
            " ($1, $2, $3), ($4, $5, $6), ($7, $8, $9)",
            tenant_a,
            "Tenant A",
            "tenant-a",
            tenant_b,
            "Tenant B",
            "tenant-b",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform",
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
        # One team per tenant -- A's team is referenceable from A's
        # projects; B's team must stay invisible to A.
        await conn.execute(
            "INSERT INTO teams (id, tenant_id, name) VALUES ($1, $2, $3), ($4, $5, $6)",
            team_a,
            tenant_a,
            "Team A",
            team_b,
            tenant_b,
            "Team B",
        )
    finally:
        await conn.close()

    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "user_a": user_a,
        "user_b": user_b,
        "team_a": team_a,
        "team_b": team_b,
    }


# ---------------------------------------------------------------------------
# Fixtures (same pattern as the other test files)
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


def _minimal_payload(**overrides) -> dict:
    base = {"name": "Demo API"}
    base.update(overrides)
    return base


# ===========================================================================
# Tests
# ===========================================================================
@pytest.mark.asyncio
async def test_projects_unauthenticated_is_401(configured_app) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get("/projects")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_project_crud_roundtrip(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        create = await client.post(
            "/projects",
            json=_minimal_payload(
                description="REST API service",
                team_id=str(seeded["team_a"]),
                worker_config={"min_workers": 1, "max_workers": 4},
                repository_config={"url": "git@github.com:demo/api.git"},
            ),
            headers=headers,
        )
        assert create.status_code == 201, create.text
        body = create.json()
        assert body["name"] == "Demo API"
        assert body["status"] == "active"  # default
        assert body["team_id"] == str(seeded["team_a"])
        assert body["paused_by_budget"] is False
        project_id = body["id"]

        listed = await client.get("/projects", headers=headers)
        assert listed.status_code == 200
        assert {p["id"] for p in listed.json()} == {project_id}

        upd = await client.put(
            f"/projects/{project_id}",
            json={"status": "paused", "description": "paused for maintenance"},
            headers=headers,
        )
        assert upd.status_code == 200
        assert upd.json()["status"] == "paused"

        # Filter by status
        paused = await client.get("/projects?status=paused", headers=headers)
        assert {p["id"] for p in paused.json()} == {project_id}
        active = await client.get("/projects?status=active", headers=headers)
        assert active.json() == []

        dele = await client.delete(f"/projects/{project_id}", headers=headers)
        assert dele.status_code == 204
        gone = await client.get(f"/projects/{project_id}", headers=headers)
        assert gone.status_code == 404


@pytest.mark.asyncio
async def test_project_model_config_roundtrip(configured_app, migrations_pg_dsn: str) -> None:
    """Ola A-UI: PUT /projects/{id} fija el modelo por defecto del proyecto y GET
    lo devuelve (clave JSON `model_config`, alias del `llm_config` Python)."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}
    cfg = {"provider": "ollama", "model": "qwen3-coder:480b", "temperature": 0.2}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        project_id = (
            await client.post(
                "/projects", json=_minimal_payload(team_id=str(seeded["team_a"])), headers=headers
            )
        ).json()["id"]
        # Recién creado: sin modelo (hereda del default de plataforma).
        assert (await client.get(f"/projects/{project_id}", headers=headers)).json()[
            "model_config"
        ] == {}

        upd = await client.put(
            f"/projects/{project_id}", json={"model_config": cfg}, headers=headers
        )
        assert upd.status_code == 200, upd.text
        assert upd.json()["model_config"] == cfg

        got = await client.get(f"/projects/{project_id}", headers=headers)
        assert got.json()["model_config"]["model"] == "qwen3-coder:480b"


@pytest.mark.asyncio
async def test_create_project_fork_team_opt_in(configured_app, migrations_pg_dsn: str) -> None:
    """Ola C / ADR 0068: `fork_team=True` al crear forkea el equipo referenciado a
    una copia editable del tenant y repunta `project.team_id` al fork; el default
    (`False`) referencia el equipo tal cual (linked)."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}
    team_a = str(seeded["team_a"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        # Default: referencia el equipo tal cual.
        ref = await client.post(
            "/projects",
            json=_minimal_payload(name="Linked", team_id=team_a),
            headers=headers,
        )
        assert ref.status_code == 201, ref.text
        assert ref.json()["team_id"] == team_a

        # Opt-in: forkea el equipo y repunta.
        forked = await client.post(
            "/projects",
            json=_minimal_payload(name="Forked", team_id=team_a, fork_team=True),
            headers=headers,
        )
        assert forked.status_code == 201, forked.text
        new_team_id = forked.json()["team_id"]
        assert new_team_id is not None and new_team_id != team_a

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        row = await conn.fetchrow(
            "SELECT is_builtin, forked_from_team_id, tenant_id FROM teams WHERE id = $1",
            UUID(new_team_id),
        )
        assert row["is_builtin"] is False
        assert row["forked_from_team_id"] == seeded["team_a"]
        assert row["tenant_id"] == seeded["tenant_a"]
        # El equipo original no se muta.
        src = await conn.fetchrow(
            "SELECT is_builtin, forked_from_team_id FROM teams WHERE id = $1", seeded["team_a"]
        )
        assert src["forked_from_team_id"] is None
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_project_cannot_reference_other_tenants_team(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        # tenant A tries to attach tenant B's team -> 404 (RLS hides it).
        resp = await client.post(
            "/projects",
            json=_minimal_payload(team_id=str(seeded["team_b"])),
            headers=headers,
        )
    assert resp.status_code == 404
    assert "team not found" in resp.text.lower()


@pytest.mark.asyncio
async def test_budget_custom_requires_period_fields(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/projects",
            json=_minimal_payload(
                budget_amount="500.00",
                budget_currency="EUR",
                budget_period="custom",
                # missing start_day + length_days
            ),
            headers=headers,
        )
    assert resp.status_code == 422
    assert "budget_period" in resp.text


@pytest.mark.asyncio
async def test_budget_non_custom_rejects_period_fields(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/projects",
            json=_minimal_payload(
                budget_amount="500.00",
                budget_currency="EUR",
                budget_period="monthly",
                budget_period_start_day=15,  # not allowed for 'monthly'
            ),
            headers=headers,
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_budget_amount_requires_currency(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/projects",
            json=_minimal_payload(budget_amount="100.00"),
            headers=headers,
        )
    assert resp.status_code == 422
    assert "currency" in resp.text.lower()


@pytest.mark.asyncio
async def test_budget_amount_with_full_custom_period(
    configured_app, migrations_pg_dsn: str
) -> None:
    """The happy-path for custom budgets -- exercises the column types
    (Numeric, char(3), enum-like text) and confirms the model_validator
    accepts the full bundle."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/projects",
            json=_minimal_payload(
                budget_amount="250.50",
                budget_currency="USD",
                budget_period="custom",
                budget_period_start_day=1,
                budget_period_length_days=14,
            ),
            headers=headers,
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["budget_currency"] == "USD"
    assert body["budget_period"] == "custom"
    assert body["budget_period_length_days"] == 14


@pytest.mark.asyncio
async def test_project_isolation_across_tenants(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token_a = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    token_b = await _mint_token(seeded["user_b"], seeded["tenant_b"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        created = await client.post(
            "/projects",
            json=_minimal_payload(name="A's project"),
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert created.status_code == 201
        project_id = created.json()["id"]

        listed_b = await client.get("/projects", headers={"Authorization": f"Bearer {token_b}"})
        assert project_id not in {p["id"] for p in listed_b.json()}

        fetch_b = await client.get(
            f"/projects/{project_id}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert fetch_b.status_code == 404


# ---------------------------------------------------------------------------
# task_06_14_15 — input-validation cleanups (api-routers-validation-4/6)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_list_projects_rejects_invalid_status_filter(
    configured_app, migrations_pg_dsn: str
) -> None:
    """`?status=` is now typed against ProjectStatus -> 422 on garbage
    (api-routers-validation-4), instead of silently matching nothing."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        bad = await client.get("/projects?status=not-a-real-status", headers=headers)
        assert bad.status_code == 422, bad.text
        # A valid enum value still works.
        good = await client.get("/projects?status=active", headers=headers)
        assert good.status_code == 200


@pytest.mark.asyncio
async def test_create_project_rejects_oversized_json_config(
    configured_app, migrations_pg_dsn: str
) -> None:
    """A free-form JSON config blob over the size cap -> 422
    (api-routers-validation-6)."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    huge = {"blob": "x" * 70_000}  # > 64 KiB once serialized

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/projects",
            json=_minimal_payload(worker_config=huge),
            headers=headers,
        )
        assert resp.status_code == 422, resp.text
        assert "worker_config" in resp.text

        # A reasonably-sized config is accepted.
        ok = await client.post(
            "/projects",
            json=_minimal_payload(name="small cfg", worker_config={"max_workers": 4}),
            headers=headers,
        )
        assert ok.status_code == 201, ok.text
