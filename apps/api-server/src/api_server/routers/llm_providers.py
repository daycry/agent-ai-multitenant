"""`/admin/llm-providers` — platform-global LLM provider CRUD (Plan 11.2 task_11_2_02).

The System-Admin surface for the four ADR-0021 provider paths (claude_sdk
/ copilot / azure_foundry / ollama). ADR 0028: providers are
**platform-global, managed ONLY by ``system_admin``** — there is no
tenant scope and no RLS on ``llm_providers``; every endpoint gates on
:func:`require_system_admin` and runs on the BYPASSRLS admin session
(:func:`get_admin_session`).

Endpoints (all System-Admin):

  * ``GET    /admin/llm-providers``        list providers (newest first)
  * ``POST   /admin/llm-providers``        create a provider
  * ``GET    /admin/llm-providers/{id}``   one provider
  * ``PUT    /admin/llm-providers/{id}``   update editable fields / rotate
  * ``DELETE /admin/llm-providers/{id}``   delete provider + its Vault secret
  * ``POST   /admin/llm-providers/{id}/test``  minimal liveness probe

Secret handling (CLAUDE.md / ADR 0028 — NON-NEGOTIABLE): the credential
arrives as :class:`pydantic.SecretStr`, is written to Vault at
``platform/llm/<provider_id>``, and the DB persists ONLY the pointer
``secret_vault_path``. The credential VALUE never lands in a DB column, is
never logged, and is NEVER returned in any response (the response model
carries only non-secret fields + a ``has_credential`` boolean). ``/test``
reads the secret from Vault to do the probe and returns a classified
ok/error that, by construction, never leaks the secret.

The Vault store is an injectable dependency (:func:`get_provider_vault_store`)
so tests run with an in-memory double and prod gets a real hvac KV-v2
binding — the same optional-dep seam the MCP layer uses for its resolver.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import (
    AuthPrincipal,
    get_admin_session,
    require_system_admin,
)
from api_server.db.llm_providers import (
    PROVIDER_SYNCED_MODELS_KEY,
    LlmProvider,
    get_llm_provider,
    get_llm_provider_by_slug,
    list_llm_providers,
)
from api_server.llm_providers.factory import list_provider_models
from api_server.llm_providers.liveness import probe_provider
from api_server.llm_providers.vault import (
    HvacLLMProviderVaultStore,
    LLMProviderVaultError,
    LLMProviderVaultStore,
    provider_secret_path,
)
from api_server.schemas.llm_providers import (
    LLMProviderCreateRequest,
    LLMProviderModelsSyncResponse,
    LLMProviderResponse,
    LLMProviderTestResponse,
    LLMProviderUpdateRequest,
    to_provider_response,
)

admin_router = APIRouter(prefix="/admin/llm-providers", tags=["admin", "llm-providers"])


# ---------------------------------------------------------------------------
# Vault store dependency seam — builds an HvacLLMProviderVaultStore lazily.
#
# Mirrors routers/mcp.py's get_vault_resolver: returns None when Vault is
# not wired (no API_SERVER_VAULT_TOKEN / hvac missing), so a write that
# needs Vault fails with a clean 503 rather than a 500. Tests override this
# dependency with an InMemoryLLMProviderVaultStore.
# ---------------------------------------------------------------------------
_UNSET: object = object()


class _StoreCache:
    """Module-level singleton holding the store (class attr, not a global,
    so ruff PLW0603 stays happy and the test reset hook reads cleanly)."""

    value: LLMProviderVaultStore | None | object = _UNSET


def get_provider_vault_store() -> LLMProviderVaultStore | None:
    """Build (lazily, once) an ``HvacLLMProviderVaultStore`` from settings.

    Returns ``None`` when ``API_SERVER_VAULT_TOKEN`` is unset or ``hvac``
    is not installed — the write paths then return a 503 ("Vault not
    configured") rather than persisting a provider with no place to store
    its credential. Tests inject the in-memory double via
    ``app.dependency_overrides``.
    """
    if _StoreCache.value is not _UNSET:
        cached = _StoreCache.value
        assert cached is None or isinstance(cached, LLMProviderVaultStore)
        return cached

    from api_server.config import get_settings

    settings = get_settings()
    if settings.vault_token is None:
        _StoreCache.value = None
        return None
    try:
        import hvac
    except ImportError:
        _StoreCache.value = None
        return None

    client = hvac.Client(url=settings.vault_url, token=settings.vault_token.get_secret_value())
    store: LLMProviderVaultStore = HvacLLMProviderVaultStore(client=client)
    _StoreCache.value = store
    return store


def reset_provider_vault_store_cache() -> None:
    """Test hook: forget the cached store so the next call rebuilds it."""
    _StoreCache.value = _UNSET


def _require_store(
    store: LLMProviderVaultStore | None,
) -> LLMProviderVaultStore:
    """Return the store or 503 — a provider credential MUST go to Vault."""
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Vault is not configured (set API_SERVER_VAULT_TOKEN); a provider "
                "credential can only be stored in Vault"
            ),
        )
    return store


async def _load_provider(session: AsyncSession, provider_id: UUID) -> LlmProvider:
    """Load a provider by id, or 404. Platform-global — no tenant filter."""
    provider = await get_llm_provider(session, provider_id)
    if provider is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="llm provider not found")
    return provider


# ===========================================================================
# GET /admin/llm-providers — list
# ===========================================================================
@admin_router.get("", response_model=list[LLMProviderResponse])
async def list_providers(
    _: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> list[LLMProviderResponse]:
    """List every configured provider, newest first (System Admin only)."""
    providers = await list_llm_providers(session)
    return [to_provider_response(p, has_credential=bool(p.secret_vault_path)) for p in providers]


# ===========================================================================
# POST /admin/llm-providers — create
# ===========================================================================
@admin_router.post("", response_model=LLMProviderResponse, status_code=status.HTTP_201_CREATED)
async def create_provider(
    payload: LLMProviderCreateRequest,
    _: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
    vault: LLMProviderVaultStore | None = Depends(get_provider_vault_store),
) -> LLMProviderResponse:
    """Create a provider; write its credential to Vault (write-only).

    The credential (SecretStr per kind) is written to Vault at
    ``platform/llm/<provider_id>`` and ONLY the pointer
    ``secret_vault_path`` is persisted. The response carries no secret.
    RBAC: ``require_system_admin`` (a tenant caller is 403); BYPASSRLS
    admin session.
    """
    store = _require_store(vault)

    # The slug is the unique handle — reject a duplicate with a clean 409 before
    # relying on the uq_llm_providers_slug constraint as the DB backstop.
    if await get_llm_provider_by_slug(session, payload.slug) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"a provider with slug '{payload.slug}' already exists",
        )

    provider = LlmProvider(
        kind=payload.kind.value,
        slug=payload.slug,
        display_name=payload.display_name,
        base_url=payload.base_url,
        is_active=payload.is_active,
        config=dict(payload.config),
    )
    session.add(provider)
    # Flush to mint the id so the Vault path is keyed by the provider id.
    await session.flush()

    credential = payload.credential_fields()
    secret_path = provider_secret_path(provider.id)
    if credential:
        try:
            store.write_secret(secret_path, credential)
        except LLMProviderVaultError as exc:
            # The DB write hasn't committed yet (admin session opened a
            # transaction) — roll back so we never persist a provider whose
            # credential failed to land in Vault.
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="failed to store the provider credential in Vault",
            ) from exc
        provider.secret_vault_path = secret_path

    await session.flush()
    await session.refresh(provider)
    return to_provider_response(provider, has_credential=bool(provider.secret_vault_path))


# ===========================================================================
# GET /admin/llm-providers/{id} — one provider
# ===========================================================================
@admin_router.get("/{provider_id}", response_model=LLMProviderResponse)
async def get_provider(
    provider_id: UUID,
    _: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> LLMProviderResponse:
    """Fetch one provider by id (404 if unknown). Carries no secret."""
    provider = await _load_provider(session, provider_id)
    return to_provider_response(provider, has_credential=bool(provider.secret_vault_path))


# ===========================================================================
# PUT /admin/llm-providers/{id} — update editable fields / rotate credential
# ===========================================================================
@admin_router.put("/{provider_id}", response_model=LLMProviderResponse)
async def update_provider(
    provider_id: UUID,
    payload: LLMProviderUpdateRequest,
    _: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
    vault: LLMProviderVaultStore | None = Depends(get_provider_vault_store),
) -> LLMProviderResponse:
    """Update a provider's editable fields and/or rotate its credential.

    ``kind`` is immutable. A supplied credential field rotates the Vault
    secret (written before the response, only the pointer persisted);
    omitting all credential fields leaves the current secret untouched. An
    empty patch is a 422. The response never carries a secret.
    """
    if not payload.has_changes():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="no fields to update",
        )
    provider = await _load_provider(session, provider_id)

    fields = payload.model_dump(exclude_unset=True)
    if "slug" in fields and fields["slug"] is not None and fields["slug"] != provider.slug:
        existing = await get_llm_provider_by_slug(session, fields["slug"])
        if existing is not None and existing.id != provider.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"a provider with slug '{fields['slug']}' already exists",
            )
        provider.slug = fields["slug"]
    if "display_name" in fields and fields["display_name"] is not None:
        provider.display_name = fields["display_name"]
    if "base_url" in fields:
        provider.base_url = fields["base_url"]
    if "is_active" in fields and fields["is_active"] is not None:
        provider.is_active = fields["is_active"]
    if "config" in fields and fields["config"] is not None:
        provider.config = dict(fields["config"])

    credential = payload.credential_fields()
    if credential:
        store = _require_store(vault)
        secret_path = provider.secret_vault_path or provider_secret_path(provider.id)
        try:
            store.write_secret(secret_path, credential)
        except LLMProviderVaultError as exc:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="failed to store the provider credential in Vault",
            ) from exc
        provider.secret_vault_path = secret_path

    await session.flush()
    await session.refresh(provider)
    return to_provider_response(provider, has_credential=bool(provider.secret_vault_path))


# ===========================================================================
# DELETE /admin/llm-providers/{id} — delete provider + its Vault secret
# ===========================================================================
@admin_router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(
    provider_id: UUID,
    _: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
    vault: LLMProviderVaultStore | None = Depends(get_provider_vault_store),
) -> None:
    """Delete a provider and its Vault credential (System Admin only).

    Best-effort: the Vault secret is deleted first (idempotent — absent is
    a no-op); a Vault transport failure is a 502 so we never delete the row
    while leaving an orphan credential. Returns 204.
    """
    provider = await _load_provider(session, provider_id)
    if provider.secret_vault_path:
        store = _require_store(vault)
        try:
            store.delete_secret(provider.secret_vault_path)
        except LLMProviderVaultError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="failed to delete the provider credential from Vault",
            ) from exc
    await session.delete(provider)
    await session.flush()


# ===========================================================================
# POST /admin/llm-providers/{id}/test — minimal liveness probe
# ===========================================================================
@admin_router.post("/{provider_id}/test", response_model=LLMProviderTestResponse)
async def test_provider(
    provider_id: UUID,
    _: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
    vault: LLMProviderVaultStore | None = Depends(get_provider_vault_store),
) -> LLMProviderTestResponse:
    """Probe the provider's connection with the credential read from Vault.

    Reads the secret from Vault (never echoing it), runs a minimal live
    call per kind, and returns a classified ok/error. A secret is never in
    the response or the error detail. RBAC: ``require_system_admin``;
    BYPASSRLS admin session.
    """
    provider = await _load_provider(session, provider_id)
    store = _require_store(vault)

    secret: dict[str, str] = {}
    if provider.secret_vault_path:
        try:
            secret = store.read_secret(provider.secret_vault_path)
        except LLMProviderVaultError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="failed to read the provider credential from Vault",
            ) from exc

    result = await probe_provider(
        kind=provider.kind,
        base_url=provider.base_url,
        secret=secret,
    )
    return LLMProviderTestResponse(ok=result.ok, status=result.status.value, detail=result.detail)


# ===========================================================================
# POST /admin/llm-providers/{id}/sync-models — discover + persist model ids
# ===========================================================================
@admin_router.post("/{provider_id}/sync-models", response_model=LLMProviderModelsSyncResponse)
async def sync_provider_models(
    provider_id: UUID,
    _: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
    vault: LLMProviderVaultStore | None = Depends(get_provider_vault_store),
) -> LLMProviderModelsSyncResponse:
    """Discover the models the provider serves and persist them on the row.

    Calls the provider's ``/v1/models`` (Ollama and other OpenAI-compatible
    providers expose it) ONCE, on demand, and stores the result in the
    non-secret ``config.models`` — so the assistant model dropdown reflects
    what the provider actually serves without a network call on every open
    (ADR 0053). A discovery that finds nothing (provider has no listing API,
    or the call failed) leaves the existing list untouched and reports
    ``count = 0`` rather than wiping it. RBAC: ``require_system_admin``.
    """
    provider = await _load_provider(session, provider_id)
    models = await list_provider_models(session, provider_id=provider_id, vault=vault)
    if models:
        provider.config = {**(provider.config or {}), PROVIDER_SYNCED_MODELS_KEY: models}
        await session.flush()
    return LLMProviderModelsSyncResponse(models=models, count=len(models))


__all__ = [
    "admin_router",
    "get_provider_vault_store",
    "reset_provider_vault_store_cache",
]
