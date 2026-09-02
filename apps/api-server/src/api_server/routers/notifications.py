"""`/notifications` endpoints — 3-layer config + manual DLQ retry (task_10_13/15).

Two surfaces live here:

**Config (task_10_15)** — the 3-layer (platform → tenant → user) channel +
preference config the admin-panel drives:

  - GET  /notifications/platform/channel-types   enabled transports (read: any member)
  - PUT  /notifications/platform/channel-types   set them (System Admin only)
  - GET  /notifications/channels                  list channels (tenant + user scope)
  - POST /notifications/channels                  create a channel
  - PUT  /notifications/channels/{id}             update a channel (rotate secret)
  - DELETE /notifications/channels/{id}           soft-delete a channel
  - GET  /notifications/preferences               list routing rules
  - PUT  /notifications/preferences               upsert one routing rule
  - DELETE /notifications/preferences/{id}        soft-delete a routing rule

Config invariants:
  * **RBAC by scope**: a tenant write is ``tenant_admin`` only (a plain
    ``tenant_user`` is 403); the platform channel-types write is System-Admin
    only. A ``user``-scoped row is owned by the requesting admin.
  * **RLS-scoped**: channel/preference rows run on the RLS-bound tenant
    session — tenant B never sees or mutates tenant A's rows (clean 404).
  * **Secret never echoed**: a channel secret is encrypted at rest
    (``secret_encrypted``) by the api-server write path so the dispatcher can
    decrypt it at send time; the API returns only ``has_secret`` +
    ``secret_source`` — the clear value never leaves the server, never lands
    in ``config``, and is never logged.

**Manual DLQ retry (task_10_13)** — the operator escape hatch: a Tenant Admin
re-drives a dead-lettered send back through the dispatcher's normal path.

  - POST /notifications/logs/{log_id}/retry   re-enqueue a dead-lettered log

Retry invariants (all tested in ``tests/integration/test_retries_dlq.py``):
  * **RBAC**: ``tenant_admin`` only — a plain ``tenant_user`` is 403.
  * **RLS-scoped**: tenant B asking to retry tenant A's log gets a clean 404.
  * **Only dead-lettered logs are retryable**: a non-dead-letter log is 409.
  * **Idempotent**: the re-enqueue flips the source row OUT of ``dead_letter``
    in the same transaction as the new ``queued`` row + the broker publish.
  * **Audited**: an append-only ``audit_log`` row (``notification.retry``) is
    written in the same transaction.

The api-server never imports the dispatcher package — it re-enqueues a send by
task name onto the shared broker via
:func:`api_server.celery_client.enqueue_notification_send` (the dispatcher owns
the retry/backoff/DLQ policy). It DOES share the channel-secret cipher key with
the dispatcher (``API_SERVER_NOTIFICATION_ENCRYPTION_KEY`` ==
``NOTIFY_NOTIFICATION_ENCRYPTION_KEY``) so what it encrypts the dispatcher can
decrypt.
"""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import ColumnElement, CursorResult, func, literal, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.audit import write_audit_log
from api_server.auth.deps import (
    AuthPrincipal,
    get_admin_session,
    get_tenant_session,
    require_system_admin,
    require_tenant_admin,
    require_tenant_member,
)
from api_server.celery_client import enqueue_notification_send
from api_server.db.notification import (
    NotificationChannel,
    NotificationChannelType,
    NotificationLog,
    NotificationLogRead,
    NotificationPreference,
    NotificationScope,
    NotificationStatus,
)
from api_server.db.platform_settings import get_platform_setting, set_platform_setting
from api_server.notifications.secrets import encrypt_channel_secret
from api_server.routers._helpers import require_tenant_id, soft_delete
from api_server.schemas.notifications import (
    MarkReadResponse,
    NotificationChannelCreate,
    NotificationChannelResponse,
    NotificationChannelUpdate,
    NotificationInboxResponse,
    NotificationLogResponse,
    NotificationPreferenceResponse,
    NotificationPreferenceUpsert,
    PlatformChannelTypesResponse,
    PlatformChannelTypesUpdate,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])

# platform_settings key holding the System-Admin-curated list of globally
# enabled channel transports. A tenant may only configure a channel whose
# transport is in this list.
PLATFORM_ENABLED_CHANNEL_TYPES_KEY = "notification_enabled_channel_types"

# Audit actions for config writes (greppable across the audit trail).
_AUDIT_PLATFORM_CHANNEL_TYPES = "notification.platform_channel_types.set"
_AUDIT_CHANNEL_CREATE = "notification.channel.create"
_AUDIT_CHANNEL_UPDATE = "notification.channel.update"
_AUDIT_CHANNEL_DELETE = "notification.channel.delete"


