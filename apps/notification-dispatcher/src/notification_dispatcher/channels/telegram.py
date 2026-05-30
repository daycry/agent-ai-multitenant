"""Telegram channel adapter (Plan 10 Fase B task_10_05).

Delivers a rendered notification to a Telegram chat via the Bot API
``sendMessage`` method. We drive the documented HTTP API directly with
``httpx`` rather than the ``python-telegram-bot`` SDK: a send is a single
``POST {base}/bot{token}/sendMessage`` with a JSON body, and the SDK ships
its own connection-pool / event-loop machinery that is awkward inside the
short-lived per-send ``asyncio.run`` the Celery dispatcher uses. httpx
keeps the async send path uniform with the other HTTP-POST channels
(WhatsApp Cloud API, Teams / Discord webhooks) and lets tests inject an
``httpx.MockTransport`` so no adapter ever touches the real network.

Secret handling (CLAUDE.md: NO plaintext secrets, never logged). The bot
token is the channel's secret. The dispatcher resolves it IN MEMORY via
:func:`notification_dispatcher.secrets.resolve_channel_secret` (Vault
``secret_ref`` or Fernet ``secret_encrypted`` — never plaintext in the DB)
and hands it to the adapter as :attr:`ChannelMessage.secret`. This adapter
reads the token only from there, puts it solely in the request URL path
(never in a log line, never in the JSON body), and never persists it.

Target + transport config. The destination ``chat_id`` is non-secret and
arrives as :attr:`ChannelMessage.target` (the dispatcher fills it from the
send request or the channel's ``config`` — ``chat_id`` / ``target``). The
``parse_mode`` (HTML by default, matching the autoescaped telegram
template channel) and an optional ``disable_web_page_preview`` /
``disable_notification`` come from the non-secret channel ``config``;
everything else is a config tunable on
:class:`~notification_dispatcher.config.Settings`, never a magic number.
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

_log = structlog.get_logger("notification_dispatcher.channels.telegram")

# Telegram parse_modes the Bot API accepts. An empty string => plain text
# (we simply omit the field). A channel config value outside this set is a
# misconfiguration we reject loudly rather than letting Telegram 400 on it.
_VALID_PARSE_MODES: frozenset[str] = frozenset({"HTML", "MarkdownV2", "Markdown", ""})

# Telegram caps a sendMessage text at 4096 UTF-16 code units. We truncate
# defensively so an over-long rendered body becomes a (delivered, trimmed)
# message instead of a hard 400 from the API. Tunable here, not inline.
_MAX_TELEGRAM_TEXT_LEN = 4096
_TRUNCATION_SUFFIX = "…"


class TelegramAdapter:
    """``telegram`` channel adapter — POST ``sendMessage`` to the Bot API.

    Implements the :class:`~notification_dispatcher.adapters.ChannelAdapter`
    Protocol. Stateless: a fresh :class:`httpx.AsyncClient` is opened per
    send and closed in a ``finally`` so there is no shared pool to leak
    across the per-send ``asyncio.run`` event loops.
    """

    channel_type: str = "telegram"

    def __init__(self, settings: Settings | None = None) -> None:
        # Defer settings read so importing the module (and registering the
        # adapter) never constructs Settings at import time — the registry
        # instance below would otherwise read env on import.
        self._settings = settings

    @property
    def settings(self) -> Settings:
        return self._settings if self._settings is not None else get_settings()

    async def send(self, message: ChannelMessage) -> DeliveryResult:
        """Deliver ``message`` to its Telegram chat.

        Raises:
            ChannelSendError: the channel is missing its bot token / chat id,
                is misconfigured (bad ``parse_mode``), or the Bot API rejects
                the send (HTTP 4xx/5xx or ``ok: false``) / the request fails
                at the transport level. The dispatcher maps this to a
                ``failed`` log + dead-letter (it never auto-retries here).
        """
        settings = self.settings

        token = message.secret
        if not token:
            # The bot token is the channel secret; without it there is
            # nothing to authenticate with. resolve_channel_secret upstream
            # returns None only for a secretless channel — a telegram
            # channel must carry one.
            raise ChannelSendError(
                "telegram channel has no bot token (secret_ref / secret_encrypted)"
            )

        chat_id = message.target
        if not chat_id:
            raise ChannelSendError("telegram channel has no chat_id (target)")

        payload = self._build_payload(message, chat_id=chat_id, settings=settings)
        url = self._send_message_url(token, settings=settings)

        try:
            async with httpx.AsyncClient(timeout=settings.telegram_request_timeout_s) as client:
                response = await client.post(url, json=payload)
        except httpx.HTTPError as exc:
            # Connection refused / DNS / read timeout, etc. — a transport
            # failure. Never include the URL (it carries the token) in the
            # surfaced message.
            raise ChannelSendError(f"telegram transport error: {type(exc).__name__}") from exc

        return self._interpret_response(response, chat_id=chat_id)

    # ------------------------------------------------------------------
    # Payload + URL construction.
    # ------------------------------------------------------------------
    def _build_payload(
        self, message: ChannelMessage, *, chat_id: str, settings: Settings
    ) -> dict[str, Any]:
        """Build the ``sendMessage`` JSON body from the rendered message.

        ``parse_mode`` comes from the channel config (falling back to the
        configured default); an empty value sends plain text (the field is
        omitted). Optional booleans (``disable_web_page_preview``,
        ``disable_notification``) ride from the non-secret channel config.
        """
        config = message.config or {}

        parse_mode = str(config.get("parse_mode", settings.telegram_default_parse_mode))
        if parse_mode not in _VALID_PARSE_MODES:
            raise ChannelSendError(
                f"telegram channel has an invalid parse_mode {parse_mode!r} "
                f"(expected one of {sorted(_VALID_PARSE_MODES)})"
            )

        text = self._truncate(message.body)
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if "disable_web_page_preview" in config:
            payload["disable_web_page_preview"] = bool(config["disable_web_page_preview"])
        if "disable_notification" in config:
            payload["disable_notification"] = bool(config["disable_notification"])
        return payload

    @staticmethod
    def _truncate(text: str) -> str:
        if len(text) <= _MAX_TELEGRAM_TEXT_LEN:
            return text
        return text[: _MAX_TELEGRAM_TEXT_LEN - len(_TRUNCATION_SUFFIX)] + _TRUNCATION_SUFFIX

    @staticmethod
    def _send_message_url(token: str, *, settings: Settings) -> str:
        """Build the sendMessage endpoint. The token lives ONLY in the URL
        path here — it is never logged and never placed in the JSON body."""
        base = settings.telegram_api_base_url.rstrip("/")
        return f"{base}/bot{token}/sendMessage"

    # ------------------------------------------------------------------
    # Response interpretation.
    # ------------------------------------------------------------------
    def _interpret_response(self, response: httpx.Response, *, chat_id: str) -> DeliveryResult:
        """Map a Bot API response to a :class:`DeliveryResult` / raise.

        Telegram returns ``{"ok": true, "result": {"message_id": ...}}`` on
        success and ``{"ok": false, "error_code": N, "description": "..."}``
        with a 4xx/5xx status on failure. We treat any non-2xx OR ``ok:
        false`` as a terminal :class:`ChannelSendError`.
        """
        try:
            body = response.json()
        except ValueError:
            body = {}

        if response.is_success and isinstance(body, dict) and body.get("ok") is True:
            result = body.get("result") or {}
            message_id = result.get("message_id")
            return DeliveryResult(
                ok=True,
                provider_message_id=(str(message_id) if message_id is not None else None),
            )

        # Failure: surface Telegram's own description + error_code (never the
        # token / URL). The dispatcher truncates before it reaches the log.
        description = ""
        error_code: Any = response.status_code
        if isinstance(body, dict):
            description = str(body.get("description", "")).strip()
            error_code = body.get("error_code", response.status_code)
        _log.warning(
            "notification_dispatcher.channels.telegram.send_failed",
            chat_id=chat_id,
            status_code=response.status_code,
            error_code=error_code,
        )
        detail = description or f"HTTP {response.status_code}"
        raise ChannelSendError(f"telegram sendMessage failed (error_code={error_code}): {detail}")


# Register the adapter at import time so `import notification_dispatcher.channels`
# wires it into the shared registry the dispatcher resolves channel_type through.
register_adapter(TelegramAdapter())


__all__ = ["TelegramAdapter"]
