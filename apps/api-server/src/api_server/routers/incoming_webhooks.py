"""PUBLIC incoming-webhook endpoint + HMAC verification (Plan 13 task_13_08).

``POST /webhooks/incoming/{origin}/{config_id}`` — the INBOUND webhook surface
(the inverse of Plan 10's OUTGOING signing). An external tool (GitHub, Jira,
Sentry, ...) POSTs an event and stamps an HMAC signature header over the raw
body with a shared secret; we re-derive that MAC with the per-PROJECT secret
and accept the event ONLY on a constant-time match.

This endpoint is PUBLIC (no ``X-API-Token`` / JWT) — the HMAC IS the
authentication — so the order of checks is the security contract:

  1. **Body cap** — reject an oversize body (413) BEFORE reading it fully into
     memory, a DDoS / memory-exhaustion guard.
  2. **Resolve config** — map ``{config_id}`` to its row on the BYPASSRLS role
     (the request is unauthenticated until the HMAC verifies). An unknown /
     disabled / soft-deleted config, or one whose ``origin`` does not match the
     URL, is a 404 — we never reveal whether a config id exists.
  3. **Rate limit** — a per-config sliding window (429 over budget), so one
     project's webhook traffic can never exhaust the endpoint for another.
  4. **Verify HMAC** — recompute ``HMAC-SHA256(secret, raw_body)`` and compare
     in constant time. A bad / missing / tampered signature is 401 and NO
     action is taken. The secret is decrypted in memory (Fernet at rest) and
     NEVER logged / echoed.
  5. **Map + act** — parse the verified payload into a normalised event
     (task_13_09), resolve the config's ``action_mappings`` to a system action
     (task_13_10: create a task, comment on a task, or escalate) and EXECUTE it
     in the SAME transaction that records the event — so the action commits
     atomically with the event and runs exactly once per delivery.
  6. **Persist** — record the verified event (raw body + headers) for replay
     (task_13_12). Idempotent: a sender's redelivery (same ``delivery_id``)
     collides on the partial UNIQUE, so neither the event NOR its action is
     re-applied — a redelivered webhook never creates a duplicate task.

Multi-tenancy (CLAUDE.md principle 1): the config carries ``tenant_id`` +
``project_id``; the resolved secret only ever validates a signature for THIS
config's own project/tenant — an event for project A can never act on tenant B.
The event row is inserted under the resolved tenant's RLS scope.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from api_server.auth.deps import get_rate_limiter
from api_server.auth.rate_limit import RateLimiter
from api_server.config import get_settings
from api_server.db.models import IncomingWebhookConfig, IncomingWebhookEvent
from api_server.db.session import get_admin_sessionmaker, get_sessionmaker
from api_server.webhooks.actions import (
    ActionResult,
    MissingTargetTaskError,
    execute_action,
)
from api_server.webhooks.mapping import (
    ResolvedAction,
    resolve_action,
)
from api_server.webhooks.secrets import (
    IncomingWebhookSecretError,
    decrypt_signing_secret,
)
from api_server.webhooks.signatures import (
    IncomingWebhookOrigin,
    signature_header_for,
    verify_incoming_signature,
)
from api_server.webhooks.templates import (
    WebhookTemplateError,
    parse_incoming_event,
)

_log = structlog.get_logger(__name__)

router = APIRouter(prefix="/webhooks/incoming", tags=["incoming-webhooks"])

# Redis namespace for the per-config sliding-window limiter. Keyed by config id
# (a UUID, not a secret) so each config has its OWN budget and one project's
# traffic never throttles another.
_RATE_LIMIT_PREFIX = "incomingwebhook:rl:"

# Sender headers we record for replay (task_13_12). Origin-agnostic best-effort:
# GitHub uses X-GitHub-Delivery / X-GitHub-Event; others differ, so a missing
# header is simply NULL.
_DELIVERY_HEADERS = ("X-GitHub-Delivery", "X-Gitlab-Event-UUID", "X-Request-Id")
_EVENT_TYPE_HEADERS = ("X-GitHub-Event", "X-Gitlab-Event", "X-Sentry-Hook-Resource")


def _rate_limit_key(config_id: UUID) -> str:
    return f"{_RATE_LIMIT_PREFIX}{config_id}"


def _first_header(request: Request, names: tuple[str, ...]) -> str | None:
    for name in names:
        value = request.headers.get(name)
        if value:
            return value
    return None


async def _resolve_config(
    *, origin: IncomingWebhookOrigin, config_id: UUID
) -> IncomingWebhookConfig:
    """Resolve a config id to its row on the BYPASSRLS role, or 404.

    The request is unauthenticated until the HMAC verifies, so there is no
    ``app.tenant_id`` to scope by yet — the lookup runs on the admin
    (BYPASSRLS) engine. A row that is missing, soft-deleted, disabled, or whose
    ``origin`` does not match the URL path segment is a 404 (we never reveal
    whether a config id exists, nor leak it to the wrong scheme).
    """
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session, session.begin():
        result = await session.execute(
            select(IncomingWebhookConfig).where(IncomingWebhookConfig.id == config_id)
        )
        config = result.scalar_one_or_none()
        if (
            config is None
            or config.deleted_at is not None
            or not config.enabled
            or config.origin != origin.value
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="webhook config not found"
            )
        # Detach a plain snapshot — the session closes on exit and the caller
        # reads attributes after. expire_on_commit is False, so the loaded
        # attributes stay valid.
        return config


@router.post("/{origin}/{config_id}", status_code=status.HTTP_202_ACCEPTED)
async def receive_incoming_webhook(
    request: Request,
    origin: IncomingWebhookOrigin = Path(...),
    config_id: UUID = Path(...),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
) -> dict[str, str]:
    """Receive, verify (HMAC) and record an incoming webhook event.

    Returns 202 with the recorded event id on success. See the module
    docstring for the (security-ordered) check sequence: body cap (413) →
    resolve config (404) → rate limit (429) → verify HMAC (401, no action) →
    persist for replay.
    """
    settings = get_settings()

    # 1. Body cap BEFORE any work. Prefer the declared Content-Length for an
    #    early reject; always re-check the ACTUAL body length (a lying or
    #    missing header can't smuggle an oversize body past us).
    max_bytes = settings.incoming_webhook_max_body_bytes
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="webhook body too large",
        )
    raw_body = await request.body()
    if len(raw_body) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="webhook body too large",
        )

    # 2. Resolve the config (BYPASSRLS) — binds the event to one project/tenant.
    config = await _resolve_config(origin=origin, config_id=config_id)

    # 3. Per-config rate limit (DDoS guard) — still BEFORE the (cheap, but not
    #    free) HMAC math, and certainly before any persistence.
    allowed, _count = await rate_limiter.check(
        _rate_limit_key(config.id),
        limit=settings.incoming_webhook_rate_limit,
        window_seconds=settings.incoming_webhook_rate_limit_window_seconds,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="incoming webhook rate limit exceeded",
            headers={"Retry-After": str(settings.incoming_webhook_rate_limit_window_seconds)},
        )

    # 4. Verify the HMAC signature BEFORE any expensive work. The secret is
    #    decrypted in memory and never logged/echoed.
    try:
        secret = decrypt_signing_secret(config.signing_secret_encrypted)
    except IncomingWebhookSecretError as exc:  # pragma: no cover - operator misconfig
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="webhook signing secret is not decryptable",
        ) from exc

    signature_value = request.headers.get(signature_header_for(origin))
    result = verify_incoming_signature(
        origin=origin,
        secret=secret,
        body=raw_body,
        signature_header=signature_value,
    )
    if not result.ok:
        # 401 on bad/missing/malformed signature — NO action taken, nothing
        # persisted. The reason is generic so we do not help an attacker probe.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid webhook signature",
        )

    # 5. Map the verified payload to a system action (pure; never touches the
    #    DB / network). A malformed or generic-origin payload that can't be
    #    normalised is recorded with NO action (best-effort: a verified-but-odd
    #    payload is not an error). A genuinely misconfigured mapping rule
    #    (InvalidMappingError) IS surfaced — see _resolve_event_action.
    event_type_header = _first_header(request, _EVENT_TYPE_HEADERS)
    resolved = _resolve_event_action(
        origin=origin,
        raw_body=raw_body,
        event_type_header=event_type_header,
        action_mappings=config.action_mappings,
        config_id=config.id,
    )

    # 6. Persist the verified event (task_13_12) AND execute the resolved action
    #    in ONE transaction, under the resolved tenant's RLS scope. The event's
    #    partial UNIQUE on (config_id, delivery_id) makes a redelivery collide,
    #    so the action only ever runs once per delivery (idempotent: no dup task).
    delivery_id = _first_header(request, _DELIVERY_HEADERS)
    event_id = uuid7()
    sessionmaker = get_sessionmaker()
    action_result = None
    try:
        async with sessionmaker() as session, session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tid, true)"),
                {"tid": str(config.tenant_id)},
            )
            session.add(
                IncomingWebhookEvent(
                    id=event_id,
                    tenant_id=config.tenant_id,
                    config_id=config.id,
                    project_id=config.project_id,
                    origin=origin.value,
                    delivery_id=delivery_id,
                    event_type=event_type_header,
                    signature=signature_value,
                    raw_body=raw_body.decode("utf-8", errors="replace"),
                    verified=True,
                )
            )
            # Flush the event FIRST so a redelivery's UNIQUE collision aborts
            # BEFORE we do any action work (and rolls the whole txn back).
            await session.flush()
            if resolved is not None:
                action_result = await _run_action(
                    session,
                    action=resolved,
                    tenant_id=config.tenant_id,
                    project_id=config.project_id,
                )
            # Best-effort observability bump on the config (same RLS scope).
            await session.execute(
                text("UPDATE incoming_webhook_configs SET last_event_at = :now WHERE id = :cid"),
                {"now": datetime.now(tz=UTC), "cid": str(config.id)},
            )
    except IntegrityError:
        # A redelivery (same delivery_id) collides on the partial UNIQUE. The
        # transaction above is rolled back by the context manager on the raised
        # error — so the action it would have run is rolled back too (no dup
        # task). Resolve the existing event id in a FRESH transaction and report
        # the redelivery as an idempotent no-op.
        existing_id = await _existing_event_id(
            tenant_id=config.tenant_id, config_id=config.id, delivery_id=delivery_id
        )
        return {"status": "duplicate", "event_id": str(existing_id)}

    response: dict[str, str] = {"status": "accepted", "event_id": str(event_id)}
    if action_result is not None:
        response["action"] = action_result.kind.value
        response["task_id"] = str(action_result.task_id)
    return response


def _resolve_event_action(
    *,
    origin: IncomingWebhookOrigin,
    raw_body: bytes,
    event_type_header: str | None,
    action_mappings: list[object],
    config_id: UUID,
) -> ResolvedAction | None:
    """Parse + map a verified payload to an action (pure; never the DB/network).

    Returns the :class:`ResolvedAction` to run, or None when there is nothing to
    do. Robustness contract (a verified payload is trusted, but may still be
    odd): a payload we cannot NORMALISE (malformed / a generic origin with no
    template) is logged and treated as "no action" — it is still RECORDED — so a
    weird-but-verified event never 500s the endpoint. Only a genuinely
    misconfigured mapping RULE (:class:`InvalidMappingError`, e.g. an unknown
    action) is re-raised, so an operator error is visible.
    """
    try:
        event = parse_incoming_event(
            origin=origin, raw_body=raw_body, event_type_header=event_type_header
        )
    except WebhookTemplateError:
        # Verified but unnormalisable (malformed body / generic origin). Record
        # the event, take no action — do not fail the inbound delivery.
        _log.info(
            "incoming_webhook.unmapped_payload", config_id=str(config_id), origin=origin.value
        )
        return None
    return resolve_action(event, list(action_mappings))


async def _run_action(
    session: AsyncSession,
    *,
    action: ResolvedAction,
    tenant_id: UUID,
    project_id: UUID,
) -> ActionResult | None:
    """Execute the resolved action on the (tenant-scoped) event transaction.

    A ``comment`` / ``escalate`` whose target task is not visible in this tenant
    (:class:`MissingTargetTaskError`) is logged and treated as a no-op: the
    event is still recorded, but no cross-tenant or dangling task is touched.
    """
    try:
        return await execute_action(
            session, action=action, tenant_id=tenant_id, project_id=project_id
        )
    except MissingTargetTaskError as exc:
        _log.warning(
            "incoming_webhook.missing_target_task",
            tenant_id=str(tenant_id),
            target_task_id=exc.target_task_id,
        )
        return None


async def _existing_event_id(*, tenant_id: UUID, config_id: UUID, delivery_id: str | None) -> UUID:
    """Look up the event id of an already-recorded delivery (idempotent path).

    Runs in its OWN tenant-scoped transaction (the insert txn that hit the
    UNIQUE is already rolled back). The (config_id, delivery_id) pair is unique
    per the partial index, so exactly one row matches.
    """
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"),
            {"tid": str(tenant_id)},
        )
        result = await session.execute(
            select(IncomingWebhookEvent.id).where(
                IncomingWebhookEvent.config_id == config_id,
                IncomingWebhookEvent.delivery_id == delivery_id,
            )
        )
        return result.scalar_one()


__all__ = ["router"]
