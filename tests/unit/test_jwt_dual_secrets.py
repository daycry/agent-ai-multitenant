"""Signing keys are RINGS: one signs, all verify (prod-05 task_prod05_04, gap2-7).

The audit's finding was not "the JWT secret cannot be changed" — it can, in one
second, by editing an env var. It was that changing it is a **flag day**:

  * every human session 401s at once (24 h of logged-in users), and
  * every ``AGENTIC_INTERNAL_TOKEN`` already injected into a RUNNING
    agent-runtime container starts failing against ``/internal/agent/*``. That
    token is minted once at launch and cannot be refreshed, so the rotation kills
    plan executions mid-flight.

So the property under test is not "a ring decodes". It is:

  1. a token signed with a RETIRED key still verifies while that key is in the
     ring (otherwise the rotation is still a flag day);
  2. a token minted AFTER the rotation is signed with the HEAD key (otherwise
     step 3 — dropping the old key — silently invalidates everything minted
     during the window, and the test suite would never notice);
  3. dropping the key ENDS the acceptance (otherwise "retiring" a compromised
     key is theatre);
  4. the two rings — sessions and worker→api — stay cryptographically separate,
     which is the prod-09 invariant this change must not erode;
  5. expiry still wins over the ring. A ring that swallowed claim failures while
     hunting for another key would turn every expired session into "bad
     signature" and, worse, could let a later key mask an expiry the first had
     already proven.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from api_server.auth import internal_agent as agent_mod
from api_server.auth import jwt as jwt_mod
from api_server.auth.internal_agent import (
    InvalidAgentTokenError,
    decode_agent_token,
    mint_agent_token,
)
from api_server.auth.jwt import InvalidTokenError, decode_jwt, encode_jwt, verify_claims_any
from api_server.config import Settings

pytestmark = pytest.mark.unit

_OLD_SESSION = "retired-human-session-secret-0123456789ab"
_NEW_SESSION = "current-human-session-secret-abcdef012345"
_OLD_AGENT = "retired-worker-internal-secret-0123456789"
_NEW_AGENT = "current-worker-internal-secret-abcdef0123"


def _settings(**overrides: str) -> Settings:
    base: dict[str, str] = {
        "environment": "dev",
        "jwt_secret": _OLD_SESSION,
        "internal_token_secret": _OLD_AGENT,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _use(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    """Point BOTH modules at the same settings object.

    They call ``get_settings`` in their own namespace, and the agent-token half of
    the story only holds if the session half is configured too (the disjointness
    guard reads both rings)."""
    monkeypatch.setattr(jwt_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(agent_mod, "get_settings", lambda: settings)


def _b64(raw: bytes) -> bytes:
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


def _sign_raw(claims: dict[str, Any], secret: str) -> str:
    """Mint an HS256 token with the stdlib only — no JOSE library, so the
    assertions cannot be satisfied by a bug the implementation shares with its
    own library."""
    signing_input = (
        _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        + b"."
        + _b64(json.dumps(claims).encode())
    )
    signature = _b64(hmac.new(secret.encode(), signing_input, hashlib.sha256).digest())
    return (signing_input + b"." + signature).decode()


def _fresh_claims(**extra: Any) -> dict[str, Any]:
    now = datetime.now(tz=UTC)
    claims: dict[str, Any] = {
        "sub": str(uuid4()),
        "sid": str(uuid4()),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=1)).timestamp()),
    }
    claims.update(extra)
    return claims


# ---------------------------------------------------------------------------
# Human sessions
# ---------------------------------------------------------------------------
def test_a_session_minted_before_the_rotation_survives_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """Phase 1 of the runbook: new key at the head, old key kept in the tail."""
    _use(monkeypatch, _settings())
    before = encode_jwt(user_id=uuid4(), session_id=uuid4())

    _use(monkeypatch, _settings(jwt_secrets=f"{_NEW_SESSION},{_OLD_SESSION}"))
    assert decode_jwt(before)["sid"]


def test_a_session_minted_after_the_rotation_uses_the_head_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The half a naive implementation gets wrong. If ``encode_jwt`` kept signing
    with the tail key, every test above would still pass and phase 3 (drop the
    old key) would log out every user who signed in during the window."""
    _use(monkeypatch, _settings(jwt_secrets=f"{_NEW_SESSION},{_OLD_SESSION}"))
    token = encode_jwt(user_id=uuid4(), session_id=uuid4())

    # Verified against ONLY the head key, independently of the ring code.
    assert verify_claims_any(token, secrets=(_NEW_SESSION,), algorithm="HS256")["sid"]
    with pytest.raises(InvalidTokenError):
        verify_claims_any(token, secrets=(_OLD_SESSION,), algorithm="HS256")


def test_dropping_the_retired_key_ends_the_acceptance(monkeypatch: pytest.MonkeyPatch) -> None:
    """Phase 3. Without this assertion the ring would be a way to NEVER retire a
    compromised key, which is the opposite of the point."""
    _use(monkeypatch, _settings())
    before = encode_jwt(user_id=uuid4(), session_id=uuid4())

    _use(monkeypatch, _settings(jwt_secrets=_NEW_SESSION))
    with pytest.raises(InvalidTokenError):
        decode_jwt(before)


def test_a_token_signed_with_a_key_that_was_never_in_the_ring_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use(monkeypatch, _settings(jwt_secrets=f"{_NEW_SESSION},{_OLD_SESSION}"))
    with pytest.raises(InvalidTokenError):
        decode_jwt(_sign_raw(_fresh_claims(), "a-key-nobody-ever-configured-xxxxxxxxx"))


