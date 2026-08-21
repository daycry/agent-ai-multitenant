"""Cross-role RBAC tests for tenant-scoped resource endpoints.

Plan 06.8 task_06_8_03 — sanity check that the gates introduced in
auth/deps.py are wired correctly on the most critical mutation endpoints
(the matrix `docs/04-reference/rbac.md` is the full contract).

For each endpoint, exercise with four callers:

  - `tenant_user`    — active membership, role=tenant_user
  - `tenant_admin`   — active membership, role=tenant_admin
  - `system_admin`   — `users.is_system_admin = true`, no membership
  - `stranger`       — registered user, NO membership in tenant

The matrix expectation per caller is hard-coded in `_EXPECTATIONS` —
when adding a new gated endpoint, extend it and the matrix together.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _fresh_global_state_shield():
    """Blindaje de orden (tanda 2, 2026-07-19): este fichero fallaba SOLO en
    la suite completa (pasa aislado) — estado global heredado del fichero
    anterior (engines/caches vivos). Reset al ENTRAR en cada test: barato,
    idempotente y sin efecto cuando el estado ya está limpio."""
    from api_server.auth.deps import reset_redis_cache
    from api_server.config import get_settings
    from api_server.db.session import reset_engine_cache

    get_settings.cache_clear()
    reset_engine_cache()
    reset_redis_cache()
    yield


# ---------------------------------------------------------------------------
# Seed: a tenant, three users in three roles, plus a stranger user.
# ---------------------------------------------------------------------------
async def _seed_db(dsn: str) -> dict[str, UUID]:
    tenant = uuid4()
    admin_user = uuid4()
    plain_user = uuid4()
    stranger = uuid4()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant,
            "Acme",
            "acme",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES"
            " ($1, 'admin@acme.test', 'argon2-placeholder'),"
            " ($2, 'user@acme.test',  'argon2-placeholder'),"
            " ($3, 'stranger@acme.test', 'argon2-placeholder')",
            admin_user,
            plain_user,
            stranger,
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES"
            " ($1, $2, $3, 'tenant_admin'),"
            " ($4, $5, $6, 'tenant_user')",
            uuid4(),
            tenant,
            admin_user,
            uuid4(),
            tenant,
            plain_user,
        )
    finally:
        await conn.close()

    return {
        "tenant": tenant,
        "admin_user": admin_user,
        "plain_user": plain_user,
        "stranger": stranger,
    }


async def _seed_project_plan_task(dsn: str) -> dict[str, UUID]:
    """`_seed_db` + an ACTIVE project with one plan and one backlog task.

    Needed by the human_06_8_04 cases: unlike the admin-gated smokes (where
    a 403 arrives before the handler), asserting that a tenant_user CAN
    mutate requires the row to really exist so a 2xx is reachable.
    """
    seed = await _seed_db(dsn)
    project = uuid4()
    plan = uuid4()
    task = uuid4()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status)"
            " VALUES ($1, $2, 'Kanban', 'active')",
            project,
            seed["tenant"],
        )
        await conn.execute(
            "INSERT INTO plans (id, tenant_id, project_id, title, status)"
            " VALUES ($1, $2, $3, 'Plan del sprint', 'draft')",
            plan,
            seed["tenant"],
            project,
        )
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, plan_id, title, status)"
            " VALUES ($1, $2, $3, $4, 'Mover esto', 'backlog')",
            task,
            seed["tenant"],
            project,
            plan,
        )
    finally:
        await conn.close()

    seed["project"] = project
    seed["plan"] = plan
    seed["task"] = task
    return seed


async def _promote_to_system_admin(dsn: str, user_id: UUID) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("UPDATE users SET is_system_admin = true WHERE id = $1", user_id)
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Fixture
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


async def _mint(user_id: UUID, tenant_id: UUID | None, *, is_system_admin: bool = False) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(
        user_id=user_id,
        session_id=sid,
        tenant_id=tenant_id,
        is_system_admin=is_system_admin,
    )


# ---------------------------------------------------------------------------
# Endpoint matrix
# ---------------------------------------------------------------------------
# Each entry: (method, path, json-body or None, expected status when authorised)
#
# A pair of (caller, endpoint) is "authorised" when the matrix
# (rbac.md) says so. The expected_authorised codes are what the
# endpoint returns on a happy path (2xx). On rejection we always expect
# 403 — there are no 401s here (every caller is logged in).
#
# Body payloads are intentionally minimal — for the RBAC test we don't
# care if the create succeeds with full fields; a 4xx from validation
# would also "leak" the gate (the gate runs first), so 403 vs 4xx tells
# us the gate is in place.
#
# `_ABSENT_PROJECT_ID` is deliberately a project that does NOT exist: the
# gate is a router-level dependency, so it runs BEFORE the handler ever
# looks the row up. That makes the 403-vs-404 distinction the whole point
# of the assertion — if the gate were downgraded to `require_tenant_member`
# (or removed), a tenant_user would sail past it and get the handler's 404.
_ABSENT_PROJECT_ID = "11111111-2222-3333-4444-555555555555"

_ADMIN_GATED: list[tuple[str, str, dict[str, Any]]] = [
    ("POST", "/projects", {"name": "p", "status": "draft"}),
    # human_06_8_01, last checklist line: "Llamar al PUT /projects/{id} con
    # curl + token de tenant_user → 403". DELETE is the same gate
    # (routers/projects.py: `require_tenant_admin` on both verbs) and the
    # same checklist line ("botón 'Editar' y 'Borrar' no aparecen").
    ("PUT", f"/projects/{_ABSENT_PROJECT_ID}", {"name": "renamed"}),
    ("DELETE", f"/projects/{_ABSENT_PROJECT_ID}", {}),
    (
        "POST",
        "/agents",
        {
            "name": "A",
            "role": "backend_dev",
            "scope": "global_tenant_template",
            "model_provider": "anthropic",
            "model_id": "claude-sonnet-4-6",
        },
    ),
    ("POST", "/teams", {"name": "T"}),
    ("POST", "/knowledge-bases", {"name": "kb", "is_public": False}),
    ("POST", "/skills", {"name": "s", "category": "general"}),
    ("POST", "/tools", {"name": "t", "implementation_type": "internal_function"}),
    ("PUT", "/tenant-settings/hourly-rate", {"hourly_rate": "75.00"}),
]

#: Endpoints con `require_system_admin`: ni `tenant_user` NI `tenant_admin`
#: pasan. Lista separada de `_ADMIN_GATED` a propósito, porque el positivo de
#: aquel (`test_admin_gated_allows_tenant_admin`) sería falso para estos.
#:
#: prod-15 `task_gov_rbac_matrix_08`: `platform_settings` y `ollama` los añadió
#: la auditoría 2026-06 (docsroadmap-5); `embeddings` lo encontró prod-15 al
#: escribir la guardia estática `tests/unit/test_rbac_matrix_drift.py`.
_SYSTEM_ADMIN_GATED: list[tuple[str, str, dict[str, Any]]] = [
    ("GET", "/admin/platform-settings", {}),
    ("GET", "/admin/platform-settings/_registry", {}),
    ("GET", "/admin/platform-settings/model-options", {}),
    ("PUT", "/admin/platform-settings/cortex.autonomy_enabled", {"value": "false"}),
    ("GET", "/admin/ollama/models", {}),
    ("POST", "/admin/ollama/models/pull", {"model": "llama3"}),
    ("DELETE", "/admin/ollama/models", {"model": "llama3"}),
    ("GET", "/admin/embeddings/available-models", {}),
]

_MEMBER_GATED: list[tuple[str, str]] = [
    ("GET", "/projects"),
    ("GET", "/agents"),
    ("GET", "/teams"),
    ("GET", "/knowledge-bases"),
    ("GET", "/memories"),
    ("GET", "/tenant-settings/_registry"),
]


# ---------------------------------------------------------------------------
# Admin-gated endpoints — tenant_user gets 403
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path", "body"), _ADMIN_GATED)
async def test_admin_gated_rejects_tenant_user(
    configured_app, migrations_pg_dsn: str, method: str, path: str, body: dict[str, Any]
) -> None:
    seed = await _seed_db(migrations_pg_dsn)
    token = await _mint(seed["plain_user"], seed["tenant"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.request(
            method, path, headers={"Authorization": f"Bearer {token}"}, json=body
        )
    assert resp.status_code == 403, f"{method} {path}: {resp.status_code} {resp.text}"


# ---------------------------------------------------------------------------
# Admin-gated endpoints — stranger (no membership) gets 403
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path", "body"), _ADMIN_GATED)
async def test_admin_gated_rejects_stranger(
    configured_app, migrations_pg_dsn: str, method: str, path: str, body: dict[str, Any]
) -> None:
    seed = await _seed_db(migrations_pg_dsn)
    token = await _mint(seed["stranger"], seed["tenant"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.request(
            method, path, headers={"Authorization": f"Bearer {token}"}, json=body
        )
    assert resp.status_code == 403, f"{method} {path}: {resp.status_code} {resp.text}"


# ---------------------------------------------------------------------------
# Member-gated GET endpoints — stranger gets 403
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path"), _MEMBER_GATED)
async def test_member_gated_rejects_stranger(
    configured_app, migrations_pg_dsn: str, method: str, path: str
) -> None:
    seed = await _seed_db(migrations_pg_dsn)
    token = await _mint(seed["stranger"], seed["tenant"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.request(method, path, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403, f"{method} {path}: {resp.status_code} {resp.text}"


# ---------------------------------------------------------------------------
# System-admin-gated endpoints — el tenant_admin TAMPOCO pasa
#
# Es lo que distingue estos endpoints de `_ADMIN_GATED`: la superficie de
# plataforma (`/admin/platform-settings`, `/admin/ollama`,
# `/admin/embeddings`) corre sobre el engine BYPASSRLS y muta el host o
# ajustes sin `tenant_id`. Si alguien retirase el `require_system_admin`, la
# respuesta dejaría de ser 403 (sería 200, 404 o 422) y estos tests lo dirían:
# no pueden pasar vacíos.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path", "body"), _SYSTEM_ADMIN_GATED)
async def test_system_admin_gated_rejects_tenant_admin(
    configured_app, migrations_pg_dsn: str, method: str, path: str, body: dict[str, Any]
) -> None:
    seed = await _seed_db(migrations_pg_dsn)
    token = await _mint(seed["admin_user"], seed["tenant"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.request(
            method, path, headers={"Authorization": f"Bearer {token}"}, json=body
        )
    assert resp.status_code == 403, f"{method} {path}: {resp.status_code} {resp.text}"


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path", "body"), _SYSTEM_ADMIN_GATED)
async def test_system_admin_gated_rejects_tenant_user(
    configured_app, migrations_pg_dsn: str, method: str, path: str, body: dict[str, Any]
) -> None:
    seed = await _seed_db(migrations_pg_dsn)
    token = await _mint(seed["plain_user"], seed["tenant"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.request(
            method, path, headers={"Authorization": f"Bearer {token}"}, json=body
        )
    assert resp.status_code == 403, f"{method} {path}: {resp.status_code} {resp.text}"


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path", "body"), _SYSTEM_ADMIN_GATED)
async def test_system_admin_gated_rejects_stranger(
    configured_app, migrations_pg_dsn: str, method: str, path: str, body: dict[str, Any]
) -> None:
    seed = await _seed_db(migrations_pg_dsn)
    token = await _mint(seed["stranger"], seed["tenant"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.request(
            method, path, headers={"Authorization": f"Bearer {token}"}, json=body
        )
    assert resp.status_code == 403, f"{method} {path}: {resp.status_code} {resp.text}"


# ---------------------------------------------------------------------------
# Member-gated GET endpoints — tenant_user passes
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path"), _MEMBER_GATED)
async def test_member_gated_allows_tenant_user(
    configured_app, migrations_pg_dsn: str, method: str, path: str
) -> None:
    seed = await _seed_db(migrations_pg_dsn)
    token = await _mint(seed["plain_user"], seed["tenant"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.request(method, path, headers={"Authorization": f"Bearer {token}"})
    # Should NOT be 403 — anything in 200 / 201 / 204 / 404 acceptable for a
    # smoke that only checks the gate (the body might be empty / missing).
    assert resp.status_code != 403, f"{method} {path}: {resp.status_code} {resp.text}"
    assert resp.status_code < 500, f"{method} {path}: {resp.status_code} {resp.text}"


# ---------------------------------------------------------------------------
# Admin-gated endpoints — system_admin always passes (no membership needed)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_admin_gated_allows_system_admin(configured_app, migrations_pg_dsn: str) -> None:
    """Smoke that system_admin bypasses the tenant_admin gate.

    We test one endpoint (POST /projects) — it's enough to validate
    the bypass path; the gates are uniform across the matrix.
    """
    seed = await _seed_db(migrations_pg_dsn)
    await _promote_to_system_admin(migrations_pg_dsn, seed["stranger"])
    token = await _mint(seed["stranger"], seed["tenant"], is_system_admin=True)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.post(
            "/projects",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "byadmin", "status": "draft"},
        )
    # 403 = gate refused; any other status means the gate let us through
    # (the body might still fail validation — that's fine, the gate ran
    # first).
    assert resp.status_code != 403, f"{resp.status_code} {resp.text}"


# ---------------------------------------------------------------------------
# Admin-gated endpoints — tenant_admin passes
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_admin_gated_allows_tenant_admin(configured_app, migrations_pg_dsn: str) -> None:
    """Smoke that tenant_admin passes the gate (with a real create).

    Picking POST /projects — same reasoning as above.
    """
    seed = await _seed_db(migrations_pg_dsn)
    token = await _mint(seed["admin_user"], seed["tenant"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.post(
            "/projects",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "byadminmember", "status": "active"},
        )
    assert resp.status_code < 400, f"{resp.status_code} {resp.text}"


# ===========================================================================
# human_06_8_04 — "Tasks se crean por cualquier member (no solo admin)"
#
# `_MEMBER_GATED` above only carries GETs, so until now NO test asserted the
# other half of the matrix: the day-to-day MUTATIONS a plain tenant_user must
# be able to do. The three checklist lines that are machine-checkable are
# "Crear tarea funciona end-to-end", "Drag-drop entre columnas del kanban
# funciona (cambio de status)" and "Comentar en un plan funciona".
#
# Verified in the routers before writing these: `create_task` and
# `update_task` (routers/tasks.py) and `post_plan_comment` (routers/plans.py)
# all depend on `require_tenant_member`, NOT `require_tenant_admin` — these
# tests are what stops a future "tighten the gates" pass from silently
# breaking the Kanban for non-admins.
# ===========================================================================
@pytest.mark.asyncio
async def test_tenant_user_can_create_task(configured_app, migrations_pg_dsn: str) -> None:
    """POST /projects/{id}/tasks with a tenant_user token → 201."""
    seed = await _seed_project_plan_task(migrations_pg_dsn)
    token = await _mint(seed["plain_user"], seed["tenant"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.post(
            f"/projects/{seed['project']}/tasks",
            headers={"Authorization": f"Bearer {token}"},
            json={"title": "Tarea del member", "status": "backlog"},
        )
    assert resp.status_code == 201, f"{resp.status_code} {resp.text}"
    assert resp.json()["title"] == "Tarea del member"


@pytest.mark.asyncio
async def test_tenant_user_can_move_task_across_the_kanban(
    configured_app, migrations_pg_dsn: str
) -> None:
    """The drag-drop status move (backlog → ready) is a tenant_user write.

    Asserts the persisted status, not just the code: a 200 that ignored the
    body would leave the task in `backlog`.
    """
    seed = await _seed_project_plan_task(migrations_pg_dsn)
    token = await _mint(seed["plain_user"], seed["tenant"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.put(
            f"/projects/{seed['project']}/tasks/{seed['task']}",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "ready"},
        )
    assert resp.status_code == 200, f"{resp.status_code} {resp.text}"
    assert resp.json()["status"] == "ready"

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        persisted = await conn.fetchval("SELECT status FROM tasks WHERE id = $1", seed["task"])
    finally:
        await conn.close()
    assert persisted == "ready"


@pytest.mark.asyncio
async def test_tenant_user_can_comment_a_plan(configured_app, migrations_pg_dsn: str) -> None:
    """POST /plans/{plan_id}/comments with a tenant_user token → 201, and the
    comment is attributed to that user (not to the admin that owns the plan)."""
    seed = await _seed_project_plan_task(migrations_pg_dsn)
    token = await _mint(seed["plain_user"], seed["tenant"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.post(
            f"/plans/{seed['plan']}/comments",
            headers={"Authorization": f"Bearer {token}"},
            json={"target_kind": "plan", "content": "Esto lo comenta un member."},
        )
    assert resp.status_code == 201, f"{resp.status_code} {resp.text}"
    body = resp.json()
    assert body["content"] == "Esto lo comenta un member."
    assert body["author_user_id"] == str(seed["plain_user"])


@pytest.mark.asyncio
async def test_stranger_cannot_create_task_or_comment(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Counterweight to the three above: the member gate is a GATE, not a
    pass-through. A registered user with NO membership in the tenant is
    rejected on the very same mutations (403)."""
    seed = await _seed_project_plan_task(migrations_pg_dsn)
    token = await _mint(seed["stranger"], seed["tenant"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        create = await client.post(
            f"/projects/{seed['project']}/tasks",
            headers=headers,
            json={"title": "no debería entrar", "status": "backlog"},
        )
        move = await client.put(
            f"/projects/{seed['project']}/tasks/{seed['task']}",
            headers=headers,
            json={"status": "ready"},
        )
        comment = await client.post(
            f"/plans/{seed['plan']}/comments",
            headers=headers,
            json={"target_kind": "plan", "content": "tampoco"},
        )
    assert create.status_code == 403, create.text
    assert move.status_code == 403, move.text
    assert comment.status_code == 403, comment.text
