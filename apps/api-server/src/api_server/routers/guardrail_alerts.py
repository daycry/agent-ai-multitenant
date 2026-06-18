"""`/guardrails/alert-rules` — tenant guardrail alert-rule CRUD (Plan 11 task_11_21).

A Tenant Admin manages the configurable guardrail alert rules for THEIR
tenant: "alert me when guardrail violations cross THRESHOLD within
WINDOW_SECONDS" (optionally scoped to a ``guardrail_type`` and/or a
``min_severity``). The evaluator (``api_server.guardrails.alerts``) counts
matching ``guardrail_events`` and fires ONE alert per rule per window
through the Plan 10 notifier.

RBAC + tenancy (CLAUDE.md principle 1):

  - Every endpoint is gated to ``tenant_admin`` (``require_tenant_admin`` —
    a plain ``tenant_user`` / member is a clean 403; managing alert rules is
    an admin surface) and runs on the tenant-scoped RLS session
    (``get_tenant_session``).
  - The ``guardrail_alert_rules`` tenant-isolation RLS policy (migration
    0053) restricts every read / write to the caller's tenant, so a Tenant
    Admin can NEVER see or mutate another tenant's rules — defence in depth
    with the explicit ``tenant_id`` predicate on each query.

CRUD: ``POST`` create, ``GET`` list (paginated), ``GET /{id}`` one,
``PATCH /{id}`` partial update (empty patch → 422), ``DELETE /{id}``
soft-delete (sets ``deleted_at`` so a removed rule stops evaluating but its
history survives).
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import (
    AuthPrincipal,
    get_tenant_session,
    require_tenant_admin,
)
from api_server.db.guardrail_alert_rule import GuardrailAlertRule
from api_server.routers._helpers import require_tenant_id
from api_server.routers._pagination import apply_pagination, limit_query, offset_query
from api_server.schemas.guardrail_alerts import (
    GuardrailAlertRuleCreateRequest,
    GuardrailAlertRuleResponse,
    GuardrailAlertRuleUpdateRequest,
    to_rule_response,
)

router = APIRouter(prefix="/guardrails/alert-rules", tags=["guardrails"])


async def _load_rule(
    session: AsyncSession, *, tenant_id: object, rule_id: UUID
) -> GuardrailAlertRule:
    """Load a live alert rule by id within the caller's tenant, or 404.

    The ``tenant_id`` predicate is defence in depth on top of RLS — RLS
    already hides another tenant's rows, so a cross-tenant id is a 404, not
    a 403 (we never reveal the row exists).
    """
    result = await session.execute(
        select(GuardrailAlertRule).where(
            GuardrailAlertRule.id == rule_id,
            GuardrailAlertRule.tenant_id == tenant_id,
            GuardrailAlertRule.deleted_at.is_(None),
        )
    )
    rule = result.scalar_one_or_none()
    if rule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="alert rule not found",
        )
    return rule


# ===========================================================================
# POST /guardrails/alert-rules — create a rule (tenant_admin)
# ===========================================================================
@router.post("", response_model=GuardrailAlertRuleResponse, status_code=status.HTTP_201_CREATED)
async def create_alert_rule(
    payload: GuardrailAlertRuleCreateRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> GuardrailAlertRuleResponse:
    """Create one guardrail alert rule for the caller's tenant.

    Tenant-scoped (RLS): the row is written with the caller's ``tenant_id``,
    which must match the RLS-bound ``app.tenant_id``. ``threshold`` /
    ``window_seconds`` come validated from the schema (defaults are the
    named ORM constants). RBAC: ``require_tenant_admin`` (a member is 403).
    """
    tenant_id = require_tenant_id(principal)
    rule = GuardrailAlertRule(
        tenant_id=tenant_id,
        name=payload.name,
        threshold=payload.threshold,
        window_seconds=payload.window_seconds,
        guardrail_type=payload.guardrail_type,
        min_severity=(payload.min_severity.value if payload.min_severity is not None else None),
        enabled=payload.enabled,
    )
    session.add(rule)
    await session.flush()
    await session.refresh(rule)
    return to_rule_response(rule)


# ===========================================================================
# GET /guardrails/alert-rules — list the tenant's rules (tenant_admin)
# ===========================================================================
@router.get("", response_model=list[GuardrailAlertRuleResponse])
async def list_alert_rules(
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
    limit: int = limit_query(),
    offset: int = offset_query(),
) -> list[GuardrailAlertRuleResponse]:
    """List this tenant's live alert rules, newest-first, paginated.

    Tenant-scoped (RLS) — only the caller tenant's rules are visible.
    """
    tenant_id = require_tenant_id(principal)
    stmt = (
        select(GuardrailAlertRule)
        .where(
            GuardrailAlertRule.tenant_id == tenant_id,
            GuardrailAlertRule.deleted_at.is_(None),
        )
        .order_by(GuardrailAlertRule.created_at.desc(), GuardrailAlertRule.id.desc())
    )
    stmt = apply_pagination(stmt, limit=limit, offset=offset)
    rows = (await session.execute(stmt)).scalars().all()
    return [to_rule_response(r) for r in rows]


# ===========================================================================
# GET /guardrails/alert-rules/{id} — one rule (tenant_admin)
# ===========================================================================
@router.get("/{rule_id}", response_model=GuardrailAlertRuleResponse)
async def get_alert_rule(
    rule_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> GuardrailAlertRuleResponse:
    """Fetch one of the caller tenant's alert rules by id (404 if unknown)."""
    tenant_id = require_tenant_id(principal)
    rule = await _load_rule(session, tenant_id=tenant_id, rule_id=rule_id)
    return to_rule_response(rule)


# ===========================================================================
# PATCH /guardrails/alert-rules/{id} — partial update (tenant_admin)
# ===========================================================================
@router.patch("/{rule_id}", response_model=GuardrailAlertRuleResponse)
async def update_alert_rule(
    rule_id: UUID,
    payload: GuardrailAlertRuleUpdateRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> GuardrailAlertRuleResponse:
    """Patch the mutable fields of a rule. Empty patch → 422.

    Only fields present in the request are applied (``model_fields_set``),
    so ``guardrail_type`` / ``min_severity`` can be explicitly set to null
    to widen the rule back to "any". Tenant-scoped (RLS). RBAC:
    ``require_tenant_admin``.
    """
    tenant_id = require_tenant_id(principal)
    provided = payload.model_dump(include=payload.model_fields_set)
    if not provided:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="no fields to update",
        )
    rule = await _load_rule(session, tenant_id=tenant_id, rule_id=rule_id)
    for field_name, value in provided.items():
        setattr(rule, field_name, value.value if isinstance(value, enum.Enum) else value)
    await session.flush()
    await session.refresh(rule)
    return to_rule_response(rule)


# ===========================================================================
# DELETE /guardrails/alert-rules/{id} — soft-delete (tenant_admin)
# ===========================================================================
@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_alert_rule(
    rule_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    """Soft-delete a rule (sets ``deleted_at``) so it stops evaluating.

    Soft-delete keeps the row for history rather than hard-deleting.
    Tenant-scoped (RLS). RBAC: ``require_tenant_admin``.
    """
    tenant_id = require_tenant_id(principal)
    rule = await _load_rule(session, tenant_id=tenant_id, rule_id=rule_id)
    rule.deleted_at = datetime.now(tz=UTC)
    await session.flush()


__all__ = ["router"]
