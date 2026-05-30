"""Discord channel adapter (Plan 10 Fase B task_10_09).

Delivers a rendered notification to a Discord channel as a rich **embed** via
an **incoming webhook** URL. A Discord webhook is a plain
``POST <webhook-url>`` with a JSON body — there is no SDK and no bearer token,
the webhook URL itself carries the secret routing token (``.../webhooks/{id}/
{token}``), so we drive the documented HTTP API directly with ``httpx`` exactly
like the other HTTP-POST channels (Telegram, Slack, the SendGrid email path,
the Teams webhook). Tests inject an ``httpx.MockTransport`` so the adapter never
touches the real network.

Embed envelope. Discord expects the embed inside an ``embeds`` array on the
webhook payload::

    {
      "embeds": [
        {
          "title": "Task blocked: X",
          "description": "Task X is blocked. Reason: timeout.",
          "color": 16776960,
          "timestamp": "2026-05-30T12:00:00+00:00",
          "fields": [
            {"name": "Event", "value": "task_blocked", "inline": true},
            {"name": "Severity", "value": "WARNING", "inline": true}
          ]
        }
      ]
    }

The embed is built from the rendered :class:`ChannelMessage`: the rendered
subject becomes the embed ``title``, the rendered body the ``description``, the
event ``severity`` selects the embed ``color`` (a decimal RGB int — Discord
embeds do not take named colours), the event metadata (event type, severity)
becomes inline ``fields``, and a ``timestamp`` (UTC ISO-8601) is stamped so the
embed shows when the event fired.

Secret handling (CLAUDE.md: NO plaintext secrets, never logged). The webhook
URL is the channel's secret — anyone holding it can post to the channel, and it
embeds the webhook id + token — so it is stored encrypted at rest
(``secret_ref`` / ``secret_encrypted``) and resolved IN MEMORY by the dispatcher
via :func:`notification_dispatcher.secrets.resolve_channel_secret`, handed to
the adapter as :attr:`ChannelMessage.secret`. This adapter reads the URL only
from there, uses it solely as the request target, and never logs it or persists
it. The per-request timeout and the default embed colour are config tunables on
:class:`~notification_dispatcher.config.Settings`, never magic numbers.
"""

from __future__ import annotations

from datetime import UTC, datetime
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

_log = structlog.get_logger("notification_dispatcher.channels.discord")

# Discord embed field caps (the documented embed limits). We truncate
# defensively so an over-long rendered value becomes a (delivered, trimmed)
# embed rather than tripping Discord's 400 on an oversized embed. Tunable here,
# not inline magic in the build path.
_MAX_TITLE_LEN = 256
_MAX_DESCRIPTION_LEN = 4096
_MAX_FIELD_VALUE_LEN = 1024
_TRUNCATION_SUFFIX = "…"

# Severity → a Discord embed ``color`` (decimal RGB int), so the urgency of an
# event reads at a glance via the embed's left colour bar. Discord embeds take
# an integer, not a named colour. Anything unknown falls back to the configured
# default embed colour.
_SEVERITY_COLORS: dict[str, int] = {
    "critical": 0xE01E5A,  # red
    "error": 0xE01E5A,  # red
    "high": 0xE67E22,  # orange
    "warning": 0xF1C40F,  # yellow
    "medium": 0xF1C40F,  # yellow
    "info": 0x3498DB,  # blue
    "low": 0x2ECC71,  # green
}


