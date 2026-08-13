"""Email channel adapter (Plan 10 Fase B task_10_06).

Delivers a rendered notification as an email. Two send paths:

  * **SMTP (primary)** — an :class:`email.message.EmailMessage` (subject +
    text body, optional HTML alternative, From / To) is sent with
    ``aiosmtplib`` over STARTTLS (submission, port 587) or implicit TLS
    (port 465) per the non-secret channel ``config``. The SMTP password is
    the channel secret, resolved IN MEMORY by the dispatcher and handed to
    the adapter as :attr:`ChannelMessage.secret`. ``aiosmtplib.send`` owns
    the connect → STARTTLS/SSL → AUTH → MAIL/RCPT/DATA → QUIT lifecycle in a
    single awaitable, which fits the short-lived per-send ``asyncio.run`` the
    Celery dispatcher uses — no shared connection pool to leak across loops.

  * **SendGrid (optional)** — guarded behind ``config.provider='sendgrid'``.
    Driven over the documented v3 HTTP API (``POST /v3/mail/send``) with
    ``httpx`` rather than the heavy ``sendgrid`` SDK, mirroring the
    semgrep-style "only used when configured, its absence never breaks
    import / CI" pattern: nothing here imports ``sendgrid`` at module load,
    so a deployment that never opts in pays no import cost. Here the channel
    secret is the SendGrid API key (a Bearer token), again never logged and
    never persisted.

Secret handling (CLAUDE.md: NO plaintext secrets, never logged). The SMTP
password / SendGrid API key arrives as :attr:`ChannelMessage.secret`
(resolved from Vault ``secret_ref`` or Fernet ``secret_encrypted`` — never
plaintext in the DB). The adapter passes it straight to the transport
(``aiosmtplib`` auth / the SendGrid Authorization header) and never puts it
in a log line, the MIME message, or the surfaced error text.

Transport config (non-secret, from :attr:`ChannelMessage.config`):
``host`` / ``port`` (SMTP host + port), ``use_tls`` (implicit SSL, e.g.
465) / ``start_tls`` (STARTTLS, e.g. 587), ``username`` (SMTP login, the
*secret* is the password), ``from`` / ``from_email`` (sender), ``html`` /
``html_body`` (optional HTML alternative), ``provider`` (``smtp`` default
or ``sendgrid``). The subject is the rendered template subject, carried on
:attr:`ChannelMessage.structured` as ``{"subject": ...}`` (the dispatcher
fills it from the template render — see ``event_mapping``); the recipient
address is :attr:`ChannelMessage.target`. Per-send timeout / default
sender / default port come from
:class:`~notification_dispatcher.config.Settings`, never magic numbers.
"""

from __future__ import annotations

from email.message import EmailMessage
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

_log = structlog.get_logger("notification_dispatcher.channels.email")

# The SendGrid v3 mail-send endpoint (relative to the configured base URL).
_SENDGRID_SEND_PATH = "/v3/mail/send"


