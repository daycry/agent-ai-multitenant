"""prod-03 task_prod03_04 — la carrera check-then-act de la resolución.

`resolve_approval` mutaba la fila en Python (`request.status = ...`) sin
`UPDATE ... WHERE status='pending'` ni `SELECT ... FOR UPDATE`, y el 409 del
router se decidía LEYENDO el estado antes de escribir. Dos revisores que
resuelven a la vez leen los dos `pending`, pasan los dos la comprobación y
escriben transiciones CONTRADICTORIAS: la ejecución acaba `done` **y**
`aborted`, la tarea `backlog` **y** `blocked`, y el `resolved_by` que queda es
el del último que escribió, no el de quien decidió.

La carrera se hace DETERMINISTA con un `asyncio.Barrier`: las dos corrutinas
leen la fila `pending`, se esperan, y solo entonces escriben. Sin la barrera el
test pasaría por suerte de planificación —el primero terminaría antes de que el
segundo leyera— y no probaría nada.

Cubre también la carrera aprobar-vs-timeout (riesgo 6 del plan): el job de
expiración y la resolución humana compiten por la misma fila.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from api_server.db.approval_repo import expire_stale_requests, resolve_approval
from api_server.db.domain import ApprovalRequest, Execution, Project, Task
from api_server.db.models import Organization, User, UserOrganizationMembership
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from uuid6 import uuid7

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 7, 29, 12, 0, 0, tzinfo=UTC)


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


async def _seed_pending(
    session: async_sessionmaker,
    *,
    requested_at: datetime,
) -> dict[str, UUID]:
    """org → project → task → execution → approval_request (pending)."""
    ids = {
        "tenant": uuid4(),
        "project": uuid4(),
        "task": uuid4(),
        "execution": uuid4(),
        "request": uuid4(),
        # Dos revisores REALES: `approval_requests.resolved_by` es FK a users, y
        # sin la fila el UPDATE muere con ForeignKeyViolation en vez de perder
        # la carrera — el test no probaría lo que dice probar.
        "reviewer_a": uuid4(),
        "reviewer_b": uuid4(),
    }
    async with session() as s, s.begin():
        await s.execute(
            text(
                "TRUNCATE approval_requests, executions, task_dependencies, tasks,"
                " projects, organizations RESTART IDENTITY CASCADE"
            )
        )
        s.add(Organization(id=ids["tenant"], name="Race tenant", slug="race-tenant"))
        for key in ("reviewer_a", "reviewer_b"):
            s.add(
                User(
                    id=ids[key],
                    email=f"{key}-{ids[key]}@example.test",
                    password_hash="x",
                )
            )
        await s.flush()
        # Membresías: el router pasa por `require_tenant_member`, y sin ellas el
        # test del 409 mediría un 403.
        for key in ("reviewer_a", "reviewer_b"):
            s.add(
                UserOrganizationMembership(
                    id=uuid4(),
                    tenant_id=ids["tenant"],
                    user_id=ids[key],
                    role="tenant_admin",
                )
            )
        await s.flush()
        s.add(
            Project(
                id=ids["project"],
                tenant_id=ids["tenant"],
                name="Race project",
                status="active",
                is_template=False,
            )
        )
        await s.flush()
        s.add(
            Task(
                id=ids["task"],
                tenant_id=ids["tenant"],
                project_id=ids["project"],
                title="Race task",
                status="awaiting_human_approval",
                priority="medium",
            )
        )
        await s.flush()
        s.add(
            Execution(
                id=ids["execution"],
                tenant_id=ids["tenant"],
                task_id=ids["task"],
                status="awaiting_human_approval",
            )
        )
        await s.flush()
        s.add(
            ApprovalRequest(
                id=ids["request"],
                tenant_id=ids["tenant"],
                execution_id=ids["execution"],
                task_id=ids["task"],
                project_id=ids["project"],
                category="production_deploy",
                status="pending",
                requested_at=requested_at,
            )
        )
    return ids


async def _contend_resolve(
    sm: async_sessionmaker,
    request_id: UUID,
    *,
    approved: bool,
    resolver_id: UUID,
    barrier: asyncio.Barrier,
) -> tuple[str, bool]:
    """Read `pending`, wait for the other contender, then resolve.

    Returns ``(outcome, approved)`` where outcome is ``won`` / ``lost``.
    """
    async with sm() as s, s.begin():
        request = await s.get(ApprovalRequest, request_id)
        assert request is not None and request.status == "pending"
        # Los DOS han leído `pending` — el check-then-act queda garantizado.
        await barrier.wait()
        resolved = await resolve_approval(
            s,
            request,
            approved=approved,
            resolver_id=resolver_id,
            reason="approve" if approved else "reject",
        )
    return ("won" if resolved is not None else "lost", approved)


@pytest.mark.asyncio
async def test_two_simultaneous_resolutions_only_one_wins(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed_pending(sm, requested_at=_NOW)
        approver, rejecter = ids["reviewer_a"], ids["reviewer_b"]

        barrier = asyncio.Barrier(2)
        outcomes = await asyncio.gather(
            _contend_resolve(
                sm, ids["request"], approved=True, resolver_id=approver, barrier=barrier
            ),
            _contend_resolve(
                sm, ids["request"], approved=False, resolver_id=rejecter, barrier=barrier
            ),
        )

        # Exactamente una gana; la perdedora lo SABE (devuelve None), que es lo
        # que el router necesita para emitir un 409 honesto.
        assert sorted(o for o, _ in outcomes) == ["lost", "won"]
        winner_approved = next(approved for outcome, approved in outcomes if outcome == "won")

        async with sm() as s:
            request = await s.get(ApprovalRequest, ids["request"])
            execution = await s.get(Execution, ids["execution"])
            task = await s.get(Task, ids["task"])
        assert request is not None and execution is not None and task is not None

        # Y el estado final es el de la ganadora, sin mezclas.
        if winner_approved:
            assert request.status == "approved"
            assert request.resolved_by == approver
            assert request.reason == "approve"
            assert execution.status == "done"
            assert execution.abort_code is None
            assert task.status == "backlog"
        else:
            assert request.status == "rejected"
            assert request.resolved_by == rejecter
            assert request.reason == "reject"
            assert execution.status == "aborted"
            assert execution.abort_code == "approval_rejected"
            assert task.status == "blocked"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_resolving_an_already_resolved_request_returns_none(
    _migrated: None, admin_database_url: str
) -> None:
    """El caso secuencial del mismo guard: la segunda resolución no muta nada.

    Sin el UPDATE condicional, la segunda pisaba la primera en silencio (la
    ejecución pasaba de `done` a `aborted` con un revisor distinto).
    """
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed_pending(sm, requested_at=_NOW)
        first, second = ids["reviewer_a"], ids["reviewer_b"]

        async with sm() as s, s.begin():
            request = await s.get(ApprovalRequest, ids["request"])
            assert request is not None
            assert await resolve_approval(s, request, approved=True, resolver_id=first) is not None

        async with sm() as s, s.begin():
            request = await s.get(ApprovalRequest, ids["request"])
            assert request is not None
            assert (
                await resolve_approval(
                    s, request, approved=False, resolver_id=second, reason="me lo pensé mejor"
                )
                is None
            )

        async with sm() as s:
            request = await s.get(ApprovalRequest, ids["request"])
            execution = await s.get(Execution, ids["execution"])
            task = await s.get(Task, ids["task"])
        assert request is not None and execution is not None and task is not None
        assert request.status == "approved"
        assert request.resolved_by == first
        assert request.reason is None
        assert execution.status == "done"
        assert task.status == "backlog"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_approve_versus_timeout_is_settled_by_the_same_guard(
    _migrated: None, admin_database_url: str
) -> None:
    """Riesgo 6 del plan: el job de expiración compite con el revisor.

    La solicitud es lo bastante vieja para caducar Y un humano la aprueba a la
    vez. Sin el guard atómico las dos escriben: la fila queda `timed_out` con la
    ejecución `done`, o `approved` con la ejecución `aborted`.
    """
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed_pending(sm, requested_at=_NOW - timedelta(hours=99))
        approver = ids["reviewer_a"]
        barrier = asyncio.Barrier(2)

        async def _expire() -> str:
            async with sm() as s, s.begin():
                # Lee el mismo `pending` que el revisor antes de escribir.
                request = await s.get(ApprovalRequest, ids["request"])
                assert request is not None and request.status == "pending"
                await barrier.wait()
                expired = await expire_stale_requests(s, now=_NOW, timeout_hours=24)
            return "won" if expired else "lost"

        async def _approve() -> str:
            outcome, _ = await _contend_resolve(
                sm, ids["request"], approved=True, resolver_id=approver, barrier=barrier
            )
            return outcome

        outcomes = await asyncio.gather(_expire(), _approve())
        assert sorted(outcomes) == ["lost", "won"]

        async with sm() as s:
            request = await s.get(ApprovalRequest, ids["request"])
            execution = await s.get(Execution, ids["execution"])
            task = await s.get(Task, ids["task"])
        assert request is not None and execution is not None and task is not None

        # Cualquiera de los dos pudo ganar; lo que NO puede pasar es la mezcla.
        if request.status == "approved":
            assert execution.status == "done"
            assert task.status == "backlog"
            assert request.resolved_by == approver
        else:
            assert request.status == "timed_out"
            assert execution.status == "aborted"
            assert execution.abort_code == "approval_timeout_exceeded"
            assert task.status == "blocked"
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# El 409 por la RUTA HTTP (lo que pide el plan: una 200 y una 409)
# ---------------------------------------------------------------------------
@pytest.fixture()
def configured_app(
    alembic_config: object,
    app_database_url: str,
    admin_database_url: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Any:
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

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
        app.dependency_overrides.clear()
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


@pytest.mark.asyncio
async def test_two_concurrent_http_resolutions_are_one_200_and_one_409(
    configured_app: Any,
    admin_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La carrera del plan, por la RUTA: `POST /approvals/{id}/resolve` ×2.

    El solape se fuerza en el punto EXACTO donde estaba el check-then-act: se
    envuelve la LECTURA que hace el router (`get_approval_request`) para que las
    dos peticiones hayan leído `pending` antes de que ninguna escriba. Sin esa
    barrera el bucle de eventos serializaría las dos peticiones y la
    comprobación previa devolvería el 409 por sí sola: el test pasaría en verde
    con el bug dentro, que es la clase de test que este repo tiene documentada
    como peor que ninguno.
    """
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed_pending(sm, requested_at=_NOW)
    finally:
        await engine.dispose()

    from api_server.db import approval_repo
    from api_server.routers import approvals as approvals_router

    barrier = asyncio.Barrier(2)

    async def _read_then_wait(session: Any, request_id: UUID) -> Any:
        request = await approval_repo.get_approval_request(session, request_id)
        await barrier.wait()
        return request

    monkeypatch.setattr(approvals_router, "get_approval_request", _read_then_wait)

    token_a = await _mint_token(ids["reviewer_a"], ids["tenant"])
    token_b = await _mint_token(ids["reviewer_b"], ids["tenant"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:

        async def _resolve(token: str, approved: bool) -> int:
            resp = await client.post(
                f"/approvals/{ids['request']}/resolve",
                json={"approved": approved, "reason": "concurrent"},
                headers={"Authorization": f"Bearer {token}"},
            )
            return resp.status_code

        codes = await asyncio.gather(
            _resolve(token_a, True),
            _resolve(token_b, False),
        )

    assert sorted(codes) == [200, 409], codes

    # Y el estado final es el de la que ganó — no una mezcla de las dos.
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as s:
            request = await s.get(ApprovalRequest, ids["request"])
            execution = await s.get(Execution, ids["execution"])
            task = await s.get(Task, ids["task"])
        assert request is not None and execution is not None and task is not None
        if request.status == "approved":
            assert execution.status == "done"
            assert task.status == "backlog"
        else:
            assert request.status == "rejected"
            assert execution.status == "aborted"
            assert task.status == "blocked"
    finally:
        await engine.dispose()
