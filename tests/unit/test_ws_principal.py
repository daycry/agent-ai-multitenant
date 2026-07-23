"""Unit tests for the WebSocket principal resolver's acting-as-tenant override.

Regression for the "live WS silent under a cross-tenant admin view" bug: a
System Admin/Owner acting on behalf of another tenant conveys that tenant to
REST via the ``X-Tenant-Id`` header (``get_principal``), but the browser
WebSocket API cannot set headers, so the WS carried only the JWT whose ``tid``
is the admin's HOME tenant. Every ``/ws/*`` stream for the acted-on tenant was
then rejected under RLS and the client reconnected forever.

The fix mirrors the REST header rule on the WS: a ``?tenant_id=`` query param
overrides the JWT ``tid`` for ``is_system_admin`` principals only; non-admins
can never escape their JWT scope.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("API_SERVER_JWT_SECRET", "test-secret")
    from api_server.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class _FakeSessions:
    """Minimal SessionStore stand-in: the resolver only needs ``get`` to
    report whether the server-side session is still live."""

    def __init__(self, *, live: bool = True) -> None:
        self._live = live

    async def get(self, _sid: object) -> dict[str, bool] | None:
        return {"live": True} if self._live else None


def _resolve(token: str, tenant_override: str | None):
    from api_server.routers.ws import _resolve_principal

    return asyncio.run(_resolve_principal(token, _FakeSessions(), tenant_override))


def test_sysadmin_query_tenant_overrides_jwt_tid() -> None:
    from api_server.auth.jwt import encode_jwt

    home, acting, user, sid = uuid4(), uuid4(), uuid4(), uuid4()
    token = encode_jwt(user_id=user, session_id=sid, tenant_id=home, is_system_admin=True)

    principal = _resolve(token, str(acting))

    assert principal is not None
    # The acted-on tenant (query param) wins over the JWT's home tenant.
    assert principal.tenant_id == acting


def test_non_admin_ignores_query_tenant() -> None:
    from api_server.auth.jwt import encode_jwt

    home, acting, user, sid = uuid4(), uuid4(), uuid4(), uuid4()
    token = encode_jwt(user_id=user, session_id=sid, tenant_id=home, is_system_admin=False)

    principal = _resolve(token, str(acting))

    assert principal is not None
    # A non-admin can't escape their JWT scope: override is ignored.
    assert principal.tenant_id == home


def test_no_override_keeps_jwt_tid() -> None:
    from api_server.auth.jwt import encode_jwt

    home, user, sid = uuid4(), uuid4(), uuid4()
    token = encode_jwt(user_id=user, session_id=sid, tenant_id=home, is_system_admin=True)

    principal = _resolve(token, None)

    assert principal is not None
    assert principal.tenant_id == home


def test_sysadmin_invalid_query_tenant_is_rejected() -> None:
    from api_server.auth.jwt import encode_jwt

    home, user, sid = uuid4(), uuid4(), uuid4()
    token = encode_jwt(user_id=user, session_id=sid, tenant_id=home, is_system_admin=True)

    # A malformed tenant override must not silently fall back to the home
    # tenant (that would act on the wrong scope) — reject the socket instead.
    assert _resolve(token, "not-a-uuid") is None
