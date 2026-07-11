"""Integration tests for the WhatsApp Cloud API channel adapter (task_10_10).

The adapter (``notification_dispatcher.channels.whatsapp.WhatsAppAdapter``)
delivers a notification to a WhatsApp number via the Meta WhatsApp **Cloud
API** — a ``POST {base}/{version}/{phone_number_id}/messages`` with a JSON
``template`` message body and a ``Bearer`` access token. The Cloud API cannot
be reached in tests (no Meta Business account, no network), so we MOCK the
HTTP transport with ``httpx.MockTransport`` — exactly the established
mocked-external-dependency pattern the Telegram / Slack / Teams adapters use —
and assert the *behaviour the dispatcher relies on*:

  * the adapter builds a well-formed Cloud API **template** message payload
    (``messaging_product=whatsapp``, ``to``, ``type=template``, a ``template``
    naming the approved template + its ``language`` code + body ``components``
    / ``parameters``) from the message + structured metadata — WhatsApp
    business-initiated messages require a PRE-APPROVED template, never free
    text;
  * a successful response (HTTP 200 with ``messages[0].id``) returns
    ``DeliveryResult(ok=True)`` carrying the ``wamid`` as the provider id;
  * a Graph API error — a non-2xx (HTTP 400 / 401 / a ``131xx`` business
    error) — raises :class:`ChannelSendError`, so the dispatcher logs
    ``failed`` + dead-letters (it never auto-retries here);
  * an **unknown / not-pre-approved template** is rejected with
    :class:`ChannelSendError` BEFORE any HTTP call is made;
  * the access token is the channel secret: it is read via
    :func:`notification_dispatcher.secrets.resolve_channel_secret` (Fernet
    ``secret_encrypted`` at rest → plaintext IN MEMORY), placed only in the
    ``Authorization: Bearer`` header, and NEVER appears in the JSON body — and
    the DB side never holds the plaintext token.

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
from notification_dispatcher.channels import whatsapp as whatsapp_mod
from notification_dispatcher.channels.whatsapp import WhatsAppAdapter
from notification_dispatcher.config import Settings
from notification_dispatcher.secrets import encrypt_secret, resolve_channel_secret

pytestmark = pytest.mark.integration

# A fake WhatsApp Cloud API access token. NOT a real token — a long-lived
# Cloud API token is opaque; this is a test sentinel. It is the channel SECRET.
_ACCESS_TOKEN = "FAKE-this-is-not-a-real-whatsapp-cloud-access-token-EAAG"
# The sender's phone_number_id (a non-secret Graph object id) + a recipient.
_PHONE_NUMBER_ID = "1234567890"
_RECIPIENT = "+15551234567"


def _test_settings() -> Settings:
    """Dispatcher Settings with a known Fernet key + a stable Graph version.

    ``environment='dev'`` keeps the dev-default-secret guard off; the
    encryption key is explicit so encrypt/decrypt round-trips in-process.
    """
    return Settings(
        environment="dev",
        notification_encryption_key="unit-test-whatsapp-key-not-a-real-secret",
        whatsapp_api_base_url="https://graph.facebook.com",
        whatsapp_api_version="v21.0",
        whatsapp_default_language="en_US",
    )


@dataclass
class _FakeChannel:
    """Duck-typed stand-in for the ``NotificationChannel`` ORM row.

    ``resolve_channel_secret`` only reads ``secret_ref`` / ``secret_encrypted``.
    The plaintext access token is NEVER stored here — only its Fernet ciphertext.
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

    monkeypatch.setattr(whatsapp_mod.httpx, "AsyncClient", _factory)

    def _install(response: httpx.Response) -> list[httpx.Request]:
        state["response"] = response
        return state["requests"]

    return _install


