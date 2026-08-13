"""Contract test for the WHOLE ``/admin/*`` surface (prod-09 task_prod09_01, authz-1).

The System-Admin surface is the highest-value target on the platform: a System
Admin acts cross-tenant on the BYPASSRLS engine, and ``/admin/backup/restore``
is outright destructive. ADR 0042 hardens it with MFA + an IP allowlist + a
short session TTL, wired as :func:`require_hardened_system_admin`.

The audit finding this file closes (authz-1) was NOT that the gate was missing —
it was that it covered ONE router out of ten. ``routers/admin.py`` carried it;
``/admin/backup`` (destructive restore), ``/admin/llm-providers`` (LLM
credentials), ``/admin/platform-settings``, ``/admin/cross-tenant-stats``,
``/admin/marketplace``, ``/admin/model-prices``, ``/admin/ollama``,
``/admin/embeddings`` and ``/admin/llm/copilot/device-flow`` did not.

So the valuable test is not "these nine routers are wired" (that ages the day a
tenth appears) but the INVARIANT: *every mounted route whose path starts with
``/admin`` carries the hardened dependency somewhere in its dependency tree*.
That is :func:`test_every_admin_route_carries_the_hardening_gate`, and it is
what makes the regression impossible rather than merely fixed.

Two more tests keep it honest:

  * the discovery assertion (``>= 40`` admin routes over ``>= 9`` distinct
    prefixes) so the invariant can never pass VACUOUSLY — a refactor that stops
    finding admin routes fails instead of going green on an empty set;
  * a BEHAVIOURAL fail-closed check: a real request to ``GET
    /admin/backup/schedule`` as a System Admin who has no MFA factor, in
    ``prod``, is rejected. Wiring proven by structure AND by a 4xx.

No DB and no Redis: the structural tests only read ``app.routes``, and the
behavioural one overrides the principal/session-store dependencies so the gate
raises before any handler (hence any engine) is reached.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from api_server.routing_introspection import iter_routes
from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, APIWebSocketRoute
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration

_ADMIN_PREFIX = "/admin"
_GATE_NAME = "require_hardened_system_admin"


# ---------------------------------------------------------------------------
# App under test — built in-process, no DB / Redis touched.
# ---------------------------------------------------------------------------
@pytest.fixture()
def app(monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    """A freshly built app with dev-safe settings (engines stay lazy).

    ``create_app`` only mounts routers; nothing connects until a handler runs,
    and in these tests no handler ever does.
    """
    monkeypatch.setenv("API_SERVER_ENVIRONMENT", "dev")
    from api_server.config import get_settings

    get_settings.cache_clear()
    from api_server.main import create_app

    built = create_app()
    try:
        yield built
    finally:
        built.dependency_overrides.clear()
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Dependency-tree walk
# ---------------------------------------------------------------------------
def _dependency_names(dependant: Dependant) -> set[str]:
    """Every callable name reachable from ``dependant``, recursively.

    Walking the RESOLVED tree (rather than ``route.dependencies``) means the
    test does not care HOW the gate was wired: router-level, mount-time
    ``include_router(dependencies=...)``, or an explicit parameter in the
    handler signature all show up here. Only "it is absent" fails.
    """
    names: set[str] = set()
    stack: list[Dependant] = [dependant]
    while stack:
        node = stack.pop()
        if node.call is not None:
            names.add(getattr(node.call, "__name__", ""))
        stack.extend(node.dependencies)
    return names


def _admin_routes(app: FastAPI) -> list[tuple[str, Dependant]]:
    """Every mounted route under ``/admin`` as ``(label, dependant)``."""
    found: list[tuple[str, Dependant]] = []
    for route in iter_routes(app):
        path = str(getattr(route, "path", ""))
        if path != _ADMIN_PREFIX and not path.startswith(f"{_ADMIN_PREFIX}/"):
            continue
        if isinstance(route, APIRoute):
            methods = ",".join(sorted(route.methods or set()))
            found.append((f"{methods} {path}", route.dependant))
        elif isinstance(route, APIWebSocketRoute):
            found.append((f"WS {path}", route.dependant))
        else:  # pragma: no cover - a non-API route under /admin would be new
            pytest.fail(f"unexpected route type under /admin: {path} ({type(route)})")
    return found


# ---------------------------------------------------------------------------
# (1) The invariant
# ---------------------------------------------------------------------------
def test_every_admin_route_carries_the_hardening_gate(app: FastAPI) -> None:
    """No route under ``/admin`` may lack ``require_hardened_system_admin``.

    This is the anti-reincidence piece: it does not enumerate routers, it
    enumerates the mounted surface. Adding a new ``/admin`` router without the
    gate turns this red.
    """
    offenders = [
        label
        for label, dependant in _admin_routes(app)
        if _GATE_NAME not in _dependency_names(dependant)
    ]
    assert not offenders, (
        f"{len(offenders)} route(s) under /admin lack {_GATE_NAME}: {sorted(offenders)}"
    )


def test_the_guard_actually_found_the_admin_surface(app: FastAPI) -> None:
    """The invariant above must not be able to pass on an empty set.

    A refactor that renames the prefix, splits the app, or stops mounting the
    admin routers would leave ``_admin_routes`` empty and make the guard green
    while protecting nothing. Pin BOTH a route count and the distinct
    second-level prefixes, which is what "the whole surface" means here.
    """
    routes = _admin_routes(app)
    assert len(routes) >= 40, f"the admin-surface discovery found only {len(routes)} routes"

    prefixes = {label.split(" ", 1)[1].split("/")[2] for label in (lbl for lbl, _ in routes)}
    # The ten sub-surfaces of the finding (nine unhardened + routers/admin.py,
    # whose routes sit directly under /admin/<something>).
    for expected in (
        "backup",
        "llm-providers",
        "platform-settings",
        "cross-tenant-stats",
        "marketplace",
        "model-prices",
        "ollama",
        "embeddings",
        "llm",  # /admin/llm/copilot/device-flow
    ):
        assert expected in prefixes, f"/admin/{expected} is no longer part of the surface"
    assert len(prefixes) >= 9, f"only {len(prefixes)} admin sub-surfaces found: {sorted(prefixes)}"


def test_admin_backup_schedule_is_not_tenant_readable(app: FastAPI) -> None:
    """``GET /admin/backup/schedule`` must not fall back to a tenant gate.

    It used to depend on ``require_tenant_member``, so any authenticated tenant
    user could read the platform's backup cadence + retention window from an
    ``/admin`` path. The route must now require the System Admin (and, by the
    invariant above, the hardened gate) and must NOT mention the tenant gate.
    """
    matches = [
        _dependency_names(dependant)
        for label, dependant in _admin_routes(app)
        if label == "GET /admin/backup/schedule"
    ]
    assert len(matches) == 1, "GET /admin/backup/schedule is no longer mounted"
    names = matches[0]
    assert "require_system_admin" in names
    assert _GATE_NAME in names
    assert "require_tenant_member" not in names
    assert "get_tenant_session" not in names


# ---------------------------------------------------------------------------
# (2) Fail-closed behaviour — a real request, not just wiring
# ---------------------------------------------------------------------------
class _FakeSessions:
    """A session store whose sessions are fresh but which is never consulted
    for MFA — enough for the gate's short-session check to pass."""

    def __init__(self, created_at: int) -> None:
        self._created_at = created_at

    async def get(self, _sid: UUID) -> dict[str, Any]:
        return {"user_id": str(uuid4()), "tenant_id": None, "created_at": self._created_at}


