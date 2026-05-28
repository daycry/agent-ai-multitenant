"""`/tenant-settings/...` endpoints (Plan 03 task_03_26).

Per-tenant configuration knobs a `tenant_admin` operator owns. The
first one to land is the hourly rate the human-cost calculator uses
(`compute_human_cost`, Plan 03 task_03_22). NULL on the
``organizations`` row means "use the platform default".

Authorization model:
  - GET: any authenticated user with an active tenant_id in their JWT.
  - PUT: the JWT user must hold `tenant_admin` (or higher) on the
    target tenant, OR be a `is_system_admin`. We resolve the role
    via the `user_org_memberships` table.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import (
    AuthPrincipal,
    get_tenant_session,
    require_tenant_admin,
    require_tenant_member,
)
from api_server.db.models import (
    Organization,
    TenantSetting,
)
from api_server.routers._helpers import require_tenant_id
from api_server.settings_registry import (
    KNOWN_SETTINGS,
    UnknownSettingError,
    registry_to_dict,
    validate_setting_value,
)

router = APIRouter(prefix="/tenant-settings", tags=["tenant-settings"])


_BASE_CONFIG = ConfigDict(populate_by_name=True, str_strip_whitespace=True)


class HourlyRateResponse(BaseModel):
    model_config = _BASE_CONFIG

    hourly_rate: Decimal | None
    hourly_rate_currency: str | None


class HourlyRateUpdateRequest(BaseModel):
    model_config = _BASE_CONFIG

    hourly_rate: Decimal | None = Field(default=None, ge=0, le=10_000)
    hourly_rate_currency: str | None = Field(default=None, min_length=3, max_length=3)


async def _load_org(session: AsyncSession, tenant_id: Any) -> Organization:
    result = await session.execute(
        select(Organization).where(Organization.id == tenant_id, Organization.deleted_at.is_(None))
    )
    org = result.scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="tenant not found")
    return org


@router.get("/hourly-rate", response_model=HourlyRateResponse)
async def get_tenant_hourly_rate(
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> HourlyRateResponse:
    tenant_id = require_tenant_id(principal)
    org = await _load_org(session, tenant_id)
    return HourlyRateResponse(
        hourly_rate=org.hourly_rate,
        hourly_rate_currency=org.hourly_rate_currency,
    )


@router.put("/hourly-rate", response_model=HourlyRateResponse)
async def update_tenant_hourly_rate(
    payload: HourlyRateUpdateRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> HourlyRateResponse:
    tenant_id = require_tenant_id(principal)
    org = await _load_org(session, tenant_id)

    # We accept either field independently. Setting both to None is the
    # "clear the override, fall back to the platform default" operation.
    if payload.hourly_rate is not None:
        org.hourly_rate = payload.hourly_rate
    if payload.hourly_rate_currency is not None:
        org.hourly_rate_currency = payload.hourly_rate_currency.upper()

    await session.flush()
    await session.refresh(org)
    return HourlyRateResponse(
        hourly_rate=org.hourly_rate,
        hourly_rate_currency=org.hourly_rate_currency,
    )


# ---------------------------------------------------------------------------
# Plan 06.7 — Generic tenant-settings (category, key, value)
# ---------------------------------------------------------------------------


class SettingValueRequest(BaseModel):
    """Body of PUT /tenant-settings/{category}/{key}."""

    model_config = _BASE_CONFIG
    value: Any


class SettingValueResponse(BaseModel):
    model_config = _BASE_CONFIG
    category: str
    key: str
    value: Any
    is_default: bool
    """True when the value comes from the registry default (no row
    in the table). Useful for the UI to show 'configurar' vs
    'editar'."""


@router.get("/_registry")
async def get_settings_registry(
    _principal: AuthPrincipal = Depends(require_tenant_member),
) -> dict[str, Any]:
    """Return the in-code registry of known categories + settings.

    The UI consumes this to render the cards on `/admin/settings`
    and the forms on each category page — no hardcoded labels or
    icons in the frontend.
    """
    return {"categories": registry_to_dict()}


@router.get("/{category}", response_model=list[SettingValueResponse])
async def list_category_settings(
    category: str,
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[SettingValueResponse]:
    """Return every (key, value) pair of a category for the current
    tenant. Keys not yet set are returned with `is_default=True` and
    the registry's default value — so the UI always has a complete
    picture without a follow-up roundtrip per key."""
    tenant_id = require_tenant_id(principal)
    if category not in KNOWN_SETTINGS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown setting category {category!r}",
        )

    cat = KNOWN_SETTINGS[category]
    # Pull all rows for this tenant + category in one query.
    stmt = select(TenantSetting).where(
        TenantSetting.tenant_id == tenant_id,
        TenantSetting.category == category,
    )
    rows = (await session.execute(stmt)).scalars().all()
    set_by_key = {row.key: row.value for row in rows}

    return [
        SettingValueResponse(
            category=category,
            key=key,
            value=set_by_key.get(key, sdef.default),
            is_default=key not in set_by_key,
        )
        for key, sdef in cat.settings.items()
    ]


@router.get("/{category}/{key}", response_model=SettingValueResponse)
async def get_category_setting(
    category: str,
    key: str,
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> SettingValueResponse:
    tenant_id = require_tenant_id(principal)
    try:
        from api_server.settings_registry import get_setting_def

        sdef = get_setting_def(category, key)
    except UnknownSettingError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    stmt = select(TenantSetting.value).where(
        TenantSetting.tenant_id == tenant_id,
        TenantSetting.category == category,
        TenantSetting.key == key,
    )
    row_value = (await session.execute(stmt)).scalar_one_or_none()
    return SettingValueResponse(
        category=category,
        key=key,
        value=row_value if row_value is not None else sdef.default,
        is_default=row_value is None,
    )


@router.put("/{category}/{key}", response_model=SettingValueResponse)
async def put_category_setting(
    category: str,
    key: str,
    payload: SettingValueRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> SettingValueResponse:
    """Upsert a single setting. Validates against the registry
    (unknown keys → 404, out-of-range values → 422). Requires
    tenant_admin (same as the hourly-rate endpoint)."""
    tenant_id = require_tenant_id(principal)

    try:
        coerced = validate_setting_value(category, key, payload.value)
    except UnknownSettingError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    # Upsert — fetch first, then add or update. Simpler than dialect-
    # specific INSERT … ON CONFLICT and the table is small.
    stmt = select(TenantSetting).where(
        TenantSetting.tenant_id == tenant_id,
        TenantSetting.category == category,
        TenantSetting.key == key,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        row = TenantSetting(
            tenant_id=tenant_id,
            category=category,
            key=key,
            value=coerced,
            updated_by_user_id=principal.user_id,
        )
        session.add(row)
    else:
        row.value = coerced
        row.updated_by_user_id = principal.user_id
        row.updated_at = datetime.now(tz=UTC)
    await session.flush()

    return SettingValueResponse(category=category, key=key, value=coerced, is_default=False)


__all__ = ["router"]
