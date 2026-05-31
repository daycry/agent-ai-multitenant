"""SMS channel adapter via Twilio (Plan 10 Fase C task_10_11).

Delivers a rendered notification to a phone number as an SMS through the
**Twilio Messages REST API** — a single
``POST {base}/{version}/Accounts/{AccountSid}/Messages.json`` with an
``application/x-www-form-urlencoded`` body (``To`` / ``From`` / ``Body``) and
**HTTP Basic auth** ``AccountSid:AuthToken``. We drive the documented HTTP API
directly with ``httpx`` rather than pulling the heavy ``twilio`` SDK — exactly
like the other HTTP channels (Telegram, Slack, the SendGrid email path, the
WhatsApp Cloud API, the Teams / Discord webhooks). The ``twilio`` Python SDK
wraps this same endpoint but ships its own synchronous ``requests``-backed HTTP
client, which is awkward inside the short-lived per-send ``asyncio.run`` the
Celery dispatcher uses; httpx keeps the async send path uniform and lets tests
inject an ``httpx.MockTransport`` so the adapter never touches the real network.
**Substitution noted**: SMS = Twilio Messages REST API over httpx, NOT the
``twilio`` SDK (matches the no-heavy-SDK convention).

SMS is plain text. There is no markup / structured payload: the adapter sends
the rendered :attr:`ChannelMessage.body` as the ``Body`` form field, truncated
to a sane cap (``sms_max_body_len``) so an over-long rendered body becomes a
delivered, trimmed message instead of a hard 400 from Twilio (Twilio itself
auto-segments a body into multiple GSM-7 / UCS-2 parts up to that cap).

Secret handling (CLAUDE.md: NO plaintext secrets, never logged). The Twilio
**AuthToken** is the channel's secret. The dispatcher resolves it IN MEMORY via
:func:`notification_dispatcher.secrets.resolve_channel_secret` (Vault
``secret_ref`` or Fernet ``secret_encrypted`` — never plaintext in the DB) and
hands it to the adapter as :attr:`ChannelMessage.secret`. This adapter reads the
AuthToken only from there, puts it solely in the HTTP **Basic auth** header
(``Authorization: Basic base64(AccountSid:AuthToken)`` — never in a log line,
never in the URL, never in the form body), and never persists it. The
``AccountSid`` (a non-secret Twilio account id) and the sender ``From`` /
recipient ``To`` (non-secret) ride the non-secret channel config / message. The
Twilio base URL, API version, default sender, body cap and per-request timeout
are config tunables on :class:`~notification_dispatcher.config.Settings`, never
magic numbers.
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

_log = structlog.get_logger("notification_dispatcher.channels.sms")

_TRUNCATION_SUFFIX = "…"


class SmsAdapter:
    """``sms`` channel adapter — POST a Message to the Twilio Messages REST API.

    Implements the :class:`~notification_dispatcher.adapters.ChannelAdapter`
    Protocol. Stateless: a fresh :class:`httpx.AsyncClient` is opened per send
    and closed via the context manager so there is no shared pool to leak across
    the per-send ``asyncio.run`` event loops.
    """

    channel_type: str = "sms"

    def __init__(self, settings: Settings | None = None) -> None:
        # Defer the settings read so importing the module (and registering the
        # adapter below) never constructs Settings at import time — the registry
        # instance would otherwise read env on import.
        self._settings = settings

    @property
    def settings(self) -> Settings:
        return self._settings if self._settings is not None else get_settings()

    async def send(self, message: ChannelMessage) -> DeliveryResult:
        """Deliver ``message`` as a Twilio SMS.

        Raises:
            ChannelSendError: the channel is missing its AuthToken / AccountSid /
                sender (``From``) / recipient (``To``), or Twilio rejects the
                send (HTTP 4xx/5xx, e.g. a 400 invalid number / 401 bad
                credentials / a 21xxx Twilio error code) / the request fails at
                the transport level. The dispatcher maps this to a ``failed`` log
                + dead-letter (it never auto-retries here).
        """
        settings = self.settings

        auth_token = message.secret
        if not auth_token:
            # The AuthToken is the channel secret; without it there is nothing to
            # authenticate with. resolve_channel_secret upstream returns None only
            # for a secretless channel — an sms channel must carry one.
            raise ChannelSendError(
                "sms channel has no Twilio AuthToken (secret_ref / secret_encrypted)"
            )

        config = message.config or {}
        account_sid = config.get("account_sid")
        if not account_sid:
            raise ChannelSendError("sms channel has no Twilio AccountSid (config.account_sid)")

        sender = config.get("from") or config.get("from_number") or settings.sms_default_from
        if not sender:
            raise ChannelSendError(
                "sms channel has no sender (config.from / config.from_number / "
                "NOTIFY_SMS_DEFAULT_FROM)"
            )

        to = message.target
        if not to:
            raise ChannelSendError("sms channel has no recipient (target)")

        # SMS is plain text: the rendered body IS the message (truncated to cap).
        body = self._truncate(message.body, settings.sms_max_body_len)
        form = {"To": to, "From": str(sender), "Body": body}
        url = self._messages_url(str(account_sid), settings=settings)
        # HTTP Basic auth AccountSid:AuthToken — the AuthToken lives ONLY in the
        # Authorization header httpx builds from this, never in the URL / body /
        # a log line.
        auth = httpx.BasicAuth(str(account_sid), auth_token)

        try:
            async with httpx.AsyncClient(timeout=settings.twilio_request_timeout_s) as client:
                response = await client.post(url, data=form, auth=auth)
        except httpx.HTTPError as exc:
            # Connection refused / DNS / read timeout, etc. — a transport
            # failure. Never include the AuthToken in the surfaced message.
            raise ChannelSendError(f"sms transport error: {type(exc).__name__}") from exc

        return self._interpret_response(response, to=to)

    # ------------------------------------------------------------------
    # Body + URL construction.
    # ------------------------------------------------------------------
    @staticmethod
    def _truncate(text: str, max_len: int) -> str:
        if len(text) <= max_len:
            return text
        return text[: max_len - len(_TRUNCATION_SUFFIX)] + _TRUNCATION_SUFFIX

    @staticmethod
    def _messages_url(account_sid: str, *, settings: Settings) -> str:
        """Build the Twilio ``Messages.json`` endpoint for the account.

        ``{base}/{version}/Accounts/{AccountSid}/Messages.json`` — none of these
        are secret (the AuthToken authenticates the call via the Basic-auth
        header)."""
        base = settings.twilio_api_base_url.rstrip("/")
        version = settings.twilio_api_version.strip("/")
        return f"{base}/{version}/Accounts/{account_sid}/Messages.json"

    # ------------------------------------------------------------------
    # Response interpretation.
    # ------------------------------------------------------------------
    def _interpret_response(self, response: httpx.Response, *, to: str) -> DeliveryResult:
        """Map a Twilio Messages response to a :class:`DeliveryResult` / raise.

        Twilio returns ``201 Created`` with ``{"sid": "SM...", "status":
        "queued", ...}`` on a successful enqueue and a non-2xx (``400`` bad
        request / invalid number, ``401`` bad credentials, a 21xxx error)
        carrying ``{"code": N, "message": "...", "status": N, ...}`` on failure.
        We treat any non-2xx as a terminal :class:`ChannelSendError`.
        """
        try:
            body = response.json()
        except ValueError:
            body = {}

        if response.is_success and isinstance(body, dict):
            sid = body.get("sid")
            return DeliveryResult(
                ok=True,
                provider_message_id=(str(sid) if sid is not None else None),
            )

        # Failure: surface Twilio's own error message + code (never the
        # AuthToken). The dispatcher truncates before it reaches the log.
        error_code: Any = response.status_code
        detail = ""
        if isinstance(body, dict):
            detail = str(body.get("message", "")).strip()
            error_code = body.get("code", response.status_code)
        _log.warning(
            "notification_dispatcher.channels.sms.send_failed",
            to=to,
            status_code=response.status_code,
            error_code=error_code,
        )
        suffix = detail or f"HTTP {response.status_code}"
        raise ChannelSendError(f"sms send failed (code={error_code}): {suffix}")


# Register the adapter at import time so `import notification_dispatcher.channels`
# wires it into the shared registry the dispatcher resolves channel_type through.
register_adapter(SmsAdapter())


__all__ = ["SmsAdapter"]
