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

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.assistant.config import get_assistant_identity, set_assistant_identity
from api_server.assistant.graph import AssistantModelClient, run_assistant_turn
from api_server.assistant.tools import AssistantToolContext
from api_server.auth.deps import (
    AuthPrincipal,
    get_tenant_session,
    require_tenant_admin,
)
from api_server.db.models import Organization
from api_server.routers._helpers import require_tenant_id
from api_server.schemas.assistant import (
    AssistantChatRequest,
    AssistantChatResponse,
    AssistantIdentityResponse,
    AssistantIdentityUpdateRequest,
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
def get_assistant_model() -> AssistantModelClient:
    """Resolve the LLM-backed assistant model.

    No provider is wired in this plan (the provider selection lands with
    the broader LLM wiring), so the default raises 503 rather than
    fabricate an answer. Tests override this dependency with a
    ``ScriptedAssistantModel`` — the established chat-test pattern — so
    the integration suite never contacts a real provider.
    """
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="no LLM provider configured for the personal assistant",
    )


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


__all__ = ["get_assistant_model", "require_assistant_access", "router"]
