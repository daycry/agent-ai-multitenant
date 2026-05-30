"""Outbound-webhook channel adapter (Plan 10 Fase C task_10_12).

Delivers a rendered notification as a **signed JSON POST** to a tenant-configured
target URL — a plain ``POST <target-url>`` with an ``application/json`` body and
three signing headers, driven with ``httpx`` exactly like the other HTTP-POST
channels (Telegram, Slack, the SendGrid email path, the Teams / Discord
webhooks). Tests inject an ``httpx.MockTransport`` so the adapter never touches
the real network. **Outbound only** — inbound webhooks are Plan 13; the
reusable verifier this task ships
(:func:`notification_dispatcher.webhook_signing.verify_webhook`) is the exact
check that inbound phase will run.

Payload + signature (Plan 10 Decisiones Clave: *HMAC SHA-256 + nonce +
timestamp anti-replay*). The body is a compact JSON envelope built from the
rendered :class:`ChannelMessage` (event type, severity, subject, body, plus any
structured context the template produced). The adapter then signs the EXACT body
bytes it will send and stamps three headers so the receiver can authenticate the
request and reject replays:

  * ``X-Signature`` — ``HMAC-SHA256(secret, timestamp + "." + nonce + "." +
    body)``, hex. The timestamp + nonce are folded into the signed material so
    they are tamper-evident.
  * ``X-Timestamp`` — Unix seconds; bounds freshness (the receiver rejects a
    stale signature).
  * ``X-Nonce`` — a fresh random 128-bit token per request; bounds single use
    (the receiver remembers accepted nonces within the freshness window and
    rejects a repeat).

The signing crypto lives in :mod:`notification_dispatcher.webhook_signing`
(reusable + independently tested); this adapter only builds the envelope,
resolves the secret, and POSTs.

Secret handling (CLAUDE.md: NO plaintext secrets, never logged). The per-channel
**signing secret** is the channel's secret. The dispatcher resolves it IN MEMORY
via :func:`notification_dispatcher.secrets.resolve_channel_secret` (Vault
``secret_ref`` or Fernet ``secret_encrypted`` — never plaintext in the DB) and
hands it to the adapter as :attr:`ChannelMessage.secret`. This adapter reads the
secret only from there, uses it solely to compute the HMAC (it NEVER appears in
the body, the URL, a header value, or a log line), and never persists it. The
non-secret **target URL** rides the message ``target`` (or ``config.url`` /
``config.webhook_url``). The per-request timeout is a config tunable on
:class:`~notification_dispatcher.config.Settings`, never a magic number.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import structlog

from notification_dispatcher.adapters import (
    ChannelMessage,
    ChannelSendError,
    DeliveryResult,
    register_adapter,
)
from notification_dispatcher.config import Settings, get_settings
from notification_dispatcher.webhook_signing import (
    NONCE_HEADER,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    current_timestamp,
    generate_nonce,
    sign_webhook,
)

_log = structlog.get_logger("notification_dispatcher.channels.webhook")


class WebhookAdapter:
    """``webhook`` channel adapter — POST a signed JSON envelope to a target URL.

    Implements the :class:`~notification_dispatcher.adapters.ChannelAdapter`
    Protocol. Stateless: a fresh :class:`httpx.AsyncClient` is opened per send
    and closed via the context manager so there is no shared pool to leak across
    the per-send ``asyncio.run`` event loops.
    """

    channel_type: str = "webhook"

    def __init__(self, settings: Settings | None = None) -> None:
        # Defer the settings read so importing the module (and registering the
        # adapter below) never constructs Settings at import time — the registry
        # instance would otherwise read env on import.
        self._settings = settings

    @property
    def settings(self) -> Settings:
        return self._settings if self._settings is not None else get_settings()

    async def send(self, message: ChannelMessage) -> DeliveryResult:
        """Deliver ``message`` as a signed JSON POST to the target URL.

        Raises:
            ChannelSendError: the channel is missing its signing secret or its
                target URL, or the receiver rejects the post (non-2xx) / the
                request fails at the transport level. The dispatcher maps this
                to a ``failed`` log + dead-letter (it never auto-retries here).
        """
        settings = self.settings

        secret = message.secret
        if not secret:
            # The per-channel signing secret is mandatory: an unsigned webhook
            # is not a webhook a receiver can trust. resolve_channel_secret
            # upstream returns None only for a secretless channel — a webhook
            # channel must carry one.
            raise ChannelSendError(
                "webhook channel has no signing secret (secret_ref / secret_encrypted)"
            )

        target = message.target or self._config_url(message.config)
        if not target:
            raise ChannelSendError(
                "webhook channel has no target URL (target / config.url / config.webhook_url)"
            )

        # Build the body bytes ONCE and sign exactly those bytes (sort_keys +
        # compact separators makes the body deterministic so a re-serialising
        # receiver can reproduce it byte-for-byte).
        body_bytes = self._build_body(message)
        timestamp = current_timestamp()
        nonce = generate_nonce()
        signature = sign_webhook(secret, body_bytes, timestamp, nonce)
        headers = {
            "Content-Type": "application/json",
            SIGNATURE_HEADER: signature,
            TIMESTAMP_HEADER: str(timestamp),
            NONCE_HEADER: nonce,
        }

        try:
            async with httpx.AsyncClient(timeout=settings.webhook_request_timeout_s) as client:
                response = await client.post(target, content=body_bytes, headers=headers)
        except httpx.HTTPError as exc:
            # Connection refused / DNS / read timeout, etc. — a transport
            # failure. Never include the signing secret in the surfaced message
            # (the secret never reaches this path anyway — defensive).
            raise ChannelSendError(f"webhook transport error: {type(exc).__name__}") from exc

        return self._interpret_response(response, target=target)

    # ------------------------------------------------------------------
    # Body + target construction.
    # ------------------------------------------------------------------
    @staticmethod
    def _config_url(config: dict[str, Any] | None) -> str | None:
        """The non-secret target URL from the channel config (fallback)."""
        cfg = config or {}
        url = cfg.get("url") or cfg.get("webhook_url")
        return str(url) if url else None

    @staticmethod
    def _build_body(message: ChannelMessage) -> bytes:
        """Serialise the notification into the canonical signed JSON envelope.

        ``event``/``severity``/``subject`` come from the structured context the
        template produced (when present); ``body`` is the rendered message;
        ``context`` carries any remaining structured fields verbatim so a
        receiver gets the full payload. ``sort_keys`` + compact separators
        makes the bytes deterministic so the signature is stable and a receiver
        re-serialising the same fields reproduces it byte-for-byte.
        """
        structured = dict(message.structured or {})
        envelope: dict[str, Any] = {"body": message.body}
        event_type = structured.pop("event_type", None)
        if event_type is not None:
            envelope["event"] = event_type
        severity = structured.pop("severity", None)
        if severity is not None:
            envelope["severity"] = severity
        subject = structured.pop("subject", None)
        if subject is not None:
            envelope["subject"] = subject
        if structured:
            envelope["context"] = structured
        return json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8")

    # ------------------------------------------------------------------
    # Response interpretation.
    # ------------------------------------------------------------------
    def _interpret_response(self, response: httpx.Response, *, target: str) -> DeliveryResult:
        """Map the receiver's response to a :class:`DeliveryResult` / raise.

        Any 2xx is success (a webhook receiver typically returns ``200``/``202``
        with no meaningful body). A non-2xx is a terminal
        :class:`ChannelSendError` (the dispatcher logs ``failed`` +
        dead-letters; it never auto-retries here).
        """
        if response.is_success:
            return DeliveryResult(ok=True)

        # Failure: surface the status + a short snippet of the (non-secret)
        # response body. The dispatcher truncates before it reaches the log.
        detail = response.text.strip()
        _log.warning(
            "notification_dispatcher.channels.webhook.send_failed",
            target=target,
            status_code=response.status_code,
            error=detail or None,
        )
        suffix = f": {detail[:200]}" if detail else ""
        raise ChannelSendError(f"webhook delivery failed (HTTP {response.status_code}){suffix}")


# Register the adapter at import time so `import notification_dispatcher.channels`
# wires it into the shared registry the dispatcher resolves channel_type through.
register_adapter(WebhookAdapter())


__all__ = ["WebhookAdapter"]
