"""`/admin/model-prices` + `/model-prices` — the global price catalog CRUD (Plan 11 task_11_12).

The price-catalog REST surface. The catalog is **platform-global** (no
``tenant_id``) and **USD-canonical** (ADR 0028 + the plan): a price is a
property of the *provider's* pricing, identical for every tenant. That
shapes the read/write split:

  WRITES (System-Admin only, BYPASSRLS ``get_admin_session``):
    - ``POST   /admin/model-prices``               create a priced period
    - ``PATCH  /admin/model-prices/{id}``           edit mutable fields
    - ``DELETE /admin/model-prices/{id}``           close (supersede) the period
  READS (open to any authenticated caller, tenant ``get_tenant_session``):
    - ``GET /model-prices``                         list (filter + paginate)
    - ``GET /model-prices/{id}``                    one row
    - ``GET /model-prices/current``                 current price for a key

Tenancy / RBAC rationale (mirrors the marketplace global-catalog split):
the model_prices table carries a **global-read RLS policy** (migration
0049): ``SELECT USING (true)`` opens reads to every authenticated session
and the ABSENCE of any write policy denies a NOBYPASSRLS (tenant) session
every INSERT/UPDATE/DELETE, while the BYPASSRLS System-Admin session
bypasses RLS and writes freely. The endpoint RBAC layers on top: writes
go through :func:`require_system_admin` (a ``tenant_admin`` / member is a
clean 403) on the BYPASSRLS :func:`get_admin_session`; reads go through
:func:`get_principal` (merely authenticated) on :func:`get_tenant_session`,
which serves both a tenant user (RLS lets them read the global catalog)
and a System Admin (BYPASSRLS reads everything). This is "reads open to
all, writes System-Admin-only" enforced at BOTH the DB and the API.

Effective dating: a catalog key ``(provider, model_id, modality)`` has at
most one OPEN (``effective_to IS NULL``) period — its current price —
backed by the partial-unique index ``uq_model_prices_current``. Creating
a price for a key that already has an open period is a 409; the supersede
flow (DELETE) closes the open period so a fresh create can open a new
one. The catalog is never hard-deleted (effective-dated history): DELETE
is a *supersede* (set ``effective_to = now()``), keeping the historical
row for the per-call price snapshot (task_11_13).

USD-canonical: no endpoint accepts a ``currency`` — the catalog is
USD-only (the ORM default + DB CHECK pin it). The schemas never invent a
conversion; display-currency conversion (``exchange_rates`` /
``Organization.display_currency``) is out of this plan's numbered scope.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import (
    AuthPrincipal,
    get_admin_session,
    get_principal,
    get_tenant_session,
    require_system_admin,
)
from api_server.config import get_settings
from api_server.db.llm_providers import get_llm_provider
from api_server.db.model_prices import ModelPrice, PriceModality
from api_server.db.price_sync_audit import PriceSyncAudit, SyncTrigger
from api_server.pricing.litellm_sync import (
    HttpxPriceFeedFetcher,
    LargeIncreaseNotConfirmedError,
    PriceFeedError,
    active_litellm_families,
    apply_sync_from_litellm,
    compute_sync_diff,
    sync_prices_from_litellm,
)
from api_server.pricing.sync_audit import write_sync_audit
from api_server.routers._pagination import apply_pagination, limit_query, offset_query
from api_server.schemas.model_prices import (
    ModelPriceCreateRequest,
    ModelPriceResponse,
    ModelPriceUpdateRequest,
    to_price_response,
)
from api_server.schemas.price_sync import (
    PriceSyncApplyRequest,
    PriceSyncDiffRequest,
    PriceSyncDiffResponse,
    PriceSyncRequest,
    PriceSyncResponse,
    to_diff_response,
    to_sync_response,
)
from api_server.schemas.price_sync_audit import (
    PriceSyncAuditResponse,
    to_audit_response,
)

# READS — mounted at /model-prices, open to any authenticated caller.
# Runs on the tenant session: the global-read RLS policy lets a tenant
# session read the whole catalog, and a System-Admin session (BYPASSRLS)
# reads it too.
router = APIRouter(prefix="/model-prices", tags=["model-prices"])

# WRITES — mounted at /admin/model-prices, System-Admin only, BYPASSRLS
# admin session. Separate router so the gate (require_system_admin) +
# session (get_admin_session) apply to the whole write surface.
admin_router = APIRouter(prefix="/admin/model-prices", tags=["admin", "model-prices"])


async def _load_price(session: AsyncSession, price_id: UUID) -> ModelPrice:
    """Load a catalog row by id, or 404. Catalog is global — no tenant filter."""
    result = await session.execute(select(ModelPrice).where(ModelPrice.id == price_id))
    price = result.scalar_one_or_none()
    if price is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="model price not found")
    return price


async def _assert_provider_exists(session: AsyncSession, provider_id: UUID) -> None:
    """422 when ``provider_id`` does not reference an existing platform provider.

    The association FK (task_11_2_06) is validated up-front so an unknown
    provider id is a clean 422 — distinct from the 409 the duplicate-open-
    period unique index raises — rather than an ambiguous IntegrityError.
    ``llm_providers`` is platform-global (ADR 0028); this runs on the same
    BYPASSRLS admin session the write endpoints already use.
    """
    if await get_llm_provider(session, provider_id) is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="provider_id does not reference an existing llm_providers row",
        )


# ===========================================================================
# GET /model-prices/current — current price for a (provider, model_id, modality)
#
# Declared BEFORE the /{price_id} route so "current" is not parsed as a UUID.
# ===========================================================================
@router.get("/current", response_model=ModelPriceResponse)
async def get_current_price(
    provider: str = Query(min_length=1, description="Provider family, e.g. 'anthropic'."),
    model_id: str = Query(min_length=1, description="Provider model id, e.g. 'claude-sonnet-4-5'."),
    modality: PriceModality = Query(
        default=PriceModality.TEXT,
        description="Price modality (text / vision / audio / embedding / image / rerank).",
    ),
    _: AuthPrincipal = Depends(get_principal),
    session: AsyncSession = Depends(get_tenant_session),
) -> ModelPriceResponse:
    """Return the row IN EFFECT for a catalog key — the single open period.

    The current price is the row whose period is open (``effective_to IS
    NULL``) for ``(provider, model_id, modality)``; the partial-unique
    index guarantees at most one. 404 when the catalog has no open period
    for that key. Reads are open to any authenticated caller (the
    global-read RLS policy + this merely-authenticated gate).
    """
    result = await session.execute(
        select(ModelPrice).where(
            ModelPrice.provider == provider,
            ModelPrice.model_id == model_id,
            ModelPrice.modality == modality.value,
            ModelPrice.effective_to.is_(None),
        )
    )
    price = result.scalar_one_or_none()
    if price is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no current price for that provider/model/modality",
        )
    return to_price_response(price)


# ===========================================================================
# GET /model-prices — list the catalog (filter + paginate)
# ===========================================================================
@router.get("", response_model=list[ModelPriceResponse])
async def list_prices(
    provider: str | None = Query(default=None, description="Filter by provider family."),
    model_id: str | None = Query(default=None, description="Filter by provider model id."),
    modality: PriceModality | None = Query(
        default=None,
        description="Filter by modality. 422 on an unknown value.",
    ),
    provider_id: UUID | None = Query(
        default=None,
        description="Filter by associated platform provider (llm_providers.id). task_11_2_06.",
    ),
    current_only: bool = Query(
        default=False,
        description="Only the open (current) priced periods (effective_to IS NULL).",
    ),
    limit: int = limit_query(),
    offset: int = offset_query(),
    _: AuthPrincipal = Depends(get_principal),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[ModelPriceResponse]:
    """Browse the price catalog.

    Open to any authenticated caller (global-read RLS). Optional filters
    by ``provider`` / ``model_id`` / ``modality`` and ``current_only`` to
    drop closed historical periods. Deterministic ordering
    (``provider, model_id, modality, effective_from desc, id``) so
    ``offset`` paging is stable and the newest period for a key leads.
    """
    stmt = select(ModelPrice)
    if provider is not None:
        stmt = stmt.where(ModelPrice.provider == provider)
    if model_id is not None:
        stmt = stmt.where(ModelPrice.model_id == model_id)
    if modality is not None:
        stmt = stmt.where(ModelPrice.modality == modality.value)
    if provider_id is not None:
        stmt = stmt.where(ModelPrice.provider_id == provider_id)
    if current_only:
        stmt = stmt.where(ModelPrice.effective_to.is_(None))
    stmt = stmt.order_by(
        ModelPrice.provider,
        ModelPrice.model_id,
        ModelPrice.modality,
        ModelPrice.effective_from.desc(),
        ModelPrice.id,
    )
    stmt = apply_pagination(stmt, limit=limit, offset=offset)
    result = await session.execute(stmt)
    return [to_price_response(p) for p in result.scalars().all()]


# ===========================================================================
# GET /model-prices/{id} — one catalog row
# ===========================================================================
@router.get("/{price_id}", response_model=ModelPriceResponse)
async def get_price(
    price_id: UUID,
    _: AuthPrincipal = Depends(get_principal),
    session: AsyncSession = Depends(get_tenant_session),
) -> ModelPriceResponse:
    """Fetch a single catalog row by id (404 if unknown). Reads open to all."""
    price = await _load_price(session, price_id)
    return to_price_response(price)


# ===========================================================================
# POST /admin/model-prices — create a priced period (System Admin only)
# ===========================================================================
@admin_router.post("", response_model=ModelPriceResponse, status_code=status.HTTP_201_CREATED)
async def create_price(
    payload: ModelPriceCreateRequest,
    principal: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> ModelPriceResponse:
    """Create a new OPEN (current) priced period for a catalog key.

    USD-canonical (no currency on the wire). The new row opens the
    current period (``effective_to`` NULL); if the key already has an
    open period, the partial-unique index ``uq_model_prices_current``
    fires and we return a 409 — close the existing period first (DELETE /
    supersede) or this would create a second "current" price. ``source``
    defaults to ``manual``; ``updated_by`` is stamped with the acting
    System Admin. RBAC: ``require_system_admin`` (a tenant caller is 403);
    BYPASSRLS admin session.
    """
    if payload.provider_id is not None:
        await _assert_provider_exists(session, payload.provider_id)
    price = ModelPrice(
        provider=payload.provider,
        model_id=payload.model_id,
        modality=payload.modality.value,
        input_price=payload.input_price,
        output_price=payload.output_price,
        cached_input_price=payload.cached_input_price,
        unit=payload.unit.value,
        context_window=payload.context_window,
        source=payload.source.value,
        provider_id=payload.provider_id,
        updated_by=principal.user_id,
    )
    session.add(price)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "a current (open) price already exists for that "
                "provider/model/modality; supersede it first"
            ),
        ) from exc
    await session.refresh(price)
    return to_price_response(price)


# ===========================================================================
# PATCH /admin/model-prices/{id} — edit mutable fields (System Admin only)
# ===========================================================================
@admin_router.patch("/{price_id}", response_model=ModelPriceResponse)
async def update_price(
    price_id: UUID,
    payload: ModelPriceUpdateRequest,
    principal: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> ModelPriceResponse:
    """Patch the mutable fields of a catalog row (System Admin only).

    The catalog key (``provider`` / ``model_id`` / ``modality``) is
    immutable — a key change means a different catalog line, so create a
    new row instead. An empty patch is a 422. ``updated_by`` is restamped
    with the acting System Admin. RBAC: ``require_system_admin`` (a tenant
    caller is 403); BYPASSRLS admin session.
    """
    if not payload.has_changes():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="no fields to update",
        )
    price = await _load_price(session, price_id)

    # exclude_unset so an omitted field is left unchanged. The enum fields
    # (unit / source) must persist as their plain string value — the column
    # type — so coerce a StrEnum instance to its value before assigning.
    fields = payload.model_dump(exclude_unset=True)
    # Associating with a provider (a non-NULL provider_id present on the
    # wire) must reference an existing platform provider — clean 422 if not.
    # ``provider_id: null`` (present, NULL) clears the association and is fine.
    if "provider_id" in fields and fields["provider_id"] is not None:
        await _assert_provider_exists(session, fields["provider_id"])
    for name, value in fields.items():
        setattr(price, name, value.value if isinstance(value, enum.Enum) else value)
    price.updated_by = principal.user_id

    await session.flush()
    await session.refresh(price)
    return to_price_response(price)


# ===========================================================================
# DELETE /admin/model-prices/{id} — supersede (close) the period (System Admin)
# ===========================================================================
@admin_router.delete("/{price_id}", response_model=ModelPriceResponse)
async def supersede_price(
    price_id: UUID,
    principal: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> ModelPriceResponse:
    """Supersede (close) a priced period rather than hard-deleting it.

    The catalog is effective-dated for historical correctness (the
    per-call price snapshot, task_11_13, refers to a row that must
    survive): a "delete" closes the OPEN period (``effective_to = now()``)
    so the key has no current price afterwards, freeing the
    partial-unique slot for a fresh create. The row stays for history. An
    already-closed period is a 409 (nothing to supersede). Returns the
    closed row (200) so the caller sees the new ``effective_to``. RBAC:
    ``require_system_admin`` (a tenant caller is 403); BYPASSRLS session.
    """
    price = await _load_price(session, price_id)
    if price.effective_to is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="price period is already closed",
        )
    price.effective_to = datetime.now(tz=UTC)
    price.updated_by = principal.user_id
    await session.flush()
    await session.refresh(price)
    return to_price_response(price)


# ===========================================================================
# POST /admin/model-prices/sync — refresh the catalog from the LiteLLM feed
#
# ADR 0021: the LiteLLM JSON is a DATA FEED only (community reference
# pricing), NOT a provider runtime — the closed runtime catalog (Claude SDK
# + Copilot + Azure Foundry APIM + Ollama) is untouched. System-Admin only;
# the sync writes through the BYPASSRLS admin session and the platform-global
# catalog (no tenant_id), so a tenant cannot trigger it.
# ===========================================================================
@admin_router.post("/sync", response_model=PriceSyncResponse)
async def sync_prices(
    payload: PriceSyncRequest | None = None,
    principal: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> PriceSyncResponse:
    """Sync the catalog from the LiteLLM community price JSON (data feed).

    Fetches + parses the feed and upserts the catalog with effective dating
    (Fase C): a changed price CLOSES the current period and opens a new one
    (``source = litellm``); an unchanged price is a no-op. Malformed feed
    entries are skipped (typed warnings in the summary), never a crash.

    Price rises above +10% on an existing key are DEFERRED for explicit
    confirmation (``confirm_large_increases=true`` applies them — task_11_16).
    A manual override (``source = manual``) is left untouched unless
    ``overwrite_manual=true``. A feed fetch / parse failure is a 502 (the
    upstream feed faulted, not the caller).

    RBAC: ``require_system_admin`` (a tenant caller is 403); BYPASSRLS admin
    session.
    """
    req = payload or PriceSyncRequest()
    settings = get_settings()
    url = req.url or settings.litellm_price_feed_url

    # plan price-sync-active-providers (task_psa_01): only the families of the
    # ACTIVE llm_providers are synced (System-Admin override wins; 0 active ⇒
    # empty ⇒ nothing imported + every catalog family closed as out-of-scope).
    allowed_families = await active_litellm_families(session)

    async with httpx.AsyncClient() as client:
        fetcher = HttpxPriceFeedFetcher(client=client, url=url)
        try:
            summary = await sync_prices_from_litellm(
                session,
                fetcher=fetcher,
                actor_id=principal.user_id,
                confirm_large_increases=req.confirm_large_increases,
                overwrite_manual=req.overwrite_manual,
                allowed_families=allowed_families,
            )
        except (PriceFeedError, httpx.HTTPError) as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"could not fetch/parse the LiteLLM price feed: {exc}",
            ) from exc

        # task_11_19: every sync (even one that only deferred spikes) leaves an
        # immutable audit trail — who, source, counts, held spikes, compact
        # diff. Written on the SAME admin session/transaction as the catalog
        # writes, so a change can never be applied without its audit row.
        await write_sync_audit(
            session,
            summary=summary,
            trigger=SyncTrigger.MANUAL,
            actor_user_id=principal.user_id,
            feed_url=url,
            confirmed=req.confirm_large_increases,
        )

    return to_sync_response(summary)


# ===========================================================================
# POST /admin/model-prices/sync/diff — dry-run: compute the diff, NO writes
#
# task_11_16 step 1. Fetches + parses the feed and compares it to the current
# catalog, returning a per-model diff (old vs new + % change, added / updated /
# unchanged / increased / removed) WITHOUT touching the DB. The UI shows this
# diff and, when ``has_large_increase`` is true, gates the confirmation dialog.
# ===========================================================================
@admin_router.post("/sync/diff", response_model=PriceSyncDiffResponse)
async def sync_prices_diff(
    payload: PriceSyncDiffRequest | None = None,
    _: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> PriceSyncDiffResponse:
    """Dry-run the sync: return the per-model diff WITHOUT writing (task_11_16).

    Step 1 of the two-step sync flow. Fetches + parses the LiteLLM feed (data
    feed only — ADR 0021) and diffs it against the current catalog: each model
    is ``added`` / ``updated`` / ``unchanged`` / ``increased`` (a >10% rise) /
    ``removed`` (a discontinued candidate the feed dropped — flagged, not
    deleted). NO catalog row is written. ``has_large_increase`` tells the UI
    whether the subsequent apply needs explicit confirmation.

    RBAC: ``require_system_admin`` (a tenant caller is 403); BYPASSRLS session.
    A feed fetch / parse failure is a 502.
    """
    req = payload or PriceSyncDiffRequest()
    settings = get_settings()
    url = req.url or settings.litellm_price_feed_url

    # Same active-family scope as the apply (task_psa_01): the dry-run diff must
    # reflect what the apply would actually do — out-of-scope feed entries are
    # skipped (never ``added``) and out-of-scope catalog rows show as ``removed``.
    allowed_families = await active_litellm_families(session)

    async with httpx.AsyncClient() as client:
        fetcher = HttpxPriceFeedFetcher(client=client, url=url)
        try:
            diff = await compute_sync_diff(
                session, fetcher=fetcher, allowed_families=allowed_families
            )
        except (PriceFeedError, httpx.HTTPError) as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"could not fetch/parse the LiteLLM price feed: {exc}",
            ) from exc

    return to_diff_response(diff)


# ===========================================================================
# POST /admin/model-prices/sync/apply — apply, REJECT >10% rise without confirm
#
# task_11_16 step 2. Applies the feed with effective dating, but if ANY price
# rises >10% and ``confirm`` is not true, the whole apply is REJECTED (409) so
# a human reviews the spike first. With ``confirm=true`` the spikes are applied.
# ===========================================================================
@admin_router.post("/sync/apply", response_model=PriceSyncResponse)
async def sync_prices_apply(
    payload: PriceSyncApplyRequest | None = None,
    principal: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> PriceSyncResponse:
    """Apply the sync; REJECT a >10% rise unless ``confirm=true`` (task_11_16).

    Step 2 of the two-step flow. Upserts the catalog with effective dating
    (Fase C). The mandatory-confirmation gate: if ANY model's price rises more
    than +10% and ``confirm`` is false, the apply writes NOTHING and returns a
    409 listing the offending models — a human must explicitly confirm the
    spike. With ``confirm=true`` every change (including the spikes) is applied.
    A manual override (``source = manual``) is left untouched unless
    ``overwrite_manual=true``. With ``discontinue_missing=true`` (task_11_17),
    open catalog periods the feed no longer lists are flagged discontinued
    (their open period is closed — never deleted, so history + snapshots stay
    valid).

    RBAC: ``require_system_admin`` (a tenant caller is 403); BYPASSRLS session.
    A feed fetch / parse failure is a 502.
    """
    req = payload or PriceSyncApplyRequest()
    settings = get_settings()
    url = req.url or settings.litellm_price_feed_url

    # task_psa_01: only the active providers' families are applied (override
    # wins; 0 active ⇒ empty ⇒ nothing added + every catalog family closed).
    allowed_families = await active_litellm_families(session)

    async with httpx.AsyncClient() as client:
        fetcher = HttpxPriceFeedFetcher(client=client, url=url)
        try:
            summary = await apply_sync_from_litellm(
                session,
                fetcher=fetcher,
                actor_id=principal.user_id,
                confirm=req.confirm,
                overwrite_manual=req.overwrite_manual,
                discontinue_missing=req.discontinue_missing,
                allowed_families=allowed_families,
            )
            # task_11_19: audit the applied change in the SAME transaction. A
            # rejected apply (an unconfirmed >10% rise) raises below BEFORE any
            # write, so it correctly writes no catalog row AND no audit row —
            # nothing was applied. A proceeding apply always leaves a trail.
            await write_sync_audit(
                session,
                summary=summary,
                trigger=SyncTrigger.MANUAL,
                actor_user_id=principal.user_id,
                feed_url=url,
                confirmed=req.confirm,
            )
        except LargeIncreaseNotConfirmedError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": str(exc),
                    "large_increases": [
                        {
                            "provider": li.provider,
                            "model_id": li.model_id,
                            "modality": li.modality,
                            "field": li.field,
                            "old_price": str(li.old_price),
                            "new_price": str(li.new_price),
                            "pct_increase": li.pct_increase,
                        }
                        for li in exc.increases
                    ],
                },
            ) from exc
        except (PriceFeedError, httpx.HTTPError) as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"could not fetch/parse the LiteLLM price feed: {exc}",
            ) from exc

    return to_sync_response(summary)


# ===========================================================================
# GET /admin/model-prices/sync/audit — the per-sync audit history (task_11_19)
#
# Surfaces the immutable audit trail every sync writes (who / when / source /
# trigger / counts / held spikes / compact diff). Feeds the "Modelos &
# Precios" screen history. System-Admin only (the audit is a platform-level
# action record); BYPASSRLS admin session.
# ===========================================================================
@admin_router.get("/sync/audit", response_model=list[PriceSyncAuditResponse])
async def list_sync_audit(
    trigger: SyncTrigger | None = Query(
        default=None,
        description="Filter by how the sync was started: manual or scheduled.",
    ),
    limit: int = limit_query(),
    offset: int = offset_query(),
    _: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> list[PriceSyncAuditResponse]:
    """List the per-sync audit records, newest first (task_11_19).

    The append-only history of every catalog sync — manual or scheduled —
    that the admin screen renders. Optional ``trigger`` filter; ``limit`` /
    ``offset`` paging over ``created_at desc, id``. RBAC:
    ``require_system_admin`` (a tenant caller is 403); BYPASSRLS admin
    session (the table's global-read RLS would let any session read, but the
    history is a System-Admin surface so the endpoint gates it).
    """
    stmt = select(PriceSyncAudit)
    if trigger is not None:
        stmt = stmt.where(PriceSyncAudit.trigger == trigger.value)
    stmt = stmt.order_by(PriceSyncAudit.created_at.desc(), PriceSyncAudit.id.desc())
    stmt = apply_pagination(stmt, limit=limit, offset=offset)
    result = await session.execute(stmt)
    return [to_audit_response(r) for r in result.scalars().all()]
