"""Integration tests for the Microsoft Teams channel adapter (task_10_08).

The adapter (``notification_dispatcher.channels.teams.TeamsAdapter``) delivers
a rendered :class:`ChannelMessage` to a Teams channel as an Adaptive Card via
an **incoming webhook** URL. Teams cannot be reached in tests (no webhook, no
network), so we MOCK the HTTP transport with ``httpx.MockTransport`` — exactly
the established mocked-external-dependency pattern the Telegram / Slack
adapters use — and assert the *behaviour the dispatcher relies on*:

  * the adapter builds a well-formed Teams envelope — a ``message`` with an
    ``attachments`` array whose single attachment declares the Adaptive Card
    ``contentType`` and carries a valid Adaptive Card (``$schema``, ``type``,
    ``version``, and a ``body`` of TextBlocks for the title/body + a FactSet
    for the event fields, the title coloured by severity) — from the message +
    structured metadata;
  * a successful webhook response (HTTP 200) returns ``DeliveryResult(ok=True)``;
  * a webhook error — a non-2xx (HTTP 400 / 404 / 429) — raises
    :class:`ChannelSendError`, so the dispatcher logs ``failed`` +
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
from notification_dispatcher.channels import teams as teams_mod
from notification_dispatcher.channels.teams import TeamsAdapter
from notification_dispatcher.config import Settings
from notification_dispatcher.secrets import encrypt_secret, resolve_channel_secret

pytestmark = pytest.mark.integration

# A fake Teams incoming-webhook URL. NOT a real webhook — the path token is
# bogus and the host is a test host. It is the channel's SECRET.
_WEBHOOK_URL = (
    "https://example.webhook.office.com/webhookb2/"
    "FAKE-this-is-not-a-real-teams-webhook-token/IncomingWebhook/abc/def"
)


def _test_settings() -> Settings:
    """Dispatcher Settings with a known Fernet key + a stable card version.

    ``environment='dev'`` keeps the dev-default-secret guard off; the
    encryption key is explicit so encrypt/decrypt round-trips in-process.
    """
    return Settings(
        environment="dev",
        notification_encryption_key="unit-test-teams-key-not-a-real-secret",
        teams_adaptive_card_version="1.4",
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

    monkeypatch.setattr(teams_mod.httpx, "AsyncClient", _factory)

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
# Happy path — valid Adaptive Card envelope + success -> DeliveryResult sent.
# ===========================================================================
@pytest.mark.asyncio
async def test_send_builds_adaptive_card_and_reports_sent(patch_transport) -> None:
    settings = _test_settings()
    # The webhook URL flows from the encrypted channel secret through resolve_channel_secret.
    channel = _FakeChannel(secret_encrypted=encrypt_secret(_WEBHOOK_URL, settings))
    webhook_url = resolve_channel_secret(channel, settings)

    # A Teams incoming webhook replies 200 with the body text "1" on success.
    requests = patch_transport(httpx.Response(200, text="1"))

    adapter = TeamsAdapter(settings=settings)
    message = ChannelMessage(
        channel_type="teams",
        target=None,  # Teams routes by the webhook URL itself, not a target id.
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

    payload = json.loads(request.content.decode("utf-8"))

    # Teams message/attachments envelope.
    assert payload["type"] == "message"
    assert len(payload["attachments"]) == 1
    attachment = payload["attachments"][0]
    assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"

    # The attachment content is a valid Adaptive Card.
    card = attachment["content"]
    assert card["type"] == "AdaptiveCard"
    assert card["version"] == "1.4"
    assert card["$schema"] == "http://adaptivecards.io/schemas/adaptive-card.json"

    body = card["body"]
    # title TextBlock -> body TextBlock -> FactSet.
    assert [b["type"] for b in body] == ["TextBlock", "TextBlock", "FactSet"]

    title = body[0]
    assert title["text"] == "Task blocked: X"
    assert title["weight"] == "Bolder"
    # warning severity colours the title.
    assert title["color"] == "warning"

    body_block = body[1]
    assert body_block["text"] == "Task X is blocked. Reason: timeout."
    assert body_block["wrap"] is True

    facts = body[2]["facts"]
    fact_pairs = {f["title"]: f["value"] for f in facts}
    assert fact_pairs["Event"] == "task_blocked"
    assert fact_pairs["Severity"] == "WARNING"

    # The secret (webhook URL) is NEVER in the JSON body.
    assert _WEBHOOK_URL not in request.content.decode("utf-8")


@pytest.mark.asyncio
async def test_send_without_subject_omits_title_block(patch_transport) -> None:
    """No subject => no empty title; the body TextBlock + FactSet remain."""
    settings = _test_settings()
    requests = patch_transport(httpx.Response(200, text="1"))

    adapter = TeamsAdapter(settings=settings)
    message = ChannelMessage(
        channel_type="teams",
        target=None,
        body="bare body, no subject",
        secret=_WEBHOOK_URL,
        structured={"event_type": "budget_alert"},
    )
    await adapter.send(message)

    payload = json.loads(requests[0].content.decode("utf-8"))
    card = payload["attachments"][0]["content"]
    block_types = [b["type"] for b in card["body"]]
    # First block is the body TextBlock (no leading title), FactSet follows.
    assert block_types == ["TextBlock", "FactSet"]
    assert card["body"][0]["text"] == "bare body, no subject"


@pytest.mark.asyncio
async def test_send_without_structured_is_body_only(patch_transport) -> None:
    """No structured metadata => a single body TextBlock (still a valid card)."""
    settings = _test_settings()
    requests = patch_transport(httpx.Response(200, text="1"))

    adapter = TeamsAdapter(settings=settings)
    message = ChannelMessage(
        channel_type="teams",
        target=None,
        body="just a body",
        secret=_WEBHOOK_URL,
    )
    await adapter.send(message)

    payload = json.loads(requests[0].content.decode("utf-8"))
    card = payload["attachments"][0]["content"]
    assert [b["type"] for b in card["body"]] == ["TextBlock"]
    assert card["body"][0]["text"] == "just a body"


@pytest.mark.asyncio
async def test_card_version_overridable_via_channel_config(patch_transport) -> None:
    """A channel may pin a different Adaptive Card version via config.card_version."""
    settings = _test_settings()
    requests = patch_transport(httpx.Response(202))

    adapter = TeamsAdapter(settings=settings)
    message = ChannelMessage(
        channel_type="teams",
        target=None,
        body="x",
        secret=_WEBHOOK_URL,
        config={"card_version": "1.5"},
    )
    result = await adapter.send(message)

    # 202 Accepted (the Workflows connector reply) is also a success.
    assert result.ok is True
    payload = json.loads(requests[0].content.decode("utf-8"))
    assert payload["attachments"][0]["content"]["version"] == "1.5"


# ===========================================================================
# Failure path — webhook error maps to ChannelSendError.
# ===========================================================================
@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [400, 404, 429, 500])
async def test_non_2xx_raises_channel_send_error(patch_transport, status_code: int) -> None:
    """A malformed-card 400, a revoked-webhook 404, a throttle 429 or a 5xx is
    a terminal ChannelSendError — and the webhook URL never leaks into it."""
    settings = _test_settings()
    patch_transport(httpx.Response(status_code, text="webhook error"))

    adapter = TeamsAdapter(settings=settings)
    message = ChannelMessage(
        channel_type="teams",
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

    monkeypatch.setattr(teams_mod.httpx, "AsyncClient", _factory)

    adapter = TeamsAdapter(settings=settings)
    message = ChannelMessage(channel_type="teams", target=None, body="x", secret=_WEBHOOK_URL)
    with pytest.raises(ChannelSendError) as excinfo:
        await adapter.send(message)
    assert _WEBHOOK_URL not in str(excinfo.value)


# ===========================================================================
# Misconfiguration — no webhook URL is a clean error, no request issued.
# ===========================================================================
@pytest.mark.asyncio
async def test_missing_webhook_url_raises_before_any_request(patch_transport) -> None:
    settings = _test_settings()
    requests = patch_transport(httpx.Response(200, text="1"))

    adapter = TeamsAdapter(settings=settings)
    message = ChannelMessage(channel_type="teams", target=None, body="x", secret=None)
    with pytest.raises(ChannelSendError):
        await adapter.send(message)
    # No HTTP call was made — the guard fires first.
    assert requests == []


# ===========================================================================
# Registration — importing the channels package wires the adapter in.
# ===========================================================================
def test_adapter_registered_under_teams_channel_type() -> None:
    from notification_dispatcher.adapters import get_adapter

    # Side-effect of importing notification_dispatcher.channels.teams above.
    adapter = get_adapter("teams")
    assert adapter is not None
    assert adapter.channel_type == "teams"
