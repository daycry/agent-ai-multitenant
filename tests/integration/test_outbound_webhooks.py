"""Integration tests for the outbound-webhook channel (task_10_12).

The adapter (``notification_dispatcher.channels.webhook.WebhookAdapter``)
delivers a notification as a **signed JSON POST** to a tenant-configured target
URL: a ``POST <url>`` with an ``application/json`` body and three signing
headers — ``X-Signature`` (HMAC-SHA256 over ``timestamp.nonce.body``),
``X-Timestamp`` (Unix seconds, freshness) and ``X-Nonce`` (single-use). The
target receiver cannot be reached in tests (no network), so we MOCK the HTTP
transport with ``httpx.MockTransport`` — exactly the established mocked-external
pattern the Telegram / Slack / SMS adapters use — and assert the *security
properties a receiver relies on*, exercising the reusable
:mod:`notification_dispatcher.webhook_signing` sign/verify helpers (which the
Plan 13 inbound verifier will also use):

  * the adapter signs the exact body bytes it POSTs and stamps the three
    headers; the signature **verifies** with the right secret;
  * a **tampered** body / timestamp / nonce, or the **wrong secret**, fails
    verification (``bad_signature``);
  * a **stale** timestamp (outside the skew window) is rejected
    (``stale_timestamp``) — replay across time is bounded;
  * a **replayed** nonce (already seen within the window) is rejected
    (``replayed_nonce``) — replay within the window is bounded;
  * a success (2xx) → ``DeliveryResult(ok=True)``; a non-2xx →
    :class:`ChannelSendError` (the dispatcher logs ``failed`` + dead-letters,
    never auto-retries here);
  * the signing secret is the channel secret: read via
    :func:`notification_dispatcher.secrets.resolve_channel_secret` (Fernet
    ``secret_encrypted`` at rest → plaintext IN MEMORY), used ONLY to compute
    the HMAC, and NEVER present in the URL, the body, a header value, or the
    DB row.

No real network: every request is served by an injected ``httpx.MockTransport``
handler, rerouted via a tiny monkeypatch on the module's ``httpx.AsyncClient`` so
the adapter's own construction (timeout, signing, headers) runs untouched.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from notification_dispatcher.adapters import ChannelMessage, ChannelSendError
from notification_dispatcher.channels import webhook as webhook_mod
from notification_dispatcher.channels.webhook import WebhookAdapter
from notification_dispatcher.config import Settings
from notification_dispatcher.secrets import encrypt_secret, resolve_channel_secret
from notification_dispatcher.webhook_signing import (
    NONCE_HEADER,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    current_timestamp,
    generate_nonce,
    sign_webhook,
    verify_webhook,
)

pytestmark = pytest.mark.integration

# The per-channel signing secret. NOT a real secret — a test sentinel. It is the
# channel SECRET (never stored plaintext, never sent over the wire).
_SIGNING_SECRET = "FAKE-webhook-signing-secret-not-a-real-one-000000"
_WRONG_SECRET = "FAKE-some-other-secret-the-attacker-guessed-000000"
_TARGET_URL = "https://receiver.example.com/hooks/agentic"
# The skew window the receiver enforces (seconds).
_MAX_SKEW_S = 300


def _test_settings(**overrides: Any) -> Settings:
    """Dispatcher Settings with a known Fernet key + stable webhook tunables.

    ``environment='dev'`` keeps the dev-default-secret guard off; the encryption
    key is explicit so encrypt/decrypt round-trips in-process.
    """
    base: dict[str, Any] = {
        "environment": "dev",
        "notification_encryption_key": "unit-test-webhook-key-not-a-real-secret",
        "webhook_signature_max_skew_s": _MAX_SKEW_S,
    }
    base.update(overrides)
    return Settings(**base)


@dataclass
class _FakeChannel:
    """Duck-typed stand-in for the ``NotificationChannel`` ORM row.

    ``resolve_channel_secret`` only reads ``secret_ref`` / ``secret_encrypted``.
    The plaintext signing secret is NEVER stored here — only its ciphertext.
    """

    secret_ref: str | None = None
    secret_encrypted: str | None = None


def _capturing_transport(
    requests: list[httpx.Request], *, response: httpx.Response
) -> httpx.MockTransport:
    """A MockTransport that records every request and replays ``response``."""

    def _handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return response

    return httpx.MockTransport(_handler)


@pytest.fixture()
def patch_transport(monkeypatch: pytest.MonkeyPatch):
    """Reroute the adapter's httpx.AsyncClient onto an injected transport.

    The adapter constructs ``httpx.AsyncClient(timeout=...)`` itself; we wrap
    that constructor so the test's MockTransport is supplied without changing
    the adapter's call site (its signing + header construction run untouched).
    Returns a setter the test uses to install the canned response + collect the
    captured requests.
    """
    state: dict[str, Any] = {"requests": [], "response": None}
    real_async_client = httpx.AsyncClient

    def _factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = _capturing_transport(state["requests"], response=state["response"])
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(webhook_mod.httpx, "AsyncClient", _factory)

    def _install(response: httpx.Response) -> list[httpx.Request]:
        state["response"] = response
        return state["requests"]

    return _install


def _message(**overrides: Any) -> ChannelMessage:
    """Build a baseline webhook ChannelMessage; override per test."""
    base: dict[str, Any] = {
        "channel_type": "webhook",
        "target": _TARGET_URL,
        "body": "Task X is blocked. Reason: timeout.",
        "secret": _SIGNING_SECRET,
        "structured": {
            "event_type": "task_blocked",
            "severity": "warning",
            "subject": "Task blocked",
        },
        "config": {},
    }
    base.update(overrides)
    return ChannelMessage(**base)


# ===========================================================================
# Pure crypto core — sign/verify round-trip + tamper / wrong-secret rejection.
# ===========================================================================
def test_sign_verify_round_trip() -> None:
    """A signature produced by sign_webhook verifies with the same secret."""
    body = b'{"body":"hello"}'
    ts = current_timestamp()
    nonce = generate_nonce()
    sig = sign_webhook(_SIGNING_SECRET, body, ts, nonce)

    result = verify_webhook(
        _SIGNING_SECRET,
        body,
        signature=sig,
        timestamp=ts,
        nonce=nonce,
        max_skew_s=_MAX_SKEW_S,
        now=ts,
    )
    assert result.ok is True
    assert result.reason == "ok"


def test_wrong_secret_fails_verification() -> None:
    """A signature does NOT verify under a different secret."""
    body = b'{"body":"hello"}'
    ts = current_timestamp()
    nonce = generate_nonce()
    sig = sign_webhook(_SIGNING_SECRET, body, ts, nonce)

    result = verify_webhook(
        _WRONG_SECRET,
        body,
        signature=sig,
        timestamp=ts,
        nonce=nonce,
        max_skew_s=_MAX_SKEW_S,
        now=ts,
    )
    assert result.ok is False
    assert result.reason == "bad_signature"


@pytest.mark.parametrize("tamper", ["body", "timestamp", "nonce", "signature"])
def test_tampered_request_fails_verification(tamper: str) -> None:
    """Tampering with the body, timestamp, nonce, or signature breaks the MAC."""
    body = b'{"body":"hello"}'
    ts = current_timestamp()
    nonce = generate_nonce()
    sig = sign_webhook(_SIGNING_SECRET, body, ts, nonce)

    kwargs: dict[str, Any] = {
        "signature": sig,
        "timestamp": ts,
        "nonce": nonce,
        "max_skew_s": _MAX_SKEW_S,
        "now": ts,
    }
    verify_body = body
    if tamper == "body":
        verify_body = b'{"body":"hello!"}'
    elif tamper == "timestamp":
        # Still inside the skew window, so this is a signature failure (the ts is
        # part of the signed material), not a freshness failure.
        kwargs["timestamp"] = ts + 1
    elif tamper == "nonce":
        kwargs["nonce"] = generate_nonce()
    elif tamper == "signature":
        kwargs["signature"] = "deadbeef" * 8

    result = verify_webhook(_SIGNING_SECRET, verify_body, **kwargs)
    assert result.ok is False
    assert result.reason == "bad_signature"


# ===========================================================================
# Anti-replay — stale timestamp + replayed nonce are rejected.
# ===========================================================================
def test_stale_timestamp_is_rejected() -> None:
    """A timestamp older than the skew window is rejected (replay across time)."""
    body = b'{"body":"hello"}'
    ts = current_timestamp()
    nonce = generate_nonce()
    sig = sign_webhook(_SIGNING_SECRET, body, ts, nonce)

    # Verify "now" is well past the freshness window -> stale.
    result = verify_webhook(
        _SIGNING_SECRET,
        body,
        signature=sig,
        timestamp=ts,
        nonce=nonce,
        max_skew_s=_MAX_SKEW_S,
        now=ts + _MAX_SKEW_S + 1,
    )
    assert result.ok is False
    assert result.reason == "stale_timestamp"


def test_far_future_timestamp_is_rejected() -> None:
    """A timestamp too far in the future is rejected too (clock-skew bound)."""
    body = b'{"body":"hello"}'
    ts = current_timestamp()
    nonce = generate_nonce()
    sig = sign_webhook(_SIGNING_SECRET, body, ts, nonce)

    result = verify_webhook(
        _SIGNING_SECRET,
        body,
        signature=sig,
        timestamp=ts,
        nonce=nonce,
        max_skew_s=_MAX_SKEW_S,
        now=ts - _MAX_SKEW_S - 1,
    )
    assert result.ok is False
    assert result.reason == "stale_timestamp"


def test_replayed_nonce_is_rejected() -> None:
    """The same nonce, accepted once, is rejected on a second presentation.

    Models the receiver's TTL nonce store via a simple seen-set: the first
    verification accepts + records the nonce, the second (a replay inside the
    freshness window with an otherwise-valid signature) is rejected.
    """
    body = b'{"body":"hello"}'
    ts = current_timestamp()
    nonce = generate_nonce()
    sig = sign_webhook(_SIGNING_SECRET, body, ts, nonce)

    seen: set[str] = set()

    def seen_nonce(n: str) -> bool:
        return n in seen

    common: dict[str, Any] = {
        "signature": sig,
        "timestamp": ts,
        "nonce": nonce,
        "max_skew_s": _MAX_SKEW_S,
        "now": ts,
        "seen_nonce": seen_nonce,
    }

    first = verify_webhook(_SIGNING_SECRET, body, **common)
    assert first.ok is True
    # The receiver records the nonce as seen only AFTER a successful verify.
    seen.add(nonce)

    second = verify_webhook(_SIGNING_SECRET, body, **common)
    assert second.ok is False
    assert second.reason == "replayed_nonce"


# ===========================================================================
# Adapter happy path — signs the exact body it POSTs; the receiver verifies it.
# ===========================================================================
@pytest.mark.asyncio
async def test_send_signs_body_and_stamps_headers(patch_transport) -> None:
    settings = _test_settings()
    # The signing secret flows from the encrypted channel secret through
    # resolve_channel_secret (Fernet at rest -> plaintext in memory).
    channel = _FakeChannel(secret_encrypted=encrypt_secret(_SIGNING_SECRET, settings))
    secret = resolve_channel_secret(channel, settings)
    assert secret == _SIGNING_SECRET

    requests = patch_transport(httpx.Response(200, json={"ok": True}))

    adapter = WebhookAdapter(settings=settings)
    result = await adapter.send(_message(secret=secret))

    assert result.ok is True

    # Exactly one POST, to the configured target URL with a JSON body.
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == _TARGET_URL
    assert request.headers["content-type"] == "application/json"

    # The three signing headers are present.
    signature = request.headers[SIGNATURE_HEADER.lower()]
    timestamp = request.headers[TIMESTAMP_HEADER.lower()]
    nonce = request.headers[NONCE_HEADER.lower()]
    assert signature and timestamp and nonce

    # The receiver re-derives the MAC over the EXACT bytes it received and the
    # adapter's signature verifies — the round-trip the receiver runs.
    body_bytes = request.content
    verification = verify_webhook(
        _SIGNING_SECRET,
        body_bytes,
        signature=signature,
        timestamp=timestamp,
        nonce=nonce,
        max_skew_s=_MAX_SKEW_S,
        now=int(timestamp),
    )
    assert verification.ok is True

    # The signing secret NEVER appears in the URL, the body, or any header.
    assert _SIGNING_SECRET not in str(request.url)
    assert _SIGNING_SECRET not in body_bytes.decode("utf-8")
    assert all(_SIGNING_SECRET not in v for v in request.headers.values())


@pytest.mark.asyncio
async def test_receiver_rejects_when_body_tampered_in_flight(patch_transport) -> None:
    """If the captured body is altered, the adapter's signature no longer
    verifies — the integrity guarantee the receiver relies on."""
    settings = _test_settings()
    requests = patch_transport(httpx.Response(200))

    adapter = WebhookAdapter(settings=settings)
    await adapter.send(_message())

    request = requests[0]
    tampered_body = request.content + b" "  # one extra byte
    verification = verify_webhook(
        _SIGNING_SECRET,
        tampered_body,
        signature=request.headers[SIGNATURE_HEADER.lower()],
        timestamp=request.headers[TIMESTAMP_HEADER.lower()],
        nonce=request.headers[NONCE_HEADER.lower()],
        max_skew_s=_MAX_SKEW_S,
        now=int(request.headers[TIMESTAMP_HEADER.lower()]),
    )
    assert verification.ok is False
    assert verification.reason == "bad_signature"


@pytest.mark.asyncio
async def test_each_send_uses_a_fresh_nonce(patch_transport) -> None:
    """Two sends produce two distinct nonces (single-use guarantee)."""
    settings = _test_settings()
    requests = patch_transport(httpx.Response(202))

    adapter = WebhookAdapter(settings=settings)
    await adapter.send(_message())
    await adapter.send(_message())

    n1 = requests[0].headers[NONCE_HEADER.lower()]
    n2 = requests[1].headers[NONCE_HEADER.lower()]
    assert n1 != n2


@pytest.mark.asyncio
async def test_target_url_from_config_when_message_omits_it(patch_transport) -> None:
    """A channel without a message target falls back to config.url."""
    settings = _test_settings()
    requests = patch_transport(httpx.Response(200))

    adapter = WebhookAdapter(settings=settings)
    await adapter.send(_message(target=None, config={"url": _TARGET_URL}))

    assert str(requests[0].url) == _TARGET_URL


# ===========================================================================
# Adapter failure path — non-2xx + transport error map to ChannelSendError.
# ===========================================================================
@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 422, 500, 503])
async def test_non_2xx_raises_channel_send_error(patch_transport, status_code: int) -> None:
    """A non-2xx receiver response is a terminal ChannelSendError — and the
    signing secret never leaks into it."""
    settings = _test_settings()
    patch_transport(httpx.Response(status_code, text="nope"))

    adapter = WebhookAdapter(settings=settings)
    with pytest.raises(ChannelSendError) as excinfo:
        await adapter.send(_message())

    err = str(excinfo.value)
    assert str(status_code) in err
    assert _SIGNING_SECRET not in err


@pytest.mark.asyncio
async def test_transport_error_raises_channel_send_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A connection-level failure (no network) is a terminal ChannelSendError,
    and the signing secret never leaks into the surfaced message."""
    settings = _test_settings()

    def _boom_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    real_async_client = httpx.AsyncClient

    def _factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(_boom_handler)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(webhook_mod.httpx, "AsyncClient", _factory)

    adapter = WebhookAdapter(settings=settings)
    with pytest.raises(ChannelSendError) as excinfo:
        await adapter.send(_message())
    assert _SIGNING_SECRET not in str(excinfo.value)