class DiscordAdapter:
    """``discord`` channel adapter — POST an embed to an incoming webhook.

    Implements the :class:`~notification_dispatcher.adapters.ChannelAdapter`
    Protocol. Stateless: a fresh :class:`httpx.AsyncClient` is opened per send
    and closed via the context manager so there is no shared pool to leak
    across the per-send ``asyncio.run`` event loops.
    """

    channel_type: str = "discord"

    def __init__(self, settings: Settings | None = None) -> None:
        # Defer the settings read so importing the module (and registering the
        # adapter below) never constructs Settings at import time — the
        # registry instance would otherwise read env on import.
        self._settings = settings

    @property
    def settings(self) -> Settings:
        return self._settings if self._settings is not None else get_settings()

    async def send(self, message: ChannelMessage) -> DeliveryResult:
        """Deliver ``message`` to its Discord channel as an embed.

        Raises:
            ChannelSendError: the channel is missing its webhook URL, or the
                webhook rejects the post (non-2xx, e.g. a 429 rate-limit) / the
                request fails at the transport level. The dispatcher maps this
                to a ``failed`` log + dead-letter (it never auto-retries here).
        """
        settings = self.settings

        webhook_url = message.secret
        if not webhook_url:
            # The webhook URL is the channel secret (it embeds the id + token);
            # without it there is nowhere to post. resolve_channel_secret
            # upstream returns None only for a secretless channel — a discord
            # channel must carry one.
            raise ChannelSendError(
                "discord channel has no webhook URL (secret_ref / secret_encrypted)"
            )

        payload = self._build_payload(message, settings=settings)

        try:
            async with httpx.AsyncClient(timeout=settings.discord_request_timeout_s) as client:
                response = await client.post(
                    webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
        except httpx.HTTPError as exc:
            # Connection refused / DNS / read timeout, etc. — a transport
            # failure. Never include the webhook URL (it carries the secret
            # token) in the surfaced message.
            raise ChannelSendError(f"discord transport error: {type(exc).__name__}") from exc

        return self._interpret_response(response)

    # ------------------------------------------------------------------
    # Payload + embed construction.
    # ------------------------------------------------------------------
    def _build_payload(self, message: ChannelMessage, *, settings: Settings) -> dict[str, Any]:
        """Wrap the embed in the Discord webhook ``{"embeds": [...]}`` payload."""
        return {"embeds": [self._build_embed(message, settings=settings)]}

    def _build_embed(self, message: ChannelMessage, *, settings: Settings) -> dict[str, Any]:
        """Build the Discord embed from the rendered message.

        Layout:

          * ``title`` from the rendered subject — omitted when there is no
            subject so we never emit an empty title.
          * ``description`` from the rendered body (always present; the template
            render guarantees a body).
          * ``color`` selected by severity (a decimal RGB int), or the channel's
            ``config.embed_color`` override, or the configured default.
          * ``fields`` reflecting the event type + severity when ``structured``
            carries them, so the metadata reads at a glance.
          * ``timestamp`` — UTC ISO-8601, so the embed shows when it fired.
        """
        structured = message.structured or {}

        embed: dict[str, Any] = {
            "description": self._truncate(message.body, _MAX_DESCRIPTION_LEN),
            "color": self._resolve_color(structured.get("severity"), message.config, settings),
            "timestamp": datetime.now(UTC).isoformat(),
        }

        subject = structured.get("subject")
        if subject:
            embed["title"] = self._truncate(str(subject), _MAX_TITLE_LEN)

        fields = self._build_fields(structured)
        if fields:
            embed["fields"] = fields

        return embed

    def _build_fields(self, structured: dict[str, Any]) -> list[dict[str, Any]]:
        """Compose the embed fields from the event metadata.

        Returns the event type + severity as inline ``{name, value, inline}``
        rows (only the keys that are present), so a misconfigured / sparse event
        context still produces a valid (possibly empty) field list.
        """
        fields: list[dict[str, Any]] = []
        event_type = structured.get("event_type")
        if event_type:
            fields.append(
                {
                    "name": "Event",
                    "value": self._truncate(str(event_type), _MAX_FIELD_VALUE_LEN),
                    "inline": True,
                }
            )
        severity = structured.get("severity")
        if severity:
            fields.append(
                {
                    "name": "Severity",
                    "value": self._truncate(str(severity).upper(), _MAX_FIELD_VALUE_LEN),
                    "inline": True,
                }
            )
        return fields

    @staticmethod
    def _resolve_color(severity: Any, config: dict[str, Any] | None, settings: Settings) -> int:
        """Pick the embed colour: severity > channel config > configured default.

        Discord embeds take a decimal RGB integer, not a named colour. A known
        severity wins; otherwise a channel may pin ``config.embed_color``;
        otherwise the configured default (Discord blurple) applies.
        """
        if severity:
            mapped = _SEVERITY_COLORS.get(str(severity).lower())
            if mapped is not None:
                return mapped
        if config:
            override = config.get("embed_color")
            if isinstance(override, int):
                return override
        return settings.discord_default_embed_color

    @staticmethod
    def _truncate(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[: limit - len(_TRUNCATION_SUFFIX)] + _TRUNCATION_SUFFIX

    # ------------------------------------------------------------------
    # Response interpretation.
    # ------------------------------------------------------------------
    def _interpret_response(self, response: httpx.Response) -> DeliveryResult:
        """Map a webhook response to a :class:`DeliveryResult` / raise.

        A Discord webhook returns ``204 No Content`` on success (or ``200`` when
        the request asks for the created message via ``?wait=true``). It returns
        a non-2xx on failure: ``400`` for a malformed embed, ``401``/``404`` for
        a revoked webhook, ``429`` when rate-limited (with a ``retry_after``
        body). The webhook carries no per-message id by default, so a success
        reports no ``provider_message_id``. We treat any non-2xx as a terminal
        :class:`ChannelSendError`.
        """
        if response.is_success:
            return DeliveryResult(ok=True)

        # Failure: surface the status + a short snippet of the (non-secret)
        # response body, never the webhook URL. The dispatcher truncates before
        # it reaches the log.
        detail = response.text.strip()
        _log.warning(
            "notification_dispatcher.channels.discord.send_failed",
            status_code=response.status_code,
            error=detail or None,
        )
        suffix = f": {detail[:200]}" if detail else ""
        raise ChannelSendError(f"discord webhook failed (HTTP {response.status_code}){suffix}")


# Register the adapter at import time so `import notification_dispatcher.channels`
# wires it into the shared registry the dispatcher resolves channel_type through.
register_adapter(DiscordAdapter())


__all__ = ["DiscordAdapter"]
