"""Notification ORM models (Plan 10 task_10_01).

The multichannel notification substrate: a configured delivery
*channel*, a per-scope *preference* that routes which event types go to
which channels, and an append-only *log* of every send attempt. Channel
adapters (Telegram, Email, Slack, …), the dispatcher service, the
template engine, and the system-event mapping all build on these three
tables in the later tasks of Plan 10.

Three tables make up the substrate:

  - **``notification_channels``** — a configured delivery endpoint of a
    given ``type`` (telegram / email / slack / teams / discord /
    whatsapp / sms / webhook / in_app). **Hybrid tenancy** keyed on a
    ``scope`` discriminator:

      * ``scope='platform'`` → ``tenant_id`` IS NULL. A platform-wide
        channel the System Admin configures (e.g. the operator's ops
        Slack). Tenant-agnostic by design — like ``platform_settings``,
        it is the same for everyone and a tenant cannot override it.
      * ``scope='tenant'`` / ``scope='user'`` → ``tenant_id`` NOT NULL.
        A tenant-owned channel (a Tenant Admin's Telegram, the tenant's
        shared email sender, …). RLS (added in task_10_02's migration)
        isolates these per tenant exactly like every other tenant table.

    ``owner_user_id`` is set for ``scope='user'`` channels (the
    individual Tenant Admin who owns it) and NULL otherwise.

    Secret handling (CLAUDE.md: NO plaintext secrets in the DB). The
    non-secret transport config (chat id, recipient address, SMTP host /
    port, webhook URL, …) lives in the ``config`` JSONB. The channel's
    *secret* (bot token, SMTP password, webhook signing key, Twilio auth
    token, …) is stored in EXACTLY ONE of two never-plaintext forms,
    mirroring the SSO / SCIM / marketplace precedent:

      * ``secret_ref`` — a Vault pointer (``vault:<mount>/data/...``)
        resolved at send time. Preferred when Vault is wired.
      * ``secret_encrypted`` — Fernet ciphertext (encrypted at rest with
        the platform notification-encryption key) for deployments
        without Vault. The plaintext only ever lives in memory during a
        send.

    A CHECK constraint in task_10_02's migration enforces "at most one
    secret source, never both". The ``config`` JSONB must NEVER carry the
    clear secret — that invariant is asserted in the unit test and
    enforced by the service layer that writes channels.

  - **``notification_preferences``** — a per-scope routing rule: for a
    given ``event_type`` (``task_blocked``, ``plan_approved``,
    ``budget_alert``, …) on a given ``channel_type``, is delivery opted
    IN or OUT, and within which quiet-hours window. Same hybrid ``scope``
    tenancy as channels: platform defaults (NULL tenant), tenant
    overrides, and per-user (Tenant Admin) overrides. The dispatcher
    resolves the effective preference most-specific-wins
    (user → tenant → platform) in task_10_04.

  - **``notification_logs``** — append-only record of each send attempt:
    the resolved channel + event, the lifecycle ``status`` (queued /
    sent / delivered / failed / retrying / dead_letter), the ``target``
    the message was addressed to, the ``attempt`` count, any ``error``,
    and timestamps. **Tenant-owned, append-only** (no ``updated_at`` /
    ``deleted_at``) — mirrors ``executions`` / the foundations
    ``audit_log`` / ``marketplace_audit_entries``. A platform-scoped send
    (NULL tenant) is still recorded; the column is nullable for that
    reason, exactly like ``audit_log.tenant_id``.

This module ships ONLY the ORM shape + enums so the rest of Plan 10 can
build against a stable contract. The migration that creates the tables,
indexes, FKs, CHECK constraints, and RLS policies is task_10_02.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from api_server.db.base import (
    Base,
    SoftDeleteMixin,
    TenantScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


# =============================================================================
# Enums (StrEnum so values are stable strings persisted as TEXT)
# =============================================================================
class NotificationChannelType(enum.StrEnum):
    """The delivery transport a channel uses (spec §17 / Plan 10).

    Closed catalogue: adding a transport means adding a member here AND a
    channel adapter (Plan 10 Fase B/C). Never rename an existing member —
    persisted channel/log rows reference the string value.
    """

    TELEGRAM = "telegram"
    EMAIL = "email"
    SLACK = "slack"
    TEAMS = "teams"
    DISCORD = "discord"
    WHATSAPP = "whatsapp"
    SMS = "sms"
    WEBHOOK = "webhook"
    IN_APP = "in_app"


class NotificationScope(enum.StrEnum):
    """The ownership layer of a channel / preference (CLAUDE.md §6: the
    three-layer platform → tenant → user model).

    - ``platform``: configured by the System Admin; tenant-agnostic
      (``tenant_id`` IS NULL).
    - ``tenant``:   owned by a tenant; visible to all its admins
      (``tenant_id`` NOT NULL, ``owner_user_id`` NULL).
    - ``user``:     owned by an individual Tenant Admin (``tenant_id``
      NOT NULL, ``owner_user_id`` set).
    """

    PLATFORM = "platform"
    TENANT = "tenant"
    USER = "user"


class NotificationLocale(enum.StrEnum):
    """The two supported template locales (CLAUDE.md §12: ES + EN only).

    A template is keyed by ``(event_type, channel_type, locale)``. We do
    NOT invest in more locales in this version, so the catalogue is closed
    to these two members — adding a third would mean translating every
    builtin template, an explicit product decision.
    """

    ES = "es"
    EN = "en"


class NotificationStatus(enum.StrEnum):
    """Lifecycle of one send attempt recorded in ``notification_logs``.

    - ``queued``:      handed to the dispatcher, not yet attempted.
    - ``sent``:        accepted by the channel API (no delivery receipt yet).
    - ``delivered``:   delivery confirmed by the channel (when supported).
    - ``failed``:      a terminal failure for this attempt.
    - ``retrying``:    failed but scheduled for a backoff retry (Plan 10
                       Fase C task_10_13).
    - ``dead_letter``: exhausted retries; parked in the DLQ for manual
                       reprocessing (task_10_13).
    """

    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"
    RETRYING = "retrying"
    DEAD_LETTER = "dead_letter"


# =============================================================================
# notification_channels (hybrid: platform | tenant | user scope)
# =============================================================================
class NotificationChannel(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """A configured delivery channel.

    Tenancy decision: **hybrid, keyed on ``scope``**. We do NOT inherit
    :class:`~api_server.db.base.TenantScopedMixin` because that mixin
    declares ``tenant_id`` NOT NULL; a platform-scoped channel is
    tenant-agnostic (NULL ``tenant_id``), so the column is declared
    explicitly. RLS (task_10_02) follows the marketplace-listing pattern:
    a tenant sees/writes only its own (``tenant_id`` = current tenant)
    rows, and the BYPASSRLS dispatcher validates ownership at the task
    boundary on top of RLS.

    See the module docstring for the never-plaintext secret contract
    (``secret_ref`` XOR ``secret_encrypted``; ``config`` never holds the
    clear secret).
    """

    __tablename__ = "notification_channels"
    __table_args__ = (
        # Declarado aquí y no sólo en la migración: desde Alembic 1.19 el
        # autogenerate SÍ detecta los CHECK, así que uno que viva sólo en la
        # migración se lee como esquema que el modelo no conoce y el siguiente
        # `--autogenerate` propone BORRARLO. Ver
        # tests/integration/test_alembic_autogenerate_clean.py.
        CheckConstraint(
            "(scope = 'platform' AND tenant_id IS NULL)"
            " OR (scope IN ('tenant', 'user') AND tenant_id IS NOT NULL)",
            name="ck_notification_channels_scope_tenant",
        ),
        CheckConstraint(
            "NOT (secret_ref IS NOT NULL AND secret_encrypted IS NOT NULL)",
            name="ck_notification_channels_single_secret",
        ),
        # A given (scope, tenant, owner) holds at most one LIVE channel of
        # a name per type. NULLs (platform tenant_id, non-user owner) never
        # collide, so this dedupes cleanly per scope.
        UniqueConstraint(
            "tenant_id",
            "owner_user_id",
            "channel_type",
            "name",
            name="uq_notification_channels_scope_type_name",
        ),
        Index(
            "ix_notification_channels_tenant_enabled",
            "tenant_id",
            "enabled",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # Fast lookup of platform-wide channels (NULL tenant) by type.
        Index(
            "ix_notification_channels_platform_type",
            "channel_type",
            postgresql_where=text("tenant_id IS NULL AND deleted_at IS NULL"),
        ),
        Index(
            "ix_notification_channels_owner",
            "owner_user_id",
            postgresql_where=text("owner_user_id IS NOT NULL"),
        ),
    )

    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    channel_type: Mapped[str] = mapped_column(String(16), nullable=False)

    # NULL => platform-scoped (tenant-agnostic); NOT NULL => tenant/user
    # scoped. No FK to organizations to avoid the RLS/FK coupling the
    # foundations tables deliberately avoid; resolved by explicit queries.
    tenant_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True, index=True)
    # Set for scope='user' (the individual Tenant Admin who owns it); NULL
    # for platform / tenant scope.
    owner_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
    )

    # Human-friendly label shown in the channel-config UI.
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    # Non-secret transport config: chat id, recipient address, SMTP
    # host/port, webhook URL, … . MUST NOT contain the clear secret (see
    # secret_ref / secret_encrypted). JSONB so the shape evolves per
    # channel type migration-free.
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # The channel secret in EXACTLY ONE never-plaintext form (the other is
    # NULL). A CHECK constraint in task_10_02 enforces "at most one".
    #   * secret_ref:       a Vault pointer (preferred when Vault is wired).
    #   * secret_encrypted: Fernet ciphertext (encrypted at rest).
    secret_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"NotificationChannel(id={self.id!r}, scope={self.scope!r}, "
            f"type={self.channel_type!r}, name={self.name!r})"
        )


# =============================================================================
# notification_preferences (hybrid: platform | tenant | user scope)
# =============================================================================
class NotificationPreference(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """A per-scope routing rule: route ``event_type`` over ``channel_type``,
    opted in or out, within an optional quiet-hours window.

    Tenancy decision: **hybrid, keyed on ``scope``** — same model as
    :class:`NotificationChannel`. Platform rows (NULL tenant) are the
    defaults; tenant and user rows override them. The dispatcher resolves
    the effective preference most-specific-wins (user → tenant → platform)
    in task_10_04. ``tenant_id`` is declared explicitly (NULL-means-
    platform) rather than via :class:`TenantScopedMixin`.
    """

    __tablename__ = "notification_preferences"
    __table_args__ = (
        # Declarado aquí y no sólo en la migración: desde Alembic 1.19 el
        # autogenerate SÍ detecta los CHECK, así que uno que viva sólo en la
        # migración se lee como esquema que el modelo no conoce y el siguiente
        # `--autogenerate` propone BORRARLO. Ver
        # tests/integration/test_alembic_autogenerate_clean.py.
        CheckConstraint(
            "(scope = 'platform' AND tenant_id IS NULL)"
            " OR (scope IN ('tenant', 'user') AND tenant_id IS NOT NULL)",
            name="ck_notification_preferences_scope_tenant",
        ),
        # One preference per (scope, tenant, owner, event_type, channel_type).
        UniqueConstraint(
            "tenant_id",
            "owner_user_id",
            "event_type",
            "channel_type",
            name="uq_notification_preferences_scope_event_channel",
        ),
        Index(
            "ix_notification_preferences_tenant_event",
            "tenant_id",
            "event_type",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_notification_preferences_owner",
            "owner_user_id",
            postgresql_where=text("owner_user_id IS NOT NULL"),
        ),
    )

    scope: Mapped[str] = mapped_column(String(16), nullable=False)

    tenant_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True, index=True)
    owner_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
    )

    # The system event this rule routes (``task_blocked``, ``plan_approved``,
    # ``budget_alert``, …). A stable string mapped from the event taxonomy in
    # task_10_04; TEXT-stored so the catalogue evolves without a migration.
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # The transport this rule applies to.
    channel_type: Mapped[str] = mapped_column(String(16), nullable=False)

    # Opt-in (default) or opt-out for this (event, channel) pair. A FALSE
    # row suppresses the matching event on the matching channel — the
    # primitive behind the human_10_02 "mute budget_alert on Slack" test.
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    # Optional quiet-hours window (local minutes-of-day [0..1439]). When
    # both are set, sends inside the window are deferred. NULL => no quiet
    # hours. The IANA timezone the window is interpreted in (NULL => UTC).
    quiet_hours_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quiet_hours_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quiet_hours_tz: Mapped[str | None] = mapped_column(String(64), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"NotificationPreference(id={self.id!r}, scope={self.scope!r}, "
            f"event={self.event_type!r}, channel={self.channel_type!r}, "
            f"enabled={self.enabled!r})"
        )


# =============================================================================
# notification_logs (tenant-owned, append-only)
# =============================================================================
class NotificationLog(Base, UUIDPrimaryKeyMixin):
    """Append-only record of one send attempt.

    Tenancy decision: **tenant-owned, append-only**. ``tenant_id`` is
    NULLABLE (a platform-scoped send is still recorded) — exactly like
    ``audit_log.tenant_id``. No ``updated_at`` / ``deleted_at``: a log row
    is immutable. A *retry* writes a NEW row (incrementing ``attempt``)
    rather than mutating the prior one, so the full send history is
    preserved. RLS in task_10_02 isolates non-NULL-tenant rows; the
    BYPASSRLS dispatcher validates ``row.tenant_id == request.tenant_id``
    at the task boundary before writing.

    We declare ``tenant_id`` explicitly (rather than via
    :class:`TenantScopedMixin`) because it is nullable and the table has no
    ``updated_at`` / ``deleted_at``.
    """

    __tablename__ = "notification_logs"
    __table_args__ = (
        Index("ix_notification_logs_tenant_created", "tenant_id", "created_at"),
        Index("ix_notification_logs_channel_created", "channel_id", "created_at"),
        Index("ix_notification_logs_event_created", "event_type", "created_at"),
        # Operator view of stuck / parked sends.
        Index(
            "ix_notification_logs_status",
            "status",
            "created_at",
            postgresql_where=text("status IN ('retrying', 'dead_letter', 'failed')"),
        ),
        # part-01 / ADR 0151: monthly RANGE partitioning on ``created_at``
        # (migration 0134). Declared on the model — not only in the migration —
        # because ``tests/unit/test_partition_planner.py`` DISCOVERS the
        # partitioned tables from here and demands the maintenance job knows
        # about them: a table converted in a migration but missing from
        # ``PARTITIONED_TABLES`` would silently have no partition next month.
        {"postgresql_partition_by": "RANGE (created_at)"},
    )

    # The channel this attempt went over. SET NULL on channel delete so the
    # historical log survives the channel being removed.
    channel_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("notification_channels.id", ondelete="SET NULL"),
        nullable=True,
    )
    # NULL => a platform-scoped send (mirrors audit_log.tenant_id).
    tenant_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True, index=True)

    # The system event that triggered the send + the resolved transport.
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    channel_type: Mapped[str] = mapped_column(String(16), nullable=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'queued'"))
    # Where the message was addressed (chat id, email, phone, webhook URL).
    # Non-secret; the channel secret never appears here.
    target: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # AUD16-11 (auditoría 2026-07-16): el CONTENIDO del mensaje para el inbox.
    # El dispatcher lo persiste TRUNCADO (200/2000) para channel_type=in_app —
    # sin esto una notif in-app solo decía "pasó un infra_alert" y el qué/cuál
    # se perdía con el render. Nullable: filas históricas y canales externos.
    subject: Mapped[str | None] = mapped_column(String(200), nullable=True)
    body: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    # 1-based attempt number; a retry writes a new row with attempt+1.
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    # Last error for a failed/retrying attempt (provider message, truncated).
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # When the channel API accepted the send (status sent/delivered).
    sent_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    # PART OF THE PRIMARY KEY since part-01 (ADR 0151, migration 0134). Not a
    # modelling preference: PostgreSQL **requires** the primary key of a
    # partitioned table to include the partition key, so the PK is
    # ``(id, created_at)``. The one thing that depended on ``id`` alone being
    # unique — the ``notification_log_reads.log_id`` foreign key — was retired
    # by ADR 0154; the column stays as a loose reference.
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        primary_key=True,
        nullable=False,
        server_default=text("now()"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"NotificationLog(id={self.id!r}, event={self.event_type!r}, "
            f"channel={self.channel_type!r}, status={self.status!r}, "
            f"attempt={self.attempt!r})"
        )


# =============================================================================
# notification_templates (tenant-owned override of a builtin template)
# =============================================================================
class NotificationTemplate(
    Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, SoftDeleteMixin
):
    """A tenant's override of a builtin notification template (task_10_03).

    Tenancy decision: **tenant-owned** (``tenant_id NOT NULL`` via
    :class:`TenantScopedMixin` + RLS). This is the deliberate counterpart
    to the channel/preference HYBRID model: the *platform* layer of the
    three-layer model is not a NULL-tenant row here — it is the set of
    **builtin templates shipped in code** (the dispatcher's
    ``notification_dispatcher.templates`` registry). A row in this table is
    therefore always a tenant override of a builtin, never a platform
    default, so it needs no ``scope`` discriminator and no NULL-tenant
    branch — it is a plain tenant-isolated table like
    ``marketplace_installations``. The dispatcher resolves a template
    most-specific-wins: a live tenant override beats the builtin fallback;
    an unknown ``(event_type, channel_type, locale)`` with no builtin is a
    clear error.

    A template is keyed by ``(event_type, channel_type, locale)`` and
    carries the Jinja2 source for the body and (optionally) the subject —
    rendered in a SANDBOXED environment (``jinja2.sandbox``) so a tenant's
    template can never execute arbitrary code or reach attributes/builtins.
    The sources are plain template text, NOT secrets, so they are stored in
    the clear (unlike channel secrets).
    """

    __tablename__ = "notification_templates"
    __table_args__ = (
        # Declarado aquí y no sólo en la migración: desde Alembic 1.19 el
        # autogenerate SÍ detecta los CHECK, así que uno que viva sólo en la
        # migración se lee como esquema que el modelo no conoce y el siguiente
        # `--autogenerate` propone BORRARLO. Ver
        # tests/integration/test_alembic_autogenerate_clean.py.
        CheckConstraint(
            "locale IN ('es', 'en')",
            name="ck_notification_templates_locale",
        ),
        # At most one LIVE override per (tenant, event, channel, locale).
        # A soft-deleted row keeps its key free for a fresh override via the
        # partial unique index below rather than this constraint, so we scope
        # the hard uniqueness to the natural key and let deleted_at IS NULL
        # drive the live-row dedupe at the index level.
        UniqueConstraint(
            "tenant_id",
            "event_type",
            "channel_type",
            "locale",
            name="uq_notification_templates_key",
        ),
        Index(
            "ix_notification_templates_tenant_lookup",
            "tenant_id",
            "event_type",
            "channel_type",
            "locale",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    # The system event this template renders (``plan_approved``,
    # ``task_failed``, ``execution_finished``, ``review_requested``, …).
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # The transport the rendered body targets.
    channel_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # Template locale — ES + EN only (CLAUDE.md §12).
    locale: Mapped[str] = mapped_column(String(8), nullable=False)

    # Jinja2 source for the message body (rendered sandboxed). NOT a secret.
    body_template: Mapped[str] = mapped_column(Text, nullable=False)
    # Optional Jinja2 source for a subject/title (email subject, push title).
    subject_template: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"NotificationTemplate(id={self.id!r}, tenant={self.tenant_id!r}, "
            f"event={self.event_type!r}, channel={self.channel_type!r}, "
            f"locale={self.locale!r})"
        )


# =============================================================================
# notification_log_reads (per-user read receipt for the in-app inbox)
# =============================================================================
class NotificationLogRead(Base, UUIDPrimaryKeyMixin):
    """A per-user read receipt for one ``notification_logs`` row (task_10_16).

    Tenancy decision: **tenant-owned, per-user receipt**. ``notification_logs``
    is append-only AND tenant-scoped (no per-user dimension); read/unread is
    inherently per-user (two Tenant Admins of the same tenant keep independent
    inboxes), so the marker lives in its own table rather than mutating the
    immutable log row.

    A row's *existence* means "``user_id`` has read ``log_id``"; "unread" is
    the absence of a row. ``UNIQUE (user_id, log_id)`` makes the mark
    idempotent (a second "mark read" is a no-op via ON CONFLICT DO NOTHING).
    RLS isolates the tenant-scoped receipts exactly like every other tenant
    table. ``tenant_id`` is NULLABLE (AUD16-10, migración 0113): el inbox de
    PLATAFORMA del System Admin marca leídos envíos ``tenant_id IS NULL`` y su
    receipt es igualmente platform-scoped — espejo de
    ``notification_logs.tenant_id``; esas filas solo las ve la sesión admin
    BYPASSRLS del endpoint de plataforma.
    """

    __tablename__ = "notification_log_reads"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "log_id",
            name="uq_notification_log_reads_user_log",
        ),
        Index("ix_notification_log_reads_user_log", "user_id", "log_id"),
    )

    tenant_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True, index=True)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # NO foreign key since part-01 / ADR 0154 (migration 0134): ``notification_logs``
    # is partitioned by month, so its primary key is ``(id, created_at)`` and a FK
    # cannot reference it without carrying both columns. The ``ON DELETE CASCADE``
    # it used to have existed "so a (hypothetical) log purge takes its receipts
    # with it" (migration 0048) — and ADR 0151 decided that purge never happens:
    # nothing is ever deleted. A loose reference is what remains; an orphan receipt
    # would only make the inbox's LEFT JOIN miss, never leak across tenants (the
    # receipt carries its own ``tenant_id`` with its own RLS).
    log_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    read_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"NotificationLogRead(user={self.user_id!r}, log={self.log_id!r}, "
            f"read_at={self.read_at!r})"
        )


__all__ = [
    "NotificationChannel",
    "NotificationChannelType",
    "NotificationLocale",
    "NotificationLog",
    "NotificationLogRead",
    "NotificationPreference",
    "NotificationScope",
    "NotificationStatus",
    "NotificationTemplate",
]
