"""Quality-drift detection (Plan 14 task_14_10) — alert on a SUSTAINED decline.

A drift alert must fire only on a *sustained* quality decline, never on a
one-off dip. Over a configurable trailing window of a benchmark's run pass
rates (oldest -> newest), drift is declared when the LATEST ``window``
consecutive steps EACH drop by at least ``drop_threshold`` vs their predecessor
— i.e. the pass rate keeps sliding for ``window`` runs in a row. A single low
run flanked by recovery breaks the run and does NOT trigger; a stable or
improving stream never triggers either.

Two layers, both small and testable (the same split Fase B/C uses):

  * **Detection** — :func:`detect_drift` is a PURE function over a sequence of
    pass rates + the two tunables. It returns a :class:`DriftDecision` (drifted
    or not, with the consecutive-decline count + the total slide), so a test
    asserts the exact verdict with no DB / no RNG. ``None`` entries (runs with
    an undefined pass rate, e.g. an empty run) break a sustained run rather than
    being read as zero — an absent signal is not a decline.

  * **Evaluation + alert** — :func:`evaluate_quality_drift` loads a tenant's
    trailing COMPLETED-run pass rates for ONE dataset under the caller's
    tenant-scoped RLS session, runs the pure detector, and on drift dispatches
    ONE ``quality_drift_alert`` event through the Plan 10 notifier (reusing the
    guardrail-alert :class:`DriftDispatcher` seam) to the tenant's Tenant
    Admins. A per-``(tenant, dataset)`` :class:`~api_server.db.evals.EvalDriftState`
    row debounces a still-declining stream so it does not spam — mirroring the
    Plan 11 guardrail-alert debounce.

Every tunable is a NAMED constant in :mod:`api_server.evals.constants`
(operator-overridable via env / explicit arg), never a magic number at a call
site. ``now`` is injectable so the debounce is deterministic in tests, and the
dispatcher is a seam so tests assert the enqueue without a live broker.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Protocol
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.evals import EvalDriftState, EvalRun, EvalRunStatus
from api_server.evals.constants import (
    DEFAULT_DRIFT_DROP_THRESHOLD,
    DEFAULT_DRIFT_WINDOW,
    DRIFT_DROP_THRESHOLD_ENV_VAR,
    DRIFT_WINDOW_ENV_VAR,
)

_log = structlog.get_logger("api_server.evals.drift")

# The notification event_type the Plan 10 dispatcher maps a fired drift alert to
# (registered in the dispatcher's EVENT_REGISTRY + builtin templates). A named
# constant, not an inline literal.
QUALITY_DRIFT_ALERT_EVENT_TYPE = "quality_drift_alert"

# The debounce window: once a drift alert fires for a (tenant, dataset), no new
# alert fires until this many seconds have elapsed since ``last_alerted_at`` —
# so a stream that keeps declining run-after-run still alerts at most once per
# window (mirrors the Plan 11 guardrail-alert debounce). Operator-tunable via
# the explicit arg; a sane default, never a magic number at the call site.
DEFAULT_DRIFT_DEBOUNCE_SECONDS = 86_400  # one day


# =============================================================================
# Pure detection over a sequence of pass rates
# =============================================================================
@dataclass(frozen=True)
class DriftDecision:
    """The verdict of :func:`detect_drift` over a trailing pass-rate window.

    ``drifted`` is the single bit the evaluator acts on. ``consecutive_declines``
    is how many of the latest steps declined by at least the threshold (it
    reaches ``window`` exactly when drift is declared); ``total_decline`` is the
    summed slide across those declining steps (a positive fraction), echoed into
    the alert so the operator sees how far quality fell. ``window`` /
    ``drop_threshold`` are echoed so a reader sees the policy that produced it.
    """

    drifted: bool
    consecutive_declines: int
    total_decline: Decimal
    window: int
    drop_threshold: Decimal
    reason: str


def detect_drift(
    pass_rates: Sequence[Decimal | None],
    *,
    window: int = DEFAULT_DRIFT_WINDOW,
    drop_threshold: Decimal = DEFAULT_DRIFT_DROP_THRESHOLD,
) -> DriftDecision:
    """Decide whether ``pass_rates`` show a SUSTAINED decline — PURE, no I/O.

    ``pass_rates`` is ordered oldest -> newest. Drift is declared iff the LATEST
    ``window`` consecutive steps EACH drop by at least ``drop_threshold`` vs the
    immediately preceding value. A step that rises, holds, drops by less than
    the threshold, or touches a ``None`` (undefined pass rate) breaks the
    sustained run — so a single dip never triggers and a stable/improving stream
    never triggers.

    Needs at least ``window + 1`` values to evaluate ``window`` steps; fewer is
    "not enough signal" -> not drifted. ``window`` must be >= 1 and
    ``drop_threshold`` a fraction in ``[0, 1]`` (else :class:`ValueError`).
    """
    if window < 1:
        raise ValueError(f"drift window must be >= 1, got {window}")
    if not (Decimal("0") <= drop_threshold <= Decimal("1")):
        raise ValueError(f"drift drop_threshold must be a fraction in [0, 1], got {drop_threshold}")

    # Walk the steps newest-first, counting the run of consecutive declines that
    # each meet the threshold; stop at the first step that breaks the run.
    consecutive = 0
    total = Decimal("0")
    for newer, older in zip(reversed(pass_rates), reversed(pass_rates[:-1]), strict=False):
        if newer is None or older is None:
            break
        step_drop = older - newer  # positive when quality fell
        if step_drop >= drop_threshold and step_drop > 0:
            consecutive += 1
            total += step_drop
        else:
            break

    drifted = consecutive >= window
    if drifted:
        reason = (
            f"sustained decline: {consecutive} consecutive run(s) each dropped "
            f">= {drop_threshold} (total slide {total}) over the trailing "
            f"window of {window}"
        )
    elif len(pass_rates) <= window:
        reason = f"not enough runs ({len(pass_rates)}) to evaluate a window of {window}"
    else:
        reason = (
            f"no sustained decline: only {consecutive} consecutive declining "
            f"run(s) (< window {window})"
        )
    return DriftDecision(
        drifted=drifted,
        consecutive_declines=consecutive,
        total_decline=total,
        window=window,
        drop_threshold=drop_threshold,
        reason=reason,
    )


# =============================================================================
# Config resolution (operator-configurable; never a magic number)
# =============================================================================
@dataclass(frozen=True)
class DriftConfig:
    """The resolved drift tunables for one evaluation."""

    window: int
    drop_threshold: Decimal


def resolve_drift_config(
    *,
    window: int | None = None,
    drop_threshold: Decimal | None = None,
    env: dict[str, str] | None = None,
) -> DriftConfig:
    """Resolve drift tunables: explicit arg > env var > constant default.

    ``window`` / ``drop_threshold`` (when not ``None``) win; otherwise the
    :data:`~api_server.evals.constants.DRIFT_WINDOW_ENV_VAR` /
    :data:`~api_server.evals.constants.DRIFT_DROP_THRESHOLD_ENV_VAR` env vars are
    the next fallback, then the named constant defaults. A non-numeric /
    out-of-range value is a :class:`ValueError`.
    """
    source = env if env is not None else dict(os.environ)
    resolved_window = (
        window if window is not None else _resolve_window(source.get(DRIFT_WINDOW_ENV_VAR))
    )
    resolved_threshold = (
        drop_threshold
        if drop_threshold is not None
        else _resolve_threshold(source.get(DRIFT_DROP_THRESHOLD_ENV_VAR))
    )
    if resolved_window < 1:
        raise ValueError(f"drift window must be >= 1, got {resolved_window}")
    if not (Decimal("0") <= resolved_threshold <= Decimal("1")):
        raise ValueError(
            f"drift drop_threshold must be a fraction in [0, 1], got {resolved_threshold}"
        )
    return DriftConfig(window=resolved_window, drop_threshold=resolved_threshold)


def _resolve_window(raw: str | None) -> int:
    if raw is None or raw == "":
        return DEFAULT_DRIFT_WINDOW
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"drift window must be an integer, got {raw!r}") from exc


def _resolve_threshold(raw: str | None) -> Decimal:
    if raw is None or raw == "":
        return DEFAULT_DRIFT_DROP_THRESHOLD
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"drift drop_threshold must be numeric, got {raw!r}") from exc


# =============================================================================
# Alert dispatch seam (reuses the Plan 10 / guardrail-alert pattern)
# =============================================================================
class DriftDispatcher(Protocol):
    """The seam through which a fired drift alert reaches the Plan 10 notifier.

    Implementations enqueue a ``quality_drift_alert`` event for the tenant; the
    notification-dispatcher resolves the tenant's channels / Tenant-Admin
    preferences and sends. Tests inject a fake to assert the enqueue without a
    live broker. Returns True iff the event was accepted.
    """

    async def dispatch(self, event: dict[str, object]) -> bool: ...  # pragma: no cover - protocol


class CeleryDriftDispatcher:
    """Default dispatcher: enqueue the event onto the Plan 10 dispatcher lane.

    Goes THROUGH the Plan 10 notification system — it produces the
    ``notification_dispatcher.dispatch_event`` task by name (the api-server never
    imports the dispatcher package). The dispatcher then fans the event out to
    the tenant's Tenant Admins' subscribed channels.
    """

    async def dispatch(self, event: dict[str, object]) -> bool:
        # Imported lazily so importing this module does not pull the Celery
        # producer (and its broker config) into every consumer.
        from api_server.celery_client import enqueue_event_dispatch

        return await enqueue_event_dispatch(event)


@dataclass(frozen=True)
class DriftEvaluationResult:
    """The outcome of evaluating drift for one (tenant, dataset) once.

    ``decision`` is the pure verdict; ``alerted`` is True when an alert was
    dispatched this pass (drift detected AND not debounced); ``debounced`` is
    True when drift was detected but an alert was suppressed because one already
    fired within the debounce window. ``runs_considered`` is how many trailing
    runs were loaded (observability).
    """

    tenant_id: UUID
    dataset_id: UUID
    decision: DriftDecision
    alerted: bool
    debounced: bool
    runs_considered: int


def _build_alert_event(
    *, tenant_id: UUID, dataset_id: UUID, decision: DriftDecision
) -> dict[str, object]:
    """Build the JSON-safe ``quality_drift_alert`` event payload for the notifier.

    Carries only non-sensitive metadata (the dataset id + the decline shape) —
    never any task output or golden content.
    """
    return {
        "event_type": QUALITY_DRIFT_ALERT_EVENT_TYPE,
        "tenant_id": str(tenant_id),
        "context": {
            "dataset_id": str(dataset_id),
            "consecutive_declines": decision.consecutive_declines,
            "total_decline": float(decision.total_decline),
            "window": decision.window,
            "drop_threshold": float(decision.drop_threshold),
        },
    }


async def _load_trailing_pass_rates(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    dataset_id: UUID,
    limit: int,
) -> list[Decimal | None]:
    """Load the latest ``limit`` COMPLETED runs' pass rates for one dataset.

    Returned oldest -> newest (the order :func:`detect_drift` expects). Scoped to
    the dataset and (defence in depth on top of RLS) the tenant; only completed
    runs carry a settled quality signal. The DB returns newest-first by
    ``created_at`` (UUID v7 ids tie-break deterministically); we reverse.
    """
    stmt = (
        select(EvalRun.pass_rate)
        .where(
            EvalRun.tenant_id == tenant_id,
            EvalRun.dataset_id == dataset_id,
            EvalRun.status == EvalRunStatus.COMPLETED.value,
        )
        .order_by(EvalRun.created_at.desc(), EvalRun.id.desc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return list(reversed(rows))


async def evaluate_quality_drift(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    dataset_id: UUID,
    window: int | None = None,
    drop_threshold: Decimal | None = None,
    debounce_seconds: int = DEFAULT_DRIFT_DEBOUNCE_SECONDS,
    dispatcher: DriftDispatcher | None = None,
    now: datetime | None = None,
) -> DriftEvaluationResult:
    """Evaluate drift for one (tenant, dataset) and fire ONE alert on a decline.

    Runs on the caller's TENANT-SCOPED RLS session: the trailing pass rates, the
    drift-state lookup/update and the alert are all scoped to ``tenant_id``, so
    tenant A's decline can never alert / debounce tenant B. The flow:

      1. Resolve the tunables (explicit arg > env > named-constant default).
      2. Load the latest ``window + 1`` COMPLETED runs' pass rates for the
         dataset and run the PURE :func:`detect_drift`.
      3. If NOT drifted -> nothing fires.
      4. If drifted but a prior alert is still within the debounce window
         (``last_alerted_at`` + ``debounce_seconds`` > now) -> suppress (no spam).
      5. Otherwise dispatch ONE ``quality_drift_alert`` event through the Plan 10
         notifier to the tenant's Tenant Admins and stamp ``last_alerted_at``.

    The caller owns the transaction — the state upsert is flushed, not committed.
    Returns the per-evaluation outcome.
    """
    now = now or datetime.now(tz=UTC)
    dispatcher = dispatcher or CeleryDriftDispatcher()
    config = resolve_drift_config(window=window, drop_threshold=drop_threshold)

    # Need window+1 values to evaluate `window` steps.
    pass_rates = await _load_trailing_pass_rates(
        session, tenant_id=tenant_id, dataset_id=dataset_id, limit=config.window + 1
    )
    decision = detect_drift(pass_rates, window=config.window, drop_threshold=config.drop_threshold)

    if not decision.drifted:
        return DriftEvaluationResult(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            decision=decision,
            alerted=False,
            debounced=False,
            runs_considered=len(pass_rates),
        )

    state = await _get_or_create_state(session, tenant_id=tenant_id, dataset_id=dataset_id)
    if _is_debounced(state, now=now, debounce_seconds=debounce_seconds):
        _log.info(
            "quality_drift.debounced",
            tenant_id=str(tenant_id),
            dataset_id=str(dataset_id),
            consecutive_declines=decision.consecutive_declines,
        )
        return DriftEvaluationResult(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            decision=decision,
            alerted=False,
            debounced=True,
            runs_considered=len(pass_rates),
        )

    # Stamp the debounce anchor BEFORE awaiting the dispatch so a concurrent
    # evaluation in the same window cannot double-fire (the row is locked in
    # this transaction).
    state.last_alerted_at = now
    await session.flush()

    dispatched = await dispatcher.dispatch(
        _build_alert_event(tenant_id=tenant_id, dataset_id=dataset_id, decision=decision)
    )
    _log.info(
        "quality_drift.alerted",
        tenant_id=str(tenant_id),
        dataset_id=str(dataset_id),
        consecutive_declines=decision.consecutive_declines,
        total_decline=str(decision.total_decline),
        dispatched=dispatched,
    )
    return DriftEvaluationResult(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        decision=decision,
        alerted=True,
        debounced=False,
        runs_considered=len(pass_rates),
    )


async def _get_or_create_state(
    session: AsyncSession, *, tenant_id: UUID, dataset_id: UUID
) -> EvalDriftState:
    """Load the (tenant, dataset) drift-state row, creating it if absent."""
    existing = (
        await session.execute(
            select(EvalDriftState).where(
                EvalDriftState.tenant_id == tenant_id,
                EvalDriftState.dataset_id == dataset_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    state = EvalDriftState(tenant_id=tenant_id, dataset_id=dataset_id)
    session.add(state)
    await session.flush()
    return state


def _is_debounced(state: EvalDriftState, *, now: datetime, debounce_seconds: int) -> bool:
    """True when a drift alert already fired within the debounce window."""
    if state.last_alerted_at is None:
        return False
    last = state.last_alerted_at
    if last.tzinfo is None:  # defensive: treat a naive timestamp as UTC
        last = last.replace(tzinfo=UTC)
    return now - last < timedelta(seconds=debounce_seconds)


__all__ = [
    "DEFAULT_DRIFT_DEBOUNCE_SECONDS",
    "QUALITY_DRIFT_ALERT_EVENT_TYPE",
    "CeleryDriftDispatcher",
    "DriftConfig",
    "DriftDecision",
    "DriftDispatcher",
    "DriftEvaluationResult",
    "detect_drift",
    "evaluate_quality_drift",
    "resolve_drift_config",
]
