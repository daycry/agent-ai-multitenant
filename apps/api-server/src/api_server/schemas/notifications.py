"""Pydantic schemas for the 3-layer notification-config endpoints (task_10_15).

The config UI spans the three CLAUDE.md scopes (platform → tenant → user):

  * **platform** — the System Admin enables which channel *platforms*
    (transports) are globally available. Stored as a ``platform_settings``
    key; shaped by :class:`PlatformChannelTypesResponse` / ...Update.
  * **tenant / user** — a Tenant Admin configures concrete channels and
    notification preferences. A channel's secret is NEVER echoed: the
    response carries only ``has_secret`` + ``secret_source``, mirroring the
    SSO config pattern.

These schemas only shape the payloads; the router does the RBAC + RLS gating.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from api_server.db.notification import (
    NotificationChannelType,
    NotificationScope,
)

_BASE_CONFIG = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

# How a stored channel secret is held (never the value itself). Mirrors the
# SSO ``client_secret_source`` discriminator.
SecretSource = Literal["vault", "encrypted"]

# The scopes a Tenant Admin may write through the config UI. ``platform`` is
# a System-Admin-only surface handled by a separate endpoint, so a tenant
# write is constrained to its own tenant- or user-owned rows.
_TENANT_WRITE_SCOPES = frozenset({NotificationScope.TENANT.value, NotificationScope.USER.value})


# ===========================================================================
# Platform layer — System Admin enables channel platforms globally
# ===========================================================================
class PlatformChannelTypesResponse(BaseModel):
    """The set of channel transports the System Admin has enabled platform-wide.

    A tenant may only configure channels whose transport is in this set.
    ``available`` is the full closed catalogue so the UI can render the
    on/off toggle for every transport.
    """

    model_config = _BASE_CONFIG

    enabled: list[str]
    available: list[str]


class PlatformChannelTypesUpdate(BaseModel):
    """System Admin sets which channel transports are globally enabled."""

    model_config = _BASE_CONFIG

    enabled: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_known_types(self) -> PlatformChannelTypesUpdate:
        known = {t.value for t in NotificationChannelType}
        unknown = [t for t in self.enabled if t not in known]
        if unknown:
            raise ValueError(f"unknown channel type(s): {', '.join(sorted(unknown))}")
        # Dedupe while preserving the catalogue order.
        self.enabled = [t.value for t in NotificationChannelType if t.value in set(self.enabled)]
        return self


# ===========================================================================
# Channels — tenant / user scoped CRUD (secret never echoed)
# ===========================================================================
class NotificationChannelResponse(BaseModel):
    """A configured channel, WITHOUT its secret.

    The secret is never returned: the UI only learns whether one is set
    (``has_secret``) and in which form (``secret_source``). ``config`` is the
    non-secret transport config (chat id, recipient, SMTP host/port, …) — the
    write path guarantees no clear secret ever lands there.
    """

    model_config = _BASE_CONFIG

    id: UUID
    scope: str
    channel_type: str
    name: str
    enabled: bool
    config: dict[str, Any]
    owner_user_id: UUID | None
    has_secret: bool
    secret_source: SecretSource | None
    created_at: datetime
    updated_at: datetime


class NotificationChannelCreate(BaseModel):
    """Create a tenant- or user-scoped channel.

    ``scope`` is constrained to ``tenant`` / ``user`` here — the platform
    layer is the System-Admin-only channel-types endpoint, not this CRUD.
    A ``user``-scoped channel is owned by the requesting Tenant Admin.
    """

    model_config = _BASE_CONFIG

    scope: str = Field(default=NotificationScope.TENANT.value)
    channel_type: str
    name: str = Field(min_length=1, max_length=160)
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)
    # Plaintext secret typed by the operator; encrypted at rest before it
    # touches the DB and never returned. Optional for a secretless transport
    # (e.g. ``in_app``).
    secret: str | None = Field(default=None, max_length=8192)

    @model_validator(mode="after")
    def _validate(self) -> NotificationChannelCreate:
        if self.scope not in _TENANT_WRITE_SCOPES:
            raise ValueError(
                "scope must be 'tenant' or 'user' (platform channels are System-Admin-only)"
            )
        known = {t.value for t in NotificationChannelType}
        if self.channel_type not in known:
            raise ValueError(f"unknown channel type: {self.channel_type}")
        # The config JSONB must never carry the clear secret (CLAUDE.md).
        for forbidden in ("secret", "token", "password", "api_key", "auth_token"):
            if forbidden in self.config:
                raise ValueError(
                    f"config must not contain the clear secret (found '{forbidden}'); "
                    "pass it via the 'secret' field instead"
                )
        return self


class NotificationChannelUpdate(BaseModel):
    """Partial update of a channel. Every field is optional.

    An omitted ``secret`` keeps the stored one; a non-empty ``secret``
    rotates it. ``enabled`` / ``name`` / ``config`` patch in place.
    """

    model_config = _BASE_CONFIG

    name: str | None = Field(default=None, min_length=1, max_length=160)
    enabled: bool | None = None
    config: dict[str, Any] | None = None
    secret: str | None = Field(default=None, max_length=8192)

    @model_validator(mode="after")
    def _validate(self) -> NotificationChannelUpdate:
        if self.config is not None:
            for forbidden in ("secret", "token", "password", "api_key", "auth_token"):
                if forbidden in self.config:
                    raise ValueError(
                        f"config must not contain the clear secret (found '{forbidden}'); "
                        "pass it via the 'secret' field instead"
                    )
        return self


# ===========================================================================
# Preferences — tenant / user scoped routing rules
# ===========================================================================
class NotificationPreferenceResponse(BaseModel):
    """A per-scope routing rule (event_type x channel_type -> opt in/out)."""

    model_config = _BASE_CONFIG

    id: UUID
    scope: str
    event_type: str
    channel_type: str
    enabled: bool
    owner_user_id: UUID | None
    quiet_hours_start: int | None
    quiet_hours_end: int | None
    quiet_hours_tz: str | None
    created_at: datetime
    updated_at: datetime


class NotificationPreferenceUpsert(BaseModel):
    """Create or update a routing rule for ``(event_type, channel_type)``.

    Upsert keyed on ``(tenant, owner, event_type, channel_type)`` — the same
    natural key as the table's unique constraint. ``scope`` selects the
    layer (``tenant`` shared default, ``user`` the requesting admin's own
    override).
    """

    model_config = _BASE_CONFIG

    scope: str = Field(default=NotificationScope.USER.value)
    event_type: str = Field(min_length=1, max_length=64)
    channel_type: str
    enabled: bool = True
    quiet_hours_start: int | None = Field(default=None, ge=0, le=1439)
    quiet_hours_end: int | None = Field(default=None, ge=0, le=1439)
    quiet_hours_tz: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _validate(self) -> NotificationPreferenceUpsert:
        if self.scope not in _TENANT_WRITE_SCOPES:
            raise ValueError("scope must be 'tenant' or 'user' (platform prefs are not set here)")
        known = {t.value for t in NotificationChannelType}
        if self.channel_type not in known:
            raise ValueError(f"unknown channel type: {self.channel_type}")
        # quiet hours must be set as a pair or not at all.
        a, b = self.quiet_hours_start, self.quiet_hours_end
        if (a is None) != (b is None):
            raise ValueError("quiet_hours_start and quiet_hours_end must both be set or both unset")
        return self


# ===========================================================================
# Inbox — paginated notification-log history (task_10_16)
# ===========================================================================
class NotificationLogResponse(BaseModel):
    """One send attempt in the in-app inbox, WITHOUT any secret.

    The log row is non-secret by construction (``target`` is a chat id /
    email / webhook URL, never a token), so the whole row is safe to surface.
    ``read`` is the per-user read marker for the requesting admin: ``true``
    when a ``notification_log_reads`` receipt exists for ``(caller, log)``.
    """

    model_config = _BASE_CONFIG

    id: UUID
    channel_id: UUID | None
    event_type: str
    channel_type: str
    status: str
    target: str | None
    attempt: int
    error: str | None
    sent_at: datetime | None
    created_at: datetime
    # AUD16-11: el contenido persistido para in_app (None en filas históricas
    # y en canales externos que no lo guardan).
    subject: str | None = None
    body: str | None = None
    read: bool


class NotificationInboxResponse(BaseModel):
    """A page of inbox history plus the counters the badge UI needs.

    ``items`` is the requested ``limit``/``offset`` window (newest first);
    ``total`` / ``unread`` are the full tenant+user-scoped counts so the UI
    can render pagination + an unread badge without a second round-trip.
    """

    model_config = _BASE_CONFIG

    items: list[NotificationLogResponse]
    total: int
    unread: int
    limit: int
    offset: int


class MarkReadResponse(BaseModel):
    """Result of marking inbox item(s) read — the new unread count."""

    model_config = _BASE_CONFIG

    marked: int = Field(description="How many receipts this call newly created.")
    unread: int = Field(description="The caller's remaining unread count.")


__all__ = [
    "MarkReadResponse",
    "NotificationChannelCreate",
    "NotificationChannelResponse",
    "NotificationChannelUpdate",
    "NotificationInboxResponse",
    "NotificationLogResponse",
    "NotificationPreferenceResponse",
    "NotificationPreferenceUpsert",
    "PlatformChannelTypesResponse",
    "PlatformChannelTypesUpdate",
    "SecretSource",
]