def _channel_to_response(channel: NotificationChannel) -> NotificationChannelResponse:
    """Project a channel ORM row to the secret-free response shape.

    The secret value is NEVER included; the UI only learns whether one is
    set and in which form (Vault ref vs Fernet-at-rest)."""
    if channel.secret_ref:
        secret_source: str | None = "vault"
    elif channel.secret_encrypted:
        secret_source = "encrypted"
    else:
        secret_source = None
    return NotificationChannelResponse(
        id=channel.id,
        scope=channel.scope,
        channel_type=channel.channel_type,
        name=channel.name,
        enabled=channel.enabled,
        config=channel.config,
        owner_user_id=channel.owner_user_id,
        has_secret=secret_source is not None,
        secret_source=secret_source,  # type: ignore[arg-type]
        created_at=channel.created_at,
        updated_at=channel.updated_at,
    )


def _preference_to_response(pref: NotificationPreference) -> NotificationPreferenceResponse:
    return NotificationPreferenceResponse(
        id=pref.id,
        scope=pref.scope,
        event_type=pref.event_type,
        channel_type=pref.channel_type,
        enabled=pref.enabled,
        owner_user_id=pref.owner_user_id,
        quiet_hours_start=pref.quiet_hours_start,
        quiet_hours_end=pref.quiet_hours_end,
        quiet_hours_tz=pref.quiet_hours_tz,
        created_at=pref.created_at,
        updated_at=pref.updated_at,
    )


# ===========================================================================
# Event catalogue — the events a tenant can subscribe to (NOTIF-3)
# ===========================================================================
# La UI de preferencias hardcodeaba 4 eventos (uno inexistente, `review_needed`)
# desalineados del registro real del dispatcher. Este catálogo es la fuente que
# consume la UI. El api-server NO importa el dispatcher (frontera limpia), así
# que la lista vive aquí y un test unitario la mantiene en sync con
# `notification_dispatcher.event_mapping.EVENT_REGISTRY` (los tests sí pueden
# importar ambos paquetes).
NOTIFICATION_EVENT_CATALOG: tuple[dict[str, str], ...] = (
    {"event_type": "task_blocked", "label_es": "Tarea bloqueada", "label_en": "Task blocked"},
    {
        "event_type": "task_unassignable",
        "label_es": "Tarea sin agente",
        "label_en": "Task has no agent",
    },
    {"event_type": "daily_standup", "label_es": "Standup diario", "label_en": "Daily standup"},
    {
        "event_type": "restore_drill_result",
        "label_es": "Resultado del restore-drill",
        "label_en": "Restore drill result",
    },
    {
        "event_type": "config_proposal",
        "label_es": "Propuesta de configuración",
        "label_en": "Configuration proposal",
    },
    {
        "event_type": "provider_recovered",
        "label_es": "Proveedor LLM recuperado",
        "label_en": "LLM provider recovered",
    },
    {"event_type": "plan_approved", "label_es": "Plan aprobado", "label_en": "Plan approved"},
    {"event_type": "plan_rejected", "label_es": "Plan rechazado", "label_en": "Plan rejected"},
    {
        "event_type": "plan_pr_failed",
        "label_es": "Auto-PR del plan fallido",
        "label_en": "Plan auto-PR failed",
    },
    {"event_type": "plan_blocked", "label_es": "Plan bloqueado", "label_en": "Plan blocked"},
    {"event_type": "plan_unblocked", "label_es": "Plan desbloqueado", "label_en": "Plan unblocked"},
    {
        "event_type": "execution_finished",
        "label_es": "Ejecución finalizada",
        "label_en": "Execution finished",
    },
    {
        "event_type": "execution_failed",
        "label_es": "Ejecución fallida",
        "label_en": "Execution failed",
    },
    {
        "event_type": "review_requested",
        "label_es": "Revisión solicitada",
        "label_en": "Review requested",
    },
    {
        "event_type": "human_validation_needed",
        "label_es": "Validación humana pendiente",
        "label_en": "Human validation needed",
    },
    {
        "event_type": "human_task_assigned",
        "label_es": "Tarea humana asignada",
        "label_en": "Human task assigned",
    },
    {
        "event_type": "review_escalated",
        "label_es": "Revisión escalada",
        "label_en": "Review escalated",
    },
    {"event_type": "budget_alert", "label_es": "Alerta de presupuesto", "label_en": "Budget alert"},
    {
        "event_type": "guardrail_alert",
        "label_es": "Alerta de guardrail",
        "label_en": "Guardrail alert",
    },
    {
        "event_type": "quality_drift_alert",
        "label_es": "Deriva de calidad",
        "label_en": "Quality drift alert",
    },
    {
        "event_type": "agent_outlier_alert",
        "label_es": "Agente atípico",
        "label_en": "Agent outlier alert",
    },
    {
        "event_type": "antivirus_unreachable",
        "label_es": "Antivirus inaccesible",
        "label_en": "Antivirus unreachable",
    },
    {
        "event_type": "credential_rotation_failed",
        "label_es": "Rotación de credenciales fallida",
        "label_en": "Credential rotation failed",
    },
    {
        "event_type": "fx_fetch_failed",
        "label_es": "Actualización de divisas fallida",
        "label_en": "FX update failed",
    },
    {
        "event_type": "infra_alert",
        "label_es": "Alerta de infraestructura",
        "label_en": "Infrastructure alert",
    },
    {
        "event_type": "cortex_message",
        "label_es": "Mensaje del córtex",
        "label_en": "Cortex message",
    },
    {
        "event_type": "provider_credential_invalid",
        "label_es": "Credencial del proveedor LLM fallando",
        "label_en": "LLM provider credential failing",
    },
)


