"""Worker->api tokens live in their own signing domain (prod-09 task_prod09_03).

Finding secrets-9: ``mint_agent_token`` signed the ``AGENTIC_INTERNAL_TOKEN``
with ``settings.jwt_secret`` — the SAME HMAC key that signs human sessions. The
workers container legitimately holds that key (it mints a token per agent run),
so compromising a worker meant being able to forge a **System-Admin session** for
any user id: mint ``{"sub": <victim>, "sid": <any>, "sys": true}`` and the
api-server's ``decode_jwt`` would accept it.

The ``kind=agent`` claim was the only thing separating the two families, and a
claim is a discipline control: it protects only for as long as every present and
future verifier remembers to check it. Two keys is a structural control.

These tests assert BOTH directions of the isolation, because only having both
makes it real:

  * a token minted for the sandbox does NOT verify under ``jwt_secret``
    (so the agent channel cannot be replayed as a session), and
  * a token signed with ``internal_token_secret`` is NOT accepted by the human
    JWT validator (so a compromised worker cannot forge a session).

Pure unit tests: both code paths only need ``get_settings``, which is patched.
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
from api_server.auth import internal_agent as internal_agent_mod
from api_server.auth import jwt as jwt_mod
from api_server.auth.internal_agent import (
    InvalidAgentTokenError,
    decode_agent_token,
    mint_agent_token,
)
from api_server.auth.jwt import InvalidTokenError, decode_jwt, encode_jwt
from api_server.config import Settings

pytestmark = pytest.mark.unit

_JWT_SECRET = "human-session-signing-key-0123456789abcd"
_INTERNAL_SECRET = "worker-internal-signing-key-0123456789ab"


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "environment": "dev",
        "jwt_secret": _JWT_SECRET,
        "internal_token_secret": _INTERNAL_SECRET,
    }
    base.update(overrides)
    return Settings(**base)


def _b64(raw: bytes) -> bytes:
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


def _sign_hs256(claims: dict[str, Any], secret: str) -> str:
    """Mint an HS256 JWT with the stdlib only.

    These tests are the signature-level proof that the two token families live in
    different key domains, so they must not lean on the same JOSE library the
    code under test uses (prod-09 task_prod09_17 unified it on `joserfc`; before
    that this file imported `python-jose`). hmac + base64url is the wire format
    itself — it cannot agree with a bug in the implementation.
    """
    signing_input = (
        _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        + b"."
        + _b64(json.dumps(claims).encode())
    )
    signature = _b64(hmac.new(secret.encode(), signing_input, hashlib.sha256).digest())
    return (signing_input + b"." + signature).decode()


def _verify_hs256(token: str, secret: str) -> dict[str, Any]:
    """Return the claims iff the HMAC checks out; raise ``AssertionError`` if not."""
    header_b64, payload_b64, signature_b64 = token.split(".")
    signing_input = f"{header_b64}.{payload_b64}".encode()
    expected = _b64(hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()).decode()
    assert hmac.compare_digest(expected, signature_b64), "HMAC does not verify under this key"
    padded = payload_b64 + "=" * (-len(payload_b64) % 4)
    claims: dict[str, Any] = json.loads(base64.urlsafe_b64decode(padded))
    return claims


@pytest.fixture()
def separated(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Patch ``get_settings`` in BOTH signing modules to the same Settings."""
    settings = _settings()
    monkeypatch.setattr(internal_agent_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(jwt_mod, "get_settings", lambda: settings)
    return settings


# ---------------------------------------------------------------------------
# The two keys are actually different (guard against a copy-paste default)
# ---------------------------------------------------------------------------
def test_the_defaults_are_two_distinct_secrets() -> None:
    """Even the dev defaults differ — otherwise dev would keep exercising the
    shared-key path and the separation would only exist on paper."""
    defaults = Settings(environment="dev")
    assert (
        defaults.internal_token_secret.get_secret_value() != defaults.jwt_secret.get_secret_value()
    )


# ---------------------------------------------------------------------------
# Direction 1: an agent token is not a session
# ---------------------------------------------------------------------------
def test_agent_token_is_signed_with_the_internal_secret(separated: Settings) -> None:
    """Signature-level proof, not just "it round-trips": verify the minted token
    under each key explicitly."""
    token = mint_agent_token(agent_id=uuid4(), tenant_id=uuid4())

    # Verifies under the internal secret...
    claims = _verify_hs256(token, _INTERNAL_SECRET)
    assert claims["kind"] == "agent"

    # ...and NOT under the session secret. This is the assertion that would have
    # failed before task_prod09_03.
    with pytest.raises(AssertionError, match="does not verify"):
        _verify_hs256(token, _JWT_SECRET)


def test_agent_token_is_rejected_by_the_human_jwt_validator(separated: Settings) -> None:
    """An agent token replayed against a human-authenticated endpoint fails at
    the signature — it can never become a session."""
    token = mint_agent_token(agent_id=uuid4(), tenant_id=uuid4())
    with pytest.raises(InvalidTokenError):
        decode_jwt(token)


def test_agent_token_still_round_trips(separated: Settings) -> None:
    """The legitimate path keeps working (the change must not break the sandbox)."""
    agent_id, tenant_id, task_id = uuid4(), uuid4(), uuid4()
    principal = decode_agent_token(
        mint_agent_token(agent_id=agent_id, tenant_id=tenant_id, task_id=task_id)
    )
    assert principal.agent_id == agent_id
    assert principal.tenant_id == tenant_id
    assert principal.task_id == task_id


# ---------------------------------------------------------------------------
# Direction 2: a compromised worker cannot forge a session
# ---------------------------------------------------------------------------
def test_a_worker_cannot_forge_a_system_admin_session(separated: Settings) -> None:
    """THE finding, spelled out: with only the internal secret in hand, minting a
    session-shaped token with ``sys: true`` is rejected by ``decode_jwt``.

    Before the split this exact payload — signed with the key the worker holds —
    decoded cleanly and, once paired with any live ``sid``, was a cross-tenant
    System-Admin session.
    """
    now = datetime.now(tz=UTC)
    forged = _sign_hs256(
        {
            "sub": str(uuid4()),
            "sid": str(uuid4()),
            "sys": True,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=1)).timestamp()),
        },
        _INTERNAL_SECRET,  # all a compromised worker has
    )
    with pytest.raises(InvalidTokenError):
        decode_jwt(forged)


