"""Microsoft Teams channel adapter (Plan 10 Fase B task_10_08).

Delivers a rendered notification to a Teams channel as an Adaptive Card via
an **incoming webhook** URL. Teams incoming webhooks (the Power Automate /
Workflows "Post to a channel" connector that supersedes the legacy Office 365
connector) are a plain ``POST <webhook-url>`` with a JSON body — there is no
SDK and no bearer token, the webhook URL itself carries the secret routing
token, so we drive the documented HTTP API directly with ``httpx`` exactly
like the other HTTP-POST channels (Telegram, Slack, the SendGrid email path,
the Discord webhook). Tests inject an ``httpx.MockTransport`` so the adapter
never touches the real network.

Card envelope. Teams expects the Adaptive Card wrapped in a message envelope
with an ``attachments`` array — each attachment declares
``contentType: application/vnd.microsoft.card.adaptive`` and carries the card
as its ``content``::

    {
      "type": "message",
      "attachments": [
        {
          "contentType": "application/vnd.microsoft.card.adaptive",
          "contentUrl": null,
          "content": { "$schema": "...", "type": "AdaptiveCard", "version": "1.4", ... }
        }
      ]
    }

The card body is built from the rendered :class:`ChannelMessage`: a bold
title ``TextBlock`` from the subject (coloured/styled by severity), a body
``TextBlock`` from the rendered body, and a ``FactSet`` of the event fields
(event type, severity) so the metadata reads at a glance.

Secret handling (CLAUDE.md: NO plaintext secrets, never logged). The webhook
URL is the channel's secret — anyone holding it can post to the channel, and
it embeds a routing token — so it is stored encrypted at rest
(``secret_ref`` / ``secret_encrypted``) and resolved IN MEMORY by the
dispatcher via :func:`notification_dispatcher.secrets.resolve_channel_secret`,
handed to the adapter as :attr:`ChannelMessage.secret`. This adapter reads the
URL only from there, uses it solely as the request target, and never logs it
or persists it. The per-request timeout and the Adaptive Card schema version
are config tunables on :class:`~notification_dispatcher.config.Settings`,
never magic numbers.
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

_log = structlog.get_logger("notification_dispatcher.channels.teams")

# The Adaptive Card schema URL Teams clients validate the card against.
_ADAPTIVE_CARD_SCHEMA = "http://adaptivecards.io/schemas/adaptive-card.json"
# The attachment contentType that tells Teams the content is an Adaptive Card.
_ADAPTIVE_CARD_CONTENT_TYPE = "application/vnd.microsoft.card.adaptive"

# Defensive caps so an over-long rendered value becomes a (delivered, trimmed)
# card rather than tripping a Teams payload-size rejection. Tunable here, not
# inline magic in the build path.
_MAX_TITLE_LEN = 300
_MAX_BODY_LEN = 8_000
_TRUNCATION_SUFFIX = "…"

# Severity → an Adaptive Card TextBlock ``color`` for the title, so the
# urgency of an event reads at a glance. Teams renders these named colours
# (the Adaptive Card colour enum); anything unknown falls back to the default.
_SEVERITY_COLORS: dict[str, str] = {
    "critical": "attention",
    "error": "attention",
    "high": "warning",
    "warning": "warning",
    "medium": "warning",
    "info": "accent",
    "low": "good",
}


class TeamsAdapter:
    """``teams`` channel adapter — POST an Adaptive Card to an incoming webhook.

    Implements the :class:`~notification_dispatcher.adapters.ChannelAdapter`
    Protocol. Stateless: a fresh :class:`httpx.AsyncClient` is opened per send
    and closed via the context manager so there is no shared pool to leak
    across the per-send ``asyncio.run`` event loops.
    """

    channel_type: str = "teams"

    def __init__(self, settings: Settings | None = None) -> None:
        # Defer the settings read so importing the module (and registering the
        # adapter below) never constructs Settings at import time — the
        # registry instance would otherwise read env on import.
        self._settings = settings

    @property
    def settings(self) -> Settings:
        return self._settings if self._settings is not None else get_settings()

    async def send(self, message: ChannelMessage) -> DeliveryResult:
        """Deliver ``message`` to its Teams channel as an Adaptive Card.

        Raises:
            ChannelSendError: the channel is missing its webhook URL, or the
                webhook rejects the post (non-2xx) / the request fails at the
                transport level. The dispatcher maps this to a ``failed`` log +
                dead-letter (it never auto-retries here).
        """
        settings = self.settings

        webhook_url = message.secret
        if not webhook_url:
            # The webhook URL is the channel secret (it embeds the routing
            # token); without it there is nowhere to post. resolve_channel_secret
            # upstream returns None only for a secretless channel — a teams
            # channel must carry one.
            raise ChannelSendError(
                "teams channel has no webhook URL (secret_ref / secret_encrypted)"
            )

        payload = self._build_payload(message, settings=settings)

        try:
            async with httpx.AsyncClient(timeout=settings.teams_request_timeout_s) as client:
                response = await client.post(
                    webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
        except httpx.HTTPError as exc:
            # Connection refused / DNS / read timeout, etc. — a transport
            # failure. Never include the webhook URL (it carries the secret
            # token) in the surfaced message.
            raise ChannelSendError(f"teams transport error: {type(exc).__name__}") from exc

        return self._interpret_response(response)

    # ------------------------------------------------------------------
    # Payload + Adaptive Card construction.
    # ------------------------------------------------------------------
    def _build_payload(self, message: ChannelMessage, *, settings: Settings) -> dict[str, Any]:
        """Wrap the Adaptive Card in the Teams ``message``/``attachments`` envelope."""
        return {
            "type": "message",
            "attachments": [
                {
                    "contentType": _ADAPTIVE_CARD_CONTENT_TYPE,
                    "contentUrl": None,
                    "content": self._build_adaptive_card(message, settings=settings),
                }
            ],
        }

    def _build_adaptive_card(
        self, message: ChannelMessage, *, settings: Settings
    ) -> dict[str, Any]:
        """Build the Adaptive Card ``content`` from the rendered message.

        Body layout (most-informative first):

          * a bold title ``TextBlock`` from the rendered subject — coloured by
            severity (``attention`` / ``warning`` / …). Omitted when there is
            no subject so we never emit an empty title block.
          * a body ``TextBlock`` from the rendered body (always present; the
            template render guarantees a body), wrapped over multiple lines.
          * a ``FactSet`` reflecting the event type + severity when
            ``structured`` carries them, so the metadata reads at a glance.
        """
        structured = message.structured or {}
        version = str(
            (message.config or {}).get("card_version", settings.teams_adaptive_card_version)
        )

        body: list[dict[str, Any]] = []

        subject = structured.get("subject")
        if subject:
            title_block: dict[str, Any] = {
                "type": "TextBlock",
                "text": self._truncate(str(subject), _MAX_TITLE_LEN),
                "weight": "Bolder",
                "size": "Large",
                "wrap": True,
            }
            color = self._severity_color(structured.get("severity"))
            if color is not None:
                title_block["color"] = color
            body.append(title_block)

        # The body TextBlock is always present (the message is never blank).
        body.append(
            {
                "type": "TextBlock",
                "text": self._truncate(message.body, _MAX_BODY_LEN),
                "wrap": True,
            }
        )

        facts = self._build_facts(structured)
        if facts:
            body.append({"type": "FactSet", "facts": facts})

        return {
            "$schema": _ADAPTIVE_CARD_SCHEMA,
            "type": "AdaptiveCard",
            "version": version,
            "body": body,
        }

    @staticmethod
    def _build_facts(structured: dict[str, Any]) -> list[dict[str, str]]:
        """Compose the FactSet rows from the event metadata.

        Returns the event type + severity as ``{title, value}`` rows (only the
        keys that are present), so a misconfigured / sparse event context still
        produces a valid (possibly empty) FactSet caller-side.
        """
        facts: list[dict[str, str]] = []
        event_type = structured.get("event_type")
        if event_type:
            facts.append({"title": "Event", "value": str(event_type)})
        severity = structured.get("severity")
        if severity:
            facts.append({"title": "Severity", "value": str(severity).upper()})
        return facts

    @staticmethod
    def _severity_color(severity: Any) -> str | None:
        """Map a severity to an Adaptive Card TextBlock colour, or None."""
        if not severity:
            return None
        return _SEVERITY_COLORS.get(str(severity).lower())

    @staticmethod
    def _truncate(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[: limit - len(_TRUNCATION_SUFFIX)] + _TRUNCATION_SUFFIX

    # ------------------------------------------------------------------
    # Response interpretation.
    # ------------------------------------------------------------------
    def _interpret_response(self, response: httpx.Response) -> DeliveryResult:
        """Map an incoming-webhook response to a :class:`DeliveryResult` / raise.

        A Teams incoming webhook returns a 2xx (the legacy connector replies
        ``200`` with the body text ``1``; the Workflows connector replies
        ``202 Accepted``) on success and a non-2xx (``400`` for a malformed
        card, ``404`` for a revoked webhook, ``429`` when throttled) on
        failure. The webhook carries no per-message id, so a success reports no
        ``provider_message_id``. We treat any non-2xx as a terminal
        :class:`ChannelSendError`.
        """
        if response.is_success:
            return DeliveryResult(ok=True)

        # Failure: surface the status + a short snippet of the (non-secret)
        # response body, never the webhook URL. The dispatcher truncates before
        # it reaches the log.
        detail = response.text.strip()
        _log.warning(
            "notification_dispatcher.channels.teams.send_failed",
            status_code=response.status_code,
            error=detail or None,
        )
        suffix = f": {detail[:200]}" if detail else ""
        raise ChannelSendError(f"teams webhook failed (HTTP {response.status_code}){suffix}")


# Register the adapter at import time so `import notification_dispatcher.channels`
# wires it into the shared registry the dispatcher resolves channel_type through.
register_adapter(TeamsAdapter())


__all__ = ["TeamsAdapter"]
