"""Admin-panel hardening invariants (Plan 15 task_15_18).

The System-Admin surface (`/admin/*`) is the highest-value target on the
platform. In production it must enforce three controls, none of which may
break local dev or a non-admin's login:

  1. MANDATORY MFA — an admin without an enrolled+confirmed second factor is
     locked out of the admin surface (forced-enrollment gate).
  2. IP ALLOWLIST — admin access is restricted to a configurable CIDR
     allowlist (reusing the api-token CIDR semantics).
  3. SHORT SESSIONS — an admin session older than the short TTL (15 min by
     default) is rejected, forcing re-authentication.

All three are enforced ONLY in staging/prod (dev stays usable). These tests
exercise the gate's pure predicates AND the FastAPI dependency end-to-end with
an in-memory fake :class:`SessionStore` and a mocked MFA lookup, so they run
deterministically in the security suite with no live Redis or DB. A regression
that drops a control, over-enforces in dev, or catches a non-admin would make
a test go RED.
"""

from __future__ import annotations

import json
import time
from typing import Any
from uuid import UUID, uuid4

import pytest
from api_server.auth import admin_hardening
from api_server.auth.admin_hardening import (
    admin_hardening_enforced,
    admin_ip_allowed,
    admin_session_expired,
    require_hardened_system_admin,
)
from api_server.auth.deps import AuthPrincipal
from api_server.auth.sessions import SessionStore
from api_server.config import Settings
from fastapi import HTTPException

pytestmark = pytest.mark.security


# ---------------------------------------------------------------------------
# Test doubles — an in-memory Redis good enough for SessionStore.create/get
# ---------------------------------------------------------------------------
class _FakeRedis:
    """Minimal async Redis stand-in: just the set/get the store needs here.

    The real :class:`SessionStore` also indexes sessions in a per-user SET
    (``sadd``/``expire``); those are best-effort and unused by the hardening
    gate, so the fake implements them as no-ops to keep ``create`` working.
    """

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._store[key] = value

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def sadd(self, *_args: Any, **_kwargs: Any) -> int:
        return 0

    async def expire(self, *_args: Any, **_kwargs: Any) -> bool:
        return True


class _Request:
    """A bare object exposing just what ``get_client_ip`` reads."""

    class _Client:
        def __init__(self, host: str) -> None:
            self.host = host

    def __init__(self, ip: str) -> None:
        self.headers: dict[str, str] = {}
        self.client = self._Client(ip)


# ---------------------------------------------------------------------------
# (1) Pure predicate: IP allowlist — reuses the api-token CIDR semantics
# ---------------------------------------------------------------------------
def test_ip_allowlist_empty_allows_any() -> None:
    """An empty allowlist means the operator opted out — any IP passes."""
    assert admin_ip_allowed("203.0.113.9", []) is True


def test_ip_allowlist_matches_cidr_and_bare_host() -> None:
    """A CIDR entry matches inside its range; a bare host is treated as /32."""
    assert admin_ip_allowed("10.0.0.42", ["10.0.0.0/24"]) is True
    assert admin_ip_allowed("10.0.0.5", ["10.0.0.5"]) is True  # bare host -> /32


def test_ip_allowlist_rejects_outside_and_malformed() -> None:
    """An IP outside every CIDR is rejected; a malformed entry never widens."""
    assert admin_ip_allowed("198.51.100.7", ["10.0.0.0/24"]) is False
    assert admin_ip_allowed("not-an-ip", ["10.0.0.0/24"]) is False
    # A malformed allowlist entry is skipped, not treated as "match all".
    assert admin_ip_allowed("198.51.100.7", ["garbage", "10.0.0.0/24"]) is False


# ---------------------------------------------------------------------------
# (2) Pure predicate: short session expiry
# ---------------------------------------------------------------------------
def test_session_within_ttl_is_not_expired() -> None:
    now = 1_000_000.0
    created = int(now) - (10 * 60)  # 10 min old, TTL 15 -> alive
    assert admin_session_expired(created, ttl_minutes=15, now=now) is False


def test_session_past_ttl_is_expired() -> None:
    now = 1_000_000.0
    created = int(now) - (16 * 60)  # 16 min old, TTL 15 -> expired
    assert admin_session_expired(created, ttl_minutes=15, now=now) is True


def test_session_without_created_at_is_expired_fail_closed() -> None:
    """A session minted before the timestamp existed fails closed for admin."""
    assert admin_session_expired(None, ttl_minutes=15, now=time.time()) is True


