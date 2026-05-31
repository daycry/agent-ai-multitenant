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

from api_server.webhooks.actions import (
    ActionResult,
    MissingTargetTaskError,
    execute_action,
)
from api_server.webhooks.mapping import (
    InvalidMappingError,
    ResolvedAction,
    WebhookActionKind,
    render_template,
    resolve_action,
)
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
    "ActionResult",
    "IncomingWebhookOrigin",
    "InvalidMappingError",
    "MalformedPayloadError",
    "MissingTargetTaskError",
    "NormalizedEvent",
    "ResolvedAction",
    "SignatureVerificationResult",
    "UnknownOriginError",
    "WebhookActionKind",
    "WebhookEventType",
    "WebhookTemplate",
    "WebhookTemplateError",
    "execute_action",
    "get_template",
    "parse_incoming_event",
    "render_template",
    "resolve_action",
    "verify_incoming_signature",
]
