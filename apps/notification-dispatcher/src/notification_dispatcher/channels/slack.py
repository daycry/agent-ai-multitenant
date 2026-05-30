"""Slack channel adapter (Plan 10 Fase B task_10_07).

Delivers a rendered notification to a Slack channel as a Block Kit message
via the Slack Web API ``chat.postMessage`` method. We drive the documented
HTTP API directly with ``httpx`` rather than the ``slack_sdk`` ``WebClient``:
``chat.postMessage`` is a single ``POST https://slack.com/api/chat.postMessage``
with a ``Bearer`` token and a JSON body, and the SDK's synchronous
``WebClient`` cannot be awaited while its async ``AsyncWebClient`` pulls in
``aiohttp`` and its own event-loop / connection-pool machinery that is
awkward inside the short-lived per-send ``asyncio.run`` the Celery dispatcher
uses. httpx keeps the async send path uniform with the other HTTP-POST
channels (Telegram, the SendGrid email path, Teams / Discord webhooks) and
lets tests inject an ``httpx.MockTransport`` so no adapter ever touches the
real network. ``slack_sdk`` is still a dev dependency so the Block Kit shape
this adapter builds stays aligned with the documented SDK contract.

Secret handling (CLAUDE.md: NO plaintext secrets, never logged). The Slack
bot token (``xoxb-…``) is the channel's secret. The dispatcher resolves it
IN MEMORY via :func:`notification_dispatcher.secrets.resolve_channel_secret`
(Vault ``secret_ref`` or Fernet ``secret_encrypted`` — never plaintext in the
DB) and hands it to the adapter as :attr:`ChannelMessage.secret`. This adapter
reads the token only from there, puts it solely in the ``Authorization``
header (never in a log line, never in the JSON body), and never persists it.

Target + transport config. The destination ``channel`` id (``C…`` / ``#name``)
is non-secret and arrives as :attr:`ChannelMessage.target` (the dispatcher
fills it from the send request or the channel's ``config`` — ``channel`` /
``channel_id`` / ``target``). The rendered body becomes the message text AND
the Block Kit ``section`` block; the rendered subject (carried on
:attr:`ChannelMessage.structured` as ``{"subject": ...}``) becomes the
``header`` block; an optional ``event_type`` / ``severity`` on ``structured``
is reflected in a trailing ``context`` block. The Slack API base URL and the
per-request timeout are config tunables on
:class:`~notification_dispatcher.config.Settings`, never magic numbers.
"""

from __future__ import annotations

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

_log = structlog.get_logger("notification_dispatcher.channels.slack")

# chat.postMessage method path, relative to the configured Slack API base.
_POST_MESSAGE_PATH = "/api/chat.postMessage"

# A Slack ``header`` block's plain_text is capped at 150 chars and a
# ``section`` block's text (mrkdwn) at 3000; a ``context`` element at 75.
# We truncate defensively so an over-long rendered value becomes a
# (delivered, trimmed) block instead of a hard ``invalid_blocks`` 400.
# Tunable here, not inline magic in the build path.
_MAX_HEADER_LEN = 150
_MAX_SECTION_LEN = 3000
_MAX_CONTEXT_LEN = 75
_TRUNCATION_SUFFIX = "…"

# Severity → a small leading glyph on the context line, so the urgency of an
# event reads at a glance in Slack without depending on any custom emoji.
_SEVERITY_GLYPHS: dict[str, str] = {
    "critical": "🔴",
    "error": "🔴",
    "high": "🟠",
    "warning": "🟡",
    "medium": "🟡",
    "info": "🔵",
    "low": "⚪",
}


