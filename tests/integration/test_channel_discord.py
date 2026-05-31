"""Integration tests for the Discord channel adapter (task_10_09).

The adapter (``notification_dispatcher.channels.discord.DiscordAdapter``)
delivers a rendered :class:`ChannelMessage` to a Discord channel as a rich
**embed** via an **incoming webhook** URL. Discord cannot be reached in tests
(no webhook, no network), so we MOCK the HTTP transport with
``httpx.MockTransport`` — exactly the established mocked-external-dependency
pattern the Telegram / Slack / Teams adapters use — and assert the *behaviour
the dispatcher relies on*:

  * the adapter builds a well-formed Discord webhook payload — an ``embeds``
    array whose single embed carries a ``title`` (from the subject), a
    ``description`` (from the body), a ``color`` selected by severity (a decimal
    RGB int), inline ``fields`` for the event metadata, and a ``timestamp`` —
    from the message + structured metadata;
  * a successful webhook response (HTTP 204 No Content / 200) returns
    ``DeliveryResult(ok=True)``;
  * a webhook error — a non-2xx, notably a 429 rate-limit (and 400 / 404 / 500)
    — raises :class:`ChannelSendError`, so the dispatcher logs ``failed`` +
    dead-letters (it never auto-retries here);
  * the webhook URL is the channel secret: it is read via
    :func:`notification_dispatcher.secrets.resolve_channel_secret` (Fernet
    ``secret_encrypted`` at rest → plaintext IN MEMORY), used only as the
    request target, and NEVER appears in the JSON body — and the DB side never
    holds the plaintext URL.

No real network: every request is served by an injected ``httpx.MockTransport``
handler, rerouted via a tiny monkeypatch on the module's ``httpx.AsyncClient``
so the adapter's own construction (timeout, url, headers) is exercised
untouched.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from notification_dispatcher.adapters import ChannelMessage, ChannelSendError
from notification_dispatcher.channels import discord as discord_mod
from notification_dispatcher.channels.discord import DiscordAdapter
from notification_dispatcher.config import Settings
from notification_dispatcher.secrets import encrypt_secret, resolve_channel_secret

pytestmark = pytest.mark.integration

# A fake Discord incoming-webhook URL. NOT a real webhook — the id + token are
# bogus and the host is the documented Discord webhook host. It is the
# channel's SECRET.
_WEBHOOK_URL = (
    "https://discord.com/api/webhooks/"
    "111111111111111111/FAKE-this-is-not-a-real-discord-webhook-token"
)

# The Discord blurple default embed colour (config default).
_DEFAULT_COLOR = 0x5865F2
# The severity→colour the adapter maps "warning" to.
_WARNING_COLOR = 0xF1C40F


def _test_settings() -> Settings:
    """Dispatcher Settings with a known Fernet key + a stable default colour.

    ``environment='dev'`` keeps the dev-default-secret guard off; the
    encryption key is explicit so encrypt/decrypt round-trips in-process.
    """
    return Settings(
        environment="dev",
        notification_encryption_key="unit-test-discord-key-not-a-real-secret",
        discord_default_embed_color=_DEFAULT_COLOR,
    )


@dataclass
class _FakeChannel:
    """Duck-typed stand-in for the ``NotificationChannel`` ORM row.

    ``resolve_channel_secret`` only reads ``secret_ref`` / ``secret_encrypted``.
    The plaintext webhook URL is NEVER stored here — only its Fernet ciphertext.
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

    monkeypatch.setattr(discord_mod.httpx, "AsyncClient", _factory)

    def _install(response: httpx.Response) -> list[httpx.Request]:
        state["response"] = response
        return state["requests"]

    return _install


# ===========================================================================
# Secret handling — webhook URL resolved via resolve_channel_secret, never plaintext.
# ===========================================================================
def test_webhook_url_resolved_via_resolve_channel_secret() -> None:
    """The DB row holds only Fernet ciphertext; the plaintext webhook URL is
    produced in memory by resolve_channel_secret — never stored clear."""
    settings = _test_settings()
    channel = _FakeChannel(secret_encrypted=encrypt_secret(_WEBHOOK_URL, settings))

    # The at-rest value is ciphertext, NOT the URL.
    assert channel.secret_encrypted is not None
    assert _WEBHOOK_URL not in channel.secret_encrypted

    resolved = resolve_channel_secret(channel, settings)
    assert resolved == _WEBHOOK_URL