def test_expiry_is_still_enforced_by_every_key_in_the_ring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The regression a "try the next key" loop invites: an expired token signed
    by the TAIL key must be rejected as expired, not silently retried until the
    ring runs out (and never accepted)."""
    _use(monkeypatch, _settings(jwt_secrets=f"{_NEW_SESSION},{_OLD_SESSION}"))
    now = datetime.now(tz=UTC)
    stale = _sign_raw(
        {
            "sub": str(uuid4()),
            "sid": str(uuid4()),
            "iat": int((now - timedelta(hours=2)).timestamp()),
            "exp": int((now - timedelta(hours=1)).timestamp()),
        },
        _OLD_SESSION,
    )
    with pytest.raises(InvalidTokenError) as excinfo:
        decode_jwt(stale)
    # The DIAGNOSIS matters: the signature was valid, the token was expired.
    assert "signature" not in str(excinfo.value).lower()


def test_a_token_without_exp_is_rejected_even_when_the_tail_key_signed_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use(monkeypatch, _settings(jwt_secrets=f"{_NEW_SESSION},{_OLD_SESSION}"))
    claims = {
        "sub": str(uuid4()),
        "sid": str(uuid4()),
        "iat": int(datetime.now(tz=UTC).timestamp()),
    }
    with pytest.raises(InvalidTokenError):
        decode_jwt(_sign_raw(claims, _OLD_SESSION))


def test_an_empty_ring_never_accepts_a_token() -> None:
    """Fail-closed: "verify against nothing" must not mean "accept anything"."""
    with pytest.raises(InvalidTokenError):
        verify_claims_any(_sign_raw(_fresh_claims(), _OLD_SESSION), secrets=(), algorithm="HS256")


# ---------------------------------------------------------------------------
# Worker -> api agent tokens (the in-flight-execution half)
# ---------------------------------------------------------------------------
def test_an_agent_token_in_flight_survives_the_rotation(monkeypatch: pytest.MonkeyPatch) -> None:
    """The token was injected into a container that is still running: nothing can
    re-mint it, so the ring is the only thing standing between a rotation and a
    dead plan execution."""
    _use(monkeypatch, _settings())
    agent_id, tenant_id, task_id = uuid4(), uuid4(), uuid4()
    in_flight = mint_agent_token(agent_id=agent_id, tenant_id=tenant_id, task_id=task_id)

    _use(monkeypatch, _settings(internal_token_secrets=f"{_NEW_AGENT},{_OLD_AGENT}"))
    principal = decode_agent_token(in_flight)
    assert principal.agent_id == agent_id
    assert principal.tenant_id == tenant_id
    assert principal.task_id == task_id


def test_a_token_minted_after_the_rotation_uses_the_head_agent_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use(monkeypatch, _settings(internal_token_secrets=f"{_NEW_AGENT},{_OLD_AGENT}"))
    token = mint_agent_token(agent_id=uuid4(), tenant_id=uuid4())

    assert verify_claims_any(token, secrets=(_NEW_AGENT,), algorithm="HS256")["kind"] == "agent"
    with pytest.raises(InvalidTokenError):
        verify_claims_any(token, secrets=(_OLD_AGENT,), algorithm="HS256")


def test_dropping_the_retired_agent_key_ends_the_acceptance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use(monkeypatch, _settings())
    in_flight = mint_agent_token(agent_id=uuid4(), tenant_id=uuid4())

    _use(monkeypatch, _settings(internal_token_secrets=_NEW_AGENT))
    with pytest.raises(InvalidAgentTokenError):
        decode_agent_token(in_flight)


def test_a_human_session_is_still_not_an_agent_token_under_rings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The prod-09 invariant (secrets-9) must not erode. Widening BOTH verifiers
    to rings would be an escalation if the rings overlapped, so this asserts the
    separation end-to-end, not just via the config guard."""
    _use(
        monkeypatch,
        _settings(
            jwt_secrets=f"{_NEW_SESSION},{_OLD_SESSION}",
            internal_token_secrets=f"{_NEW_AGENT},{_OLD_AGENT}",
        ),
    )
    human = encode_jwt(user_id=uuid4(), session_id=uuid4(), is_system_admin=True)
    with pytest.raises(InvalidAgentTokenError):
        decode_agent_token(human)

    agent = mint_agent_token(agent_id=uuid4(), tenant_id=uuid4())
    with pytest.raises(InvalidTokenError):
        decode_jwt(agent)


def test_an_overlapping_pair_of_rings_is_refused_at_configuration_time() -> None:
    """The escalation the test above proves impossible is only impossible while
    the rings are disjoint — so the config must be what forbids the overlap."""
    with pytest.raises(ValueError, match="share NO key"):
        Settings(
            environment="staging",
            jwt_secret=_NEW_SESSION,
            jwt_secrets=f"{_NEW_SESSION},{_OLD_SESSION}",
            internal_token_secret=_NEW_AGENT,
            internal_token_secrets=f"{_NEW_AGENT},{_OLD_SESSION}",
            review_url_signing_secret="real-review-url-secret",
            sso_encryption_key="real-sso-key",
            notification_encryption_key="real-notification-key",
            incoming_webhook_encryption_key="real-webhook-key",
            minio_secret_key="real-minio-secret",
            minio_access_key="real-minio-access",
            database_url="postgresql+asyncpg://app:realpw@db/agentic_platform",
            admin_database_url="postgresql+asyncpg://svc:realpw@db/agentic_platform",
        )
