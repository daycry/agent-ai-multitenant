"""Pydantic schemas for per-project incoming-webhook config management (task_13_11).

The project owner / Tenant Admin manages a project's INCOMING webhook configs
through ``/projects/{project_id}/incoming-webhooks`` (RBAC ``tenant_admin``, RLS
scoped to the caller's tenant + the path project). These schemas shape those
request / response bodies. They are the operator-facing CONFIG surface; the
PUBLIC receive endpoint (``/webhooks/incoming/{origin}/{config_id}``, task_13_08)
and the verify/map/act pipeline (task_13_09/10) are separate.

Secret handling (CLAUDE.md: no plaintext secrets, never echoed twice). The HMAC
signing secret is minted server-side and returned in the CREATE / ROTATE
response EXACTLY ONCE (:class:`IncomingWebhookConfigSecretResponse.signing_secret`)
so the operator can paste it into the external provider; it is stored only as
Fernet ciphertext and never appears in a list / get response. The clear value
can never be retrieved again — losing it means rotating.

The ``incoming_path`` (relative ``/webhooks/incoming/{origin}/{config_id}``) is
surfaced on every response so the UI can show the operator the exact URL to
register at the provider (prefixed with the deployment's public base URL by the
frontend). It is non-secret — the HMAC, not the URL, is the authentication.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from api_server.webhooks.mapping import WebhookActionKind
from api_server.webhooks.signatures import IncomingWebhookOrigin


class ActionMappingRule(BaseModel):
    """One ``event_type -> system action`` rule in a config's mapping list.

    Mirrors the rule shape :mod:`api_server.webhooks.mapping` interprets at
    receive time. Validated here so the UI gets a clean 422 instead of a config
    that only fails when a real event arrives: a ``comment`` / ``escalate``
    rule REQUIRES a ``target_task_id`` (it acts on an existing task);
    ``create_task`` must NOT set one (it creates a new task).
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    # The normalised event type the rule matches (e.g. "github.pull_request_review"),
    # or "*" / empty for a catch-all. Free-form: the closed WebhookEventType set is
    # provider-specific and the mapping layer matches by exact string, so we keep
    # this a bounded string rather than an enum (a config may target a type the UI
    # has not enumerated yet).
    event_type: str = Field(default="*", max_length=128)
    action: WebhookActionKind
    title_template: str | None = Field(default=None, max_length=2000)
    body_template: str | None = Field(default=None, max_length=8000)
    # Required for comment/escalate (the existing task they act on); forbidden
    # for create_task. Stored as a string (matches the JSONB rule shape).
    target_task_id: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _check_target_task_id(self) -> ActionMappingRule:
        target = (self.target_task_id or "").strip() or None
        if self.action is WebhookActionKind.CREATE_TASK:
            if target is not None:
                raise ValueError("create_task rule must not set target_task_id")
        elif target is None:
            raise ValueError(f"{self.action.value} rule requires a target_task_id")
        return self


class IncomingWebhookConfigCreateRequest(BaseModel):
    """Body for creating a per-project incoming-webhook config (tenant_admin).

    The operator picks the external ``origin`` (which selects the HMAC
    signature scheme), a human label, the optional ``event -> action``
    mappings and whether the config starts enabled. The signing secret is
    minted server-side and returned ONCE in the response.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    origin: IncomingWebhookOrigin
    name: str = Field(min_length=1, max_length=255)
    enabled: bool = Field(default=True)
    action_mappings: list[ActionMappingRule] = Field(default_factory=list)


class IncomingWebhookConfigUpdateRequest(BaseModel):
    """Body for editing a config's NON-secret fields (PATCH-like, tenant_admin).

    Only the fields present are changed (``model_fields_set``); the secret is
    never editable here — rotate it through the dedicated endpoint. ``origin``
    is immutable after create (the public URL embeds it and external providers
    are configured against it), so it is intentionally absent.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=255)
    enabled: bool | None = Field(default=None)
    action_mappings: list[ActionMappingRule] | None = Field(default=None)


class IncomingWebhookConfigResponse(BaseModel):
    """A config's metadata — NEVER the signing secret.

    Surfaces origin / name / enabled / mappings / last_event_at and the
    relative ``incoming_path`` the operator registers at the provider. The
    clear signing secret only ever appears in the CREATE / ROTATE response.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    origin: str
    name: str
    enabled: bool
    action_mappings: list[Any]
    last_event_at: datetime | None
    created_at: datetime
    updated_at: datetime
    # Relative path the external provider POSTs to; the UI prefixes the public
    # base URL. Non-secret (the HMAC is the auth). Computed in the router.
    incoming_path: str

    @field_validator("action_mappings", mode="before")
    @classmethod
    def _coerce_mappings(cls, value: Any) -> list[Any]:
        """A NULL/odd JSONB value degrades to an empty list rather than 500."""
        return list(value) if isinstance(value, list) else []


class IncomingWebhookDeliveryResponse(BaseModel):
    """One recorded incoming-webhook delivery — metadata only (task_13_11).

    The recent-deliveries view shows the operator that events are arriving and
    verifying, without exposing the raw body (that is replay territory,
    task_13_12) or any secret. ``delivery_id`` / ``event_type`` are the sender's
    own ids; ``verified`` is always true today (only verified events persist).
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    origin: str
    delivery_id: str | None
    event_type: str | None
    verified: bool
    received_at: datetime
    # Set when this row is a REPLAY (task_13_12) of an earlier delivery; the
    # original event's id. NULL for a genuine inbound delivery. Lets the
    # operator tell replays apart in the trail without exposing any payload.
    replayed_from_event_id: UUID | None = None


class IncomingWebhookReplayResponse(BaseModel):
    """The outcome of replaying a recorded delivery (task_13_12).

    A replay re-runs verify + parse + map + action against the STORED payload of
    a recorded delivery and is itself audited as a NEW delivery row.
    ``replay_event_id`` is that new audit row; ``source_event_id`` is the
    delivery that was re-run. ``action`` / ``task_id`` describe what the replay
    re-executed (both NULL when the stored payload maps to no action — the
    replay is still recorded). No raw body / signature / secret is exposed.
    """

    model_config = ConfigDict(from_attributes=True)

    replay_event_id: UUID
    source_event_id: UUID
    action: str | None = None
    task_id: UUID | None = None


class IncomingWebhookConfigSecretResponse(IncomingWebhookConfigResponse):
    """The create / rotate response — carries the clear secret EXACTLY ONCE.

    ``signing_secret`` is the clear HMAC secret to paste into the external
    provider; it is returned only here and never retrievable again (only its
    Fernet ciphertext is stored).
    """

    signing_secret: str


__all__ = [
    "ActionMappingRule",
    "IncomingWebhookConfigCreateRequest",
    "IncomingWebhookConfigResponse",
    "IncomingWebhookConfigSecretResponse",
    "IncomingWebhookConfigUpdateRequest",
    "IncomingWebhookDeliveryResponse",
    "IncomingWebhookReplayResponse",
]
