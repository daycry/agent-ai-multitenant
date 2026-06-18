"""Per-project incoming-webhook config management (Plan 13 task_13_11).

``/projects/{project_id}/incoming-webhooks`` — the operator-facing CONFIG
surface for INBOUND webhooks (the inverse of Plan 10's OUTGOING signing). The
project owner / Tenant Admin creates, lists, edits, rotates and disables the
per-project configs that the PUBLIC receive endpoint
(``/webhooks/incoming/{origin}/{config_id}``, task_13_08) resolves and whose
HMAC it verifies. This router only MANAGES configs; it never receives events.

RBAC + multi-tenancy (CLAUDE.md principle 1): every endpoint is
JWT-authenticated and gated on ``tenant_admin`` (the project-management gate
used across the codebase — there is no distinct ``project_owner`` role; the
plan's "project_owner/tenant_admin" maps to it) and runs on a tenant-scoped RLS
session, so an operator only ever sees / mutates configs of their OWN tenant.
Every config is additionally PROJECT scoped: the path ``project_id`` is verified
visible (404 otherwise) and stamped on the row, and the listing / mutations
filter by it — a config of project A is invisible to a request for project B,
and an event for project A can never act on tenant B. The
``@pytest.mark.cross_tenant`` test pins this.

Secret handling (CLAUDE.md: no plaintext secrets, never echoed twice). The HMAC
signing secret is minted server-side, stored ONLY as Fernet ciphertext, and the
clear value is returned EXACTLY ONCE in the create / rotate response so the
operator can paste it into the external provider. It never appears in a list /
get response and can never be retrieved again — losing it means rotating.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from api_server.auth.deps import (
    AuthPrincipal,
    get_tenant_session,
    require_tenant_admin,
)
from api_server.db.domain import Project
from api_server.db.models import IncomingWebhookConfig, IncomingWebhookEvent
from api_server.routers._helpers import (
    get_writable_or_404,
    require_tenant_id,
    soft_delete,
)
from api_server.schemas.incoming_webhooks import (
    IncomingWebhookConfigCreateRequest,
    IncomingWebhookConfigResponse,
    IncomingWebhookConfigSecretResponse,
    IncomingWebhookConfigUpdateRequest,
    IncomingWebhookDeliveryResponse,
    IncomingWebhookReplayResponse,
)
from api_server.webhooks.replay import WebhookReplayError, replay_delivery
from api_server.webhooks.secrets import encrypt_signing_secret, generate_signing_secret

router = APIRouter(prefix="/projects/{project_id}/incoming-webhooks", tags=["incoming-webhooks"])


def _incoming_path(config: IncomingWebhookConfig) -> str:
    """The relative path the external provider POSTs verified events to.

    Mirrors :mod:`api_server.routers.incoming_webhooks` route shape. Non-secret
    (the HMAC is the auth); the UI prefixes the deployment's public base URL.
    """
    return f"/webhooks/incoming/{config.origin}/{config.id}"


def _to_response(config: IncomingWebhookConfig) -> IncomingWebhookConfigResponse:
    """Project a config row to its metadata response (NEVER the secret)."""
    return IncomingWebhookConfigResponse(
        id=config.id,
        project_id=config.project_id,
        origin=config.origin,
        name=config.name,
        enabled=config.enabled,
        action_mappings=list(config.action_mappings),
        last_event_at=config.last_event_at,
        created_at=config.created_at,
        updated_at=config.updated_at,
        incoming_path=_incoming_path(config),
    )


async def _verify_project_visible(session: AsyncSession, project_id: UUID) -> None:
    """Resolve the path project under RLS, or 404.

    RLS already scopes to the caller's tenant; this lookup turns a cross-tenant
    / missing / soft-deleted project into an explicit 404 (never leaking whether
    a project id exists elsewhere) before we create or list its configs.
    """
    result = await session.execute(
        select(Project.id).where(Project.id == project_id, Project.deleted_at.is_(None))
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")


async def _get_config_or_404(
    session: AsyncSession,
    *,
    project_id: UUID,
    config_id: UUID,
    principal: AuthPrincipal,
) -> IncomingWebhookConfig:
    """Load a writable config scoped to the caller's tenant AND the path project.

    Tenant scoping is via ``get_writable_or_404`` (tenant_id + non-deleted); the
    extra ``project_id`` filter ensures a config of another project in the same
    tenant 404s rather than being editable through the wrong project's URL.
    """
    return await get_writable_or_404(
        session,
        IncomingWebhookConfig,
        config_id,
        principal,
        not_found_detail="incoming webhook config not found",
        extra_filters=(IncomingWebhookConfig.project_id == project_id,),
    )


@router.get("", response_model=list[IncomingWebhookConfigResponse])
async def list_incoming_webhook_configs(
    project_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[IncomingWebhookConfigResponse]:
    """List a project's incoming-webhook configs — NEVER the secret. tenant_admin.

    RLS scopes to the caller's tenant; the ``project_id`` filter scopes to the
    path project. Soft-deleted configs are excluded.
    """
    require_tenant_id(principal)
    await _verify_project_visible(session, project_id)
    result = await session.execute(
        select(IncomingWebhookConfig)
        .where(
            IncomingWebhookConfig.project_id == project_id,
            IncomingWebhookConfig.deleted_at.is_(None),
        )
        .order_by(IncomingWebhookConfig.created_at.desc())
    )
    return [_to_response(row) for row in result.scalars().all()]


@router.post(
    "",
    response_model=IncomingWebhookConfigSecretResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_incoming_webhook_config(
    project_id: UUID,
    payload: IncomingWebhookConfigCreateRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> IncomingWebhookConfigSecretResponse:
    """Create a per-project incoming-webhook config. tenant_admin only.

    Mints the HMAC signing secret server-side and returns the clear value
    EXACTLY ONCE (the response carries ``signing_secret``); only its Fernet
    ciphertext is stored. The operator pastes the clear secret into the external
    provider so it stamps a signature we can verify.
    """
    tenant_id = require_tenant_id(principal)
    await _verify_project_visible(session, project_id)

    secret = generate_signing_secret()
    config = IncomingWebhookConfig(
        id=uuid7(),
        tenant_id=tenant_id,
        project_id=project_id,
        origin=payload.origin.value,
        name=payload.name,
        signing_secret_encrypted=encrypt_signing_secret(secret),
        enabled=payload.enabled,
        action_mappings=[rule.model_dump(mode="json") for rule in payload.action_mappings],
    )
    session.add(config)
    await session.flush()
    await session.refresh(config)
    return IncomingWebhookConfigSecretResponse(
        **_to_response(config).model_dump(),
        signing_secret=secret,
    )


@router.put("/{config_id}", response_model=IncomingWebhookConfigResponse)
async def update_incoming_webhook_config(
    project_id: UUID,
    config_id: UUID,
    payload: IncomingWebhookConfigUpdateRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> IncomingWebhookConfigResponse:
    """Edit a config's NON-secret fields (name / enabled / mappings). tenant_admin.

    The secret is never touched here (rotate it through the dedicated endpoint);
    ``origin`` is immutable (the public URL embeds it). Use this to DISABLE a
    config (``enabled=false``) — a disabled config rejects every event (404).
    """
    require_tenant_id(principal)
    config = await _get_config_or_404(
        session, project_id=project_id, config_id=config_id, principal=principal
    )
    changes = payload.model_dump(exclude_unset=True)
    if "action_mappings" in changes and changes["action_mappings"] is not None:
        changes["action_mappings"] = [
            rule.model_dump(mode="json") for rule in (payload.action_mappings or [])
        ]
    for attr, value in changes.items():
        setattr(config, attr, value)
    await session.flush()
    await session.refresh(config)
    return _to_response(config)


@router.post("/{config_id}/rotate-secret", response_model=IncomingWebhookConfigSecretResponse)
async def rotate_incoming_webhook_secret(
    project_id: UUID,
    config_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> IncomingWebhookConfigSecretResponse:
    """Mint a NEW signing secret for a config, invalidating the old one. tenant_admin.

    Returns the new clear secret EXACTLY ONCE; the previous secret stops
    verifying immediately (the stored ciphertext is replaced). The operator must
    update the external provider with the new value. Used when a secret is
    suspected leaked.
    """
    require_tenant_id(principal)
    config = await _get_config_or_404(
        session, project_id=project_id, config_id=config_id, principal=principal
    )
    secret = generate_signing_secret()
    config.signing_secret_encrypted = encrypt_signing_secret(secret)
    await session.flush()
    await session.refresh(config)
    return IncomingWebhookConfigSecretResponse(
        **_to_response(config).model_dump(),
        signing_secret=secret,
    )


@router.get(
    "/{config_id}/deliveries",
    response_model=list[IncomingWebhookDeliveryResponse],
)
async def list_incoming_webhook_deliveries(
    project_id: UUID,
    config_id: UUID,
    limit: int = Query(default=20, ge=1, le=100),
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[IncomingWebhookDeliveryResponse]:
    """Recent VERIFIED deliveries for a config — metadata only. tenant_admin.

    Shows the operator that events are arriving + verifying, newest first. The
    raw body / signature are NOT returned (that is replay territory,
    task_13_12); no secret is ever exposed. RLS scopes events to the caller's
    tenant; ``_get_config_or_404`` first confirms the config belongs to the
    caller's tenant AND the path project, so a cross-tenant config 404s.
    """
    require_tenant_id(principal)
    await _get_config_or_404(
        session, project_id=project_id, config_id=config_id, principal=principal
    )
    result = await session.execute(
        select(IncomingWebhookEvent)
        .where(IncomingWebhookEvent.config_id == config_id)
        .order_by(IncomingWebhookEvent.received_at.desc())
        .limit(limit)
    )
    return [IncomingWebhookDeliveryResponse.model_validate(row) for row in result.scalars().all()]


@router.post(
    "/{config_id}/deliveries/{event_id}/replay",
    response_model=IncomingWebhookReplayResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def replay_incoming_webhook_delivery(
    project_id: UUID,
    config_id: UUID,
    event_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> IncomingWebhookReplayResponse:
    """Replay a recorded delivery — re-run verify+parse+map+action. tenant_admin.

    For debugging: re-runs the pipeline against the delivery's STORED payload
    (task_13_12). The replay RE-VERIFIES the stored signature against the
    config's current secret, re-maps the payload and re-executes the mapped
    action, and is ITSELF audited (a new ``incoming_webhook_events`` row with
    ``replayed_from_event_id`` set). It is explicitly operator-initiated, so the
    replay row carries no ``delivery_id`` and never collides with inbound
    idempotency — replaying twice records two replay rows.

    Multi-tenancy: ``_get_config_or_404`` confirms the config belongs to the
    caller's tenant AND the path project; the source event is then loaded under
    the same RLS scope (config_id + project_id filter) and 404s otherwise — so a
    tenant can never replay another tenant's (or project's) delivery. A replay
    whose stored signature no longer verifies (e.g. the secret was rotated) is a
    422 rather than a silent re-run.
    """
    require_tenant_id(principal)
    config = await _get_config_or_404(
        session, project_id=project_id, config_id=config_id, principal=principal
    )
    result = await session.execute(
        select(IncomingWebhookEvent).where(
            IncomingWebhookEvent.id == event_id,
            IncomingWebhookEvent.config_id == config_id,
            IncomingWebhookEvent.project_id == project_id,
        )
    )
    source_event = result.scalar_one_or_none()
    if source_event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="webhook delivery not found"
        )
    try:
        outcome = await replay_delivery(session, config=config, source_event=source_event)
    except WebhookReplayError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return IncomingWebhookReplayResponse(
        replay_event_id=outcome.replay_event_id,
        source_event_id=outcome.source_event_id,
        action=outcome.action.kind.value if outcome.action is not None else None,
        task_id=outcome.action.task_id if outcome.action is not None else None,
    )


@router.delete("/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_incoming_webhook_config(
    project_id: UUID,
    config_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    """Soft-delete a config. tenant_admin only.

    The row stays for audit (its received events reference it) but the public
    endpoint treats a soft-deleted config as not found (404). RLS + the
    ``project_id`` filter scope the lookup, so a tenant cannot delete another
    tenant's (or another project's) config — it 404s rather than silently
    succeeding.
    """
    require_tenant_id(principal)
    config = await _get_config_or_404(
        session, project_id=project_id, config_id=config_id, principal=principal
    )
    await soft_delete(session, config)


__all__ = ["router"]