def _message(**overrides: Any) -> ChannelMessage:
    """Build a baseline WhatsApp ChannelMessage; override per test."""
    base: dict[str, Any] = {
        "channel_type": "whatsapp",
        "target": _RECIPIENT,
        "body": "Task X is blocked. Reason: timeout.",
        "secret": _ACCESS_TOKEN,
        "structured": {
            "subject": "Task blocked: X",
            "body": "Task X is blocked. Reason: timeout.",
            "event_type": "task_blocked",
            "severity": "warning",
            "template": "agentic_alert",
        },
        "config": {"phone_number_id": _PHONE_NUMBER_ID},
    }
    base.update(overrides)
    return ChannelMessage(**base)


# ===========================================================================
# Secret handling — access token resolved via resolve_channel_secret, never plaintext.
# ===========================================================================
def test_access_token_resolved_via_resolve_channel_secret() -> None:
    """The DB row holds only Fernet ciphertext; the plaintext access token is
    produced in memory by resolve_channel_secret — never stored clear."""
    settings = _test_settings()
    channel = _FakeChannel(secret_encrypted=encrypt_secret(_ACCESS_TOKEN, settings))

    # The at-rest value is ciphertext, NOT the token.
    assert channel.secret_encrypted is not None
    assert _ACCESS_TOKEN not in channel.secret_encrypted

    resolved = resolve_channel_secret(channel, settings)
    assert resolved == _ACCESS_TOKEN


# ===========================================================================
# Happy path — valid template message payload + success -> DeliveryResult sent.
# ===========================================================================
@pytest.mark.asyncio
async def test_send_builds_template_payload_and_reports_sent(patch_transport) -> None:
    settings = _test_settings()
    # The access token flows from the encrypted channel secret through resolve_channel_secret.
    channel = _FakeChannel(secret_encrypted=encrypt_secret(_ACCESS_TOKEN, settings))
    token = resolve_channel_secret(channel, settings)

    # Cloud API success: 200 with the accepted message's wamid.
    wamid = "wamid.HBgLABCDEF1234567890"
    requests = patch_transport(
        httpx.Response(
            200,
            json={
                "messaging_product": "whatsapp",
                "contacts": [{"input": _RECIPIENT, "wa_id": "15551234567"}],
                "messages": [{"id": wamid}],
            },
        )
    )

    adapter = WhatsAppAdapter(settings=settings)
    result = await adapter.send(_message(secret=token))

    # Success -> DeliveryResult sent, carrying the wamid as the provider id.
    assert result.ok is True
    assert result.provider_message_id == wamid
    assert result.error is None

    # Exactly one POST, to the Cloud API /messages endpoint for the sender number.
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == (f"https://graph.facebook.com/v21.0/{_PHONE_NUMBER_ID}/messages")

    # The token rides ONLY in the Authorization header, never in the body.
    assert request.headers["Authorization"] == f"Bearer {token}"

    payload = json.loads(request.content.decode("utf-8"))
    assert payload["messaging_product"] == "whatsapp"
    assert payload["to"] == _RECIPIENT
    assert payload["type"] == "template"

    template = payload["template"]
    assert template["name"] == "agentic_alert"
    assert template["language"] == {"code": "en_US"}

    # Body components carry the ordered parameters (subject, body) as text.
    components = template["components"]
    assert len(components) == 1
    body_component = components[0]
    assert body_component["type"] == "body"
    params = body_component["parameters"]
    assert [p["type"] for p in params] == ["text", "text"]
    assert params[0]["text"] == "Task blocked: X"
    assert params[1]["text"] == "Task X is blocked. Reason: timeout."

    # The secret (access token) is NEVER in the JSON body.
    assert _ACCESS_TOKEN not in request.content.decode("utf-8")
    assert token not in request.content.decode("utf-8")


@pytest.mark.asyncio
async def test_send_with_explicit_params_overrides_field_mapping(patch_transport) -> None:
    """An explicit ordered structured.params list fills the body placeholders."""
    settings = _test_settings()
    requests = patch_transport(httpx.Response(200, json={"messages": [{"id": "wamid.x"}]}))

    adapter = WhatsAppAdapter(settings=settings)
    message = _message(
        structured={"template": "agentic_alert", "params": ["Hello", "World"]},
    )
    await adapter.send(message)

    payload = json.loads(requests[0].content.decode("utf-8"))
    params = payload["template"]["components"][0]["parameters"]
    assert [p["text"] for p in params] == ["Hello", "World"]