# ===========================================================================
# Misconfiguration — missing secret / target is clean (no request made).
# ===========================================================================
@pytest.mark.asyncio
async def test_missing_signing_secret_raises_before_any_request(patch_transport) -> None:
    settings = _test_settings()
    requests = patch_transport(httpx.Response(200))

    adapter = WebhookAdapter(settings=settings)
    with pytest.raises(ChannelSendError):
        await adapter.send(_message(secret=None))
    assert requests == []


@pytest.mark.asyncio
async def test_missing_target_raises_before_any_request(patch_transport) -> None:
    settings = _test_settings()
    requests = patch_transport(httpx.Response(200))

    adapter = WebhookAdapter(settings=settings)
    with pytest.raises(ChannelSendError):
        await adapter.send(_message(target=None, config={}))
    assert requests == []


# ===========================================================================
# Secret handling — signing secret resolved via resolve_channel_secret, never plaintext.
# ===========================================================================
def test_signing_secret_resolved_via_resolve_channel_secret() -> None:
    """The DB row holds only Fernet ciphertext; the plaintext signing secret is
    produced in memory by resolve_channel_secret — never stored clear."""
    settings = _test_settings()
    channel = _FakeChannel(secret_encrypted=encrypt_secret(_SIGNING_SECRET, settings))

    # The at-rest value is ciphertext, NOT the secret.
    assert channel.secret_encrypted is not None
    assert _SIGNING_SECRET not in channel.secret_encrypted

    resolved = resolve_channel_secret(channel, settings)
    assert resolved == _SIGNING_SECRET


# ===========================================================================
# Registration — importing the channels package wires the adapter in.
# ===========================================================================
def test_adapter_registered_under_webhook_channel_type() -> None:
    from notification_dispatcher.adapters import get_adapter

    # Side-effect of importing notification_dispatcher.channels.webhook above.
    adapter = get_adapter("webhook")
    assert adapter is not None
    assert adapter.channel_type == "webhook"