def _stub_db_admin_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the DB re-read of ``users.is_system_admin`` answer True.

    ``require_system_admin`` re-verifies the flag against the database since
    prod-09 task_prod09_04, and these tests deliberately run with NO database (the
    point is that the hardening gate rejects before any handler). Only the DB EDGE
    is stubbed — the real ``require_system_admin`` stays in the dependency chain,
    so the composition under test (``require_hardened_system_admin`` on top of the
    admin gate) is the production one.
    """
    from api_server.auth import deps as deps_mod

    async def _yes(_user_id: UUID) -> bool:
        return True

    monkeypatch.setattr(deps_mod, "_is_db_system_admin", _yes)


@pytest.mark.asyncio
async def test_prod_admin_without_mfa_is_rejected_on_a_previously_open_route(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: in ``prod``, an MFA-less System Admin is refused by
    ``GET /admin/backup/schedule`` — one of the nine routes that answered 200
    before this task. The handler is never reached, so no DB is touched.
    """
    import time

    from api_server.auth import admin_hardening
    from api_server.auth.deps import AuthPrincipal, get_principal, get_session_store

    principal = AuthPrincipal(
        user_id=uuid4(), session_id=uuid4(), tenant_id=None, is_system_admin=True
    )
    app.dependency_overrides[get_principal] = lambda: principal
    app.dependency_overrides[get_session_store] = lambda: _FakeSessions(int(time.time()))
    _stub_db_admin_lookup(monkeypatch)

    monkeypatch.setattr(
        admin_hardening, "get_settings", lambda: _prod_settings(admin_ip_allowlist=[])
    )

    async def _no_mfa(_user_id: UUID) -> list[str]:
        return []

    monkeypatch.setattr(admin_hardening, "user_mfa_methods", _no_mfa)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/admin/backup/schedule")

    assert resp.status_code == 403, resp.text
    assert "MFA" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_prod_admin_off_the_ip_allowlist_is_rejected(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same route, the OTHER control: a source IP outside a non-empty allowlist
    is 403'd. Proves the whole gate — not just its MFA leg — now guards the
    routers that were mounted bare."""
    import time

    from api_server.auth import admin_hardening
    from api_server.auth.deps import AuthPrincipal, get_principal, get_session_store

    principal = AuthPrincipal(
        user_id=uuid4(), session_id=uuid4(), tenant_id=None, is_system_admin=True
    )
    app.dependency_overrides[get_principal] = lambda: principal
    app.dependency_overrides[get_session_store] = lambda: _FakeSessions(int(time.time()))
    _stub_db_admin_lookup(monkeypatch)
    monkeypatch.setattr(
        admin_hardening,
        "get_settings",
        lambda: _prod_settings(admin_ip_allowlist=["10.0.0.0/24"]),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/admin/backup/schedule", headers={"X-Forwarded-For": "203.0.113.9"}
        )

    assert resp.status_code == 403, resp.text
    assert "allowlist" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_dev_does_not_over_enforce_the_widened_surface(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The counterpart that keeps the fix from being a lockout: in ``dev`` the
    gate is a pass-through, so widening it to nine more routers does not force
    MFA on local development. The request gets past the gate and dies later (on
    the DB the handler needs), which is exactly the proof we want: NOT a 401/403.

    ``raise_app_exceptions=False`` is load-bearing, not a convenience. Getting
    past the gate means reaching a handler that WILL blow up here (the app role
    has no grant on ``platform_settings``, and these tests deliberately run with
    no usable DB). Starlette's ``ServerErrorMiddleware`` renders its 500 and then
    RE-RAISES so the server logs the fault, and httpx's default re-raises it at
    the caller — so with the default the test could never read a status code at
    all: it errored with ``ProgrammingError`` instead of asserting anything. With
    the flag off we read the real status line, and a gate that started enforcing
    in ``dev`` would still show up as the 401/403 this asserts against.
    """
    import time

    from api_server.auth import admin_hardening
    from api_server.auth.deps import AuthPrincipal, get_principal, get_session_store

    principal = AuthPrincipal(
        user_id=uuid4(), session_id=uuid4(), tenant_id=None, is_system_admin=True
    )
    app.dependency_overrides[get_principal] = lambda: principal
    app.dependency_overrides[get_session_store] = lambda: _FakeSessions(
        int(time.time()) - 24 * 3600  # ancient: would 401 in prod
    )
    _stub_db_admin_lookup(monkeypatch)

    async def _no_mfa(_user_id: UUID) -> list[str]:
        return []

    monkeypatch.setattr(admin_hardening, "user_mfa_methods", _no_mfa)

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/admin/backup/schedule", headers={"X-Forwarded-For": "203.0.113.9"}
        )

    assert resp.status_code not in (401, 403), resp.text


def _prod_settings(**overrides: Any) -> Any:
    """A ``prod`` Settings that satisfies the fail-closed secret guard."""
    from api_server.config import Settings

    base: dict[str, Any] = {
        "environment": "prod",
        "admin_require_mfa": True,
        "admin_session_ttl_minutes": 15,
        "jwt_secret": "p" * 48,
        "internal_token_secret": "q" * 48,
        "review_url_signing_secret": "r" * 48,
        "sso_encryption_key": "s" * 48,
        "notification_encryption_key": "t" * 48,
        "incoming_webhook_encryption_key": "u" * 48,
        "minio_secret_key": "v" * 48,
        "minio_access_key": "prod-minio-access",
        "database_url": "postgresql+asyncpg://app_user:realpw@db/agentic_platform",
        "admin_database_url": "postgresql+asyncpg://migrations_user:realpw@db/agentic_platform",
    }
    base.update(overrides)
    return Settings(**base)