class EmailAdapter:
    """``email`` channel adapter — SMTP (primary) or SendGrid (optional).

    Implements the :class:`~notification_dispatcher.adapters.ChannelAdapter`
    Protocol. Stateless: each send opens (and tears down) its own transport,
    so there is no shared SMTP connection / httpx pool to leak across the
    per-send ``asyncio.run`` event loops the dispatcher uses.
    """

    channel_type: str = "email"

    def __init__(self, settings: Settings | None = None) -> None:
        # Defer the settings read so importing the module (and registering
        # the adapter below) never constructs Settings at import time.
        self._settings = settings

    @property
    def settings(self) -> Settings:
        return self._settings if self._settings is not None else get_settings()

    async def send(self, message: ChannelMessage) -> DeliveryResult:
        """Deliver ``message`` as an email.

        Raises:
            ChannelSendError: the channel is missing its recipient / sender,
                or the transport rejects the send (an SMTP error, a SendGrid
                4xx/5xx, a connection failure). The dispatcher maps this to a
                ``failed`` log + dead-letter (it never auto-retries here).
        """
        settings = self.settings
        config = message.config or {}

        recipient = message.target
        if not recipient:
            raise ChannelSendError("email channel has no recipient (target)")

        mime = self._build_mime(message, recipient=recipient, settings=settings)

        provider = str(config.get("provider", "smtp")).lower()
        if provider == "sendgrid":
            return await self._send_sendgrid(mime, message, settings=settings)
        return await self._send_smtp(mime, message, settings=settings)

    # ------------------------------------------------------------------
    # MIME construction (shared by both transports).
    # ------------------------------------------------------------------
    def _build_mime(
        self, message: ChannelMessage, *, recipient: str, settings: Settings
    ) -> EmailMessage:
        """Build the MIME message from the rendered template.

        Subject comes from the rendered template subject on
        ``message.structured['subject']`` (the dispatcher fills it — see
        ``event_mapping``); the text body is ``message.body``. When the
        channel config carries an HTML body (``html`` / ``html_body``) it is
        added as the rich alternative, making a proper multipart/alternative
        message (text first, then HTML).
        """
        config = message.config or {}
        structured = message.structured or {}

        sender = config.get("from") or config.get("from_email") or settings.email_default_from
        subject = str(structured.get("subject") or config.get("subject") or "")

        mime = EmailMessage()
        mime["From"] = str(sender)
        mime["To"] = recipient
        mime["Subject"] = subject
        mime.set_content(message.body)

        html_body = config.get("html") or config.get("html_body")
        if html_body:
            # text/plain stays the fallback; text/html becomes the rich
            # alternative (set_content above already wrote the plain part).
            mime.add_alternative(str(html_body), subtype="html")
        return mime

    # ------------------------------------------------------------------
    # SMTP path (primary) — aiosmtplib.
    # ------------------------------------------------------------------
    async def _send_smtp(
        self,
        mime: EmailMessage,
        message: ChannelMessage,
        *,
        settings: Settings,
    ) -> DeliveryResult:
        """Send ``mime`` over SMTP with ``aiosmtplib`` (STARTTLS / SSL + auth).

        The SMTP password is ``message.secret`` — passed straight to the auth
        step, never logged. TLS mode is wired from the channel config:
        ``use_tls`` (implicit SSL, port 465) XOR ``start_tls`` (STARTTLS,
        port 587, the default). A username without a password (or vice
        versa) just sends unauthenticated, which aiosmtplib handles.
        """
        # Imported lazily so a transport-level error type is only resolved
        # when the SMTP path actually runs (keeps the module import light and
        # lets tests monkeypatch ``aiosmtplib.send`` cleanly).
        import aiosmtplib

        config = message.config or {}
        host = str(config.get("host", "localhost"))
        port = int(config.get("port", settings.email_default_smtp_port))
        username = config.get("username")
        password = message.secret  # the channel secret — never logged.

        # TLS: implicit SSL (use_tls) takes precedence; otherwise STARTTLS is
        # the submission default. A channel can disable both for a plain relay.
        use_tls = bool(config.get("use_tls", False))
        start_tls = bool(config.get("start_tls", not use_tls))

        send_kwargs: dict[str, Any] = {
            "hostname": host,
            "port": port,
            "timeout": settings.email_request_timeout_s,
            "use_tls": use_tls,
            "start_tls": start_tls if not use_tls else False,
        }
        if username and password:
            send_kwargs["username"] = str(username)
            send_kwargs["password"] = password

        try:
            errors, _response = await aiosmtplib.send(mime, **send_kwargs)
        except aiosmtplib.SMTPException as exc:
            # SMTP-level failure (auth refused, recipient refused, connect /
            # timeout). Surface the exception TYPE + its message — never the
            # password (it is not in the exception text). The dispatcher
            # truncates before it reaches the log.
            _log.warning(
                "notification_dispatcher.channels.email.smtp_send_failed",
                host=host,
                port=port,
                error_type=type(exc).__name__,
            )
            raise ChannelSendError(f"email SMTP send failed: {type(exc).__name__}: {exc}") from exc

        # aiosmtplib.send returns ({recipient: SMTPResponse} of REFUSED
        # recipients, final_response). An empty errors dict => all accepted.
        if errors:
            refused = ", ".join(sorted(errors))
            raise ChannelSendError(f"email SMTP send refused recipient(s): {refused}")

        return DeliveryResult(ok=True, provider_message_id=self._message_id(mime))

    # ------------------------------------------------------------------
    # SendGrid path (optional) — documented v3 HTTP API via httpx.
    # ------------------------------------------------------------------
    async def _send_sendgrid(
        self,
        mime: EmailMessage,
        message: ChannelMessage,
        *,
        settings: Settings,
    ) -> DeliveryResult:
        """Send via the SendGrid v3 API (``POST /v3/mail/send``).

        The channel secret is the SendGrid API key (a Bearer token) — set in
        the Authorization header only, never logged / never in the body. A
        2xx (SendGrid returns 202 Accepted) is success; any other status is a
        terminal :class:`ChannelSendError`.
        """
        api_key = message.secret
        if not api_key:
            raise ChannelSendError(
                "email channel uses provider=sendgrid but has no API key "
                "(secret_ref / secret_encrypted)"
            )

        payload = self._sendgrid_payload(mime)
        url = f"{settings.sendgrid_api_base_url.rstrip('/')}{_SENDGRID_SEND_PATH}"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        try:
            async with httpx.AsyncClient(timeout=settings.email_request_timeout_s) as client:
                response = await client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise ChannelSendError(f"email SendGrid transport error: {type(exc).__name__}") from exc

        if response.is_success:
            # SendGrid returns the queued message id in X-Message-Id.
            return DeliveryResult(
                ok=True,
                provider_message_id=response.headers.get("X-Message-Id"),
            )

        _log.warning(
            "notification_dispatcher.channels.email.sendgrid_send_failed",
            status_code=response.status_code,
        )
        raise ChannelSendError(f"email SendGrid send failed: HTTP {response.status_code}")

    @staticmethod
    def _sendgrid_payload(mime: EmailMessage) -> dict[str, Any]:
        """Build the SendGrid v3 ``mail/send`` JSON body from the MIME message.

        Reuses the already-built MIME so the From / To / Subject / bodies are
        defined in exactly one place. text/plain → text/html content order
        matches SendGrid's "increasing preference" requirement.
        """
        to_addr = mime["To"]
        from_addr = mime["From"]
        subject = mime["Subject"] or ""

        content: list[dict[str, str]] = []
        plain = _body_for(mime, "plain")
        if plain is not None:
            content.append({"type": "text/plain", "value": plain})
        html = _body_for(mime, "html")
        if html is not None:
            content.append({"type": "text/html", "value": html})
        if not content:  # pragma: no cover - defensive: always have a body
            content.append({"type": "text/plain", "value": ""})

        return {
            "personalizations": [{"to": [{"email": str(to_addr)}]}],
            "from": {"email": str(from_addr)},
            "subject": str(subject),
            "content": content,
        }

    @staticmethod
    def _message_id(mime: EmailMessage) -> str | None:
        """Return the MIME Message-ID if Python assigned one, else None."""
        value = mime["Message-ID"]
        return str(value) if value is not None else None


def _body_for(mime: EmailMessage, subtype: str) -> str | None:
    """Return the decoded body of the ``text/<subtype>`` part, or None.

    Handles both the single-part (``set_content`` only) and the multipart/
    alternative (``set_content`` + ``add_alternative``) shapes the adapter
    builds, so the SendGrid payload reflects exactly what the MIME carries.
    """
    for part in mime.walk():
        # walk() on an EmailMessage yields EmailMessage parts, so get_content()
        # is available (typeshed narrows EmailMessage.walk() accordingly).
        if part.get_content_maintype() != "text":
            continue
        if part.get_content_subtype() != subtype:
            continue
        payload = part.get_content()
        return payload if isinstance(payload, str) else None
    return None


# Register the adapter at import time so `import notification_dispatcher.channels`
# wires it into the shared registry the dispatcher resolves channel_type through.
register_adapter(EmailAdapter())


__all__ = ["EmailAdapter"]