@pytest.mark.asyncio
async def test_parameterless_template_omits_components(patch_transport) -> None:
    """A pre-approved template with zero body params sends no components array."""
    settings = _test_settings()
    requests = patch_transport(httpx.Response(200, json={"messages": [{"id": "wamid.ping"}]}))

    adapter = WhatsAppAdapter(settings=settings)
    message = _message(structured={"template": "agentic_ping"})
    result = await adapter.send(message)

    assert result.ok is True
    payload = json.loads(requests[0].content.decode("utf-8"))
    assert payload["template"]["name"] == "agentic_ping"
    # The Cloud API rejects an empty body component, so it is omitted entirely.
    assert "components" not in payload["template"]


@pytest.mark.asyncio
async def test_language_overridable_via_structured(patch_transport) -> None:
    """A send may pin a different approved language/locale via structured.language."""
    settings = _test_settings()
    requests = patch_transport(httpx.Response(200, json={"messages": [{"id": "wamid.es"}]}))

    adapter = WhatsAppAdapter(settings=settings)
    message = _message(
        structured={"template": "agentic_alert", "language": "es_ES", "subject": "S", "body": "B"},
    )
    await adapter.send(message)

    payload = json.loads(requests[0].content.decode("utf-8"))
    assert payload["template"]["language"] == {"code": "es_ES"}


# ===========================================================================
# Pre-approved templates — an unknown template is rejected BEFORE sending.
# ===========================================================================
@pytest.mark.asyncio
async def test_unknown_template_rejected_before_any_request(patch_transport) -> None:
    """A template name outside the pre-approved registry never hits the network."""
    settings = _test_settings()
    requests = patch_transport(httpx.Response(200, json={"messages": [{"id": "wamid.x"}]}))

    adapter = WhatsAppAdapter(settings=settings)
    message = _message(structured={"template": "not_approved_template"})

    with pytest.raises(ChannelSendError) as excinfo:
        await adapter.send(message)

    err = str(excinfo.value)
    assert "not_approved_template" in err
    assert "not pre-approved" in err
    # No HTTP call was made — the registry guard fires first.
    assert requests == []


@pytest.mark.asyncio
async def test_missing_template_name_rejected_before_any_request(patch_transport) -> None:
    """No template named at all is a clean error — WhatsApp requires a template."""
    settings = _test_settings()
    requests = patch_transport(httpx.Response(200, json={"messages": [{"id": "wamid.x"}]}))

    adapter = WhatsAppAdapter(settings=settings)
    message = _message(structured={"subject": "S", "body": "B"})  # no template key
    with pytest.raises(ChannelSendError):
        await adapter.send(message)
    assert requests == []


@pytest.mark.asyncio
async def test_param_count_mismatch_rejected_before_any_request(patch_transport) -> None:
    """An explicit params list of the wrong arity is caught before sending."""
    settings = _test_settings()
    requests = patch_transport(httpx.Response(200, json={"messages": [{"id": "wamid.x"}]}))

    adapter = WhatsAppAdapter(settings=settings)
    # agentic_alert declares 2 body params; supply only 1.
    message = _message(structured={"template": "agentic_alert", "params": ["only-one"]})
    with pytest.raises(ChannelSendError) as excinfo:
        await adapter.send(message)
    assert "body parameter" in str(excinfo.value)
    assert requests == []


