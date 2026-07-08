"""Integration tests de `POST /plans/{id}/generate-corrections` (ADR 0107).

Sobre un plan RECHAZADO con una sesión de review que guarda el
`rejection_reason`, el endpoint genera (vía LLM del proyecto, aquí
monkeypatcheado) tareas correctivas y las añade al spec del plan:
`specification.tasks` gana las `fix-*` con `origin: correction` y
`specification.corrections` la entrada `proposed` del ciclo. Idempotente
por sesión: repetir devuelve lo ya propuesto sin regenerar.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")

_PLAN_SPEC = {
    "tasks": [
        {"id": "t1", "title": "Original A", "complexity": "m"},
        {"id": "t2", "title": "Original B", "complexity": "s", "depends_on": ["t1"]},
    ],
}

_REASON = "El filtro JSON es global y rompe la portada HTML; acotarlo a api/v1."

_FIXES = [
    {
        "id": "fix-1",
        "title": "Acotar filtro Content-Type a api/v1",
        "description": "Aplicar el filtro JSON solo al grupo de rutas api/v1",
        "role": "backend_dev",
        "complexity": "s",
        "depends_on": [],
        "acceptance_criteria": ["La portada responde text/html"],
        "origin": "correction",
    },
    {
        "id": "fix-2",
        "title": "Test de regresión del filtro",
        "description": "Cubrir portada HTML y api/v1 JSON",
        "role": "qa",
        "complexity": "s",
        "depends_on": ["fix-1"],
        "acceptance_criteria": ["La suite pasa en verde"],
        "origin": "correction",
    },
]


async def _seed(dsn: str) -> dict[str, UUID]:
    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE review_sessions, task_dependencies, tasks, plan_comments, plans,"
            " conversations, projects, agents, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            tenant_id,
            "Tenant GenCorr",
            "tenant-gencorr",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-gencorr",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3)",
            user_id,
            "alice@gencorr.test",
            "h",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, $4)",
            uuid4(),
            tenant_id,
            user_id,
            "tenant_admin",
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, $3)",
            project_id,
            tenant_id,
            "GenCorr Project",
        )
    finally:
        await conn.close()
    return {"tenant_id": tenant_id, "user_id": user_id, "project_id": project_id}


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


async def _create_rejected_plan(client: AsyncClient, project_id: UUID, headers: dict) -> str:
    create = await client.post(
        f"/projects/{project_id}/plans",
        json={"title": "Plan rechazado", "specification": _PLAN_SPEC},
        headers=headers,
    )
    assert create.status_code == 201, create.text
    plan_id: str = create.json()["id"]
    moved = await client.put(
        f"/plans/{plan_id}", json={"status": "pending_approval"}, headers=headers
    )
    assert moved.status_code == 200, moved.text
    approved = await client.post(f"/plans/{plan_id}/approve", headers=headers)
    assert approved.status_code == 200, approved.text
    for next_status in ("in_progress", "pending_human_validation", "rejected"):
        upd = await client.put(f"/plans/{plan_id}", json={"status": next_status}, headers=headers)
        assert upd.status_code == 200, upd.text
    return plan_id


async def _seed_rejected_session(
    dsn: str, tenant_id: UUID, plan_id: str, reason: str | None = _REASON
) -> UUID:
    """La fila que deja `submit_verdict` al rechazar: verdict + motivo."""
    session_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO review_sessions"
            " (id, tenant_id, plan_id, spec, status, verdict, rejection_reason, expires_at)"
            " VALUES ($1, $2, $3, $4::jsonb, 'rejected', 'rejected', $5, now() + interval '1h')",
            session_id,
            tenant_id,
            UUID(plan_id),
            json.dumps({}),
            reason,
        )
    finally:
        await conn.close()
    return session_id


class _StubProvider:
    async def aclose(self) -> None:  # pragma: no cover - trivial
        return None


def _patch_llm(monkeypatch: pytest.MonkeyPatch, captured: dict[str, Any] | None = None) -> None:
    async def _fake_resolve(session: Any, effective: Any, vault: Any):
        return _StubProvider(), "ollama", "m"

    async def _fake_generate(provider: Any, **kwargs: Any) -> list[dict[str, Any]]:
        if captured is not None:
            captured.update(kwargs)
        return [dict(t) for t in _FIXES]

    monkeypatch.setattr("api_server.routers.plans._resolve_chat_provider", _fake_resolve)
    monkeypatch.setattr("api_server.routers.plans.generate_corrective_tasks", _fake_generate)


@pytest.mark.asyncio
async def test_generate_appends_corrective_tasks_to_the_spec(
    configured_app, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}
    captured: dict[str, Any] = {}
    _patch_llm(monkeypatch, captured)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        plan_id = await _create_rejected_plan(client, seeded["project_id"], headers)
        session_id = await _seed_rejected_session(migrations_pg_dsn, seeded["tenant_id"], plan_id)

        resp = await client.post(f"/plans/{plan_id}/generate-corrections", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["session_id"] == str(session_id)
        assert body["reason"] == _REASON
        assert body["task_ids"] == ["fix-1", "fix-2"]
        assert body["already_generated"] is False
        assert [t["id"] for t in body["tasks"]] == ["fix-1", "fix-2"]

        # El motivo llegó al generador.
        assert captured.get("rejection_reason") == _REASON

        # El spec del plan ganó las tareas y la entrada proposed.
        plan = (await client.get(f"/plans/{plan_id}", headers=headers)).json()
        spec_ids = [t["id"] for t in plan["specification"]["tasks"]]
        assert spec_ids == ["t1", "t2", "fix-1", "fix-2"]
        fix1 = next(t for t in plan["specification"]["tasks"] if t["id"] == "fix-1")
        assert fix1["origin"] == "correction"
        entry = plan["specification"]["corrections"][0]
        assert entry["session_id"] == str(session_id)
        assert entry["status"] == "proposed"
        assert entry["task_ids"] == ["fix-1", "fix-2"]
        # Sigue rechazado: generar propone, no reactiva.
        assert plan["status"] == "rejected"


@pytest.mark.asyncio
async def test_generate_is_idempotent_per_session(
    configured_app, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}
    _patch_llm(monkeypatch)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        plan_id = await _create_rejected_plan(client, seeded["project_id"], headers)
        await _seed_rejected_session(migrations_pg_dsn, seeded["tenant_id"], plan_id)

        first = await client.post(f"/plans/{plan_id}/generate-corrections", headers=headers)
        assert first.status_code == 200, first.text
        second = await client.post(f"/plans/{plan_id}/generate-corrections", headers=headers)
        assert second.status_code == 200, second.text
        assert second.json()["already_generated"] is True
        assert second.json()["task_ids"] == ["fix-1", "fix-2"]

        plan = (await client.get(f"/plans/{plan_id}", headers=headers)).json()
        spec_ids = [t["id"] for t in plan["specification"]["tasks"]]
        assert spec_ids.count("fix-1") == 1
        assert len(plan["specification"]["corrections"]) == 1


@pytest.mark.asyncio
async def test_generate_conflicts_when_plan_not_rejected(
    configured_app, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}
    _patch_llm(monkeypatch)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        create = await client.post(
            f"/projects/{seeded['project_id']}/plans",
            json={"title": "Draft", "specification": _PLAN_SPEC},
            headers=headers,
        )
        plan_id = create.json()["id"]
        resp = await client.post(f"/plans/{plan_id}/generate-corrections", headers=headers)
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["error"] == "plan_not_rejected"


@pytest.mark.asyncio
async def test_generate_conflicts_without_rejection_reason(
    configured_app, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}
    _patch_llm(monkeypatch)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        plan_id = await _create_rejected_plan(client, seeded["project_id"], headers)
        # Sin sesión rechazada con motivo → nada que convertir en trabajo.
        resp = await client.post(f"/plans/{plan_id}/generate-corrections", headers=headers)
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["error"] == "no_rejection_reason"


@pytest.mark.asyncio
async def test_generate_conflicts_without_provider(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        plan_id = await _create_rejected_plan(client, seeded["project_id"], headers)
        await _seed_rejected_session(migrations_pg_dsn, seeded["tenant_id"], plan_id)
        resp = await client.post(f"/plans/{plan_id}/generate-corrections", headers=headers)
        assert resp.status_code == 409, resp.text
