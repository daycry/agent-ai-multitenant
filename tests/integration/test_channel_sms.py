"""Integration tests for the SMS channel adapter via Twilio (task_10_11).

The adapter (``notification_dispatcher.channels.sms.SmsAdapter``) delivers a
notification to a phone number through the **Twilio Messages REST API** — a
``POST {base}/{version}/Accounts/{AccountSid}/Messages.json`` with an
``application/x-www-form-urlencoded`` body (``To`` / ``From`` / ``Body``) and
HTTP Basic auth ``AccountSid:AuthToken``. Twilio cannot be reached in tests (no
account, no network), so we MOCK the HTTP transport with ``httpx.MockTransport``
— exactly the established mocked-external-dependency pattern the Telegram /
Slack / WhatsApp adapters use — and assert the *behaviour the dispatcher relies
on*:

  * the adapter builds a well-formed, form-encoded Messages payload (``To`` =
    recipient, ``From`` = configured sender, ``Body`` = the rendered plain-text
    message) and the HTTP **Basic auth** header is built from AccountSid +
    AuthToken — SMS is plain text, no markup / structured payload;
  * a successful response (HTTP 201 with ``sid``) returns
    ``DeliveryResult(ok=True)`` carrying the Twilio message SID as the provider
    id;
  * a Twilio error — a non-2xx (HTTP 400 / 401 / a 21xxx error) — raises
    :class:`ChannelSendError`, so the dispatcher logs ``failed`` +
    dead-letters (it never auto-retries here);
  * the AuthToken is the channel secret: it is read via
    :func:`notification_dispatcher.secrets.resolve_channel_secret` (Fernet
    ``secret_encrypted`` at rest → plaintext IN MEMORY), placed only in the
    Basic-auth ``Authorization`` header, and NEVER appears in the URL or the
    form body — and the DB side never holds the plaintext token.

No real network: every request is served by an injected ``httpx.MockTransport``
handler, rerouted via a tiny monkeypatch on the module's ``httpx.AsyncClient``
so the adapter's own construction (timeout, url, Basic auth) is exercised
untouched.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from notification_dispatcher.adapters import ChannelMessage, ChannelSendError
from notification_dispatcher.channels import sms as sms_mod
from notification_dispatcher.channels.sms import SmsAdapter
from notification_dispatcher.config import Settings
from notification_dispatcher.secrets import encrypt_secret, resolve_channel_secret

pytestmark = pytest.mark.integration

# A fake Twilio AuthToken. NOT a real token — a Twilio AuthToken is a 32-char
# hex string; this is a test sentinel. It is the channel SECRET.
_AUTH_TOKEN = "FAKE-this-is-not-a-real-twilio-auth-token-00000000"
# The Twilio AccountSid (a non-secret account id, "AC" + 32 hex) + sender + recipient.
_ACCOUNT_SID = "ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
_FROM = "+15557654321"
_RECIPIENT = "+15551234567"


def _test_settings() -> Settings:
    """Dispatcher Settings with a known Fernet key + a stable Twilio host/version.

    ``environment='dev'`` keeps the dev-default-secret guard off; the encryption
    key is explicit so encrypt/decrypt round-trips in-process.
    """
    return Settings(
        environment="dev",
        notification_encryption_key="unit-test-sms-key-not-a-real-secret",
        twilio_api_base_url="https://api.twilio.com",
        twilio_api_version="2010-04-01",
    )


@dataclass
class _FakeChannel:
    """Duck-typed stand-in for the ``NotificationChannel`` ORM row.

    ``resolve_channel_secret`` only reads ``secret_ref`` / ``secret_encrypted``.
    The plaintext AuthToken is NEVER stored here — only its Fernet ciphertext.
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
    the adapter's call site (its Basic-auth + form construction run untouched).
    Returns a setter the test uses to install the canned response + collect the
    captured requests.
    """
    state: dict[str, Any] = {"requests": [], "response": None}
    real_async_client = httpx.AsyncClient

    def _factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = _capturing_transport(state["requests"], response=state["response"])
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(sms_mod.httpx, "AsyncClient", _factory)

    def _install(response: httpx.Response) -> list[httpx.Request]:
        state["response"] = response
        return state["requests"]

    return _install


def _message(**overrides: Any) -> ChannelMessage:
    """Build a baseline SMS ChannelMessage; override per test."""
    base: dict[str, Any] = {
        "channel_type": "sms",
        "target": _RECIPIENT,
        "body": "Task X is blocked. Reason: timeout.",
        "secret": _AUTH_TOKEN,
        "structured": {"event_type": "task_blocked", "severity": "warning"},
        "config": {"account_sid": _ACCOUNT_SID, "from": _FROM},
    }
    base.update(overrides)
    return ChannelMessage(**base)


def _form(request: httpx.Request) -> dict[str, str]:
    """Parse the captured form-encoded body into a flat dict."""
    parsed = parse_qs(request.content.decode("utf-8"))
    return {k: v[0] for k, v in parsed.items()}


# ===========================================================================
# Secret handling — AuthToken resolved via resolve_channel_secret, never plaintext.
# ===========================================================================
def test_auth_token_resolved_via_resolve_channel_secret() -> None:
    """The DB row holds only Fernet ciphertext; the plaintext AuthToken is
    produced in memory by resolve_channel_secret — never stored clear."""
    settings = _test_settings()
    channel = _FakeChannel(secret_encrypted=encrypt_secret(_AUTH_TOKEN, settings))

    # The at-rest value is ciphertext, NOT the token.
    assert channel.secret_encrypted is not None
    assert _AUTH_TOKEN not in channel.secret_encrypted

    resolved = resolve_channel_secret(channel, settings)
    assert resolved == _AUTH_TOKEN


# ===========================================================================
# Happy path — valid form payload + Basic auth + success -> DeliveryResult sent.
# ===========================================================================
@pytest.mark.asyncio
async def test_send_builds_form_payload_basic_auth_and_reports_sent(patch_transport) -> None:
    settings = _test_settings()
    # The AuthToken flows from the encrypted channel secret through resolve_channel_secret.
    channel = _FakeChannel(secret_encrypted=encrypt_secret(_AUTH_TOKEN, settings))
    token = resolve_channel_secret(channel, settings)

    # Twilio success: 201 Created with the accepted message's sid.
    sid = "SM0123456789abcdef0123456789abcdef"
    requests = patch_transport(
        httpx.Response(
            201,
            json={"sid": sid, "status": "queued", "to": _RECIPIENT, "from": _FROM},
        )
    )

    adapter = SmsAdapter(settings=settings)
    result = await adapter.send(_message(secret=token))

    # Success -> DeliveryResult sent, carrying the Twilio SID as the provider id.
    assert result.ok is True
    assert result.provider_message_id == sid
    assert result.error is None

    # Exactly one POST, to the Messages.json endpoint for the account.
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == (
        f"https://api.twilio.com/2010-04-01/Accounts/{_ACCOUNT_SID}/Messages.json"
    )

    # The form body carries To / From / Body, x-www-form-urlencoded.
    assert request.headers["content-type"].startswith("application/x-www-form-urlencoded")
    form = _form(request)
    assert form["To"] == _RECIPIENT
    assert form["From"] == _FROM
    assert form["Body"] == "Task X is blocked. Reason: timeout."

    # HTTP Basic auth header built from AccountSid:AuthToken.
    expected = base64.b64encode(f"{_ACCOUNT_SID}:{token}".encode()).decode("ascii")
    assert request.headers["Authorization"] == f"Basic {expected}"

    # The AuthToken NEVER appears in the URL or the form body — only the
    # (base64-wrapped) Basic-auth header carries it.
    assert _AUTH_TOKEN not in str(request.url)
    assert token not in str(request.url)
    assert _AUTH_TOKEN not in request.content.decode("utf-8")
    assert token not in request.content.decode("utf-8")


@pytest.mark.asyncio
async def test_send_uses_default_from_when_config_omits_it(patch_transport) -> None:
    """A channel without config.from falls back to NOTIFY_SMS_DEFAULT_FROM."""
    settings = Settings(
        environment="dev",
        notification_encryption_key="unit-test-sms-key-not-a-real-secret",
        sms_default_from="+15550009999",
    )
    requests = patch_transport(httpx.Response(201, json={"sid": "SMdefaultfrom"}))

    adapter = SmsAdapter(settings=settings)
    await adapter.send(_message(config={"account_sid": _ACCOUNT_SID}))

    form = _form(requests[0])
    assert form["From"] == "+15550009999"


@pytest.mark.asyncio
async def test_long_body_is_truncated_to_cap(patch_transport) -> None:
    """An over-long rendered body is trimmed to sms_max_body_len, not 400'd."""
    settings = Settings(
        environment="dev",
        notification_encryption_key="unit-test-sms-key-not-a-real-secret",
        sms_max_body_len=20,
    )
    requests = patch_transport(httpx.Response(201, json={"sid": "SMtrunc"}))

    adapter = SmsAdapter(settings=settings)
    await adapter.send(_message(body="x" * 100))

    form = _form(requests[0])
    assert len(form["Body"]) == 20
    assert form["Body"].endswith("…")