# ===========================================================================
# Happy path — valid embed payload + success -> DeliveryResult sent.
# ===========================================================================
@pytest.mark.asyncio
async def test_send_builds_embed_and_reports_sent(patch_transport) -> None:
    settings = _test_settings()
    # The webhook URL flows from the encrypted channel secret through resolve_channel_secret.
    channel = _FakeChannel(secret_encrypted=encrypt_secret(_WEBHOOK_URL, settings))
    webhook_url = resolve_channel_secret(channel, settings)

    # A Discord webhook replies 204 No Content on success.
    requests = patch_transport(httpx.Response(204))

    adapter = DiscordAdapter(settings=settings)
    message = ChannelMessage(
        channel_type="discord",
        target=None,  # Discord routes by the webhook URL itself, not a target id.
        body="Task X is blocked. Reason: timeout.",
        secret=webhook_url,
        structured={
            "subject": "Task blocked: X",
            "event_type": "task_blocked",
            "severity": "warning",
        },
    )

    result = await adapter.send(message)

    # Success -> DeliveryResult sent. A webhook carries no per-message id.
    assert result.ok is True
    assert result.provider_message_id is None
    assert result.error is None

    # Exactly one POST was issued, to the (secret) webhook URL.
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == _WEBHOOK_URL
    assert request.headers["Content-Type"] == "application/json"

    payload = json.loads(request.content.decode("utf-8"))

    # Discord embeds envelope: one embed.
    assert list(payload.keys()) == ["embeds"]
    assert len(payload["embeds"]) == 1
    embed = payload["embeds"][0]

    # Title from subject, description from body.
    assert embed["title"] == "Task blocked: X"
    assert embed["description"] == "Task X is blocked. Reason: timeout."

    # warning severity colours the embed (a decimal RGB int, not a named colour).
    assert embed["color"] == _WARNING_COLOR
    assert isinstance(embed["color"], int)

    # A UTC ISO-8601 timestamp is stamped.
    assert "timestamp" in embed
    assert embed["timestamp"].endswith("+00:00")

    # Inline fields reflect the event metadata.
    fields = {f["name"]: f for f in embed["fields"]}
    assert fields["Event"]["value"] == "task_blocked"
    assert fields["Event"]["inline"] is True
    assert fields["Severity"]["value"] == "WARNING"
    assert fields["Severity"]["inline"] is True

    # The secret (webhook URL) is NEVER in the JSON body.
    assert _WEBHOOK_URL not in request.content.decode("utf-8")


@pytest.mark.asyncio
async def test_send_200_is_also_success(patch_transport) -> None:
    """A 200 (when Discord echoes the created message) is also a success."""
    settings = _test_settings()
    patch_transport(httpx.Response(200, json={"id": "999"}))

    adapter = DiscordAdapter(settings=settings)
    message = ChannelMessage(channel_type="discord", target=None, body="x", secret=_WEBHOOK_URL)
    result = await adapter.send(message)
    assert result.ok is True


@pytest.mark.asyncio
async def test_send_without_subject_omits_title(patch_transport) -> None:
    """No subject => no empty title; description + default colour remain."""
    settings = _test_settings()
    requests = patch_transport(httpx.Response(204))

    adapter = DiscordAdapter(settings=settings)
    message = ChannelMessage(
        channel_type="discord",
        target=None,
        body="bare body, no subject",
        secret=_WEBHOOK_URL,
        structured={"event_type": "budget_alert"},
    )
    await adapter.send(message)

    embed = json.loads(requests[0].content.decode("utf-8"))["embeds"][0]
    assert "title" not in embed
    assert embed["description"] == "bare body, no subject"
    # No severity => the configured default (blurple) colour.
    assert embed["color"] == _DEFAULT_COLOR
    # Only the Event field (no severity field).
    assert [f["name"] for f in embed["fields"]] == ["Event"]


@pytest.mark.asyncio
async def test_send_without_structured_is_description_only(patch_transport) -> None:
    """No structured metadata => a description-only embed (no title, no fields)."""
    settings = _test_settings()
    requests = patch_transport(httpx.Response(204))

    adapter = DiscordAdapter(settings=settings)
    message = ChannelMessage(
        channel_type="discord", target=None, body="just a body", secret=_WEBHOOK_URL
    )
    await adapter.send(message)

    embed = json.loads(requests[0].content.decode("utf-8"))["embeds"][0]
    assert "title" not in embed
    assert "fields" not in embed
    assert embed["description"] == "just a body"
    assert embed["color"] == _DEFAULT_COLOR


@pytest.mark.asyncio
async def test_embed_color_overridable_via_channel_config(patch_transport) -> None:
    """A channel may pin a custom embed colour via config.embed_color when no
    severity is present (severity-mapped colours otherwise win)."""
    settings = _test_settings()
    requests = patch_transport(httpx.Response(204))

    custom = 0x123456
    adapter = DiscordAdapter(settings=settings)
    message = ChannelMessage(
        channel_type="discord",
        target=None,
        body="x",
        secret=_WEBHOOK_URL,
        config={"embed_color": custom},
    )
    await adapter.send(message)

    embed = json.loads(requests[0].content.decode("utf-8"))["embeds"][0]
    assert embed["color"] == custom