@router.get("/event-catalog")
async def get_event_catalog(
    _: AuthPrincipal = Depends(require_tenant_member),
) -> list[dict[str, str]]:
    """Los eventos suscribibles, con etiquetas ES/EN — la fuente de la UI de
    preferencias (antes hardcodeaba una lista desalineada del registro)."""
    return list(NOTIFICATION_EVENT_CATALOG)


# ===========================================================================
# Platform layer — System Admin enables channel transports globally
# ===========================================================================
@router.get("/platform/channel-types", response_model=PlatformChannelTypesResponse)
async def get_platform_channel_types(
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> PlatformChannelTypesResponse:
    """The channel transports the System Admin enabled platform-wide.

    Readable by any tenant member so the config UI can show which transports
    a Tenant Admin is allowed to configure. ``platform_settings`` carries no
    RLS, so this is a plain global read. When unset, every transport in the
    catalogue is considered enabled (a permissive default — the System Admin
    narrows it).
    """
    catalogue = [t.value for t in NotificationChannelType]
    stored = await get_platform_setting(session, PLATFORM_ENABLED_CHANNEL_TYPES_KEY, default=None)
    if not isinstance(stored, list):
        enabled = list(catalogue)
    else:
        wanted = {str(t) for t in stored}
        enabled = [t for t in catalogue if t in wanted]
    return PlatformChannelTypesResponse(enabled=enabled, available=catalogue)


@router.put("/platform/channel-types", response_model=PlatformChannelTypesResponse)
async def set_platform_channel_types(
    payload: PlatformChannelTypesUpdate,
    principal: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> PlatformChannelTypesResponse:
    """Set the globally enabled channel transports (System Admin only).

    Uses the BYPASSRLS admin session (``get_admin_session`` is itself gated
    to System Admin). ``set_platform_setting`` re-checks the actor is a
    System Admin, so a Tenant Admin can never reach this write."""
    from api_server.db.models import User

    actor = await session.get(User, principal.user_id)
    if actor is None:  # pragma: no cover - a valid session always has a user
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="actor not found")

    await set_platform_setting(
        session,
        PLATFORM_ENABLED_CHANNEL_TYPES_KEY,
        payload.enabled,
        actor=actor,
    )
    await write_audit_log(
        session,
        action=_AUDIT_PLATFORM_CHANNEL_TYPES,
        actor_user_id=principal.user_id,
        tenant_id=None,
        resource_type="platform_setting",
        resource_id=None,
        changes={"enabled": payload.enabled},
    )
    catalogue = [t.value for t in NotificationChannelType]
    return PlatformChannelTypesResponse(enabled=list(payload.enabled), available=catalogue)


async def _platform_enabled_types(session: AsyncSession) -> set[str]:
    """The set of globally enabled transports (all, if unset)."""
    catalogue = {t.value for t in NotificationChannelType}
    stored = await get_platform_setting(session, PLATFORM_ENABLED_CHANNEL_TYPES_KEY, default=None)
    if not isinstance(stored, list):
        return catalogue
    return {str(t) for t in stored if str(t) in catalogue}


# ===========================================================================
# Channels — tenant / user scoped CRUD (secret never echoed)
# ===========================================================================
@router.get("/channels", response_model=list[NotificationChannelResponse])
async def list_channels(
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[NotificationChannelResponse]:
    """List the tenant's channels (tenant + user scope), newest first.

    RLS scopes this to the caller's tenant, so another tenant's channels are
    invisible. The secret is never included."""
    result = await session.execute(
        select(NotificationChannel)
        .where(NotificationChannel.deleted_at.is_(None))
        .order_by(NotificationChannel.created_at.desc())
    )
    return [_channel_to_response(c) for c in result.scalars().all()]


@router.post(
    "/channels",
    response_model=NotificationChannelResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_channel(
    payload: NotificationChannelCreate,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> NotificationChannelResponse:
    """Create a tenant- or user-scoped channel (tenant_admin only).

    The transport must be enabled platform-wide (System Admin gate). The
    plaintext ``secret`` is encrypted at rest before it touches the DB and
    never echoed back. A ``user``-scoped channel is owned by the requesting
    admin (``owner_user_id`` = the caller)."""
    tenant_id = require_tenant_id(principal)

    if payload.channel_type not in await _platform_enabled_types(session):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"channel type '{payload.channel_type}' is not enabled platform-wide",
        )

    owner_user_id = principal.user_id if payload.scope == NotificationScope.USER.value else None
    secret_encrypted = encrypt_channel_secret(payload.secret) if payload.secret else None

    channel = NotificationChannel(
        scope=payload.scope,
        channel_type=payload.channel_type,
        tenant_id=tenant_id,
        owner_user_id=owner_user_id,
        name=payload.name,
        enabled=payload.enabled,
        config=payload.config,
        secret_encrypted=secret_encrypted,
    )
    session.add(channel)
    await session.flush()
    await write_audit_log(
        session,
        action=_AUDIT_CHANNEL_CREATE,
        actor_user_id=principal.user_id,
        tenant_id=tenant_id,
        resource_type="notification_channel",
        resource_id=channel.id,
        changes={"scope": payload.scope, "channel_type": payload.channel_type},
    )
    return _channel_to_response(channel)


async def _get_writable_channel(
    session: AsyncSession, channel_id: UUID, tenant_id: UUID
) -> NotificationChannel:
    """Load a live, tenant-owned channel for write, or 404.

    RLS already scopes the query to the caller's tenant; the explicit
    ``tenant_id`` filter is belt-and-braces. A user-scoped channel owned by
    a different admin is still visible to a tenant_admin (tenant-level
    administration), matching how a Tenant Admin administers tenant resources."""
    result = await session.execute(
        select(NotificationChannel).where(
            NotificationChannel.id == channel_id,
            NotificationChannel.tenant_id == tenant_id,
            NotificationChannel.deleted_at.is_(None),
        )
    )
    channel = result.scalar_one_or_none()
    if channel is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="notification channel not found"
        )
    return channel


@router.put("/channels/{channel_id}", response_model=NotificationChannelResponse)
async def update_channel(
    channel_id: UUID,
    payload: NotificationChannelUpdate,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> NotificationChannelResponse:
    """Patch a channel (tenant_admin only). An omitted ``secret`` keeps the
    stored one; a non-empty ``secret`` rotates it (re-encrypted at rest)."""
    tenant_id = require_tenant_id(principal)
    channel = await _get_writable_channel(session, channel_id, tenant_id)

    if payload.name is not None:
        channel.name = payload.name
    if payload.enabled is not None:
        channel.enabled = payload.enabled
    if payload.config is not None:
        channel.config = payload.config
    if payload.secret:
        channel.secret_encrypted = encrypt_channel_secret(payload.secret)
        channel.secret_ref = None

    await session.flush()
    # The server-side ``updated_at = now()`` (onupdate) expires the attribute
    # after flush; refresh it inside the async context so building the
    # response below doesn't trigger a lazy load in a sync greenlet.
    await session.refresh(channel)
    await write_audit_log(
        session,
        action=_AUDIT_CHANNEL_UPDATE,
        actor_user_id=principal.user_id,
        tenant_id=tenant_id,
        resource_type="notification_channel",
        resource_id=channel.id,
        changes={"secret_rotated": bool(payload.secret)},
    )
    return _channel_to_response(channel)


@router.delete("/channels/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_channel(
    channel_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    """Soft-delete a channel (tenant_admin only)."""
    tenant_id = require_tenant_id(principal)
    channel = await _get_writable_channel(session, channel_id, tenant_id)
    await soft_delete(session, channel)
    await write_audit_log(
        session,
        action=_AUDIT_CHANNEL_DELETE,
        actor_user_id=principal.user_id,
        tenant_id=tenant_id,
        resource_type="notification_channel",
        resource_id=channel.id,
        changes={"name": channel.name},
    )


# ===========================================================================
# Preferences — tenant / user scoped routing rules
# ===========================================================================
@router.get("/preferences", response_model=list[NotificationPreferenceResponse])
async def list_preferences(
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[NotificationPreferenceResponse]:
    """List the tenant's routing rules (tenant + user scope)."""
    result = await session.execute(
        select(NotificationPreference)
        .where(NotificationPreference.deleted_at.is_(None))
        .order_by(NotificationPreference.event_type, NotificationPreference.channel_type)
    )
    return [_preference_to_response(p) for p in result.scalars().all()]


@router.put("/preferences", response_model=NotificationPreferenceResponse)
async def upsert_preference(
    payload: NotificationPreferenceUpsert,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> NotificationPreferenceResponse:
    """Create or update a routing rule (tenant_admin only).

    Upsert keyed on ``(tenant, owner, event_type, channel_type)`` — the
    table's natural key. A ``user``-scoped rule is owned by the requesting
    admin. This is the primitive behind the human_10_02 "mute budget_alert on
    Slack but keep it on email" preference."""
    tenant_id = require_tenant_id(principal)
    owner_user_id = principal.user_id if payload.scope == NotificationScope.USER.value else None

    values = {
        "scope": payload.scope,
        "tenant_id": tenant_id,
        "owner_user_id": owner_user_id,
        "event_type": payload.event_type,
        "channel_type": payload.channel_type,
        "enabled": payload.enabled,
        "quiet_hours_start": payload.quiet_hours_start,
        "quiet_hours_end": payload.quiet_hours_end,
        "quiet_hours_tz": payload.quiet_hours_tz,
        "deleted_at": None,
    }
    update_cols = {
        "enabled": payload.enabled,
        "quiet_hours_start": payload.quiet_hours_start,
        "quiet_hours_end": payload.quiet_hours_end,
        "quiet_hours_tz": payload.quiet_hours_tz,
        "scope": payload.scope,
        "deleted_at": None,
    }
    stmt = (
        pg_insert(NotificationPreference)
        .values(**values)
        .on_conflict_do_update(
            constraint="uq_notification_preferences_scope_event_channel",
            set_=update_cols,
        )
        .returning(NotificationPreference)
    )
    result = await session.execute(stmt)
    pref = result.scalar_one()
    return _preference_to_response(pref)


@router.delete("/preferences/{preference_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_preference(
    preference_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    """Soft-delete a routing rule (tenant_admin only)."""
    tenant_id = require_tenant_id(principal)
    result = await session.execute(
        select(NotificationPreference).where(
            NotificationPreference.id == preference_id,
            NotificationPreference.tenant_id == tenant_id,
            NotificationPreference.deleted_at.is_(None),
        )
    )
    pref = result.scalar_one_or_none()
    if pref is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="notification preference not found"
        )
    await soft_delete(session, pref)


# ===========================================================================
# Inbox — paginated notification-log history + per-user read/unread (task_10_16)
# ===========================================================================
def _log_to_response(log: NotificationLog, *, read: bool) -> NotificationLogResponse:
    """Project an append-only log row to the inbox shape (no secret involved).

    A log row is non-secret by construction (``target`` is a chat id / email /
    webhook URL, never a token; the channel secret lives elsewhere), so the
    whole row is safe to surface. ``read`` is the per-user marker resolved by
    the caller's read-receipt left-join."""
    return NotificationLogResponse(
        id=log.id,
        channel_id=log.channel_id,
        event_type=log.event_type,
        channel_type=log.channel_type,
        status=log.status,
        target=log.target,
        attempt=log.attempt,
        error=log.error,
        sent_at=log.sent_at,
        created_at=log.created_at,
        subject=log.subject,
        body=log.body,
        read=read,
    )


async def _unread_count(session: AsyncSession, *, tenant_id: UUID, user_id: UUID) -> int:
    """Count the caller's unread tenant logs (no receipt for this user).

    RLS already scopes ``notification_logs`` to the caller's tenant; the
    LEFT JOIN to the caller's own receipts surfaces the unread rows as the
    ones with no matching receipt."""
    read_subq = (
        select(NotificationLogRead.log_id)
        .where(NotificationLogRead.user_id == user_id)
        .scalar_subquery()
    )
    stmt = (
        select(func.count())
        .select_from(NotificationLog)
        .where(
            NotificationLog.tenant_id == tenant_id,
            NotificationLog.id.not_in(read_subq),
        )
    )
    return int((await session.execute(stmt)).scalar_one())


@router.get("/logs", response_model=NotificationInboxResponse)
async def list_notification_logs(
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    status_filter: str | None = Query(default=None, alias="status", max_length=16),
    channel_type: str | None = Query(default=None, max_length=16),
    event_type: str | None = Query(default=None, max_length=64),
    unread_only: bool = Query(default=False),
) -> NotificationInboxResponse:
    """The in-app inbox: the caller's tenant notification-log history, newest
    first, paginated, with a per-user read marker (task_10_16).

    RLS scopes ``notification_logs`` to the caller's tenant, so another
    tenant's history is invisible. The per-user read marker comes from a
    LEFT JOIN to the caller's OWN ``notification_log_reads`` receipts — each
    Tenant Admin keeps an independent inbox. ``limit``/``offset`` are bounded
    (1..200 / >=0). Optional ``status`` / ``channel_type`` / ``event_type``
    filters narrow the window; ``unread_only`` keeps only rows the caller has
    not read. No secret is ever surfaced — a log row carries only the
    non-secret ``target`` + transport metadata.
    """
    tenant_id = require_tenant_id(principal)

    # Per-user read marker: is there a receipt for (caller, this log)?
    read_marker = (
        select(NotificationLogRead.id)
        .where(
            NotificationLogRead.log_id == NotificationLog.id,
            NotificationLogRead.user_id == principal.user_id,
        )
        .exists()
    )

    # The belt-and-braces tenant filter rides on top of RLS (the session is
    # already RLS-bound to the caller's tenant) so the inbox can never include
    # a NULL-tenant platform send.
    conditions = [NotificationLog.tenant_id == tenant_id]
    if status_filter is not None:
        conditions.append(NotificationLog.status == status_filter)
    if channel_type is not None:
        conditions.append(NotificationLog.channel_type == channel_type)
    if event_type is not None:
        conditions.append(NotificationLog.event_type == event_type)
    if unread_only:
        conditions.append(~read_marker)

    total = int(
        (
            await session.execute(
                select(func.count()).select_from(NotificationLog).where(*conditions)
            )
        ).scalar_one()
    )

    result = await session.execute(
        select(NotificationLog, read_marker.label("is_read"))
        .where(*conditions)
        .order_by(NotificationLog.created_at.desc(), NotificationLog.id.desc())
        .limit(limit)
        .offset(offset)
    )
    items = [_log_to_response(row[0], read=bool(row[1])) for row in result.all()]

    unread = await _unread_count(session, tenant_id=tenant_id, user_id=principal.user_id)
    return NotificationInboxResponse(
        items=items, total=total, unread=unread, limit=limit, offset=offset
    )


@router.post("/logs/{log_id}/read", response_model=MarkReadResponse)
async def mark_log_read(
    log_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> MarkReadResponse:
    """Mark one inbox item read for the calling user (idempotent).

    Loads the log via the RLS-bound session (another tenant's log → 404), then
    upserts a per-user receipt. A second call is a no-op (ON CONFLICT DO
    NOTHING) so the marker is idempotent."""
    tenant_id = require_tenant_id(principal)

    # RLS scopes this to the caller's tenant: another tenant's log is a 404.
    log = (
        await session.execute(select(NotificationLog).where(NotificationLog.id == log_id))
    ).scalar_one_or_none()
    if log is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="notification log not found"
        )

    stmt = (
        pg_insert(NotificationLogRead)
        .values(tenant_id=tenant_id, user_id=principal.user_id, log_id=log_id)
        .on_conflict_do_nothing(constraint="uq_notification_log_reads_user_log")
    )
    # ``rowcount`` lives on the CursorResult an INSERT yields at runtime; the
    # async ``execute`` is typed as the broader ``Result``, hence the cast.
    res = cast("CursorResult[Any]", await session.execute(stmt))
    marked = int(res.rowcount or 0)

    unread = await _unread_count(session, tenant_id=tenant_id, user_id=principal.user_id)
    return MarkReadResponse(marked=marked, unread=unread)


@router.post("/logs/read-all", response_model=MarkReadResponse)
async def mark_all_logs_read(
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> MarkReadResponse:
    """Mark every unread inbox item read for the calling user (idempotent).

    Inserts a receipt for each of the caller's tenant logs that has none yet,
    in one statement. A second call inserts nothing (every log already has a
    receipt). RLS keeps this to the caller's tenant."""
    tenant_id = require_tenant_id(principal)

    already_read = (
        select(NotificationLogRead.log_id)
        .where(NotificationLogRead.user_id == principal.user_id)
        .scalar_subquery()
    )
    unread_logs = select(
        func.gen_random_uuid().label("id"),
        NotificationLog.tenant_id.label("tenant_id"),
        # the caller's user id, bound as a literal column for the INSERT...SELECT
        literal(principal.user_id).label("user_id"),
        NotificationLog.id.label("log_id"),
    ).where(
        NotificationLog.tenant_id == tenant_id,
        NotificationLog.id.not_in(already_read),
    )
    stmt = pg_insert(NotificationLogRead).from_select(
        ["id", "tenant_id", "user_id", "log_id"], unread_logs
    )
    res = cast("CursorResult[Any]", await session.execute(stmt))
    marked = int(res.rowcount or 0)

    return MarkReadResponse(marked=marked, unread=0)


# ===========================================================================
# Inbox de PLATAFORMA (AUD16-10) — System Admin, sesión admin BYPASSRLS.
#
# TODOS los envíos reales del sistema (infra_alert, cortex_message,
# credential_rotation_failed, fx_fetch_failed…) son platform-scoped
# (tenant_id IS NULL) y el inbox de tenant los excluye por diseño: sin este
# camino, NINGUNA notificación llegaba de facto a un ojo humano
# (notification_log_reads llevaba 0 filas en toda la historia). El read-marker
# reusa notification_log_reads con tenant_id NULL (migración 0113).
# ===========================================================================
async def _platform_unread_count(session: AsyncSession, *, user_id: UUID) -> int:
    read_subq = (
        select(NotificationLogRead.log_id)
        .where(NotificationLogRead.user_id == user_id)
        .scalar_subquery()
    )
    stmt = (
        select(func.count())
        .select_from(NotificationLog)
        .where(
            NotificationLog.tenant_id.is_(None),
            NotificationLog.id.not_in(read_subq),
        )
    )
    return int((await session.execute(stmt)).scalar_one())


@router.get("/platform/logs", response_model=NotificationInboxResponse)
async def list_platform_notification_logs(
    principal: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    status_filter: str | None = Query(default=None, alias="status", max_length=16),
    channel_type: str | None = Query(default=None, max_length=16),
    event_type: str | None = Query(default=None, max_length=64),
    unread_only: bool = Query(default=False),
) -> NotificationInboxResponse:
    """El inbox de plataforma: SOLO los envíos ``tenant_id IS NULL``, newest
    first, paginado, con read-marker por usuario — la contrapartida System
    Admin del inbox de tenant (misma forma de respuesta, mismos filtros)."""
    read_marker = (
        select(NotificationLogRead.id)
        .where(
            NotificationLogRead.log_id == NotificationLog.id,
            NotificationLogRead.user_id == principal.user_id,
        )
        .exists()
    )
    conditions: list[ColumnElement[bool]] = [NotificationLog.tenant_id.is_(None)]
    if status_filter is not None:
        conditions.append(NotificationLog.status == status_filter)
    if channel_type is not None:
        conditions.append(NotificationLog.channel_type == channel_type)
    if event_type is not None:
        conditions.append(NotificationLog.event_type == event_type)
    if unread_only:
        conditions.append(~read_marker)

    total = int(
        (
            await session.execute(
                select(func.count()).select_from(NotificationLog).where(*conditions)
            )
        ).scalar_one()
    )
    result = await session.execute(
        select(NotificationLog, read_marker.label("is_read"))
        .where(*conditions)
        .order_by(NotificationLog.created_at.desc(), NotificationLog.id.desc())
        .limit(limit)
        .offset(offset)
    )
    items = [_log_to_response(row[0], read=bool(row[1])) for row in result.all()]
    unread = await _platform_unread_count(session, user_id=principal.user_id)
    return NotificationInboxResponse(
        items=items, total=total, unread=unread, limit=limit, offset=offset
    )


@router.post("/platform/logs/{log_id}/read", response_model=MarkReadResponse)
async def mark_platform_log_read(
    log_id: UUID,
    principal: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> MarkReadResponse:
    """Marca leída una notificación de PLATAFORMA (idempotente).

    Solo aplica a filas ``tenant_id IS NULL`` — una fila de tenant se marca por
    su endpoint de tenant (404 aquí, sin filtrar la existencia cruzada)."""
    log = (
        await session.execute(
            select(NotificationLog).where(
                NotificationLog.id == log_id, NotificationLog.tenant_id.is_(None)
            )
        )
    ).scalar_one_or_none()
    if log is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="notification log not found"
        )
    stmt = (
        pg_insert(NotificationLogRead)
        .values(tenant_id=None, user_id=principal.user_id, log_id=log_id)
        .on_conflict_do_nothing(constraint="uq_notification_log_reads_user_log")
    )
    res = cast("CursorResult[Any]", await session.execute(stmt))
    marked = int(res.rowcount or 0)
    unread = await _platform_unread_count(session, user_id=principal.user_id)
    return MarkReadResponse(marked=marked, unread=unread)


@router.post("/platform/logs/read-all", response_model=MarkReadResponse)
async def mark_all_platform_logs_read(
    principal: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> MarkReadResponse:
    """Marca leídas TODAS las notificaciones de plataforma pendientes del
    System Admin llamante (idempotente, un solo INSERT…SELECT)."""
    already_read = (
        select(NotificationLogRead.log_id)
        .where(NotificationLogRead.user_id == principal.user_id)
        .scalar_subquery()
    )
    unread_logs = select(
        func.gen_random_uuid().label("id"),
        NotificationLog.tenant_id.label("tenant_id"),
        literal(principal.user_id).label("user_id"),
        NotificationLog.id.label("log_id"),
    ).where(
        NotificationLog.tenant_id.is_(None),
        NotificationLog.id.not_in(already_read),
    )
    stmt = pg_insert(NotificationLogRead).from_select(
        ["id", "tenant_id", "user_id", "log_id"], unread_logs
    )
    res = cast("CursorResult[Any]", await session.execute(stmt))
    marked = int(res.rowcount or 0)
    return MarkReadResponse(marked=marked, unread=0)


# Audit action recorded when an operator manually re-enqueues a dead-lettered
# send. Greppable across the audit trail.
_AUDIT_ACTION = "notification.retry"


class NotificationRetryResponse(BaseModel):
    """The fresh ``queued`` attempt produced by a manual retry."""

    log_id: UUID = Field(description="The id of the new queued NotificationLog row.")
    status: str = Field(description="Status of the new row (always 'queued').")
    source_log_id: UUID = Field(description="The dead-lettered log that was retried.")
    attempt: int = Field(description="1-based attempt number of the new row.")


@router.post(
    "/logs/{log_id}/retry",
    response_model=NotificationRetryResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_notification(
    log_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> NotificationRetryResponse:
    """Re-enqueue a dead-lettered notification send (tenant_admin only).

    Loads the log via the RLS-bound tenant session (another tenant's log →
    404), rejects a non-dead-lettered log (409), then — in one transaction —
    writes a fresh ``queued`` log row, flips the source row out of
    ``dead_letter`` (idempotency), appends a ``notification.retry`` audit row,
    and publishes the send onto the dispatcher's default lane.
    """
    tenant_id = require_tenant_id(principal)

    # RLS scopes this SELECT to the caller's tenant: another tenant's log row
    # is invisible and surfaces as a clean 404 (no cross-tenant confirmation).
    result = await session.execute(select(NotificationLog).where(NotificationLog.id == log_id))
    log = result.scalar_one_or_none()
    if log is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="notification log not found"
        )

    # Only a dead-lettered send is retryable — the endpoint is the DLQ escape
    # hatch, not a generic send surface. A second click finds the already-
    # flipped row here and 409s (idempotency: no duplicate live send).
    if log.status != NotificationStatus.DEAD_LETTER.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"notification log is '{log.status}', not '{NotificationStatus.DEAD_LETTER.value}'"
                " — only a dead-lettered send can be retried"
            ),
        )

    # A retry cannot re-drive a send whose channel was deleted (FK SET NULL).
    if log.channel_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="notification log has no channel (it was deleted); cannot retry",
        )

    # Flip the source row out of dead_letter BEFORE enqueueing so a concurrent
    # double-click loses the race on this UPDATE (the per-request transaction
    # holds the row) and finds a non-dead-letter row → 409. The append-only
    # invariant is preserved: we record the new attempt as a NEW row below.
    log.status = NotificationStatus.RETRYING.value

    # The fresh attempt — a new append-only row carrying attempt+1 and the
    # resolved transport, so the full retry history is preserved.
    new_attempt = log.attempt + 1
    new_log = NotificationLog(
        channel_id=log.channel_id,
        tenant_id=tenant_id,
        event_type=log.event_type,
        channel_type=log.channel_type,
        status=NotificationStatus.QUEUED.value,
        target=log.target,
        attempt=new_attempt,
    )
    session.add(new_log)
    await session.flush()

    # Mandatory append-only audit — same transaction as the status flip + new
    # row, so the retry can never commit without its audit record.
    await write_audit_log(
        session,
        action=_AUDIT_ACTION,
        actor_user_id=principal.user_id,
        tenant_id=tenant_id,
        resource_type="notification_log",
        resource_id=new_log.id,
        changes={"source_log_id": str(log_id), "attempt": new_attempt},
    )

    # Re-enqueue onto the dispatcher's default lane. This is a network call to
    # the broker; if it fails the whole transaction rolls back (the row flip,
    # the new row, the audit) so we never claim a retry that was not enqueued.
    send_request: dict[str, Any] = {
        "channel_id": str(log.channel_id),
        "event_type": log.event_type,
        "tenant_id": str(tenant_id),
        "target": log.target,
        "body": "",
        "structured": None,
    }
    await enqueue_notification_send(send_request, queue="notifications.default")

    return NotificationRetryResponse(
        log_id=new_log.id,
        status=NotificationStatus.QUEUED.value,
        source_log_id=log_id,
        attempt=new_attempt,
    )
