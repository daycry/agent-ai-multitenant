"""WhatsApp Cloud API channel adapter (Plan 10 Fase C task_10_10).

Delivers a notification to a WhatsApp number via the Meta **WhatsApp Cloud
API** — a plain ``POST {base}/{version}/{phone_number_id}/messages`` with a
JSON body and a ``Bearer`` access token. We drive the documented HTTP Graph
API directly with ``httpx`` rather than a heavy SDK, exactly like the other
HTTP-POST channels (Telegram, Slack, the SendGrid email path, the Teams /
Discord webhooks). Tests inject an ``httpx.MockTransport`` so the adapter
never touches the real network.

Pre-approved templates (the WhatsApp constraint). WhatsApp does **not** allow
a business to send free-form text to a user who is outside the 24-hour
customer-service window: a *business-initiated* message MUST use a
**template** that Meta has pre-approved (the ``template`` message type). So
this adapter never sends free text — it sends a ``template`` message naming an
approved template + its language and supplying the body ``components`` /
``parameters``. To stop a typo / an un-approved template name from being
POSTed (Meta would reject it with a ``132001`` "template does not exist"
error, and worse it couples our code to template names Meta has not blessed),
we keep a small **registry** of the pre-approved template names → their
parameter mapping (how many body ``{{n}}`` placeholders the template has and
which message fields fill them). A send naming a template that is **not** in
the registry is rejected with :class:`ChannelSendError` *before* any HTTP call.

The template a send uses comes from ``structured.template`` (or the channel's
``config.template``); its body parameters are filled from
``structured.params`` (an explicit ordered list) when present, else mapped
from named message fields per the registry entry. The language code comes from
``structured.language`` / ``config.language`` / the registry default / the
configured global default.

Secret handling (CLAUDE.md: NO plaintext secrets, never logged). The Cloud API
access token is the channel's secret. The dispatcher resolves it IN MEMORY via
:func:`notification_dispatcher.secrets.resolve_channel_secret` (Vault
``secret_ref`` or Fernet ``secret_encrypted`` — never plaintext in the DB) and
hands it to the adapter as :attr:`ChannelMessage.secret`. This adapter reads
the token only from there, puts it solely in the ``Authorization: Bearer``
header (never in a log line, never in the JSON body), and never persists it.
The ``phone_number_id`` (the sender, a non-secret Graph object id) and the
recipient ``to`` (non-secret, ``ChannelMessage.target``) ride the non-secret
channel config / message. The Graph base URL, API version, default language
and per-request timeout are config tunables on
:class:`~notification_dispatcher.config.Settings`, never magic numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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

_log = structlog.get_logger("notification_dispatcher.channels.whatsapp")


@dataclass(frozen=True)
class WhatsAppTemplate:
    """A pre-approved WhatsApp template and how to fill its body parameters.

    ``name`` is the exact template name as approved in Meta Business Manager.
    ``param_fields`` is the ORDERED list of message field names whose values
    fill the template's body ``{{1}}``, ``{{2}}`` … placeholders (in order),
    so a registry entry both *declares* the template's arity and *maps* which
    rendered field fills each placeholder. An empty list is a template with no
    body parameters (a static notice). ``default_language`` pins the approved
    language/locale when a send does not name one.
    """

    name: str
    param_fields: tuple[str, ...] = ()
    default_language: str | None = None


# The registry of PRE-APPROVED templates. Only a template whose name is a key
# here may be sent — a name outside this set is rejected before any HTTP call
# (it would be a ``132001`` "template does not exist" from Meta otherwise, and
# silently coupling to an un-approved name is worse). Operators extend this as
# Meta approves new templates; the parameter mapping mirrors the placeholders
# in the approved template body. These three mirror the plan's Telegram/Email
# notification shapes (subject + body lines) so the same rendered fields fill
# them.
_PRE_APPROVED_TEMPLATES: dict[str, WhatsAppTemplate] = {
    # Generic one-line notice: body is "{{1}}" filled by the rendered body.
    "agentic_notification": WhatsAppTemplate(
        name="agentic_notification",
        param_fields=("body",),
    ),
    # Subject + body: "{{1}}" = subject, "{{2}}" = body. Used for richer events.
    "agentic_alert": WhatsAppTemplate(
        name="agentic_alert",
        param_fields=("subject", "body"),
    ),
    # A static, parameterless approved template (e.g. a fixed "you have a new
    # notification, open the app" nudge) — declares zero body parameters.
    "agentic_ping": WhatsAppTemplate(name="agentic_ping", param_fields=()),
}


@dataclass
class _SendPlan:
    """Resolved, validated inputs for one WhatsApp template send."""

    template: WhatsAppTemplate
    language: str
    params: list[str] = field(default_factory=list)


class WhatsAppAdapter:
    """``whatsapp`` channel adapter — POST a template message to the Cloud API.

    Implements the :class:`~notification_dispatcher.adapters.ChannelAdapter`
    Protocol. Stateless: a fresh :class:`httpx.AsyncClient` is opened per send
    and closed via the context manager so there is no shared pool to leak
    across the per-send ``asyncio.run`` event loops.
    """

    channel_type: str = "whatsapp"

    def __init__(
        self,
        settings: Settings | None = None,
        templates: dict[str, WhatsAppTemplate] | None = None,
    ) -> None:
        # Defer the settings read so importing the module (and registering the
        # adapter below) never constructs Settings at import time — the registry
        # instance would otherwise read env on import.
        self._settings = settings
        # The pre-approved template registry. Defaults to the module registry;
        # injectable so a deployment / test can supply its own approved set.
        self._templates = templates if templates is not None else _PRE_APPROVED_TEMPLATES

    @property
    def settings(self) -> Settings:
        return self._settings if self._settings is not None else get_settings()

    async def send(self, message: ChannelMessage) -> DeliveryResult:
        """Deliver ``message`` as a pre-approved WhatsApp template message.

        Raises:
            ChannelSendError: the channel is missing its access token / sender
                phone_number_id / recipient, the named template is NOT in the
                pre-approved registry (rejected before any HTTP call), or the
                Cloud API rejects the send (HTTP 4xx/5xx, e.g. a 400 / 401 / a
                ``131xx`` error) / the request fails at the transport level. The
                dispatcher maps this to a ``failed`` log + dead-letter (it never
                auto-retries here).
        """
        settings = self.settings

        token = message.secret
        if not token:
            # The access token is the channel secret; without it there is
            # nothing to authenticate with. resolve_channel_secret upstream
            # returns None only for a secretless channel — a whatsapp channel
            # must carry one.
            raise ChannelSendError(
                "whatsapp channel has no access token (secret_ref / secret_encrypted)"
            )

        config = message.config or {}
        # ADR 0109: `provider: "neonize"` desvía al sidecar whatsmeow
        # self-hosted (texto libre) — misma rama de config.provider que el
        # canal email (SMTP vs SendGrid). Default "cloud" = camino Meta intacto.
        if str(config.get("provider") or "cloud").lower() == "neonize":
            return await self._send_neonize(message, token=str(token), settings=settings)

        phone_number_id = config.get("phone_number_id")
        if not phone_number_id:
            raise ChannelSendError(
                "whatsapp channel has no phone_number_id (config.phone_number_id)"
            )

        to = message.target
        if not to:
            raise ChannelSendError("whatsapp channel has no recipient (target)")

        # Resolve + VALIDATE the template BEFORE building/POSTing anything: an
        # unknown / not-pre-approved template never reaches the network.
        plan = self._resolve_send_plan(message, settings=settings)

        payload = self._build_payload(to=to, plan=plan)
        url = self._messages_url(str(phone_number_id), settings=settings)
        # The token lives ONLY in the Authorization header — never in the JSON
        # body, never in a log line.
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=settings.whatsapp_request_timeout_s) as client:
                response = await client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            # Connection refused / DNS / read timeout, etc. — a transport
            # failure. Never include the token / URL in the surfaced message.
            raise ChannelSendError(f"whatsapp transport error: {type(exc).__name__}") from exc

        return self._interpret_response(response, to=to)

    # ------------------------------------------------------------------
    # ADR 0109: transporte alternativo neonize (sidecar whatsmeow).
    # ------------------------------------------------------------------
    async def _send_neonize(
        self, message: ChannelMessage, *, token: str, settings: Settings
    ) -> DeliveryResult:
        """Texto libre vía el sidecar neonize: ``POST {base}/send {to, text}``.

        Sin plantillas de Meta: el ``message.body`` ya renderizado (builtins
        ES/EN del Plan 10) ES el mensaje. El Bearer es el secreto del canal
        (token del sidecar, no de Meta). Un 409 ``not_paired`` significa que la
        sesión QR no está vinculada — error de canal accionable, no transporte."""
        config = message.config or {}
        to = message.target or config.get("to")
        if not to:
            raise ChannelSendError("whatsapp(neonize) channel has no recipient (target)")
        text = (message.body or "").strip()
        if not text:
            raise ChannelSendError("whatsapp(neonize) send has an empty body")

        base_url = str(config.get("base_url") or settings.whatsapp_neonize_base_url).rstrip("/")
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(
                timeout=settings.whatsapp_neonize_request_timeout_s
            ) as client:
                response = await client.post(
                    f"{base_url}/send", json={"to": str(to), "text": text}, headers=headers
                )
        except httpx.HTTPError as exc:
            raise ChannelSendError(
                f"whatsapp(neonize) transport error: {type(exc).__name__}"
            ) from exc

        if response.status_code >= 400:
            detail = ""
            try:
                detail = str((response.json() or {}).get("error") or "")
            except Exception:  # cuerpo no-JSON — el status basta
                detail = ""
            raise ChannelSendError(
                f"whatsapp(neonize) send failed: HTTP {response.status_code}"
                + (f" ({detail})" if detail else "")
            )
        provider_id: str | None = None
        try:
            body = response.json()
            if isinstance(body, dict) and body.get("id") is not None:
                provider_id = str(body["id"])
        except Exception:  # cuerpo no-JSON — el 2xx basta
            provider_id = None
        return DeliveryResult(ok=True, provider_message_id=provider_id)

    # ------------------------------------------------------------------
    # Template resolution + validation.
    # ------------------------------------------------------------------
    def _resolve_send_plan(self, message: ChannelMessage, *, settings: Settings) -> _SendPlan:
        """Resolve the template + language + body params, rejecting unknowns.

        The template name comes from ``structured.template`` then
        ``config.template``; it MUST be in the pre-approved registry or this
        raises (before any HTTP call). Body parameters come from
        ``structured.params`` (an explicit ordered list) when present, else are
        mapped from the named message fields the registry entry declares. The
        language comes from ``structured.language`` / ``config.language`` / the
        template's default / the configured global default.
        """
        structured = message.structured or {}
        config = message.config or {}

        template_name = structured.get("template") or config.get("template")
        if not template_name:
            raise ChannelSendError(
                "whatsapp send has no template name (structured.template / config.template); "
                "WhatsApp business-initiated messages require a pre-approved template"
            )

        template = self._templates.get(str(template_name))
        if template is None:
            # Not pre-approved → reject BEFORE sending. Surface the (non-secret)
            # name + the approved set so the misconfiguration is obvious.
            approved = sorted(self._templates)
            raise ChannelSendError(
                f"whatsapp template {template_name!r} is not pre-approved "
                f"(approved templates: {approved}); refusing to send"
            )

        language = (
            structured.get("language")
            or config.get("language")
            or template.default_language
            or settings.whatsapp_default_language
        )

        params = self._resolve_params(template, structured, fallback_body=message.body)
        return _SendPlan(template=template, language=str(language), params=params)

    @staticmethod
    def _resolve_params(
        template: WhatsAppTemplate,
        structured: dict[str, Any],
        *,
        fallback_body: str | None = None,
    ) -> list[str]:
        """Fill the template's body parameters in order.

        Explicit ``structured.params`` wins (an ordered list the caller built);
        otherwise each declared ``param_fields`` name is read from
        ``structured`` (missing → empty string, so a sparse context still
        produces a well-formed, if blank, parameter rather than a KeyError).
        The count is validated against the template's declared arity so a
        mismatched explicit list is caught before sending.

        NOTIF-1 (auditoría 2026-07-12): the ``body`` param falls back to the
        rendered ``message.body`` when ``structured`` lacks it — the historical
        pipeline only carried ``subject`` in structured, so every event-driven
        WhatsApp template went out with a BLANK body.
        """
        explicit = structured.get("params")
        if explicit is not None:
            params = [str(p) for p in explicit]
            if len(params) != len(template.param_fields):
                raise ChannelSendError(
                    f"whatsapp template {template.name!r} expects "
                    f"{len(template.param_fields)} body parameter(s), got {len(params)}"
                )
            return params
        params = []
        for field_name in template.param_fields:
            value = structured.get(field_name, "")
            if not value and field_name == "body" and fallback_body:
                value = fallback_body
            params.append(str(value))
        return params

    # ------------------------------------------------------------------
    # Payload + URL construction.
    # ------------------------------------------------------------------
    def _build_payload(self, *, to: str, plan: _SendPlan) -> dict[str, Any]:
        """Build the Cloud API ``template`` message body.

        Shape (Cloud API ``/messages``)::

            {
              "messaging_product": "whatsapp",
              "to": "<recipient>",
              "type": "template",
              "template": {
                "name": "<approved name>",
                "language": {"code": "en_US"},
                "components": [
                  {"type": "body", "parameters": [
                    {"type": "text", "text": "<p1>"}, ...
                  ]}
                ]
              }
            }

        A parameterless template omits ``components`` entirely (the Cloud API
        rejects an empty body component).
        """
        template_obj: dict[str, Any] = {
            "name": plan.template.name,
            "language": {"code": plan.language},
        }
        if plan.params:
            template_obj["components"] = [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": p} for p in plan.params],
                }
            ]
        return {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": template_obj,
        }

    @staticmethod
    def _messages_url(phone_number_id: str, *, settings: Settings) -> str:
        """Build the Cloud API ``/messages`` endpoint for the sender number.

        ``{base}/{version}/{phone_number_id}/messages`` — none of these are
        secret (the access token authenticates the call via the header)."""
        base = settings.whatsapp_api_base_url.rstrip("/")
        version = settings.whatsapp_api_version.strip("/")
        return f"{base}/{version}/{phone_number_id}/messages"

    # ------------------------------------------------------------------
    # Response interpretation.
    # ------------------------------------------------------------------
    def _interpret_response(self, response: httpx.Response, *, to: str) -> DeliveryResult:
        """Map a Cloud API response to a :class:`DeliveryResult` / raise.

        The Cloud API returns ``200`` with ``{"messages": [{"id": "wamid..."}]}``
        on success and a non-2xx (``400`` bad request, ``401`` bad token, a
        ``131xx`` business error) carrying ``{"error": {"message": ...,
        "code": N, ...}}`` on failure. We treat any non-2xx as a terminal
        :class:`ChannelSendError`.
        """
        try:
            body = response.json()
        except ValueError:
            body = {}

        if response.is_success and isinstance(body, dict):
            messages = body.get("messages") or []
            message_id = (
                messages[0].get("id") if messages and isinstance(messages[0], dict) else None
            )
            return DeliveryResult(
                ok=True,
                provider_message_id=(str(message_id) if message_id is not None else None),
            )

        # Failure: surface Meta's own error message + code (never the token /
        # URL). The dispatcher truncates before it reaches the log.
        error_code: Any = response.status_code
        detail = ""
        if isinstance(body, dict):
            error = body.get("error")
            if isinstance(error, dict):
                detail = str(error.get("message", "")).strip()
                error_code = error.get("code", response.status_code)
        _log.warning(
            "notification_dispatcher.channels.whatsapp.send_failed",
            to=to,
            status_code=response.status_code,
            error_code=error_code,
        )
        suffix = detail or f"HTTP {response.status_code}"
        raise ChannelSendError(f"whatsapp send failed (code={error_code}): {suffix}")


# Register the adapter at import time so `import notification_dispatcher.channels`
# wires it into the shared registry the dispatcher resolves channel_type through.
register_adapter(WhatsAppAdapter())


__all__ = ["WhatsAppAdapter", "WhatsAppTemplate"]
