"""Replay a recorded incoming-webhook delivery from audit (Plan 13 task_13_12).

Every received webhook is RECORDED (task_13_08) with its raw payload + the
signature we verified + the action it triggered — an audit trail. When an
operator needs to debug "why did (or didn't) this delivery do what I expected?",
this module RE-RUNS the verify + parse + map + action pipeline against the STORED
payload, in the config's own tenant/project, and is ITSELF audited (a fresh
``incoming_webhook_events`` row that points at the source via
``replayed_from_event_id``).

A replay is EXPLICITLY operator-initiated, so its idempotency contract differs
from inbound redelivery: the replay audit row carries ``delivery_id = NULL`` so
it never collides with the source's partial UNIQUE on
``(config_id, delivery_id)`` — replaying twice records two replay rows (each its
own audit). The action it re-runs still rides that same transaction, so the
replay's action commits atomically with its audit row.

Multi-tenancy (CLAUDE.md principle 1): the caller resolves the source event +
config under the operator's tenant RLS scope BEFORE calling here, and this
function binds ``app.tenant_id`` to that SAME tenant on its own transaction —
so a replay can only ever re-run an action in the config's own tenant/project
(an event for project A can never act on tenant B). The signing secret is
decrypted in memory only to RE-VERIFY the stored signature and is never
returned / logged.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from api_server.db.models import IncomingWebhookConfig, IncomingWebhookEvent
from api_server.webhooks.actions import (
    ActionResult,
    MissingTargetTaskError,
    execute_action,
)
from api_server.webhooks.mapping import resolve_action
from api_server.webhooks.secrets import (
    IncomingWebhookSecretError,
    decrypt_signing_secret,
)
from api_server.webhooks.signatures import (
    IncomingWebhookOrigin,
    verify_incoming_signature,
)
from api_server.webhooks.templates import (
    WebhookTemplateError,
    parse_incoming_event,
)

_log = structlog.get_logger(__name__)


class WebhookReplayError(Exception):
    """A replay could not be carried out (decryptable-secret / verification).

    Raised so the caller can map a replay that fails its RE-VERIFICATION (e.g.
    the secret was rotated since the original delivery, so the stored signature
    no longer verifies) to a clear error rather than silently recording a
    no-op replay. Carries no secret / payload content.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ReplayOutcome:
    """The result of replaying one recorded delivery.

    ``replay_event_id`` is the NEW audit row recording the replay;
    ``source_event_id`` is the original delivery that was re-run; ``action`` is
    the action the replay re-executed (None when the stored payload maps to no
    action, e.g. no matching rule — the replay is still recorded).
    """

    replay_event_id: UUID
    source_event_id: UUID
    action: ActionResult | None


async def replay_delivery(
    session: AsyncSession,
    *,
    config: IncomingWebhookConfig,
    source_event: IncomingWebhookEvent,
) -> ReplayOutcome:
    """Re-run a recorded delivery's verify + parse + map + action, audited.

    Pre-conditions (the caller guarantees): ``config`` and ``source_event`` were
    BOTH resolved under the operator's tenant RLS scope (so they belong to the
    caller's tenant + project), and ``source_event.config_id == config.id``.

    Steps:

      1. **Re-verify** the stored signature against the config's CURRENT secret
         (decrypted in memory). A replay re-uses the stored payload, so this
         proves the recorded MAC still holds; a rotated secret makes it fail
         (:class:`WebhookReplayError`) rather than silently re-running.
      2. **Parse + map** the stored raw body (task_13_09 / task_13_10), exactly
         as the inbound path does — an unnormalisable payload maps to no action
         (the replay is still recorded).
      3. **Execute** the resolved action under ``app.tenant_id`` bound to the
         config's tenant (RLS), in the SAME transaction that inserts the replay
         audit row, so the action commits atomically with its audit.
      4. **Record** a NEW ``incoming_webhook_events`` row (``delivery_id`` NULL,
         ``replayed_from_event_id`` = the source) so the replay is itself in the
         deliveries audit trail.
    """
    if source_event.config_id != config.id:  # pragma: no cover - caller invariant
        raise WebhookReplayError("source event does not belong to the config")

    origin = IncomingWebhookOrigin(config.origin)

    # 1. Re-verify the stored signature against the config's current secret.
    try:
        secret = decrypt_signing_secret(config.signing_secret_encrypted)
    except IncomingWebhookSecretError as exc:
        raise WebhookReplayError("webhook signing secret is not decryptable") from exc

    raw_body = source_event.raw_body.encode("utf-8")
    verification = verify_incoming_signature(
        origin=origin,
        secret=secret,
        body=raw_body,
        signature_header=source_event.signature,
    )
    if not verification.ok:
        # The stored signature no longer verifies (e.g. the secret was rotated
        # since the original delivery). Surface it — never silently re-run.
        raise WebhookReplayError("stored signature does not verify (secret rotated?)")

    # 2. Parse + map the stored payload (same logic as the inbound path).
    resolved = None
    try:
        event = parse_incoming_event(
            origin=origin,
            raw_body=raw_body,
            event_type_header=source_event.event_type,
        )
    except WebhookTemplateError:
        _log.info(
            "incoming_webhook.replay_unmapped_payload",
            config_id=str(config.id),
            source_event_id=str(source_event.id),
        )
    else:
        resolved = resolve_action(event, list(config.action_mappings))

    # 3 + 4. Execute the action and record the replay audit row in ONE txn,
    #        under the config's tenant RLS scope.
    replay_event_id = uuid7()
    await session.execute(
        text("SELECT set_config('app.tenant_id', :tid, true)"),
        {"tid": str(config.tenant_id)},
    )
    session.add(
        IncomingWebhookEvent(
            id=replay_event_id,
            tenant_id=config.tenant_id,
            config_id=config.id,
            project_id=config.project_id,
            origin=origin.value,
            delivery_id=None,  # operator-initiated replay never collides
            event_type=source_event.event_type,
            signature=source_event.signature,
            raw_body=source_event.raw_body,
            verified=True,
            replayed_from_event_id=source_event.id,
        )
    )
    await session.flush()

    action_result: ActionResult | None = None
    if resolved is not None:
        try:
            action_result = await execute_action(
                session,
                action=resolved,
                tenant_id=config.tenant_id,
                project_id=config.project_id,
            )
        except MissingTargetTaskError as exc:
            # The target task is gone / in another tenant — record the replay,
            # report no action (same robustness contract as the inbound path).
            _log.warning(
                "incoming_webhook.replay_missing_target_task",
                tenant_id=str(config.tenant_id),
                target_task_id=exc.target_task_id,
            )

    return ReplayOutcome(
        replay_event_id=replay_event_id,
        source_event_id=source_event.id,
        action=action_result,
    )


__all__ = [
    "ReplayOutcome",
    "WebhookReplayError",
    "replay_delivery",
]
