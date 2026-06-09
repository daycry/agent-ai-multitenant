"""``/admin/platform-settings`` — edit operator-tunable PLATFORM defaults (ADR 0028).

The platform-global analog of ``/tenant-settings`` (per-tenant). It serves the
registry of tunable ``platform_settings`` that lacked a dedicated page
(:mod:`api_server.platform_settings_registry`) so a System Admin can edit them
from the panel — chief among them ``model.default_config`` (the agent default
model, ADR 0055), which had no write surface.

  * ``GET /admin/platform-settings/_registry`` — grouped registry (labels,
    types, bounds) the UI renders controls from.
  * ``GET /admin/platform-settings``           — current value of every tunable
    key (with ``is_default``).
  * ``PUT /admin/platform-settings/{key}``      — validate (per type) + persist.

All System-Admin only (``require_system_admin`` on the BYPASSRLS admin session);
``set_platform_setting`` re-checks the actor is a System Admin.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import AuthPrincipal, get_admin_session, require_system_admin
from api_server.db.llm_providers import list_llm_providers
from api_server.db.models import PlatformSetting, User
from api_server.db.platform_settings import set_platform_setting
from api_server.platform_settings_registry import (
    PLATFORM_KNOWN_SETTINGS,
    UnknownPlatformSettingError,
    platform_registry_to_dict,
    validate_platform_setting_value,
)

admin_router = APIRouter(prefix="/admin/platform-settings", tags=["admin", "platform-settings"])

_BASE_CONFIG = ConfigDict(populate_by_name=True)


class PlatformSettingValue(BaseModel):
    model_config = _BASE_CONFIG
    key: str
    value: Any
    is_default: bool
    """True when no row exists yet (the value is the registry default)."""


class PlatformSettingUpdateRequest(BaseModel):
    model_config = _BASE_CONFIG
    value: Any


class ModelOptionsResponse(BaseModel):
    model_config = _BASE_CONFIG
    by_kind: dict[str, list[str]]
    """Selectable model ids per provider kind — fills the model dropdown when a
    provider (kind) is chosen for ``model.default_config``. Built from the
    ACTIVE providers' synced/catalogued models (union per kind)."""


@admin_router.get("/model-options", response_model=ModelOptionsResponse)
async def get_model_options(
    _: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> ModelOptionsResponse:
    """Models selectable per provider kind (no network — read from the DB).

    Mirrors how the agent dispatch resolves a ``model.default_config`` (by kind),
    so the UI can offer a real dropdown of models for the chosen provider."""
    from api_server.assistant.model_config import list_available_models_for_provider

    providers = await list_llm_providers(session, active_only=True)
    by_kind: dict[str, set[str]] = {}
    for provider in providers:
        models = await list_available_models_for_provider(session, provider)
        by_kind.setdefault(provider.kind, set()).update(models)
    return ModelOptionsResponse(by_kind={kind: sorted(models) for kind, models in by_kind.items()})


@admin_router.get("/_registry")
async def get_platform_settings_registry(
    _: AuthPrincipal = Depends(require_system_admin),
) -> dict[str, Any]:
    """The in-code registry of tunable platform settings (grouped). The UI
    renders the typed controls from this — no hardcoded labels/types."""
    return {"categories": platform_registry_to_dict()}


@admin_router.get("", response_model=list[PlatformSettingValue])
async def list_platform_settings(
    _: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> list[PlatformSettingValue]:
    """Current value of every tunable key. Unset keys come back with the
    registry default + ``is_default=True`` so the UI has a complete picture."""
    keys = [key for cat in PLATFORM_KNOWN_SETTINGS.values() for key in cat.settings]
    rows = (
        (await session.execute(select(PlatformSetting).where(PlatformSetting.key.in_(keys))))
        .scalars()
        .all()
    )
    set_by_key = {row.key: row.value for row in rows}
    out: list[PlatformSettingValue] = []
    for cat in PLATFORM_KNOWN_SETTINGS.values():
        for key, sdef in cat.settings.items():
            out.append(
                PlatformSettingValue(
                    key=key,
                    value=set_by_key.get(key, sdef.default),
                    is_default=key not in set_by_key,
                )
            )
    return out


@admin_router.put("/{key}", response_model=PlatformSettingValue)
async def update_platform_setting(
    key: str,
    payload: PlatformSettingUpdateRequest,
    principal: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> PlatformSettingValue:
    """Validate (per the registry type) + persist a platform setting.

    404 if ``key`` is not a registered tunable setting; 422 if the value fails
    its type/bounds validation; otherwise it is written and echoed back."""
    try:
        coerced = validate_platform_setting_value(key, payload.value)
    except UnknownPlatformSettingError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    actor = await session.get(User, principal.user_id)
    if actor is None:  # pragma: no cover - a valid session always has a user
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="actor not found")

    await set_platform_setting(session, key, coerced, actor=actor)
    return PlatformSettingValue(key=key, value=coerced, is_default=False)
