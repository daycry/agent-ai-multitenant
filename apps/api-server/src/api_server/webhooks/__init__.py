"""Incoming webhooks (Plan 13 Fase C).

External tools (GitHub, Jira, Sentry, Linear, GitLab, ...) POST events to a
PUBLIC, per-project endpoint (``/webhooks/incoming/{origin}/{config_id}``).
This is the INVERSE of Plan 10's OUTGOING webhooks: there we SIGN payloads we
send; here we VERIFY the HMAC signature an external sender stamps on ITS
payload, against a per-project secret, BEFORE doing any work.

Multi-tenancy (CLAUDE.md principle 1): a webhook config, its received events
and the actions it triggers are tenant + PROJECT scoped (``tenant_id`` +
``project_id`` + RLS). The ``{config_id}`` in the URL resolves to a specific
project's config and its tenant — an event for project A NEVER acts on
tenant B.
"""

from __future__ import annotations

from api_server.webhooks.signatures import (
    IncomingWebhookOrigin,
    SignatureVerificationResult,
    verify_incoming_signature,
)
from api_server.webhooks.templates import (
    MalformedPayloadError,
    NormalizedEvent,
    UnknownOriginError,
    WebhookEventType,
    WebhookTemplate,
    WebhookTemplateError,
    get_template,
    parse_incoming_event,
)

__all__ = [
    "IncomingWebhookOrigin",
    "MalformedPayloadError",
    "NormalizedEvent",
    "SignatureVerificationResult",
    "UnknownOriginError",
    "WebhookEventType",
    "WebhookTemplate",
    "WebhookTemplateError",
    "get_template",
    "parse_incoming_event",
    "verify_incoming_signature",
]
