"""Shadow evals (Plan 14 task_14_09) — sample real tasks, RECORD, never block.

A shadow eval replays a configurable random SAMPLE (5% default) of real,
COMPLETED tasks through a specialised reviewer agent / the LLM-as-judge to
RECORD a quality signal. The single binding decision (Plan 14 *Decisiones
Clave*): **shadow evals NEVER block or alter the real execution** — they only
record. Nothing in this module ever writes a ``tasks`` / ``executions`` row;
the shadow path produces its OWN :class:`~api_server.db.evals.EvalRun` (against
a ``shadow``-kind dataset, reusing the Fase B judge engine) plus one
:class:`~api_server.db.evals.EvalShadowRecord` linking the sampled real task to
that run + its verdict.

Two layers, both small and testable:

  * **Sampling** — a :class:`Sampler` seam decides, per task, whether it is in
    the sample. The production default (:class:`DeterministicSampler`) is a
    SEEDED hash sampler: deterministic for a given ``(seed, task_id)`` so a
    re-run picks the SAME set (and a test can predict it exactly). Tests inject
    their own scripted sampler so the expected set is sampled with no RNG luck.
    :func:`select_shadow_sample` is a PURE function over a list of task ids.

  * **Recording** — :func:`record_shadow_eval` runs the judge for ONE sampled
    task's replica (the judge/subject are the same injectable seams Fase B
    uses, so tests never touch a real LLM) and persists the shadow run + the
    shadow record. The ``session`` is already tenant-bound (RLS) by the caller,
    so every read/write stays inside that tenant.

The sample rate is an operator-configurable tunable
(:data:`~api_server.evals.constants.DEFAULT_SHADOW_SAMPLE_RATE`, overridable via
the :data:`~api_server.evals.constants.SHADOW_SAMPLE_RATE_ENV_VAR` env var) —
never a hard-coded magic number.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from api_server.db.evals import (
    EvalResultVerdict,
    EvalRun,
    EvalShadowRecord,
    ShadowEvalStatus,
)
from api_server.evals.constants import (
    DEFAULT_SHADOW_SAMPLE_RATE,
    SHADOW_SAMPLE_RATE_ENV_VAR,
)
from api_server.evals.judge import JudgeModel, SubjectModel, run_eval

# Resolution of the deterministic hash sampler: the 128-bit space the MD5
# digest of ``seed:task_id`` is mapped into. A task is sampled when that
# fraction is below the rate — a stable, uniform, seed-controlled decision (no
# global RNG state, so it is reproducible and free of cross-call ordering
# effects). MD5 is used purely as a fast, well-distributed hash here, NOT for
# any security purpose.
_HASH_SPACE = 1 << 32


# =============================================================================
# Sampler seam — decides, per task, whether it is in the shadow sample
# =============================================================================
@runtime_checkable
class Sampler(Protocol):
    """The shadow-sampling seam.

    ``should_sample(token, rate)`` returns whether the item identified by
    ``token`` (a task id string) is in the sample at the given ``rate`` (a
    fraction in ``[0, 1]``). Kept tiny so a test injects a scripted sampler
    (a fixed allow-set) and the expected sample is deterministic with no RNG.
    """

    def should_sample(self, token: str, rate: Decimal) -> bool: ...


@dataclass(frozen=True)
class DeterministicSampler:
    """A SEEDED, stateless hash sampler — the production default.

    For a fixed ``seed`` the decision is a pure function of the task id: the
    MD5 digest of ``f"{seed}:{token}"`` is mapped uniformly into ``[0, 1)`` and
    the task is sampled iff that value ``< rate``. Two consequences make it the
    right default: (1) it is DETERMINISTIC — a re-run with the same seed picks
    the exact same ~5% set (reproducible audits, predictable load), and (2) it
    holds no global RNG state, so the decision for one task never depends on the
    order tasks were considered in. A test can change ``seed`` to control which
    ids fall in the sample, or inject a scripted sampler entirely.
    """

    seed: int = 0

    def should_sample(self, token: str, rate: Decimal) -> bool:
        if rate <= 0:
            return False
        if rate >= 1:
            return True
        # MD5 is used purely as a fast, well-distributed hash here, NOT for any
        # security purpose (the digest only buckets the task id uniformly).
        digest = hashlib.md5(f"{self.seed}:{token}".encode()).digest()
        bucket = int.from_bytes(digest[:4], "big")
        return Decimal(bucket) / Decimal(_HASH_SPACE) < rate


@dataclass
class FixedSampler:
    """A scripted sampler that samples EXACTLY the tokens in ``allow``.

    The deterministic test double (mirrors ``ScriptedJudgeModel``): the sample
    is precisely the ``allow`` set regardless of ``rate``, so a test asserts
    the recorded set is exactly the one it chose — no RNG, no hashing luck.
    """

    allow: frozenset[str]

    def should_sample(self, token: str, rate: Decimal) -> bool:  # noqa: ARG002 - Protocol contract
        return token in self.allow


# =============================================================================
# Config resolution (operator-configurable; never a magic number)
# =============================================================================
def resolve_sample_rate(
    explicit: Decimal | None = None,
    *,
    env: dict[str, str] | None = None,
) -> Decimal:
    """Resolve the shadow sample rate: explicit arg > env var > constant default.

    ``explicit`` (when not ``None``) wins; otherwise the
    :data:`~api_server.evals.constants.SHADOW_SAMPLE_RATE_ENV_VAR` env var is the
    next fallback, then the
    :data:`~api_server.evals.constants.DEFAULT_SHADOW_SAMPLE_RATE` constant. The
    rate is a fraction in ``[0, 1]``; a non-numeric or out-of-range value is a
    :class:`ValueError`.
    """
    if explicit is not None:
        return _validate_rate(explicit)
    source = env if env is not None else dict(os.environ)
    raw = source.get(SHADOW_SAMPLE_RATE_ENV_VAR)
    if raw is None or raw == "":
        return DEFAULT_SHADOW_SAMPLE_RATE
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"shadow sample rate must be numeric, got {raw!r}") from exc
    return _validate_rate(value)


def _validate_rate(value: Decimal) -> Decimal:
    if not (Decimal("0") <= value <= Decimal("1")):
        raise ValueError(f"shadow sample rate must be a fraction in [0, 1], got {value}")
    return value


# =============================================================================
# Pure sampling over a list of task ids
# =============================================================================
def select_shadow_sample(
    task_ids: Sequence[UUID],
    *,
    rate: Decimal,
    sampler: Sampler,
) -> list[UUID]:
    """Pick the subset of ``task_ids`` to shadow-evaluate (PURE).

    Asks ``sampler`` once per task id; preserves input order so the selection
    is stable and reproducible. With an injected deterministic / scripted
    sampler the result is fully predictable (no RNG), which is exactly what the
    test asserts. Touches no session — selection is independent of persistence.
    """
    return [tid for tid in task_ids if sampler.should_sample(str(tid), rate)]


# =============================================================================
# Record ONE sampled task's shadow eval (judge the replica, persist, never block)
# =============================================================================
async def record_shadow_eval(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    dataset_id: UUID,
    source_task_id: UUID,
    source_execution_id: UUID | None,
    judge: JudgeModel,
    subject_model: str,
    sample_rate: Decimal,
    subject: SubjectModel | None = None,
    produced_outputs: dict[UUID, str] | None = None,
    subject_agent_id: UUID | None = None,
    subject_prompt_version: str | None = None,
) -> EvalShadowRecord:
    """Run + persist ONE sampled task's shadow eval — the real task is UNTOUCHED.

    Creates a ``shadow``-kind :class:`~api_server.db.evals.EvalRun` against
    ``dataset_id`` and judges its items via the Fase B engine
    (:func:`~api_server.evals.judge.run_eval`) using the injected ``judge`` +
    ``subject`` seams (NO real LLM in tests). It then persists an
    :class:`~api_server.db.evals.EvalShadowRecord` linking the sampled real task
    (``source_task_id`` / ``source_execution_id``) to that run, with the
    replica's overall verdict + the sampling rate for provenance.

    The verdict is ``pass`` only if EVERY judged item passed (an ``error`` when
    the dataset produced no items — e.g. cross-tenant, see the test). Crucially,
    this function NEVER reads or writes the ``tasks`` / ``executions`` rows: the
    shadow signal is recorded in its own tables, so the real execution can
    neither be blocked nor altered by it (binding decision).

    ``session`` is already tenant-bound (RLS) by the caller; the run, its
    results and the shadow record are all written within that tenant. Flushed,
    not committed — the caller owns the transaction.
    """
    run = EvalRun(
        id=uuid7(),
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        subject_agent_id=subject_agent_id,
        subject_prompt_version=subject_prompt_version,
    )
    session.add(run)
    await session.flush()

    results = await run_eval(
        session,
        run,
        judge=judge,
        subject_model=subject_model,
        subject=subject,
        produced_outputs=produced_outputs,
    )

    verdict = _overall_verdict(results)
    record = EvalShadowRecord(
        id=uuid7(),
        tenant_id=tenant_id,
        source_task_id=source_task_id,
        source_execution_id=source_execution_id,
        shadow_run_id=run.id,
        status=ShadowEvalStatus.JUDGED.value,
        verdict=verdict.value,
        sample_rate=sample_rate,
    )
    session.add(record)
    await session.flush()
    return record


def _overall_verdict(results: Sequence[Any]) -> EvalResultVerdict:
    """The replica's overall verdict: pass iff EVERY item passed.

    A single non-pass item makes the replica a ``fail``; no items at all (an
    empty shadow run, e.g. nothing visible under the tenant's RLS scope) is an
    ``error`` — there is no quality signal to record as pass/fail.
    """
    if not results:
        return EvalResultVerdict.ERROR
    if all(r.verdict == EvalResultVerdict.PASS.value for r in results):
        return EvalResultVerdict.PASS
    return EvalResultVerdict.FAIL


__all__ = [
    "DeterministicSampler",
    "FixedSampler",
    "Sampler",
    "record_shadow_eval",
    "resolve_sample_rate",
    "select_shadow_sample",
]
