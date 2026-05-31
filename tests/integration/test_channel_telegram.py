"""Integration tests for the Telegram channel adapter (Plan 10 task_10_05).

The adapter (``notification_dispatcher.channels.telegram.TelegramAdapter``)
delivers a rendered :class:`ChannelMessage` to a Telegram chat via the Bot
API ``sendMessage`` method. Telegram cannot be reached in tests (no bot
token, no network), so we MOCK the HTTP transport with
``httpx.MockTransport`` — exactly the established mocked-external-dependency
pattern — and assert the *behaviour the dispatcher relies on*:

  * the adapter builds the correct ``sendMessage`` payload (``chat_id``,
    ``text``, ``parse_mode``) from the message + channel config;
  * a successful Bot API response (``{"ok": true, ...}``) returns
    ``DeliveryResult(ok=True, provider_message_id=...)``;
  * a Bot API error (HTTP 400 / 403 with ``{"ok": false, ...}``) raises
    :class:`ChannelSendError`, so the dispatcher logs ``failed`` +
    dead-letters (it never auto-retries here);
  * the bot token is read via
    :func:`notification_dispatcher.secrets.resolve_channel_secret` (Fernet
    ``secret_encrypted`` at rest → plaintext IN MEMORY), is placed only in
    the request URL path, and NEVER appears in the JSON body — and the DB
    side never holds the plaintext token.

No real network: every request is served by an injected
``httpx.MockTransport`` handler. The single ``httpx.AsyncClient`` the
adapter opens is rerouted to that transport via a tiny monkeypatch on the
module's ``httpx.AsyncClient`` so the adapter's own construction (timeout,
url) is exercised untouched.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from notification_dispatcher.adapters import ChannelMessage, ChannelSendError
from notification_dispatcher.channels import telegram as telegram_mod
from notification_dispatcher.channels.telegram import TelegramAdapter
from notification_dispatcher.config import Settings
from notification_dispatcher.secrets import encrypt_secret, resolve_channel_secret

pytestmark = pytest.mark.integration

_BOT_TOKEN = "123456789:AAFKEY-this-is-a-fake-bot-token-never-real"
_CHAT_ID = "-1001234567890"


def _test_settings() -> Settings:
    """Dispatcher Settings with a known Fernet key + a stable API base.

    ``environment='dev'`` keeps the dev-default-secret guard off; the
    encryption key is explicit so encrypt/decrypt round-trips in-process.
    """
    return Settings(
        environment="dev",
        notification_encryption_key="unit-test-telegram-key-not-a-real-secret",
        telegram_api_base_url="https://api.telegram.test",
    )


@dataclass
class _FakeChannel:
    """Duck-typed stand-in for the ``NotificationChannel`` ORM row.

    ``resolve_channel_secret`` only reads ``secret_ref`` / ``secret_encrypted``;
    the non-secret ``config`` (chat id, parse_mode) lives separately. The
    plaintext token is NEVER stored here — only its Fernet ciphertext.
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
    the adapter's call site. Returns a setter the test uses to install the
    canned response + collect the captured requests.
    """
    state: dict[str, Any] = {"requests": [], "response": None}
    real_async_client = httpx.AsyncClient

    def _factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = _capturing_transport(state["requests"], response=state["response"])
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(telegram_mod.httpx, "AsyncClient", _factory)

    def _install(response: httpx.Response) -> list[httpx.Request]:
        state["response"] = response
        return state["requests"]

    return _install


# ===========================================================================
# Secret handling — token resolved via resolve_channel_secret, never plaintext.
# ===========================================================================
def test_bot_token_resolved_via_resolve_channel_secret() -> None:
    """The DB row holds only Fernet ciphertext; the plaintext token is
    produced in memory by resolve_channel_secret — never stored clear."""
    settings = _test_settings()
    channel = _FakeChannel(secret_encrypted=encrypt_secret(_BOT_TOKEN, settings))

    # The at-rest value is ciphertext, NOT the token.
    assert channel.secret_encrypted is not None
    assert _BOT_TOKEN not in channel.secret_encrypted

    resolved = resolve_channel_secret(channel, settings)
    assert resolved == _BOT_TOKEN


# ===========================================================================
# Happy path — correct payload + success maps to DeliveryResult(ok=True).
# ===========================================================================
@pytest.mark.asyncio
async def test_send_builds_payload_and_reports_sent(patch_transport) -> None:
    settings = _test_settings()
    # Token flows from the encrypted channel secret through resolve_channel_secret.
    channel = _FakeChannel(secret_encrypted=encrypt_secret(_BOT_TOKEN, settings))
    token = resolve_channel_secret(channel, settings)

    requests = patch_transport(
        httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": 4242}},
        )
    )

    adapter = TelegramAdapter(settings=settings)
    message = ChannelMessage(
        channel_type="telegram",
        target=_CHAT_ID,
        body="Task <b>X</b> is blocked.",
        secret=token,
        config={"parse_mode": "HTML"},
    )

    result = await adapter.send(message)

    # Success -> DeliveryResult sent, carrying Telegram's message_id.
    assert result.ok is True
    assert result.provider_message_id == "4242"
    assert result.error is None

    # Exactly one sendMessage POST was issued.
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    # The endpoint is {base}/bot{token}/sendMessage — the token lives ONLY
    # in the URL path (never in the body / a log line).
    assert str(request.url) == f"https://api.telegram.test/bot{_BOT_TOKEN}/sendMessage"
    assert request.url.path.endswith("/sendMessage")

    # Correct sendMessage payload: chat_id, text, parse_mode.
    payload = json.loads(request.content.decode("utf-8"))
    assert payload["chat_id"] == _CHAT_ID
    assert payload["text"] == "Task <b>X</b> is blocked."
    assert payload["parse_mode"] == "HTML"
    # The secret is NEVER in the JSON body.
    assert _BOT_TOKEN not in request.content.decode("utf-8")


@pytest.mark.asyncio
async def test_parse_mode_defaults_to_settings_when_not_in_config(patch_transport) -> None:
    """No config.parse_mode => the configured default (HTML) is sent."""
    settings = _test_settings()
    requests = patch_transport(httpx.Response(200, json={"ok": True, "result": {"message_id": 1}}))

    adapter = TelegramAdapter(settings=settings)
    message = ChannelMessage(
        channel_type="telegram",
        target=_CHAT_ID,
        body="hello",
        secret=_BOT_TOKEN,
        config={},
    )
    await adapter.send(message)

    payload = json.loads(requests[0].content.decode("utf-8"))
    assert payload["parse_mode"] == settings.telegram_default_parse_mode == "HTML"


@pytest.mark.asyncio
async def test_empty_parse_mode_sends_plain_text(patch_transport) -> None:
    """config.parse_mode='' => plain text: the field is omitted entirely."""
    settings = _test_settings()
    requests = patch_transport(httpx.Response(200, json={"ok": True, "result": {"message_id": 1}}))

    adapter = TelegramAdapter(settings=settings)
    message = ChannelMessage(
        channel_type="telegram",
        target=_CHAT_ID,
        body="plain body",
        secret=_BOT_TOKEN,
        config={"parse_mode": ""},
    )
    await adapter.send(message)

    payload = json.loads(requests[0].content.decode("utf-8"))
    assert "parse_mode" not in payload
    assert payload["text"] == "plain body"


# ===========================================================================
# Failure path — Telegram API error maps to ChannelSendError.
# ===========================================================================
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "error_code", "description"),
    [
        (400, 400, "Bad Request: chat not found"),
        (403, 403, "Forbidden: bot was blocked by the user"),
    ],
)
async def test_api_error_raises_channel_send_error(
    patch_transport, status_code: int, error_code: int, description: str
) -> None:
    settings = _test_settings()
    patch_transport(
        httpx.Response(
            status_code,
            json={"ok": False, "error_code": error_code, "description": description},
        )
    )

    adapter = TelegramAdapter(settings=settings)
    message = ChannelMessage(
        channel_type="telegram",
        target=_CHAT_ID,
        body="will fail",
        secret=_BOT_TOKEN,
        config={"parse_mode": "HTML"},
    )

    with pytest.raises(ChannelSendError) as excinfo:
        await adapter.send(message)

    # The surfaced error carries Telegram's description + code, never the token.
    err = str(excinfo.value)
    assert str(error_code) in err
    assert description in err
    assert _BOT_TOKEN not in err


@pytest.mark.asyncio
async def test_transport_error_raises_channel_send_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A connection-level failure (no network) is a terminal ChannelSendError,
    and the token never leaks into the surfaced message."""
    settings = _test_settings()

    def _boom_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    real_async_client = httpx.AsyncClient

    def _factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(_boom_handler)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(telegram_mod.httpx, "AsyncClient", _factory)

    adapter = TelegramAdapter(settings=settings)
    message = ChannelMessage(
        channel_type="telegram",
        target=_CHAT_ID,
        body="x",
        secret=_BOT_TOKEN,
        config={},
    )
    with pytest.raises(ChannelSendError) as excinfo:
        await adapter.send(message)
    assert _BOT_TOKEN not in str(excinfo.value)