# ===========================================================================
# Failure path — a Twilio error maps to ChannelSendError.
# ===========================================================================
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "error_body"),
    [
        (400, {"code": 21211, "message": "Invalid 'To' Phone Number", "status": 400}),
        (401, {"code": 20003, "message": "Authentication Error", "status": 401}),
        (400, {"code": 21606, "message": "The 'From' number is not a valid", "status": 400}),
        (500, {"code": 20500, "message": "Internal Server Error", "status": 500}),
    ],
)
async def test_twilio_error_raises_channel_send_error(
    patch_transport, status_code: int, error_body: dict[str, Any]
) -> None:
    """A 400 / 401 / a 21xxx Twilio error is a terminal ChannelSendError — and
    neither the AuthToken nor the URL leaks into it."""
    settings = _test_settings()
    patch_transport(httpx.Response(status_code, json=error_body))

    adapter = SmsAdapter(settings=settings)
    with pytest.raises(ChannelSendError) as excinfo:
        await adapter.send(_message())

    err = str(excinfo.value)
    # The Twilio error code is surfaced (in code= or the message).
    assert str(error_body["code"]) in err
    assert _AUTH_TOKEN not in err


@pytest.mark.asyncio
async def test_transport_error_raises_channel_send_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A connection-level failure (no network) is a terminal ChannelSendError,
    and the AuthToken never leaks into the surfaced message."""
    settings = _test_settings()

    def _boom_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    real_async_client = httpx.AsyncClient

    def _factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(_boom_handler)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(sms_mod.httpx, "AsyncClient", _factory)

    adapter = SmsAdapter(settings=settings)
    with pytest.raises(ChannelSendError) as excinfo:
        await adapter.send(_message())
    assert _AUTH_TOKEN not in str(excinfo.value)


# ===========================================================================
# Misconfiguration — missing token / account_sid / sender / recipient is clean.
# ===========================================================================
@pytest.mark.asyncio
async def test_missing_token_raises_before_any_request(patch_transport) -> None:
    settings = _test_settings()
    requests = patch_transport(httpx.Response(201, json={"sid": "x"}))

    adapter = SmsAdapter(settings=settings)
    with pytest.raises(ChannelSendError):
        await adapter.send(_message(secret=None))
    assert requests == []


@pytest.mark.asyncio
async def test_missing_account_sid_raises_before_any_request(patch_transport) -> None:
    settings = _test_settings()
    requests = patch_transport(httpx.Response(201, json={"sid": "x"}))

    adapter = SmsAdapter(settings=settings)
    with pytest.raises(ChannelSendError):
        await adapter.send(_message(config={"from": _FROM}))
    assert requests == []


@pytest.mark.asyncio
async def test_missing_sender_raises_before_any_request(patch_transport) -> None:
    settings = _test_settings()  # sms_default_from defaults to "" -> no fallback
    requests = patch_transport(httpx.Response(201, json={"sid": "x"}))

    adapter = SmsAdapter(settings=settings)
    with pytest.raises(ChannelSendError):
        await adapter.send(_message(config={"account_sid": _ACCOUNT_SID}))
    assert requests == []


@pytest.mark.asyncio
async def test_missing_recipient_raises_before_any_request(patch_transport) -> None:
    settings = _test_settings()
    requests = patch_transport(httpx.Response(201, json={"sid": "x"}))

    adapter = SmsAdapter(settings=settings)
    with pytest.raises(ChannelSendError):
        await adapter.send(_message(target=None))
    assert requests == []


# ===========================================================================
# Registration — importing the channels package wires the adapter in.
# ===========================================================================
def test_adapter_registered_under_sms_channel_type() -> None:
    from notification_dispatcher.adapters import get_adapter

    # Side-effect of importing notification_dispatcher.channels.sms above.
    adapter = get_adapter("sms")
    assert adapter is not None
    assert adapter.channel_type == "sms"