class SlackAdapter:
    """``slack`` channel adapter — POST a Block Kit ``chat.postMessage``.

    Implements the :class:`~notification_dispatcher.adapters.ChannelAdapter`
    Protocol. Stateless: a fresh :class:`httpx.AsyncClient` is opened per
    send and closed in a ``finally`` (the context manager) so there is no
    shared pool to leak across the per-send ``asyncio.run`` event loops.
    """

    channel_type: str = "slack"

    def __init__(self, settings: Settings | None = None) -> None:
        # Defer the settings read so importing the module (and registering the
        # adapter below) never constructs Settings at import time — the
        # registry instance would otherwise read env on import.
        self._settings = settings

    @property
    def settings(self) -> Settings:
        return self._settings if self._settings is not None else get_settings()

    async def send(self, message: ChannelMessage) -> DeliveryResult:
        """Deliver ``message`` to its Slack channel as a Block Kit message.

        Raises:
            ChannelSendError: the channel is missing its bot token / channel
                id, or the Web API rejects the send (HTTP 4xx/5xx or
                ``ok: false``) / the request fails at the transport level. The
                dispatcher maps this to a ``failed`` log + dead-letter (it
                never auto-retries here).
        """
        settings = self.settings

        token = message.secret
        if not token:
            # The bot token is the channel secret; without it there is nothing
            # to authenticate with. resolve_channel_secret upstream returns
            # None only for a secretless channel — a slack channel must carry
            # one.
            raise ChannelSendError("slack channel has no bot token (secret_ref / secret_encrypted)")

        channel = message.target
        if not channel:
            raise ChannelSendError("slack channel has no channel id (target)")

        payload = self._build_payload(message, channel=channel)
        url = f"{settings.slack_api_base_url.rstrip('/')}{_POST_MESSAGE_PATH}"
        # The token lives ONLY in the Authorization header — never in the JSON
        # body, never in a log line.
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }

        try:
            async with httpx.AsyncClient(timeout=settings.slack_request_timeout_s) as client:
                response = await client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            # Connection refused / DNS / read timeout, etc. — a transport
            # failure. Never include the token in the surfaced message.
            raise ChannelSendError(f"slack transport error: {type(exc).__name__}") from exc

        return self._interpret_response(response, channel=channel)

    # ------------------------------------------------------------------
    # Payload + Block Kit construction.
    # ------------------------------------------------------------------
    def _build_payload(self, message: ChannelMessage, *, channel: str) -> dict[str, Any]:
        """Build the ``chat.postMessage`` JSON body from the rendered message.

        ``text`` is always included as the notification fallback (the line a
        client shows in notifications / when blocks can't render). ``blocks``
        carries the Block Kit layout built by :meth:`_build_blocks`.
        """
        return {
            "channel": channel,
            # Fallback text — Slack uses it for the push/desktop notification
            # preview and for accessibility when blocks don't render.
            "text": self._truncate(message.body, _MAX_SECTION_LEN),
            "blocks": self._build_blocks(message),
        }

    def _build_blocks(self, message: ChannelMessage) -> list[dict[str, Any]]:
        """Build the Block Kit ``blocks`` array from the rendered message.

        Layout (most-informative first):

          * ``header`` — the rendered subject (plain_text). Omitted when the
            message carries no subject so we never post an empty header.
          * ``section`` — the rendered body (mrkdwn). Always present.
          * ``context`` — a compact line reflecting the event_type + severity
            when ``structured`` carries them, so the urgency reads at a glance.
        """
        structured = message.structured or {}
        blocks: list[dict[str, Any]] = []

        subject = structured.get("subject")
        if subject:
            blocks.append(
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": self._truncate(str(subject), _MAX_HEADER_LEN),
                        "emoji": True,
                    },
                }
            )

        # The body section is always present (the message is never blank — the
        # template render guarantees a body); mrkdwn lets Slack render links.
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": self._truncate(message.body, _MAX_SECTION_LEN),
                },
            }
        )

        context_text = self._context_text(structured)
        if context_text:
            blocks.append(
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": self._truncate(context_text, _MAX_CONTEXT_LEN),
                        }
                    ],
                }
            )

        return blocks

    @staticmethod
    def _context_text(structured: dict[str, Any]) -> str | None:
        """Compose the context line from event_type + severity, or None.

        Reflects the triggering event and its urgency (a leading glyph for the
        severity) so an operator sees *what kind* of event this is at a glance.
        Returns None when neither is present, so we never post an empty
        context block.
        """
        event_type = structured.get("event_type")
        severity = structured.get("severity")

        parts: list[str] = []
        if severity:
            glyph = _SEVERITY_GLYPHS.get(str(severity).lower())
            label = str(severity).upper()
            parts.append(f"{glyph} {label}" if glyph else label)
        if event_type:
            parts.append(f"`{event_type}`")
        if not parts:
            return None
        return "  ·  ".join(parts)

    @staticmethod
    def _truncate(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[: limit - len(_TRUNCATION_SUFFIX)] + _TRUNCATION_SUFFIX

    # ------------------------------------------------------------------
    # Response interpretation.
    # ------------------------------------------------------------------
    def _interpret_response(self, response: httpx.Response, *, channel: str) -> DeliveryResult:
        """Map a Web API response to a :class:`DeliveryResult` / raise.

        Slack returns HTTP 200 with ``{"ok": true, "ts": "...", "channel":
        "..."}`` on success and HTTP 200 with ``{"ok": false, "error":
        "<code>"}`` on an application error (a Web API quirk — most failures
        are 200-with-ok:false). A non-2xx (rate limit 429, 5xx) is also a
        failure. We treat any non-2xx OR ``ok: false`` as a terminal
        :class:`ChannelSendError`.
        """
        try:
            body = response.json()
        except ValueError:
            body = {}

        if response.is_success and isinstance(body, dict) and body.get("ok") is True:
            # Slack's message timestamp ("ts") is the provider message id.
            ts = body.get("ts")
            return DeliveryResult(
                ok=True,
                provider_message_id=(str(ts) if ts is not None else None),
            )

        # Failure: surface Slack's own error code (e.g. ``channel_not_found``,
        # ``invalid_auth``, ``not_in_channel``) — never the token. The
        # dispatcher truncates before it reaches the log.
        error_code = ""
        if isinstance(body, dict):
            error_code = str(body.get("error", "")).strip()
        _log.warning(
            "notification_dispatcher.channels.slack.send_failed",
            channel=channel,
            status_code=response.status_code,
            error=error_code or None,
        )
        detail = error_code or f"HTTP {response.status_code}"
        raise ChannelSendError(f"slack chat.postMessage failed: {detail}")


# Register the adapter at import time so `import notification_dispatcher.channels`
# wires it into the shared registry the dispatcher resolves channel_type through.
register_adapter(SlackAdapter())


__all__ = ["SlackAdapter"]
