"""Integration tests for the Email channel adapter (Plan 10 task_10_06).

The adapter (``notification_dispatcher.channels.email.EmailAdapter``)
delivers a rendered :class:`ChannelMessage` as an email over two paths:

  * **SMTP (primary)** via ``aiosmtplib`` — no real SMTP server exists in
    tests (no host, no creds, no network), so we MOCK ``aiosmtplib.send``
    (monkeypatch) and capture the :class:`email.message.EmailMessage` the
    adapter built + the connection kwargs it passed;
  * **SendGrid (optional)** via the documented v3 HTTP API — we MOCK the
    HTTP transport with ``httpx.MockTransport`` (the established
    mocked-external-dependency pattern).

Asserted behaviour the dispatcher relies on:

  * the adapter builds the correct MIME (Subject from the rendered template
    subject on ``structured``; From / To; text + optional HTML body);
  * a successful send returns ``DeliveryResult(ok=True)``;
  * an SMTP error (and a SendGrid 4xx) maps to :class:`ChannelSendError`, so
    the dispatcher logs ``failed`` + dead-letters (it never auto-retries);
  * TLS mode + SMTP auth are wired from the channel config + the secret;
  * the SMTP password / SendGrid API key is read via
    :func:`notification_dispatcher.secrets.resolve_channel_secret` (Fernet
    ``secret_encrypted`` at rest → plaintext IN MEMORY), is passed only to
    the transport, and NEVER appears in the MIME / a log / the surfaced
    error — and the DB side never holds the plaintext;
  * the optional SendGrid path is guarded: without ``provider='sendgrid'``
    the SMTP path runs, and nothing imports the ``sendgrid`` SDK.

No real network: SMTP is monkeypatched; SendGrid is served by an injected
``httpx.MockTransport``.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any

import httpx
import pytest
from notification_dispatcher.adapters import ChannelMessage, ChannelSendError
from notification_dispatcher.channels import email as email_mod
from notification_dispatcher.channels.email import EmailAdapter
from notification_dispatcher.config import Settings
from notification_dispatcher.secrets import encrypt_secret, resolve_channel_secret

pytestmark = pytest.mark.integration

_SMTP_PASSWORD = "s3cr3t-smtp-pw-this-is-a-fake-password-never-real"
_SENDGRID_KEY = "SG.fake-sendgrid-api-key-never-real.never-real"
_RECIPIENT = "ops@example.test"
_SENDER = "notifications@example.test"


def _test_settings() -> Settings:
    """Dispatcher Settings with a known Fernet key + stable API bases.

    ``environment='dev'`` keeps the dev-default-secret guard off; the
    encryption key is explicit so encrypt/decrypt round-trips in-process.
    """
    return Settings(
        environment="dev",
        notification_encryption_key="unit-test-email-key-not-a-real-secret",
        sendgrid_api_base_url="https://api.sendgrid.test",
    )


@dataclass
class _FakeChannel:
    """Duck-typed stand-in for the ``NotificationChannel`` ORM row.

    ``resolve_channel_secret`` only reads ``secret_ref`` / ``secret_encrypted``;
    the non-secret transport ``config`` lives separately. The plaintext
    password is NEVER stored here — only its Fernet ciphertext.
    """

    secret_ref: str | None = None
    secret_encrypted: str | None = None


@pytest.fixture()
def capture_smtp(monkeypatch: pytest.MonkeyPatch):
    """Monkeypatch ``aiosmtplib.send`` to capture the MIME + kwargs.

    Returns a setter the test uses to choose the canned outcome (a clean
    accept, a refused-recipient dict, or an SMTPException to raise) and to
    read back the captured call.
    """
    import aiosmtplib

    state: dict[str, Any] = {"calls": [], "errors": {}, "raise": None}

    async def _fake_send(message: Any, **kwargs: Any) -> tuple[dict[str, Any], str]:
        state["calls"].append({"message": message, "kwargs": kwargs})
        if state["raise"] is not None:
            raise state["raise"]
        return dict(state["errors"]), "250 OK"

    monkeypatch.setattr(aiosmtplib, "send", _fake_send)

    def _configure(*, errors: dict[str, Any] | None = None, raise_exc: Exception | None = None):
        state["errors"] = errors or {}
        state["raise"] = raise_exc
        return state["calls"]

    return _configure


@pytest.fixture()
def patch_sendgrid_transport(monkeypatch: pytest.MonkeyPatch):
    """Reroute the adapter's httpx.AsyncClient (SendGrid path) onto a mock.

    The adapter constructs ``httpx.AsyncClient(timeout=...)`` itself; we wrap
    that constructor so the test's MockTransport is supplied without changing
    the adapter's call site. Returns a setter that installs the canned
    response + hands back the captured requests.
    """
    state: dict[str, Any] = {"requests": [], "response": None}
    real_async_client = httpx.AsyncClient

    def _handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        return state["response"]

    def _factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(_handler)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(email_mod.httpx, "AsyncClient", _factory)

    def _install(response: httpx.Response) -> list[httpx.Request]:
        state["response"] = response
        return state["requests"]

    return _install


# ===========================================================================
# Secret handling — password resolved via resolve_channel_secret, never clear.
# ===========================================================================
def test_smtp_password_resolved_via_resolve_channel_secret() -> None:
    """The DB row holds only Fernet ciphertext; the plaintext password is
    produced in memory by resolve_channel_secret — never stored clear."""
    settings = _test_settings()
    channel = _FakeChannel(secret_encrypted=encrypt_secret(_SMTP_PASSWORD, settings))

    assert channel.secret_encrypted is not None
    assert _SMTP_PASSWORD not in channel.secret_encrypted

    resolved = resolve_channel_secret(channel, settings)
    assert resolved == _SMTP_PASSWORD


# ===========================================================================
# SMTP happy path — correct MIME + success maps to DeliveryResult(ok=True).
# ===========================================================================
@pytest.mark.asyncio
async def test_smtp_send_builds_mime_and_reports_sent(capture_smtp) -> None:
    settings = _test_settings()
    channel = _FakeChannel(secret_encrypted=encrypt_secret(_SMTP_PASSWORD, settings))
    password = resolve_channel_secret(channel, settings)

    calls = capture_smtp()  # clean accept

    adapter = EmailAdapter(settings=settings)
    message = ChannelMessage(
        channel_type="email",
        target=_RECIPIENT,
        body="Task X is blocked.",
        structured={"subject": "Task blocked: X"},
        secret=password,
        config={
            "host": "smtp.example.test",
            "port": 587,
            "username": "smtp-user",
            "from": _SENDER,
        },
    )

    result = await adapter.send(message)

    # Success -> DeliveryResult sent.
    assert result.ok is True
    assert result.error is None

    # Exactly one SMTP send, with the MIME the adapter built.
    assert len(calls) == 1
    mime = calls[0]["message"]
    assert isinstance(mime, EmailMessage)
    assert mime["Subject"] == "Task blocked: X"
    assert mime["From"] == _SENDER
    assert mime["To"] == _RECIPIENT
    assert mime.get_content().strip() == "Task X is blocked."

    # Connection kwargs wired from config: host/port + STARTTLS + auth.
    kwargs = calls[0]["kwargs"]
    assert kwargs["hostname"] == "smtp.example.test"
    assert kwargs["port"] == 587
    assert kwargs["start_tls"] is True
    assert kwargs["use_tls"] is False
    assert kwargs["username"] == "smtp-user"
    assert kwargs["password"] == _SMTP_PASSWORD

    # The secret never leaks into the MIME bytes.
    assert _SMTP_PASSWORD not in mime.as_string()


@pytest.mark.asyncio
async def test_smtp_html_alternative_added_when_configured(capture_smtp) -> None:
    """A config HTML body becomes a multipart/alternative (text + html)."""
    settings = _test_settings()
    calls = capture_smtp()

    adapter = EmailAdapter(settings=settings)
    message = ChannelMessage(
        channel_type="email",
        target=_RECIPIENT,
        body="plain text",
        structured={"subject": "Subj"},
        secret=None,
        config={"from": _SENDER, "html": "<p>rich</p>"},
    )
    await adapter.send(message)

    mime = calls[0]["message"]
    assert mime.is_multipart()
    subtypes = {
        part.get_content_subtype() for part in mime.walk() if part.get_content_maintype() == "text"
    }
    assert subtypes == {"plain", "html"}


@pytest.mark.asyncio
async def test_smtp_implicit_tls_when_use_tls_set(capture_smtp) -> None:
    """config.use_tls=True => implicit SSL; STARTTLS is NOT also requested."""
    settings = _test_settings()
    calls = capture_smtp()

    adapter = EmailAdapter(settings=settings)
    message = ChannelMessage(
        channel_type="email",
        target=_RECIPIENT,
        body="x",
        structured={"subject": "s"},
        secret=None,
        config={"from": _SENDER, "port": 465, "use_tls": True},
    )
    await adapter.send(message)

    kwargs = calls[0]["kwargs"]
    assert kwargs["use_tls"] is True
    assert kwargs["start_tls"] is False
    assert kwargs["port"] == 465


@pytest.mark.asyncio
async def test_smtp_default_port_and_from_from_settings(capture_smtp) -> None:
    """No config port / sender => the Settings defaults are used."""
    settings = _test_settings()
    calls = capture_smtp()

    adapter = EmailAdapter(settings=settings)
    message = ChannelMessage(
        channel_type="email",
        target=_RECIPIENT,
        body="x",
        structured={"subject": "s"},
        secret=None,
        config={},
    )
    await adapter.send(message)

    kwargs = calls[0]["kwargs"]
    assert kwargs["port"] == settings.email_default_smtp_port == 587
    assert calls[0]["message"]["From"] == settings.email_default_from


# ===========================================================================
# SMTP failure path — SMTP error / refused recipient map to ChannelSendError.
# ===========================================================================
@pytest.mark.asyncio
async def test_smtp_exception_raises_channel_send_error(capture_smtp) -> None:
    import aiosmtplib

    settings = _test_settings()
    capture_smtp(raise_exc=aiosmtplib.SMTPAuthenticationError(535, "auth failed"))

    adapter = EmailAdapter(settings=settings)
    message = ChannelMessage(
        channel_type="email",
        target=_RECIPIENT,
        body="will fail",
        structured={"subject": "s"},
        secret=_SMTP_PASSWORD,
        config={"username": "u", "from": _SENDER},
    )

    with pytest.raises(ChannelSendError) as excinfo:
        await adapter.send(message)
    # The surfaced error names the failure but never the password.
    assert "SMTP" in str(excinfo.value)
    assert _SMTP_PASSWORD not in str(excinfo.value)


@pytest.mark.asyncio
async def test_smtp_refused_recipient_raises_channel_send_error(capture_smtp) -> None:
    settings = _test_settings()
    capture_smtp(errors={_RECIPIENT: (550, "mailbox unavailable")})

    adapter = EmailAdapter(settings=settings)
    message = ChannelMessage(
        channel_type="email",
        target=_RECIPIENT,
        body="x",
        structured={"subject": "s"},
        secret=None,
        config={"from": _SENDER},
    )

    with pytest.raises(ChannelSendError) as excinfo:
        await adapter.send(message)
    assert _RECIPIENT in str(excinfo.value)


@pytest.mark.asyncio
async def test_missing_recipient_raises_before_any_send(capture_smtp) -> None:
    settings = _test_settings()
    calls = capture_smtp()

    adapter = EmailAdapter(settings=settings)
    message = ChannelMessage(
        channel_type="email", target=None, body="x", secret=None, config={"from": _SENDER}
    )
    with pytest.raises(ChannelSendError):
        await adapter.send(message)
    # The guard fires before any transport call.
    assert calls == []


# ===========================================================================
# Optional SendGrid path — guarded by provider='sendgrid', driven over HTTP.
# ===========================================================================
@pytest.mark.asyncio
async def test_sendgrid_path_builds_payload_and_reports_sent(patch_sendgrid_transport) -> None:
    settings = _test_settings()
    channel = _FakeChannel(secret_encrypted=encrypt_secret(_SENDGRID_KEY, settings))
    api_key = resolve_channel_secret(channel, settings)

    requests = patch_sendgrid_transport(httpx.Response(202, headers={"X-Message-Id": "sg-abc-123"}))

    adapter = EmailAdapter(settings=settings)
    message = ChannelMessage(
        channel_type="email",
        target=_RECIPIENT,
        body="Body via SendGrid.",
        structured={"subject": "Subj SG"},
        secret=api_key,
        config={"provider": "sendgrid", "from": _SENDER},
    )

    result = await adapter.send(message)

    assert result.ok is True
    assert result.provider_message_id == "sg-abc-123"

    # Exactly one POST to the v3 mail/send endpoint with a Bearer auth header.
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == "https://api.sendgrid.test/v3/mail/send"
    assert request.headers["Authorization"] == f"Bearer {_SENDGRID_KEY}"

    import json

    body = json.loads(request.content.decode("utf-8"))
    assert body["from"]["email"] == _SENDER
    assert body["personalizations"][0]["to"][0]["email"] == _RECIPIENT
    assert body["subject"] == "Subj SG"
    # The content reflects the MIME body verbatim (canonical trailing newline
    # from EmailMessage.set_content is preserved — it is correct MIME).
    assert any(
        c["type"] == "text/plain" and c["value"].strip() == "Body via SendGrid."
        for c in body["content"]
    )
    # The API key never appears in the request body.
    assert _SENDGRID_KEY not in request.content.decode("utf-8")


@pytest.mark.asyncio
async def test_sendgrid_api_error_raises_channel_send_error(patch_sendgrid_transport) -> None:
    settings = _test_settings()
    patch_sendgrid_transport(httpx.Response(401, json={"errors": [{"message": "unauthorized"}]}))

    adapter = EmailAdapter(settings=settings)
    message = ChannelMessage(
        channel_type="email",
        target=_RECIPIENT,
        body="x",
        structured={"subject": "s"},
        secret=_SENDGRID_KEY,
        config={"provider": "sendgrid", "from": _SENDER},
    )

    with pytest.raises(ChannelSendError) as excinfo:
        await adapter.send(message)
    assert "401" in str(excinfo.value)
    assert _SENDGRID_KEY not in str(excinfo.value)


@pytest.mark.asyncio
async def test_sendgrid_path_without_api_key_raises(patch_sendgrid_transport) -> None:
    """provider=sendgrid but no secret => clean error, no HTTP call."""
    settings = _test_settings()
    requests = patch_sendgrid_transport(httpx.Response(202))

    adapter = EmailAdapter(settings=settings)
    message = ChannelMessage(
        channel_type="email",
        target=_RECIPIENT,
        body="x",
        structured={"subject": "s"},
        secret=None,
        config={"provider": "sendgrid", "from": _SENDER},
    )
    with pytest.raises(ChannelSendError):
        await adapter.send(message)
    assert requests == []


def test_default_provider_does_not_import_sendgrid_sdk(capture_smtp) -> None:
    """The optional SendGrid SDK is never imported (semgrep-style guard).

    The SMTP path is the default and the SendGrid HTTP path uses httpx, so
    the heavy ``sendgrid`` SDK must never be imported by this adapter — its
    absence can never break import / CI.
    """
    # Importing + exercising the adapter module must not have pulled the SDK in.
    assert "sendgrid" not in sys.modules


# ===========================================================================
# Registration — importing the channels package wires the adapter in.
# ===========================================================================
def test_adapter_registered_under_email_channel_type() -> None:
    from notification_dispatcher.adapters import get_adapter

    adapter = get_adapter("email")
    assert adapter is not None
    assert adapter.channel_type == "email"