# ===========================================================================
# Failure path — a Graph API error maps to ChannelSendError.
# ===========================================================================
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "error_body"),
    [
        (400, {"error": {"message": "Invalid parameter", "code": 100}}),
        (401, {"error": {"message": "Invalid OAuth access token", "code": 190}}),
        (400, {"error": {"message": "Template does not exist", "code": 132001}}),
        (500, {"error": {"message": "internal", "code": 131000}}),
    ],
)
async def test_graph_api_error_raises_channel_send_error(
    patch_transport, status_code: int, error_body: dict[str, Any]
) -> None:
    """A 400 / 401 / a 131xx business error is a terminal ChannelSendError —
    and neither the token nor the URL leaks into it."""
    settings = _test_settings()
    patch_transport(httpx.Response(status_code, json=error_body))

    adapter = WhatsAppAdapter(settings=settings)
    with pytest.raises(ChannelSendError) as excinfo:
        await adapter.send(_message())

    err = str(excinfo.value)
    # The Graph error code is surfaced (in code= or the message).
    assert str(error_body["error"]["code"]) in err
    assert _ACCESS_TOKEN not in err


@pytest.mark.asyncio
async def test_transport_error_raises_channel_send_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A connection-level failure (no network) is a terminal ChannelSendError,
    and the access token never leaks into the surfaced message."""
    settings = _test_settings()

    def _boom_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    real_async_client = httpx.AsyncClient

    def _factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(_boom_handler)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(whatsapp_mod.httpx, "AsyncClient", _factory)

    adapter = WhatsAppAdapter(settings=settings)
    with pytest.raises(ChannelSendError) as excinfo:
        await adapter.send(_message())
    assert _ACCESS_TOKEN not in str(excinfo.value)


# ===========================================================================
# Misconfiguration — missing token / phone_number_id / recipient is clean.
# ===========================================================================
@pytest.mark.asyncio
async def test_missing_token_raises_before_any_request(patch_transport) -> None:
    settings = _test_settings()
    requests = patch_transport(httpx.Response(200, json={"messages": [{"id": "x"}]}))

    adapter = WhatsAppAdapter(settings=settings)
    with pytest.raises(ChannelSendError):
        await adapter.send(_message(secret=None))
    assert requests == []


@pytest.mark.asyncio
async def test_missing_phone_number_id_raises_before_any_request(patch_transport) -> None:
    settings = _test_settings()
    requests = patch_transport(httpx.Response(200, json={"messages": [{"id": "x"}]}))

    adapter = WhatsAppAdapter(settings=settings)
    with pytest.raises(ChannelSendError):
        await adapter.send(_message(config={}))
    assert requests == []


@pytest.mark.asyncio
async def test_missing_recipient_raises_before_any_request(patch_transport) -> None:
    settings = _test_settings()
    requests = patch_transport(httpx.Response(200, json={"messages": [{"id": "x"}]}))

    adapter = WhatsAppAdapter(settings=settings)
    with pytest.raises(ChannelSendError):
        await adapter.send(_message(target=None))
    assert requests == []


# ===========================================================================
# Registration — importing the channels package wires the adapter in.
# ===========================================================================
def test_adapter_registered_under_whatsapp_channel_type() -> None:
    from notification_dispatcher.adapters import get_adapter

    # Side-effect of importing notification_dispatcher.channels.whatsapp above.
    adapter = get_adapter("whatsapp")
    assert adapter is not None
    assert adapter.channel_type == "whatsapp"


@pytest.mark.asyncio
async def test_body_param_falls_back_to_message_body(patch_transport) -> None:
    """NOTIF-1: un structured SIN 'body' (la forma histórica del pipeline) no
    puede producir una plantilla con cuerpo en blanco — el param 'body' cae al
    message.body renderizado."""
    settings = _test_settings()
    requests = patch_transport(httpx.Response(200, json={"messages": [{"id": "wamid.fb"}]}))

    adapter = WhatsAppAdapter(settings=settings)
    message = _message(structured={"template": "agentic_notification", "subject": "S"})
    result = await adapter.send(message)

    assert result.ok is True
    payload = json.loads(requests[0].content.decode("utf-8"))
    params = payload["template"]["components"][0]["parameters"]
    assert params[0]["text"] == "Task X is blocked. Reason: timeout."