def test_non_positive_ttl_disables_the_clamp() -> None:
    assert admin_session_expired(None, ttl_minutes=0, now=time.time()) is False


# ---------------------------------------------------------------------------
# (3) Enforcement env predicate — dev is not over-enforced
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("env", "enforced"),
    [("dev", False), ("staging", True), ("prod", True), ("weird", False)],
)
def test_only_staging_prod_enforce(env: str, enforced: bool) -> None:
    # staging/prod trip the dev-secret guard unless the secrets are real, so
    # `_settings(environment=...)` supplies non-dev values for those cases.
    settings = _settings(environment=env) if enforced else Settings(environment=env)
    assert admin_hardening_enforced(settings) is enforced


# ---------------------------------------------------------------------------
# End-to-end dependency tests — fake SessionStore + mocked MFA lookup
# ---------------------------------------------------------------------------
def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "environment": "prod",
        "admin_require_mfa": True,
        "admin_ip_allowlist": ["10.0.0.0/24"],
        "admin_session_ttl_minutes": 15,
        # Keep the prod dev-secret guard happy: give every guarded secret a
        # non-dev value so Settings(environment="prod") constructs.
        "jwt_secret": "prod-jwt-secret-xxxxxxxxxxxxxxxxxxxx",
        "review_url_signing_secret": "prod-review-secret-xxxxxxxxxxxx",
        "sso_encryption_key": "prod-sso-key-xxxxxxxxxxxxxxxxxxxx",
        "notification_encryption_key": "prod-notif-key-xxxxxxxxxxxxxxx",
        "incoming_webhook_encryption_key": "prod-webhook-key-xxxxxxxxxxxx",
        "minio_secret_key": "prod-minio-secret-xxxxxxxxxxxxxxxx",
        "minio_access_key": "prod-minio-access",
        "database_url": "postgresql+asyncpg://app_user:prodpw@db/agentic_platform",
        "admin_database_url": "postgresql+asyncpg://migrations_user:prodpw@db/agentic_platform",
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture()
def admin_principal() -> AuthPrincipal:
    return AuthPrincipal(
        user_id=uuid4(),
        session_id=uuid4(),
        tenant_id=None,
        is_system_admin=True,
    )


async def _seed_session(store: SessionStore, principal: AuthPrincipal, *, age_seconds: int) -> None:
    """Create a session, then back-date its ``created_at`` by ``age_seconds``."""
    await store.create(
        principal.session_id,
        user_id=principal.user_id,
        tenant_id=None,
        ttl_seconds=3600,
    )
    # Back-date created_at directly in the fake so we control session age.
    redis = store._redis  # type: ignore[attr-defined]
    key = f"session:{principal.session_id}"
    payload = json.loads(await redis.get(key))
    payload["created_at"] = int(time.time()) - age_seconds
    await redis.set(key, json.dumps(payload))


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    *,
    settings: Settings,
    mfa_methods: list[str],
) -> None:
    monkeypatch.setattr(admin_hardening, "get_settings", lambda: settings)

    async def _fake_user_mfa_methods(_user_id: UUID) -> list[str]:
        return mfa_methods

    monkeypatch.setattr(admin_hardening, "user_mfa_methods", _fake_user_mfa_methods)


@pytest.mark.asyncio
async def test_prod_admin_with_mfa_and_allowlisted_ip_passes(
    monkeypatch: pytest.MonkeyPatch, admin_principal: AuthPrincipal
) -> None:
    """A prod admin WITH a confirmed MFA factor, on an allowlisted IP, with a
    fresh session, sails through the gate."""
    store = SessionStore(_FakeRedis())  # type: ignore[arg-type]
    await _seed_session(store, admin_principal, age_seconds=60)
    _patch(monkeypatch, settings=_settings(), mfa_methods=["totp"])

    result = await require_hardened_system_admin(
        request=_Request("10.0.0.42"),  # type: ignore[arg-type]
        principal=admin_principal,
        sessions=store,
    )
    assert result is admin_principal