@pytest.mark.asyncio
async def test_severity_color_takes_precedence_over_config(patch_transport) -> None:
    """A known severity wins over a channel config.embed_color override."""
    settings = _test_settings()
    requests = patch_transport(httpx.Response(204))

    adapter = DiscordAdapter(settings=settings)
    message = ChannelMessage(
        channel_type="discord",
        target=None,
        body="x",
        secret=_WEBHOOK_URL,
        config={"embed_color": 0x123456},
        structured={"severity": "warning"},
    )
    await adapter.send(message)

    embed = json.loads(requests[0].content.decode("utf-8"))["embeds"][0]
    assert embed["color"] == _WARNING_COLOR


# ===========================================================================
# Failure path — webhook error (notably 429 rate-limit) maps to ChannelSendError.
# ===========================================================================
@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [400, 401, 404, 429, 500])
async def test_non_2xx_raises_channel_send_error(patch_transport, status_code: int) -> None:
    """A malformed-embed 400, a revoked-webhook 401/404, a rate-limit 429 or a
    5xx is a terminal ChannelSendError — and the webhook URL never leaks."""
    settings = _test_settings()
    patch_transport(httpx.Response(status_code, text="webhook error"))

    adapter = DiscordAdapter(settings=settings)
    message = ChannelMessage(
        channel_type="discord",
        target=None,
        body="will fail",
        secret=_WEBHOOK_URL,
        structured={"subject": "S"},
    )

    with pytest.raises(ChannelSendError) as excinfo:
        await adapter.send(message)

    err = str(excinfo.value)
    assert str(status_code) in err
    assert _WEBHOOK_URL not in err


@pytest.mark.asyncio
async def test_rate_limit_429_with_retry_after_body_raises(patch_transport) -> None:
    """A 429 with Discord's documented ``retry_after`` body is a terminal
    ChannelSendError here (the backoff/retry policy is task_10_13)."""
    settings = _test_settings()
    patch_transport(
        httpx.Response(429, json={"message": "You are being rate limited.", "retry_after": 1.5})
    )

    adapter = DiscordAdapter(settings=settings)
    message = ChannelMessage(channel_type="discord", target=None, body="x", secret=_WEBHOOK_URL)
    with pytest.raises(ChannelSendError) as excinfo:
        await adapter.send(message)
    assert "429" in str(excinfo.value)


@pytest.mark.asyncio
async def test_transport_error_raises_channel_send_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A connection-level failure (no network) is a terminal ChannelSendError,
    and the webhook URL never leaks into the surfaced message."""
    settings = _test_settings()

    def _boom_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    real_async_client = httpx.AsyncClient

    def _factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(_boom_handler)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(discord_mod.httpx, "AsyncClient", _factory)

    adapter = DiscordAdapter(settings=settings)
    message = ChannelMessage(channel_type="discord", target=None, body="x", secret=_WEBHOOK_URL)
    with pytest.raises(ChannelSendError) as excinfo:
        await adapter.send(message)
    assert _WEBHOOK_URL not in str(excinfo.value)


# ===========================================================================
# Misconfiguration — no webhook URL is a clean error, no request issued.
# ===========================================================================
@pytest.mark.asyncio
async def test_missing_webhook_url_raises_before_any_request(patch_transport) -> None:
    settings = _test_settings()
    requests = patch_transport(httpx.Response(204))

    adapter = DiscordAdapter(settings=settings)
    message = ChannelMessage(channel_type="discord", target=None, body="x", secret=None)
    with pytest.raises(ChannelSendError):
        await adapter.send(message)
    # No HTTP call was made — the guard fires first.
    assert requests == []


# ===========================================================================
# Truncation — an over-long body becomes a (delivered, trimmed) description.
# ===========================================================================
@pytest.mark.asyncio
async def test_overlong_body_truncated_to_embed_limit(patch_transport) -> None:
    settings = _test_settings()
    requests = patch_transport(httpx.Response(204))

    adapter = DiscordAdapter(settings=settings)
    message = ChannelMessage(
        channel_type="discord", target=None, body="x" * 5000, secret=_WEBHOOK_URL
    )
    await adapter.send(message)

    embed = json.loads(requests[0].content.decode("utf-8"))["embeds"][0]
    # Discord caps embed description at 4096; the adapter trims to that.
    assert len(embed["description"]) == 4096
    assert embed["description"].endswith("…")


# ===========================================================================
# Registration — importing the channels package wires the adapter in.
# ===========================================================================
def test_adapter_registered_under_discord_channel_type() -> None:
    from notification_dispatcher.adapters import get_adapter

    # Side-effect of importing notification_dispatcher.channels.discord above.
    adapter = get_adapter("discord")
    assert adapter is not None
    assert adapter.channel_type == "discord"