# ===========================================================================
# Misconfiguration — no token / no chat_id / bad parse_mode are clean errors.
# ===========================================================================
@pytest.mark.asyncio
async def test_missing_token_raises_before_any_request(patch_transport) -> None:
    settings = _test_settings()
    requests = patch_transport(httpx.Response(200, json={"ok": True}))

    adapter = TelegramAdapter(settings=settings)
    message = ChannelMessage(
        channel_type="telegram", target=_CHAT_ID, body="x", secret=None, config={}
    )
    with pytest.raises(ChannelSendError):
        await adapter.send(message)
    # No HTTP call was made — the guard fires first.
    assert requests == []


@pytest.mark.asyncio
async def test_missing_chat_id_raises_before_any_request(patch_transport) -> None:
    settings = _test_settings()
    requests = patch_transport(httpx.Response(200, json={"ok": True}))

    adapter = TelegramAdapter(settings=settings)
    message = ChannelMessage(
        channel_type="telegram", target=None, body="x", secret=_BOT_TOKEN, config={}
    )
    with pytest.raises(ChannelSendError):
        await adapter.send(message)
    assert requests == []


@pytest.mark.asyncio
async def test_invalid_parse_mode_raises_before_any_request(patch_transport) -> None:
    settings = _test_settings()
    requests = patch_transport(httpx.Response(200, json={"ok": True}))

    adapter = TelegramAdapter(settings=settings)
    message = ChannelMessage(
        channel_type="telegram",
        target=_CHAT_ID,
        body="x",
        secret=_BOT_TOKEN,
        config={"parse_mode": "Klingon"},
    )
    with pytest.raises(ChannelSendError):
        await adapter.send(message)
    assert requests == []


# ===========================================================================
# Registration — importing the channels package wires the adapter in.
# ===========================================================================
def test_adapter_registered_under_telegram_channel_type() -> None:
    from notification_dispatcher.adapters import get_adapter

    # Side-effect of importing notification_dispatcher.channels.telegram above.
    adapter = get_adapter("telegram")
    assert adapter is not None
    assert adapter.channel_type == "telegram"
