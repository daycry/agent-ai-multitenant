"""`/admin/llm/copilot/device-flow` — GitHub Copilot OAuth Device Flow (Plan 11.2 task_11_2_03).

The System-Admin surface that bootstraps a ``copilot`` provider's
credential WITHOUT the operator pasting a token: it drives GitHub's OAuth
Device Flow (ADR 0021) end to end and lands the resulting long-lived
GitHub OAuth token in Vault, exactly like the rest of the provider admin
surface (ADR 0028).

Endpoints (both System-Admin, BYPASSRLS admin session):

  * ``POST /admin/llm/copilot/device-flow/start``
      Body ``{provider_id}``. Starts the device flow for an existing
      ``copilot`` provider and returns the operator-facing codes
      (``user_code`` + ``verification_uri``) plus the ``device_code`` /
      ``interval`` / ``expires_in`` the browser needs to drive ``/poll``.
  * ``POST /admin/llm/copilot/device-flow/poll``
      Body ``{provider_id, device_code, interval?}``. Performs ONE poll
      attempt against GitHub. While the operator has not authorised it
      returns ``status=pending`` (or ``slow_down``) so the browser keeps
      polling; on ``authorized`` it stores the minted OAuth token in Vault
      at ``platform/llm/<provider_id>`` and sets the provider's
      ``secret_vault_path`` — and NEVER returns the token.

Secret handling (CLAUDE.md / ADR 0028 — NON-NEGOTIABLE): the GitHub OAuth
token the device flow yields is a credential. It is written ONLY to Vault
(via the same :class:`LLMProviderVaultStore` seam the CRUD router uses),
the DB persists ONLY the pointer ``secret_vault_path``, and the token VALUE
is never logged nor returned in any response. The reusable device-flow +
token machinery lives in ``shared_llm.providers.copilot`` — this module
orchestrates it; it does NOT re-implement the protocol.

The httpx client used to talk to GitHub is an injectable dependency
(:func:`get_device_flow_client_factory`) so tests drive the flow with a
mock transport (no network) while prod gets a real ``httpx.AsyncClient``.
"""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from shared_llm.exceptions import AuthError
from shared_llm.providers.copilot import (
    POLL_AUTHORIZED,
    POLL_DENIED,
    POLL_EXPIRED,
    POLL_PENDING,
    POLL_SLOW_DOWN,
    CopilotProvider,
)
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import (
    AuthPrincipal,
    get_admin_session,
    require_system_admin,
)
from api_server.db.llm_providers import LlmProvider, LLMProviderKind, get_llm_provider
from api_server.llm_providers.vault import (
    SECRET_FIELD_OAUTH_TOKEN,
    LLMProviderVaultError,
    LLMProviderVaultStore,
    provider_secret_path,
)
from api_server.routers.llm_providers import get_provider_vault_store
from api_server.schemas.copilot_device_flow import (
    DeviceFlowPollRequest,
    DeviceFlowPollResponse,
    DeviceFlowStartRequest,
    DeviceFlowStartResponse,
)

admin_router = APIRouter(
    prefix="/admin/llm/copilot/device-flow",
    tags=["admin", "llm-providers", "copilot"],
)

# Type of the injectable httpx client factory the CopilotProvider uses.
DeviceFlowClientFactory = Callable[[], httpx.AsyncClient]


def get_device_flow_client_factory() -> DeviceFlowClientFactory:
    """Return a factory that builds the httpx client talking to GitHub.

    Production builds a fresh ``httpx.AsyncClient`` per device-flow call.
    Tests override this dependency to return a client wired to a mock
    transport, so no real GitHub request is ever made.
    """

    def _factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=30.0)

    return _factory


def _require_store(store: LLMProviderVaultStore | None) -> LLMProviderVaultStore:
    """Return the Vault store or 503 — the OAuth token MUST land in Vault."""
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Vault is not configured (set API_SERVER_VAULT_TOKEN); the Copilot "
                "OAuth token can only be stored in Vault"
            ),
        )
    return store


async def _load_copilot_provider(session: AsyncSession, provider_id: UUID) -> LlmProvider:
    """Load a provider by id and assert it is a ``copilot`` kind, else 4xx."""
    provider = await get_llm_provider(session, provider_id)
    if provider is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="llm provider not found")
    if provider.kind != LLMProviderKind.COPILOT.value:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="device flow is only valid for a copilot provider",
        )
    return provider


