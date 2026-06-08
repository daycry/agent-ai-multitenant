"""`/assistant` endpoints — conversational personal assistant (Plan 10 task_10_14).

REST shape:

  POST /assistant/chat        ask the assistant a question
  GET  /assistant/identity    read the tenant-level assistant identity
  PUT  /assistant/identity    update the tenant-level assistant identity

ACCESS (binding constraints — see docs/roadmap/10-asistente-personal.md):

  * Tenant-Admin-only. ``require_tenant_admin`` already 403s a
    ``tenant_user`` / member.
  * Toggle-gated. ``Organization.personal_assistant_enabled`` DEFAULTS to
    false; when off, even a Tenant Admin is denied (403 "disabled"). The
    ``require_assistant_access`` dependency enforces both.

Cross-project read tools run through the request's RLS-bound session
(``get_tenant_session``), so a tool can never see another tenant's data
and never more than the admin's RLS scope permits.

The LLM is injected via ``get_assistant_model`` so tests override it with
a ``ScriptedAssistantModel`` — no real provider is contacted (the chat-test
pattern). The default factory raises a clear 503 until a provider is wired,
rather than fabricating answers.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.assistant.config import get_assistant_identity, set_assistant_identity
from api_server.assistant.graph import AssistantModelClient, run_assistant_turn
from api_server.assistant.llm import LLMAssistantModel
from api_server.assistant.model_config import (
    AssistantModelSelection,
    ResolvedAssistantModel,
    clear_platform_default_model,
    clear_tenant_model_override,
    get_platform_default_model,
    get_tenant_model_override,
    is_valid_selection,
    list_catalog_models_for_provider,
    resolve_assistant_model,
    set_platform_default_model,
    set_tenant_model_override,
)
from api_server.assistant.tools import AssistantToolContext
from api_server.auth.deps import (
    AuthPrincipal,
    get_admin_session,
    get_tenant_session,
    require_system_admin,
    require_tenant_admin,
)
from api_server.db.llm_providers import get_llm_provider, list_llm_providers
from api_server.db.models import Organization, User
from api_server.db.session import get_admin_sessionmaker
from api_server.llm_providers.factory import build_llm_provider
from api_server.llm_providers.vault import LLMProviderVaultStore
from api_server.routers._helpers import require_tenant_id
from api_server.routers.llm_providers import get_provider_vault_store
from api_server.schemas.assistant import (
    AssistantChatRequest,
    AssistantChatResponse,
    AssistantDefaultModelResponse,
    AssistantDefaultModelUpdateRequest,
    AssistantIdentityResponse,
    AssistantIdentityUpdateRequest,
    AssistantModelOption,
    AssistantModelOptionsResponse,
    AssistantModelResponse,
    AssistantModelUpdateRequest,
    to_identity_response,
)

router = APIRouter(prefix="/assistant", tags=["assistant"])


# ---------------------------------------------------------------------------
# Access gate: Tenant Admin AND personal_assistant_enabled
# ---------------------------------------------------------------------------
async def require_assistant_access(
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> AuthPrincipal:
    """Gate every assistant endpoint.

    ``require_tenant_admin`` already 403s a non-admin member. On top of
    that we require the per-tenant toggle to be ON: a Tenant Admin of a
    tenant with ``personal_assistant_enabled = false`` (the default) gets
    a 403 telling them the feature is disabled.

    A System Admin acting WITHOUT a tenant context (no ``tid``) has no
    tenant whose toggle to check, so we 400 — they must pick a tenant
    first (the same rule every tenant-scoped write follows).
    """
    tenant_id = require_tenant_id(principal)
    enabled = await session.scalar(
        select(Organization.personal_assistant_enabled).where(Organization.id == tenant_id)
    )
    if not enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="personal assistant is disabled for this tenant",
        )
    return principal


# ---------------------------------------------------------------------------
# Model injection seam (overridden in tests with a ScriptedAssistantModel)
# ---------------------------------------------------------------------------
async def get_assistant_model(
    principal: AuthPrincipal = Depends(require_assistant_access),
    vault: LLMProviderVaultStore | None = Depends(get_provider_vault_store),
) -> AssistantModelClient:
    """Resolve + build the LLM-backed assistant model for the tenant (ADR 0053).

    Resolves the effective ``(provider_id, model_id)`` with inheritance
    (tenant override → platform default), then builds the concrete provider.
    ``llm_providers`` is platform-global with NO RLS (ADR 0028) and the caller
    is a Tenant Admin, so we open a BYPASSRLS admin session *internally* — it
    is used only to construct the provider server-side; nothing about the
    provider config is ever returned to the tenant. A 503 is raised (rather
    than fabricating an answer) when nothing is configured or the provider's
    optional SDK / credential is unavailable.

    Tests override this dependency with a ``ScriptedAssistantModel`` (the
    established chat-test pattern), so the integration suite never contacts a
    real provider.
    """
    tenant_id = require_tenant_id(principal)
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as admin_session:
        resolved = await resolve_assistant_model(admin_session, tenant_id)
        if resolved is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="no LLM model configured for the personal assistant",
            )
        provider = await build_llm_provider(
            admin_session,
            provider_id=resolved.provider_id,
            model=resolved.model_id,
            vault=vault,
        )
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="the configured LLM provider is unavailable",
        )
    return LLMAssistantModel(provider=provider, model=resolved.model_id)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.post("/chat", response_model=AssistantChatResponse)
async def assistant_chat(
    payload: AssistantChatRequest,
    principal: AuthPrincipal = Depends(require_assistant_access),
    session: AsyncSession = Depends(get_tenant_session),
    model: AssistantModelClient = Depends(get_assistant_model),
) -> AssistantChatResponse:
    tenant_id = require_tenant_id(principal)
    identity = await get_assistant_identity(session, tenant_id)
    tool_ctx = AssistantToolContext(
        session=session,
        tenant_id=tenant_id,
        user_id=principal.user_id,
    )
    result = await run_assistant_turn(
        model,
        system_prompt=identity.system_prompt(),
        enabled_tools=identity.effective_tools(),
        tool_ctx=tool_ctx,
        chat_history=[{"role": "user", "content": payload.message}],
    )
    return AssistantChatResponse(
        answer=result.content,
        tools_called=list(result.tools_called),
        rounds=result.rounds,
    )


@router.get("/identity", response_model=AssistantIdentityResponse)
async def get_identity(
    principal: AuthPrincipal = Depends(require_assistant_access),
    session: AsyncSession = Depends(get_tenant_session),
) -> AssistantIdentityResponse:
    tenant_id = require_tenant_id(principal)
    identity = await get_assistant_identity(session, tenant_id)
    return to_identity_response(identity)


@router.put("/identity", response_model=AssistantIdentityResponse)
async def put_identity(
    payload: AssistantIdentityUpdateRequest,
    principal: AuthPrincipal = Depends(require_assistant_access),
    session: AsyncSession = Depends(get_tenant_session),
) -> AssistantIdentityResponse:
    tenant_id = require_tenant_id(principal)
    stored = await set_assistant_identity(
        session,
        tenant_id,
        payload.to_identity(),
        updated_by_user_id=principal.user_id,
    )
    return to_identity_response(stored)


# ===========================================================================
# Model selection (ADR 0053)
# ===========================================================================
def _parse_selection(provider_id: str | None, model_id: str | None) -> AssistantModelSelection:
    """Coerce a request's provider_id/model_id into a selection (422 on a
    malformed UUID). Callers pass non-None values (the schema enforces
    both-or-neither and the clear path is handled before this)."""
    try:
        parsed = UUID(str(provider_id))
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="provider_id must be a valid UUID",
        ) from exc
    return AssistantModelSelection(provider_id=parsed, model_id=str(model_id))


async def _validate_selection_or_422(
    admin_session: AsyncSession, selection: AssistantModelSelection
) -> None:
    """422 unless the selection names an ACTIVE provider and a CATALOGUED
    model (ADR 0021 closed catalogue)."""
    if not await is_valid_selection(admin_session, selection):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "invalid selection: provider_id must be an active provider and "
                "model_id must be in that provider's catalogue"
            ),
        )


def _model_response(
    resolved: ResolvedAssistantModel | None, *, has_tenant_override: bool
) -> AssistantModelResponse:
    if resolved is None:
        return AssistantModelResponse(has_tenant_override=has_tenant_override)
    return AssistantModelResponse(
        provider_id=str(resolved.provider_id),
        model_id=resolved.model_id,
        source=resolved.source,
        provider_kind=resolved.provider_kind,
        provider_display_name=resolved.provider_display_name,
        has_tenant_override=has_tenant_override,
    )


@router.get("/model", response_model=AssistantModelResponse)
async def get_model(
    principal: AuthPrincipal = Depends(require_assistant_access),
) -> AssistantModelResponse:
    """The effective model for the tenant's assistant (resolved with
    inheritance) plus whether a tenant override is set."""
    tenant_id = require_tenant_id(principal)
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as admin_session:
        resolved = await resolve_assistant_model(admin_session, tenant_id)
        override = await get_tenant_model_override(admin_session, tenant_id)
    return _model_response(resolved, has_tenant_override=override is not None)


@router.put("/model", response_model=AssistantModelResponse)
async def put_model(
    payload: AssistantModelUpdateRequest,
    principal: AuthPrincipal = Depends(require_assistant_access),
) -> AssistantModelResponse:
    """Set or clear the tenant model override.

    The whole operation runs on ONE BYPASSRLS admin transaction so the
    response reflects the write (a tenant session's uncommitted change would
    be invisible to the separate admin session the resolver needs for
    ``llm_providers``). Authorization is already enforced by
    ``require_assistant_access`` (Tenant Admin + toggle ON); the tenant_id is
    taken from the principal, never the body, and the write filters tenant_id
    explicitly.
    """
    tenant_id = require_tenant_id(principal)
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as admin_session, admin_session.begin():
        if payload.is_clear:
            await clear_tenant_model_override(admin_session, tenant_id)
        else:
            selection = _parse_selection(payload.provider_id, payload.model_id)
            await _validate_selection_or_422(admin_session, selection)
            await set_tenant_model_override(
                admin_session, tenant_id, selection, updated_by_user_id=principal.user_id
            )
        resolved = await resolve_assistant_model(admin_session, tenant_id)
        override = await get_tenant_model_override(admin_session, tenant_id)
    return _model_response(resolved, has_tenant_override=override is not None)


async def _build_model_options(admin_session: AsyncSession) -> AssistantModelOptionsResponse:
    """Active providers + their catalogued model ids (no secrets) — the shared
    dropdown source for both the tenant and the platform-default surfaces."""
    providers = await list_llm_providers(admin_session, active_only=True)
    options = [
        AssistantModelOption(
            provider_id=str(provider.id),
            kind=provider.kind,
            display_name=provider.display_name,
            models=await list_catalog_models_for_provider(admin_session, provider),
        )
        for provider in providers
    ]
    return AssistantModelOptionsResponse(providers=options)


@router.get(
    "/model/options",
    response_model=AssistantModelOptionsResponse,
    dependencies=[Depends(require_assistant_access)],
)
async def get_model_options() -> AssistantModelOptionsResponse:
    """Active providers and the model ids selectable on each — the dropdown
    source for the tenant UI. Gated to Tenant Admins (toggle ON); no secrets
    are exposed (only kind + display_name + catalogued model ids)."""
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as admin_session:
        return await _build_model_options(admin_session)


# ---------------------------------------------------------------------------
# Platform default (System-Admin surface)
# ---------------------------------------------------------------------------
@router.get("/default-model/options", response_model=AssistantModelOptionsResponse)
async def get_default_model_options(
    admin_session: AsyncSession = Depends(get_admin_session),
) -> AssistantModelOptionsResponse:
    """Provider/model dropdown source for the System-Admin platform-default
    control. Runs on the admin session (System Admin only) so it needs no
    tenant context — unlike the tenant ``/model/options``."""
    return await _build_model_options(admin_session)


@router.get("/default-model", response_model=AssistantDefaultModelResponse)
async def get_default_model(
    admin_session: AsyncSession = Depends(get_admin_session),
) -> AssistantDefaultModelResponse:
    """The platform default model (or unset). ``is_valid`` flags a stale
    default (disabled provider / retired model) so the operator can fix it."""
    default = await get_platform_default_model(admin_session)
    if default is None:
        return AssistantDefaultModelResponse()
    provider = await get_llm_provider(admin_session, default.provider_id)
    return AssistantDefaultModelResponse(
        provider_id=str(default.provider_id),
        model_id=default.model_id,
        is_valid=await is_valid_selection(admin_session, default),
        provider_display_name=(provider.display_name if provider else None),
    )


@router.put("/default-model", response_model=AssistantDefaultModelResponse)
async def put_default_model(
    payload: AssistantDefaultModelUpdateRequest,
    principal: AuthPrincipal = Depends(require_system_admin),
    admin_session: AsyncSession = Depends(get_admin_session),
) -> AssistantDefaultModelResponse:
    """Set or clear the platform default model (System Admin only)."""
    actor = await admin_session.get(User, principal.user_id)
    if actor is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="actor user not found")
    if payload.is_clear:
        await clear_platform_default_model(admin_session, actor=actor)
        return AssistantDefaultModelResponse()
    selection = _parse_selection(payload.provider_id, payload.model_id)
    await _validate_selection_or_422(admin_session, selection)
    await set_platform_default_model(admin_session, selection, actor=actor)
    provider = await get_llm_provider(admin_session, selection.provider_id)
    return AssistantDefaultModelResponse(
        provider_id=str(selection.provider_id),
        model_id=selection.model_id,
        is_valid=True,
        provider_display_name=(provider.display_name if provider else None),
    )


__all__ = ["get_assistant_model", "require_assistant_access", "router"]
