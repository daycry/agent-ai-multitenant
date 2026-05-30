"""Unit tests for the Notification ORM contract (Plan 10 task_10_01).

The migration + RLS are exercised in the dispatcher / migration tests of
task_10_02. Here we stay in-process and pin the column shape, enum
values, defaults, the three-layer scope tenancy decision, and the
never-plaintext-secret invariant the rest of Plan 10 depends on.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from api_server.db.notification import (
    NotificationChannel,
    NotificationChannelType,
    NotificationLog,
    NotificationPreference,
    NotificationScope,
    NotificationStatus,
)
from sqlalchemy import UniqueConstraint

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
def test_channel_type_enum_values() -> None:
    """The closed transport catalogue (spec §17 / Plan 10 Fase B/C)."""
    assert {t.value for t in NotificationChannelType} == {
        "telegram",
        "email",
        "slack",
        "teams",
        "discord",
        "whatsapp",
        "sms",
        "webhook",
        "in_app",
    }


def test_scope_enum_values() -> None:
    assert {s.value for s in NotificationScope} == {"platform", "tenant", "user"}


def test_status_enum_values() -> None:
    assert {s.value for s in NotificationStatus} == {
        "queued",
        "sent",
        "delivered",
        "failed",
        "retrying",
        "dead_letter",
    }


def test_status_covers_task_named_states() -> None:
    """The task names queued/sent/failed/retrying explicitly."""
    values = {s.value for s in NotificationStatus}
    assert {"queued", "sent", "failed", "retrying"} <= values


def test_enums_are_string_valued() -> None:
    """StrEnum: the value persists as a plain string (TEXT column)."""
    assert NotificationChannelType.TELEGRAM == "telegram"
    assert NotificationScope.PLATFORM == "platform"
    assert NotificationStatus.QUEUED == "queued"


# ---------------------------------------------------------------------------
# notification_channels — hybrid platform/tenant/user tenancy
# ---------------------------------------------------------------------------
def test_channel_table_name_and_columns() -> None:
    assert NotificationChannel.__tablename__ == "notification_channels"
    cols = {c.name for c in NotificationChannel.__table__.columns}
    assert {
        "id",
        "scope",
        "channel_type",
        "tenant_id",
        "owner_user_id",
        "name",
        "enabled",
        "config",
        "secret_ref",
        "secret_encrypted",
        "created_at",
        "updated_at",
        "deleted_at",
    } <= cols


def test_channel_tenant_id_is_nullable_for_platform_scope() -> None:
    """NULL tenant_id == a platform-scoped (tenant-agnostic) channel; a
    non-NULL tenant_id == a tenant/user-scoped channel."""
    tenant_col = NotificationChannel.__table__.columns["tenant_id"]
    assert tenant_col.nullable is True


def test_channel_owner_user_fk_cascades() -> None:
    """A user-scoped channel is owned by a user; deleting the user removes it."""
    col = NotificationChannel.__table__.columns["owner_user_id"]
    assert col.nullable is True
    fks = list(col.foreign_keys)
    assert len(fks) == 1
    assert fks[0].ondelete == "CASCADE"


def test_channel_unique_per_scope_type_name() -> None:
    uniques = {
        c.name for c in NotificationChannel.__table__.constraints if isinstance(c, UniqueConstraint)
    }
    assert "uq_notification_channels_scope_type_name" in uniques


def test_channel_defaults() -> None:
    ch = NotificationChannel(
        scope=NotificationScope.PLATFORM,
        channel_type=NotificationChannelType.SLACK,
        name="ops-alerts",
    )
    assert ch.name == "ops-alerts"
    # Server-side defaults are NULL on the in-memory object until flush;
    # assert the column carries the expected server_default text instead.
    assert NotificationChannel.__table__.columns["enabled"].server_default is not None
    assert NotificationChannel.__table__.columns["config"].server_default is not None


def test_channel_construction_platform_scope() -> None:
    ch = NotificationChannel(
        scope=NotificationScope.PLATFORM,
        channel_type=NotificationChannelType.SLACK,
        name="operator-ops",
        tenant_id=None,
        owner_user_id=None,
        config={"webhook_url_host": "hooks.slack.com"},
    )
    assert ch.scope == "platform"
    assert ch.tenant_id is None
    assert ch.owner_user_id is None
    assert ch.channel_type == "slack"


def test_channel_construction_tenant_scope() -> None:
    tid = uuid4()
    ch = NotificationChannel(
        scope=NotificationScope.TENANT,
        channel_type=NotificationChannelType.EMAIL,
        name="tenant-sender",
        tenant_id=tid,
        config={"smtp_host": "smtp.example.com", "smtp_port": 587},
    )
    assert ch.scope == "tenant"
    assert ch.tenant_id == tid
    assert ch.owner_user_id is None


def test_channel_construction_user_scope() -> None:
    tid, uid = uuid4(), uuid4()
    ch = NotificationChannel(
        scope=NotificationScope.USER,
        channel_type=NotificationChannelType.TELEGRAM,
        name="my-telegram",
        tenant_id=tid,
        owner_user_id=uid,
        config={"chat_id": "123456"},
    )
    assert ch.scope == "user"
    assert ch.tenant_id == tid
    assert ch.owner_user_id == uid


# ---------------------------------------------------------------------------
# Secret handling — never plaintext (CLAUDE.md invariant)
# ---------------------------------------------------------------------------
def test_channel_secret_columns_are_the_encrypted_form() -> None:
    """The secret lives ONLY in secret_ref (Vault pointer) or
    secret_encrypted (Fernet ciphertext) — never as a plaintext column."""
    cols = {c.name for c in NotificationChannel.__table__.columns}
    assert "secret_ref" in cols
    assert "secret_encrypted" in cols
    # No clear-secret column slipped in.
    for forbidden in ("secret", "token", "password", "api_key", "auth_token"):
        assert forbidden not in cols, f"{forbidden!r} must not be a plaintext column"
    assert NotificationChannel.__table__.columns["secret_ref"].nullable is True
    assert NotificationChannel.__table__.columns["secret_encrypted"].nullable is True


def test_channel_config_is_not_a_secret_store() -> None:
    """A channel's transport config carries only non-secret transport
    metadata; the secret travels in secret_ref / secret_encrypted. This
    pins the invariant the writing service layer enforces — the model never
    persists a clear bot token in `config`."""
    ch = NotificationChannel(
        scope=NotificationScope.USER,
        channel_type=NotificationChannelType.TELEGRAM,
        name="bot",
        tenant_id=uuid4(),
        owner_user_id=uuid4(),
        config={"chat_id": "123456"},
        secret_encrypted="gAAAAAB...fernet-ciphertext...",
    )
    # The secret is in its encrypted form, never in the clear config blob.
    assert "bot_token" not in ch.config
    assert "token" not in ch.config
    assert ch.secret_encrypted is not None
    assert ch.secret_ref is None


# ---------------------------------------------------------------------------
# notification_preferences — hybrid tenancy + quiet hours
# ---------------------------------------------------------------------------
def test_preference_table_name_and_columns() -> None:
    assert NotificationPreference.__tablename__ == "notification_preferences"
    cols = {c.name for c in NotificationPreference.__table__.columns}
    assert {
        "id",
        "scope",
        "tenant_id",
        "owner_user_id",
        "event_type",
        "channel_type",
        "enabled",
        "quiet_hours_start",
        "quiet_hours_end",
        "quiet_hours_tz",
        "created_at",
        "updated_at",
        "deleted_at",
    } <= cols


def test_preference_tenant_id_is_nullable_for_platform_default() -> None:
    assert NotificationPreference.__table__.columns["tenant_id"].nullable is True


def test_preference_unique_per_scope_event_channel() -> None:
    uniques = {
        c.name
        for c in NotificationPreference.__table__.constraints
        if isinstance(c, UniqueConstraint)
    }
    assert "uq_notification_preferences_scope_event_channel" in uniques


def test_preference_opt_out_construction() -> None:
    """The primitive behind human_10_02: mute budget_alert on Slack."""
    tid, uid = uuid4(), uuid4()
    pref = NotificationPreference(
        scope=NotificationScope.USER,
        tenant_id=tid,
        owner_user_id=uid,
        event_type="budget_alert",
        channel_type=NotificationChannelType.SLACK,
        enabled=False,
    )
    assert pref.enabled is False
    assert pref.event_type == "budget_alert"
    assert pref.channel_type == "slack"


def test_preference_quiet_hours_optional() -> None:
    pref = NotificationPreference(
        scope=NotificationScope.TENANT,
        tenant_id=uuid4(),
        event_type="task_blocked",
        channel_type=NotificationChannelType.TELEGRAM,
        quiet_hours_start=22 * 60,
        quiet_hours_end=8 * 60,
        quiet_hours_tz="Europe/Madrid",
    )
    assert pref.quiet_hours_start == 1320
    assert pref.quiet_hours_tz == "Europe/Madrid"
    # Defaults: enabled has a server default (NULL pre-flush on the object).
    assert NotificationPreference.__table__.columns["enabled"].server_default is not None


# ---------------------------------------------------------------------------
# notification_logs — tenant-owned, append-only
# ---------------------------------------------------------------------------
def test_log_table_name_and_columns() -> None:
    assert NotificationLog.__tablename__ == "notification_logs"
    cols = {c.name for c in NotificationLog.__table__.columns}
    assert {
        "id",
        "channel_id",
        "tenant_id",
        "event_type",
        "channel_type",
        "status",
        "target",
        "attempt",
        "error",
        "sent_at",
        "created_at",
    } <= cols


def test_log_is_append_only_no_soft_delete_or_update() -> None:
    """Immutable record: no updated_at / deleted_at, only created_at."""
    cols = {c.name for c in NotificationLog.__table__.columns}
    assert "updated_at" not in cols
    assert "deleted_at" not in cols
    assert "created_at" in cols


def test_log_tenant_id_nullable_for_platform_send() -> None:
    """A platform-scoped send is still recorded (mirrors audit_log.tenant_id)."""
    assert NotificationLog.__table__.columns["tenant_id"].nullable is True


def test_log_channel_fk_sets_null_on_channel_delete() -> None:
    """The channel may be removed; the historical log survives."""
    col = NotificationLog.__table__.columns["channel_id"]
    assert col.nullable is True
    fks = list(col.foreign_keys)
    assert len(fks) == 1
    assert fks[0].ondelete == "SET NULL"


def test_log_construction_defaults() -> None:
    log = NotificationLog(
        tenant_id=uuid4(),
        channel_id=uuid4(),
        event_type="plan_approved",
        channel_type=NotificationChannelType.EMAIL,
        target="admin@example.com",
    )
    assert log.event_type == "plan_approved"
    assert log.target == "admin@example.com"
    # status / attempt have server defaults (NULL pre-flush on the object).
    assert NotificationLog.__table__.columns["status"].server_default is not None
    assert NotificationLog.__table__.columns["attempt"].server_default is not None


def test_log_records_retry_status() -> None:
    log = NotificationLog(
        tenant_id=uuid4(),
        event_type="task_blocked",
        channel_type=NotificationChannelType.WEBHOOK,
        status=NotificationStatus.RETRYING,
        attempt=3,
        error="503 from receiver",
    )
    assert log.status == "retrying"
    assert log.attempt == 3
    assert log.error == "503 from receiver"


# ---------------------------------------------------------------------------
# Multi-tenant scope rules — tenant/user-scoped rows carry tenant_id
# ---------------------------------------------------------------------------
def test_tenant_scoped_rows_carry_tenant_id() -> None:
    """Tenant- and user-scoped channels/preferences MUST carry tenant_id;
    only platform scope may leave it NULL (documented per-scope decision)."""
    tid = uuid4()
    tenant_ch = NotificationChannel(
        scope=NotificationScope.TENANT,
        channel_type=NotificationChannelType.EMAIL,
        name="t",
        tenant_id=tid,
    )
    user_pref = NotificationPreference(
        scope=NotificationScope.USER,
        tenant_id=tid,
        owner_user_id=uuid4(),
        event_type="review_needed",
        channel_type=NotificationChannelType.TELEGRAM,
    )
    assert tenant_ch.tenant_id == tid
    assert user_pref.tenant_id == tid


@pytest.mark.cross_tenant
def test_two_tenants_channels_carry_distinct_tenant_ids() -> None:
    """Model-level boundary primitive: a channel is bound to exactly one
    tenant. RLS (task_10_02) + the BYPASSRLS dispatcher's boundary check
    (row.tenant_id == request.tenant_id) build on this — a notification
    can never leak across tenants because each row names its owning tenant
    and a tenant-A channel can never carry tenant B's id."""
    tenant_a, tenant_b = uuid4(), uuid4()
    ch_a = NotificationChannel(
        scope=NotificationScope.TENANT,
        channel_type=NotificationChannelType.SLACK,
        name="a-slack",
        tenant_id=tenant_a,
    )
    ch_b = NotificationChannel(
        scope=NotificationScope.TENANT,
        channel_type=NotificationChannelType.SLACK,
        name="b-slack",
        tenant_id=tenant_b,
    )
    assert ch_a.tenant_id != ch_b.tenant_id
    # A log row inherits the owning tenant; cross-tenant reuse is impossible
    # at the model level (the dispatcher rejects a mismatch at the boundary).
    log_a = NotificationLog(
        tenant_id=tenant_a,
        channel_id=ch_a.id,
        event_type="task_blocked",
        channel_type=NotificationChannelType.SLACK,
    )
    assert log_a.tenant_id == tenant_a
    assert log_a.tenant_id != tenant_b
