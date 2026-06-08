"""Resolve + persist the personal assistant's LLM model selection (ADR 0053).

The assistant answers with a concrete ``(provider_id, model_id)``. That
selection is resolved with **inheritance**: a per-tenant override wins,
falling back to a platform default, falling back to *nothing* (the chat
endpoint then returns a clear 503 rather than fabricate an answer).

Storage reuses the generic config tables — no migration:

  * Tenant override → ``tenant_settings`` (``category='assistant'``,
    ``key='model'``) holding ``{"provider_id", "model_id"}``. Kept apart
    from the identity blob (``key='identity'``) so persona and model evolve
    independently.
  * Platform default → ``platform_settings`` (``key='assistant.default_model'``),
    same shape, written only by a System Admin.

Validity (ADR 0021 closed catalogue): a selection is valid only when its
``provider_id`` is an ACTIVE ``llm_providers`` row AND its ``model_id`` is in
that provider's current price catalogue (``model_prices`` rows whose period
is open, matched by the provider association OR the kind→family map). A
selection that stops being valid (provider disabled, model retired) silently
falls through to the next tier — never an error to the end user.

``llm_providers`` is platform-global with NO RLS (ADR 0028): the resolver
runs on the BYPASSRLS admin session the caller opens internally (the tenant
``app_user`` role cannot read the table at all). Reads of ``tenant_settings``
filter ``tenant_id`` explicitly so they are correct on either an RLS-bound
tenant session (the read endpoint) or the admin session (resolution).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import delete, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.assistant.config import ASSISTANT_SETTINGS_CATEGORY
from api_server.db.llm_providers import LlmProvider, get_llm_provider
from api_server.db.model_prices import ModelPrice
from api_server.db.models import TenantSetting, User
from api_server.db.platform_settings import get_platform_setting, set_platform_setting
from api_server.pricing.litellm_sync import KIND_TO_LITELLM_FAMILIES

# Settings coordinates.
ASSISTANT_MODEL_KEY = "model"  # tenant_settings (category 'assistant')
PLATFORM_DEFAULT_MODEL_KEY = "assistant.default_model"  # platform_settings

ModelSource = Literal["tenant_override", "platform_default"]


@dataclass(frozen=True)
class AssistantModelSelection:
    """A stored ``(provider_id, model_id)`` choice."""

    provider_id: UUID
    model_id: str

    def to_value(self) -> dict[str, str]:
        return {"provider_id": str(self.provider_id), "model_id": self.model_id}


@dataclass(frozen=True)
class ResolvedAssistantModel:
    """A *valid* selection plus where it came from and the provider it names."""

    provider_id: UUID
    model_id: str
    source: ModelSource
    provider_kind: str
    provider_display_name: str


def _selection_from_value(raw: Any) -> AssistantModelSelection | None:
    """Parse a stored ``{"provider_id", "model_id"}`` blob, or ``None``.

    Tolerant of a missing/garbled value (returns ``None``) so a hand-edited
    or legacy row can never raise on the hot path.
    """
    if not isinstance(raw, dict):
        return None
    provider_raw = raw.get("provider_id")
    model_id = raw.get("model_id")
    if not provider_raw or not model_id or not isinstance(model_id, str):
        return None
    try:
        provider_id = UUID(str(provider_raw))
    except (ValueError, TypeError):
        return None
    return AssistantModelSelection(provider_id=provider_id, model_id=model_id)


# ---------------------------------------------------------------------------
# Tenant override (tenant_settings: category 'assistant', key 'model')
# ---------------------------------------------------------------------------
async def get_tenant_model_override(
    session: AsyncSession, tenant_id: UUID
) -> AssistantModelSelection | None:
    """The tenant's model override, or ``None`` when unset."""
    stmt = select(TenantSetting.value).where(
        TenantSetting.tenant_id == tenant_id,
        TenantSetting.category == ASSISTANT_SETTINGS_CATEGORY,
        TenantSetting.key == ASSISTANT_MODEL_KEY,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    return _selection_from_value(row)


async def set_tenant_model_override(
    session: AsyncSession,
    tenant_id: UUID,
    selection: AssistantModelSelection,
    *,
    updated_by_user_id: UUID | None = None,
) -> None:
    """Upsert the tenant's model override. The caller owns validation."""
    value = selection.to_value()
    stmt = (
        pg_insert(TenantSetting)
        .values(
            tenant_id=tenant_id,
            category=ASSISTANT_SETTINGS_CATEGORY,
            key=ASSISTANT_MODEL_KEY,
            value=value,
            updated_by_user_id=updated_by_user_id,
        )
        .on_conflict_do_update(
            index_elements=[
                TenantSetting.tenant_id,
                TenantSetting.category,
                TenantSetting.key,
            ],
            set_={"value": value, "updated_by_user_id": updated_by_user_id},
        )
    )
    await session.execute(stmt)


async def clear_tenant_model_override(session: AsyncSession, tenant_id: UUID) -> None:
    """Remove the tenant override (the assistant then inherits the default)."""
    await session.execute(
        delete(TenantSetting).where(
            TenantSetting.tenant_id == tenant_id,
            TenantSetting.category == ASSISTANT_SETTINGS_CATEGORY,
            TenantSetting.key == ASSISTANT_MODEL_KEY,
        )
    )


# ---------------------------------------------------------------------------
# Platform default (platform_settings: key 'assistant.default_model')
# ---------------------------------------------------------------------------
async def get_platform_default_model(session: AsyncSession) -> AssistantModelSelection | None:
    """The platform-wide default model selection, or ``None`` when unset."""
    value = await get_platform_setting(session, PLATFORM_DEFAULT_MODEL_KEY, default=None)
    return _selection_from_value(value)


async def set_platform_default_model(
    session: AsyncSession,
    selection: AssistantModelSelection,
    *,
    actor: User,
) -> None:
    """Set the platform default (System Admin only — enforced downstream)."""
    await set_platform_setting(
        session, PLATFORM_DEFAULT_MODEL_KEY, selection.to_value(), actor=actor
    )


async def clear_platform_default_model(session: AsyncSession, *, actor: User) -> None:
    """Unset the platform default (System Admin only)."""
    await set_platform_setting(session, PLATFORM_DEFAULT_MODEL_KEY, None, actor=actor)


# ---------------------------------------------------------------------------
# Catalogue + validity (ADR 0021 closed catalogue)
# ---------------------------------------------------------------------------
async def list_catalog_models_for_provider(
    admin_session: AsyncSession, provider: LlmProvider
) -> list[str]:
    """Current model ids selectable for ``provider`` (sorted, de-duplicated).

    A model is selectable when an OPEN ``model_prices`` period
    (``effective_to IS NULL``) names it AND that price is associated with this
    provider — either explicitly (``provider_id``) or by the provider's
    kind→family map (``claude_sdk→anthropic``, ...). Ollama has no LiteLLM
    family, so its models come solely from explicit ``provider_id`` rows the
    System Admin catalogues.
    """
    families = KIND_TO_LITELLM_FAMILIES.get(provider.kind, frozenset())
    match_clauses = [ModelPrice.provider_id == provider.id]
    if families:
        match_clauses.append(ModelPrice.provider.in_(families))
    stmt = (
        select(ModelPrice.model_id)
        .where(ModelPrice.effective_to.is_(None), or_(*match_clauses))
        .distinct()
    )
    result = await admin_session.execute(stmt)
    return sorted({str(model_id) for model_id in result.scalars().all()})


async def is_valid_selection(
    admin_session: AsyncSession, selection: AssistantModelSelection
) -> bool:
    """True iff the provider is active AND the model is in its catalogue."""
    provider = await get_llm_provider(admin_session, selection.provider_id)
    if provider is None or not provider.is_active:
        return False
    models = await list_catalog_models_for_provider(admin_session, provider)
    return selection.model_id in models


# ---------------------------------------------------------------------------
# Resolution (inheritance: tenant override → platform default → none)
# ---------------------------------------------------------------------------
async def resolve_assistant_model(
    admin_session: AsyncSession, tenant_id: UUID
) -> ResolvedAssistantModel | None:
    """Resolve the effective model for ``tenant_id``, or ``None``.

    Returns the first VALID tier: the tenant override, then the platform
    default. A tier whose stored selection is no longer valid (provider
    disabled, model retired) is skipped. ``None`` means nothing usable is
    configured — the chat endpoint surfaces that as a 503.
    """
    for source, selection in (
        ("tenant_override", await get_tenant_model_override(admin_session, tenant_id)),
        ("platform_default", await get_platform_default_model(admin_session)),
    ):
        if selection is None:
            continue
        provider = await get_llm_provider(admin_session, selection.provider_id)
        if provider is None or not provider.is_active:
            continue
        models = await list_catalog_models_for_provider(admin_session, provider)
        if selection.model_id not in models:
            continue
        return ResolvedAssistantModel(
            provider_id=selection.provider_id,
            model_id=selection.model_id,
            source=source,  # type: ignore[arg-type]
            provider_kind=provider.kind,
            provider_display_name=provider.display_name,
        )
    return None


__all__ = [
    "ASSISTANT_MODEL_KEY",
    "PLATFORM_DEFAULT_MODEL_KEY",
    "AssistantModelSelection",
    "ResolvedAssistantModel",
    "clear_platform_default_model",
    "clear_tenant_model_override",
    "get_platform_default_model",
    "get_tenant_model_override",
    "is_valid_selection",
    "list_catalog_models_for_provider",
    "resolve_assistant_model",
    "set_platform_default_model",
    "set_tenant_model_override",
]
