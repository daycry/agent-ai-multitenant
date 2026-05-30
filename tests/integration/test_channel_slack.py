"""Integration tests for the Slack channel adapter (Plan 10 task_10_07).

The adapter (``notification_dispatcher.channels.slack.SlackAdapter``)
delivers a rendered :class:`ChannelMessage` to a Slack channel as a Block
Kit ``chat.postMessage``. Slack cannot be reached in tests (no bot token, no
network), so we MOCK the HTTP transport with ``httpx.MockTransport`` —
exactly the established mocked-external-dependency pattern the Telegram /
SendGrid adapters use — and assert the *behaviour the dispatcher relies on*:

  * the adapter builds a well-formed Block Kit payload (``channel``, a
    fallback ``text`` and a ``blocks`` array: header from the subject,
    section from the body, a context line reflecting event_type + severity)
    from the message + structured metadata;
  * a successful Web API response (``{"ok": true, "ts": "..."}``) returns
    ``DeliveryResult(ok=True, provider_message_id=<ts>)``;
  * a Slack error — both the ``ok: false`` (HTTP 200) quirk and a non-2xx
    (HTTP 429 / 500) — raises :class:`ChannelSendError`, so the dispatcher
    logs ``failed`` + dead-letters (it never auto-retries here);
  * the bot token is read via
    :func:`notification_dispatcher.secrets.resolve_channel_secret` (Fernet
    ``secret_encrypted`` at rest → plaintext IN MEMORY), is placed only in
    the ``Authorization`` header, and NEVER appears in the JSON body — and
    the DB side never holds the plaintext token.

No real network: every request is served by an injected
``httpx.MockTransport`` handler. The single ``httpx.AsyncClient`` the adapter
opens is rerouted to that transport via a tiny monkeypatch on the module's
``httpx.AsyncClient`` so the adapter's own construction (timeout, url,
headers) is exercised untouched.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from notification_dispatcher.adapters import ChannelMessage, ChannelSendError
from notification_dispatcher.channels import slack as slack_mod
from notification_dispatcher.channels.slack import SlackAdapter
from notification_dispatcher.config import Settings
from notification_dispatcher.secrets import encrypt_secret, resolve_channel_secret

pytestmark = pytest.mark.integration

_BOT_TOKEN = "xoxb-123456789012-FAKE-this-is-not-a-real-slack-bot-token"
_CHANNEL = "C0123456789"


def _test_settings() -> Settings:
    """Dispatcher Settings with a known Fernet key + a stable API base.

    ``environment='dev'`` keeps the dev-default-secret guard off; the
    encryption key is explicit so encrypt/decrypt round-trips in-process.
    """
    return Settings(
        environment="dev",
        notification_encryption_key="unit-test-slack-key-not-a-real-secret",
        slack_api_base_url="https://slack.test",
    )


@dataclass
class _FakeChannel:
    """Duck-typed stand-in for the ``NotificationChannel`` ORM row.

    ``resolve_channel_secret`` only reads ``secret_ref`` / ``secret_encrypted``.
    The plaintext token is NEVER stored here — only its Fernet ciphertext.
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

    monkeypatch.setattr(slack_mod.httpx, "AsyncClient", _factory)

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
# Happy path — well-formed Block Kit payload + success -> DeliveryResult sent.
# ===========================================================================
@pytest.mark.asyncio
async def test_send_builds_block_kit_payload_and_reports_sent(patch_transport) -> None:
    settings = _test_settings()
    # Token flows from the encrypted channel secret through resolve_channel_secret.
    channel = _FakeChannel(secret_encrypted=encrypt_secret(_BOT_TOKEN, settings))
    token = resolve_channel_secret(channel, settings)

    requests = patch_transport(
        httpx.Response(
            200,
            json={"ok": True, "ts": "1503435956.000247", "channel": _CHANNEL},
        )
    )

    adapter = SlackAdapter(settings=settings)
    message = ChannelMessage(
        channel_type="slack",
        target=_CHANNEL,
        body="Task *X* is blocked. Reason: timeout.",
        secret=token,
        structured={
            "subject": "Task blocked: X",
            "event_type": "task_blocked",
            "severity": "warning",
        },
    )

    result = await adapter.send(message)

    # Success -> DeliveryResult sent, carrying Slack's message ts.
    assert result.ok is True
    assert result.provider_message_id == "1503435956.000247"
    assert result.error is None

    # Exactly one chat.postMessage POST was issued, to the configured base.
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == "https://slack.test/api/chat.postMessage"

    # The token lives ONLY in the Authorization header — never in the body.
    assert request.headers["Authorization"] == f"Bearer {_BOT_TOKEN}"

    payload = json.loads(request.content.decode("utf-8"))
    assert payload["channel"] == _CHANNEL
    # Fallback text is the rendered body (used for notification previews).
    assert payload["text"] == "Task *X* is blocked. Reason: timeout."

    blocks = payload["blocks"]
    # header (subject) -> section (body) -> context (event/severity).
    assert [b["type"] for b in blocks] == ["header", "section", "context"]

    header = blocks[0]
    assert header["text"]["type"] == "plain_text"
    assert header["text"]["text"] == "Task blocked: X"

    section = blocks[1]
    assert section["text"]["type"] == "mrkdwn"
    assert section["text"]["text"] == "Task *X* is blocked. Reason: timeout."

    context = blocks[2]
    assert context["elements"][0]["type"] == "mrkdwn"
    context_text = context["elements"][0]["text"]
    # The context line reflects the event type + severity.
    assert "task_blocked" in context_text
    assert "WARNING" in context_text

    # The secret is NEVER in the JSON body.
    assert _BOT_TOKEN not in request.content.decode("utf-8")