# ===========================================================================
# POST /admin/llm/copilot/device-flow/start
# ===========================================================================
@admin_router.post("/start", response_model=DeviceFlowStartResponse)
async def start_device_flow(
    payload: DeviceFlowStartRequest,
    _: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
    client_factory: DeviceFlowClientFactory = Depends(get_device_flow_client_factory),
) -> DeviceFlowStartResponse:
    """Start the Copilot device flow for an existing ``copilot`` provider.

    Returns the operator-facing ``user_code`` + ``verification_uri`` and the
    ``device_code`` / ``interval`` / ``expires_in`` the browser passes back
    to ``/poll``. No credential is involved yet (the token is minted only on
    a successful poll). RBAC: ``require_system_admin``.
    """
    await _load_copilot_provider(session, payload.provider_id)

    provider = CopilotProvider(http_client=client_factory())
    try:
        info = await provider.start_device_flow()
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="failed to start the GitHub device flow",
        ) from exc
    finally:
        await provider.aclose()

    return DeviceFlowStartResponse(
        provider_id=payload.provider_id,
        device_code=info.device_code,
        user_code=info.user_code,
        verification_uri=info.verification_uri,
        expires_in=info.expires_in,
        interval=info.interval,
    )


# ===========================================================================
# POST /admin/llm/copilot/device-flow/poll
# ===========================================================================
@admin_router.post("/poll", response_model=DeviceFlowPollResponse)
async def poll_device_flow(
    payload: DeviceFlowPollRequest,
    _: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
    client_factory: DeviceFlowClientFactory = Depends(get_device_flow_client_factory),
    vault: LLMProviderVaultStore | None = Depends(get_provider_vault_store),
) -> DeviceFlowPollResponse:
    """Perform ONE device-flow poll; on authorisation store the token in Vault.

    While the operator has not authorised, returns ``status=pending`` (or
    ``slow_down`` with a backed-off ``interval``) so the browser keeps
    polling. On ``authorized`` the long-lived GitHub OAuth token is written
    to Vault at ``platform/llm/<provider_id>`` (field ``oauth_token``), the
    provider's ``secret_vault_path`` is set, and the token VALUE is NEVER
    returned. RBAC: ``require_system_admin``; BYPASSRLS admin session.
    """
    provider = await _load_copilot_provider(session, payload.provider_id)

    copilot = CopilotProvider(http_client=client_factory())
    try:
        result = await copilot.poll_device_flow_once(payload.device_code, interval=payload.interval)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="GitHub device-flow poll failed",
        ) from exc
    finally:
        await copilot.aclose()

    if result.status != POLL_AUTHORIZED:
        # Pending / slow_down / expired / denied — no token to store. Echo
        # the (possibly backed-off) interval so the UI paces its next poll.
        return DeviceFlowPollResponse(
            status=_PUBLIC_STATUS[result.status],
            authorized=False,
            interval=result.interval or payload.interval,
        )

    # Authorised: the token is the credential — straight to Vault, never out.
    token = result.token
    if not token:  # defensive: authorized implies a token
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="GitHub authorised the device flow but returned no token",
        )

    store = _require_store(vault)
    secret_path = provider.secret_vault_path or provider_secret_path(provider.id)
    try:
        store.write_secret(secret_path, {SECRET_FIELD_OAUTH_TOKEN: token})
    except LLMProviderVaultError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="failed to store the Copilot OAuth token in Vault",
        ) from exc
    provider.secret_vault_path = secret_path
    await session.flush()

    return DeviceFlowPollResponse(status=POLL_AUTHORIZED, authorized=True, interval=None)


# Map the internal poll-status strings to the public response status set.
# (They already match 1:1; the explicit map keeps the wire contract pinned
# even if the shared-llm constants are ever reworded.)
_PUBLIC_STATUS: dict[str, str] = {
    POLL_PENDING: POLL_PENDING,
    POLL_SLOW_DOWN: POLL_SLOW_DOWN,
    POLL_EXPIRED: POLL_EXPIRED,
    POLL_DENIED: POLL_DENIED,
}


__all__ = [
    "admin_router",
    "get_device_flow_client_factory",
]