def test_a_human_jwt_is_rejected_by_the_agent_validator(separated: Settings) -> None:
    """The mirror image: a real user session presented to
    ``/internal/agent/*`` fails at the signature, before the ``kind`` check."""
    session_token = encode_jwt(user_id=uuid4(), session_id=uuid4(), is_system_admin=True)
    with pytest.raises(InvalidAgentTokenError):
        decode_agent_token(session_token)


def test_human_jwt_still_round_trips(separated: Settings) -> None:
    """And the human path is untouched."""
    user_id, session_id = uuid4(), uuid4()
    claims = decode_jwt(encode_jwt(user_id=user_id, session_id=session_id))
    assert claims["sub"] == str(user_id)
    assert claims["sid"] == str(session_id)


# ---------------------------------------------------------------------------
# Regression tripwire: nothing may reintroduce jwt_secret into this module
# ---------------------------------------------------------------------------
def test_internal_agent_module_never_reads_jwt_secret() -> None:
    """Static tripwire on the mint/verify module.

    The failure mode is a one-word edit (``internal_token_secret`` ->
    ``jwt_secret``) that no behavioural test catches unless it happens to check
    the signature under both keys. The two tests above do exactly that, so this
    is belt and braces — and it is the assertion that names the invariant for
    the next reader.
    """
    import inspect

    source = inspect.getsource(internal_agent_mod)
    # Only the prose (docstring/comments) may mention jwt_secret; no code may
    # read it. Strip the obvious comment lines and look for the attribute access.
    code_lines = [
        line
        for line in source.splitlines()
        if not line.lstrip().startswith("#") and "``jwt_secret``" not in line
    ]
    offenders = [line.strip() for line in code_lines if "settings.jwt_secret" in line]
    assert not offenders, f"internal agent tokens must not use jwt_secret: {offenders}"
    # The guard must be able to fail: the module MUST read the dedicated secret.
    assert "settings.internal_token_secret" in source