@pytest.mark.asyncio
async def test_send_without_subject_omits_header_block(patch_transport) -> None:
    """No subject => no empty header; the body section + context remain."""
    settings = _test_settings()
    requests = patch_transport(httpx.Response(200, json={"ok": True, "ts": "1.1"}))

    adapter = SlackAdapter(settings=settings)
    message = ChannelMessage(
        channel_type="slack",
        target=_CHANNEL,
        body="bare body, no subject",
        secret=_BOT_TOKEN,
        structured={"event_type": "budget_alert"},
    )
    await adapter.send(message)

    payload = json.loads(requests[0].content.decode("utf-8"))
    block_types = [b["type"] for b in payload["blocks"]]
    assert "header" not in block_types
    assert block_types[0] == "section"
    # event_type present but no severity -> still a context block.
    assert "context" in block_types


@pytest.mark.asyncio
async def test_send_without_structured_is_section_only(patch_transport) -> None:
    """No structured metadata => a single section block (still valid)."""
    settings = _test_settings()
    requests = patch_transport(httpx.Response(200, json={"ok": True, "ts": "1.1"}))

    adapter = SlackAdapter(settings=settings)
    message = ChannelMessage(
        channel_type="slack",
        target=_CHANNEL,
        body="just a body",
        secret=_BOT_TOKEN,
    )
    await adapter.send(message)

    payload = json.loads(requests[0].content.decode("utf-8"))
    assert [b["type"] for b in payload["blocks"]] == ["section"]
    assert payload["blocks"][0]["text"]["text"] == "just a body"


# ===========================================================================
# Failure path — Slack API error maps to ChannelSendError.
# ===========================================================================
@pytest.mark.asyncio
async def test_ok_false_raises_channel_send_error(patch_transport) -> None:
    """Slack's HTTP-200-with-ok:false quirk is a terminal ChannelSendError,
    surfacing Slack's error code (never the token)."""
    settings = _test_settings()
    patch_transport(httpx.Response(200, json={"ok": False, "error": "channel_not_found"}))

    adapter = SlackAdapter(settings=settings)
    message = ChannelMessage(
        channel_type="slack",
        target=_CHANNEL,
        body="will fail",
        secret=_BOT_TOKEN,
        structured={"subject": "S"},
    )

    with pytest.raises(ChannelSendError) as excinfo:
        await adapter.send(message)

    err = str(excinfo.value)
    assert "channel_not_found" in err
    assert _BOT_TOKEN not in err


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [429, 500])
async def test_non_2xx_raises_channel_send_error(patch_transport, status_code: int) -> None:
    """A rate-limit (429) or 5xx is also a terminal ChannelSendError."""
    settings = _test_settings()
    patch_transport(httpx.Response(status_code, text="upstream error"))

    adapter = SlackAdapter(settings=settings)
    message = ChannelMessage(
        channel_type="slack",
        target=_CHANNEL,
        body="will fail",
        secret=_BOT_TOKEN,
    )

    with pytest.raises(ChannelSendError) as excinfo:
        await adapter.send(message)
    assert _BOT_TOKEN not in str(excinfo.value)


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

    monkeypatch.setattr(slack_mod.httpx, "AsyncClient", _factory)

    adapter = SlackAdapter(settings=settings)
    message = ChannelMessage(
        channel_type="slack",
        target=_CHANNEL,
        body="x",
        secret=_BOT_TOKEN,
    )
    with pytest.raises(ChannelSendError) as excinfo:
        await adapter.send(message)
    assert _BOT_TOKEN not in str(excinfo.value)


# ===========================================================================
# Misconfiguration — no token / no channel id are clean errors, no request.
# ===========================================================================
@pytest.mark.asyncio
async def test_missing_token_raises_before_any_request(patch_transport) -> None:
    settings = _test_settings()
    requests = patch_transport(httpx.Response(200, json={"ok": True, "ts": "1.1"}))

    adapter = SlackAdapter(settings=settings)
    message = ChannelMessage(channel_type="slack", target=_CHANNEL, body="x", secret=None)
    with pytest.raises(ChannelSendError):
        await adapter.send(message)
    # No HTTP call was made — the guard fires first.
    assert requests == []


@pytest.mark.asyncio
async def test_missing_channel_raises_before_any_request(patch_transport) -> None:
    settings = _test_settings()
    requests = patch_transport(httpx.Response(200, json={"ok": True, "ts": "1.1"}))

    adapter = SlackAdapter(settings=settings)
    message = ChannelMessage(channel_type="slack", target=None, body="x", secret=_BOT_TOKEN)
    with pytest.raises(ChannelSendError):
        await adapter.send(message)
    assert requests == []


# ===========================================================================
# Registration — importing the channels package wires the adapter in.
# ===========================================================================
def test_adapter_registered_under_slack_channel_type() -> None:
    from notification_dispatcher.adapters import get_adapter

    # Side-effect of importing notification_dispatcher.channels.slack above.
    adapter = get_adapter("slack")
    assert adapter is not None
    assert adapter.channel_type == "slack"