@pytest.mark.asyncio
async def test_prod_admin_without_mfa_is_blocked(
    monkeypatch: pytest.MonkeyPatch, admin_principal: AuthPrincipal
) -> None:
    """A prod admin with NO enrolled MFA factor is locked out (403)."""
    store = SessionStore(_FakeRedis())  # type: ignore[arg-type]
    await _seed_session(store, admin_principal, age_seconds=60)
    _patch(monkeypatch, settings=_settings(), mfa_methods=[])  # no factor

    with pytest.raises(HTTPException) as excinfo:
        await require_hardened_system_admin(
            request=_Request("10.0.0.42"),  # type: ignore[arg-type]
            principal=admin_principal,
            sessions=store,
        )
    assert excinfo.value.status_code == 403
    assert "MFA" in excinfo.value.detail


@pytest.mark.asyncio
async def test_prod_admin_from_non_allowlisted_ip_is_403(
    monkeypatch: pytest.MonkeyPatch, admin_principal: AuthPrincipal
) -> None:
    """An admin off the allowlist is rejected (403) BEFORE the MFA round-trip."""
    store = SessionStore(_FakeRedis())  # type: ignore[arg-type]
    await _seed_session(store, admin_principal, age_seconds=60)
    _patch(monkeypatch, settings=_settings(), mfa_methods=["totp"])

    with pytest.raises(HTTPException) as excinfo:
        await require_hardened_system_admin(
            request=_Request("203.0.113.9"),  # type: ignore[arg-type]
            principal=admin_principal,
            sessions=store,
        )
    assert excinfo.value.status_code == 403
    assert "allowlist" in excinfo.value.detail


@pytest.mark.asyncio
async def test_prod_admin_session_expires_at_short_ttl(
    monkeypatch: pytest.MonkeyPatch, admin_principal: AuthPrincipal
) -> None:
    """A session older than the 15-minute admin TTL is rejected (401), even
    though its underlying Redis TTL (1h) has not elapsed."""
    store = SessionStore(_FakeRedis())  # type: ignore[arg-type]
    await _seed_session(store, admin_principal, age_seconds=16 * 60)  # 16 min old
    _patch(monkeypatch, settings=_settings(), mfa_methods=["totp"])

    with pytest.raises(HTTPException) as excinfo:
        await require_hardened_system_admin(
            request=_Request("10.0.0.42"),  # type: ignore[arg-type]
            principal=admin_principal,
            sessions=store,
        )
    assert excinfo.value.status_code == 401
    assert "expired" in excinfo.value.detail


@pytest.mark.asyncio
async def test_dev_mode_does_not_over_enforce(
    monkeypatch: pytest.MonkeyPatch, admin_principal: AuthPrincipal
) -> None:
    """In dev the gate is a pass-through: no MFA, no allowlist, no short TTL.

    An admin with NO MFA factor, on an arbitrary IP, with an OLD session would
    be blocked in prod — in dev it passes, proving the hardening does not
    over-enforce locally.
    """
    store = SessionStore(_FakeRedis())  # type: ignore[arg-type]
    await _seed_session(store, admin_principal, age_seconds=24 * 3600)  # ancient
    _patch(
        monkeypatch,
        settings=_settings(environment="dev"),
        mfa_methods=[],  # no MFA at all
    )

    result = await require_hardened_system_admin(
        request=_Request("203.0.113.9"),  # off any allowlist  # type: ignore[arg-type]
        principal=admin_principal,
        sessions=store,
    )
    assert result is admin_principal


@pytest.mark.asyncio
async def test_regular_tenant_user_never_reaches_the_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-admin is stopped by `require_system_admin` BEFORE the hardening
    gate ever runs — the gate composes that base check, so the MFA/allowlist
    controls never even touch a regular tenant user.

    We assert the composition directly: `require_system_admin` 403s a
    non-admin principal, which is what the router-level dependency chains
    first. The hardening predicates are admin-surface-only by construction.
    """
    from api_server.auth.deps import require_system_admin

    regular = AuthPrincipal(
        user_id=uuid4(),
        session_id=uuid4(),
        tenant_id=uuid4(),
        is_system_admin=False,
    )
    with pytest.raises(HTTPException) as excinfo:
        require_system_admin(principal=regular)
    assert excinfo.value.status_code == 403


def test_admin_router_wires_the_hardening_dependency() -> None:
    """Structural guard: the `/admin` router carries the hardening gate as a
    router-level dependency, so EVERY admin route is hardened without relying
    on each handler to opt in. A regression that drops the dependency (or the
    router) makes this fail."""
    from api_server.routers.admin import router

    dep_calls = {getattr(d.dependency, "__name__", "") for d in router.dependencies if d.dependency}
    assert "require_hardened_system_admin" in dep_calls
