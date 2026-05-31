"""LLM-as-judge engine (Plan 14 task_14_04).

Given an :class:`~api_server.db.evals.EvalRun` over a dataset, for each
golden item we take (or produce) the *subject* output and JUDGE it against
each :class:`~api_server.db.evals.EvalCriterion` using a **judge LLM** that
MUST be a different model than the subject under evaluation (avoids
self-bias — Plan 14 *Decisiones Clave*: "LLM-as-judge usa un modelo distinto
al que evalúa"). Running with ``judge_model == subject_model`` is rejected
with :class:`SameModelJudgeError`.

The flow, per item:

  1. Take the subject output (pre-produced, or produced lazily via the
     injected :class:`SubjectModel` seam).
  2. For each criterion, build a judge prompt from the criterion rubric
     (``judge_instruction``) + the item input + its ``expected_output`` +
     the produced output, ask the judge LLM, and parse its structured JSON
     answer into a ``{score, passed, rationale}`` triple.
  3. Aggregate the per-criterion scores into one verdict + a weighted
     overall score (weighted by ``EvalCriterion.weight``); a single failing
     criterion drives a ``fail`` verdict.
  4. Persist an :class:`~api_server.db.evals.EvalResult` row.

After all items, roll the per-item results up onto the
:class:`~api_server.db.evals.EvalRun` (pass rate, mean latency/tokens/cost,
status → completed).

**Injectable seams (NO real provider in tests).** Both the judge and the
subject sit behind small Protocols (:class:`JudgeModel`, :class:`SubjectModel`)
so the integration test drives the engine with a SCRIPTED judge/subject
(mirroring ``ScriptedPlanningModel`` / the agent-runtime scripted client) and
never touches a real LLM. The production wiring adapts a
``shared_llm.LLMProvider`` behind the same surface.

**Multi-tenancy.** The engine is handed an already tenant-bound
``AsyncSession`` (RLS scope set by the caller). Every read (criteria, items)
and every write (results, run roll-up) therefore stays inside the caller's
tenant — a run can only ever judge / persist within its own tenant.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from api_server.db.evals import (
    EvalCriterion,
    EvalDatasetItem,
    EvalResult,
    EvalResultVerdict,
    EvalRun,
    EvalRunStatus,
)
from api_server.evals.metrics import apply_to_run, compute_run_metrics


# =============================================================================
# Errors
# =============================================================================
class SameModelJudgeError(ValueError):
    """The judge model is the same as the subject model.

    LLM-as-judge must use a DIFFERENT model than the one being evaluated
    (avoids self-bias). Raised before any judging happens so a misconfigured
    run never produces biased scores.
    """


class JudgeResponseError(ValueError):
    """The judge LLM returned something we could not parse into a score."""


# =============================================================================
# Injectable seams — judge + subject behind small Protocols
# =============================================================================
@runtime_checkable
class JudgeModel(Protocol):
    """The judge LLM seam.

    ``model`` is the judge's model identifier (validated to differ from the
    subject). ``judge(prompt)`` returns the judge's raw textual answer (a JSON
    object the engine parses) plus best-effort usage. Kept tiny so a scripted
    test double drives the engine without any provider round-trip.
    """

    model: str

    async def judge(self, prompt: str) -> JudgeCallResult: ...


@runtime_checkable
class SubjectModel(Protocol):
    """The subject-under-evaluation seam.

    ``model`` is the subject's model identifier. ``produce(item_input)``
    returns the output the subject produced for one golden item plus usage.
    A run can also be fed pre-produced outputs (see :func:`run_eval`'s
    ``produced_outputs``), in which case no subject is invoked.
    """

    model: str

    async def produce(self, item_input: dict[str, Any]) -> SubjectOutput: ...


@dataclass(frozen=True)
class JudgeCallResult:
    """One judge LLM answer — the raw text + best-effort usage."""

    text: str
    tokens: int = 0
    cost_usd: Decimal = Decimal("0")
    latency_ms: int = 0


@dataclass(frozen=True)
class SubjectOutput:
    """One subject output for a golden item + its best-effort usage."""

    output: str
    tokens: int = 0
    cost_usd: Decimal = Decimal("0")
    latency_ms: int = 0


# =============================================================================
# Parsed scoring shapes
# =============================================================================
@dataclass(frozen=True)
class CriterionScore:
    """A judge's verdict on ONE criterion for ONE item.

    ``score`` is normalised to [0, 1]; ``passed`` is ``score >=
    pass_threshold``; ``rationale`` is the judge's free-text justification.
    """

    criterion_id: UUID
    score: Decimal
    passed: bool
    rationale: str

    def to_json(self) -> dict[str, Any]:
        """The JSONB shape persisted in ``EvalResult.criterion_scores``."""
        return {
            "criterion_id": str(self.criterion_id),
            "score": float(self.score),
            "passed": self.passed,
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class JudgedItem:
    """The full outcome of judging one item against all its criteria."""

    item_id: UUID | None
    produced_output: str | None
    criterion_scores: tuple[CriterionScore, ...]
    verdict: EvalResultVerdict
    overall_score: Decimal
    latency_ms: int = 0
    tokens: int = 0
    cost_usd: Decimal = Decimal("0")


# =============================================================================
# Scripted test doubles (mirror ScriptedPlanningModel / agent-runtime scripts)
# =============================================================================
@dataclass
class ScriptedJudgeModel:
    """Replays canned judge answers — drives the engine with NO real LLM.

    ``responses`` maps a criterion name -> the judge's raw JSON answer for
    that criterion (the engine asks the judge once per criterion). A
    ``default`` answer covers any criterion not in the map. Every prompt the
    engine builds is recorded in ``prompts`` so the test can assert the
    rubric + the produced output flowed into it.
    """

    model: str
    responses: dict[str, str] = field(default_factory=dict)
    default: str = '{"score": 1.0, "rationale": "ok"}'
    prompts: list[str] = field(default_factory=list)
    tokens: int = 7
    cost_usd: Decimal = Decimal("0.0001")
    latency_ms: int = 12

    async def judge(self, prompt: str) -> JudgeCallResult:
        self.prompts.append(prompt)
        text = self.default
        # The engine tags each prompt with the criterion name; resolve the
        # canned answer by the first matching key (cheap + deterministic).
        for name, answer in self.responses.items():
            if f"Criterion: {name}\n" in prompt:
                text = answer
                break
        return JudgeCallResult(
            text=text,
            tokens=self.tokens,
            cost_usd=self.cost_usd,
            latency_ms=self.latency_ms,
        )


@dataclass
class ScriptedSubjectModel:
    """Replays canned subject outputs keyed by a field of the item input.

    ``outputs`` maps ``item_input[key]`` -> the subject output; ``default``
    covers anything else. Mirrors the scripted-model pattern used elsewhere.
    """

    model: str
    outputs: dict[str, str] = field(default_factory=dict)
    key: str = "prompt"
    default: str = ""
    tokens: int = 11
    cost_usd: Decimal = Decimal("0.0002")
    latency_ms: int = 20

    async def produce(self, item_input: dict[str, Any]) -> SubjectOutput:
        lookup = str(item_input.get(self.key, ""))
        return SubjectOutput(
            output=self.outputs.get(lookup, self.default),
            tokens=self.tokens,
            cost_usd=self.cost_usd,
            latency_ms=self.latency_ms,
        )


# =============================================================================
# Prompt building
# =============================================================================
def build_judge_prompt(
    criterion: EvalCriterion,
    *,
    item_input: dict[str, Any],
    expected_output: str | None,
    produced_output: str,
) -> str:
    """Build the judge prompt for ONE criterion against ONE produced output.

    Custom per dataset: the criterion's ``judge_instruction`` rubric is the
    heart of the prompt. The item input, the reference (``expected_output``)
    and the produced output give the judge everything it needs to score, and
    we ask for a strict JSON object so the answer is machine-parseable.

    The ``Criterion: <name>`` line is a stable marker the scripted test
    double keys on (and a human reading the prompt finds useful too).
    """
    reference = expected_output if expected_output is not None else "(no reference output)"
    return (
        "You are an impartial evaluation judge. Score the SUBMISSION against "
        "the single criterion below.\n\n"
        f"Criterion: {criterion.name}\n"
        f"Rubric: {criterion.judge_instruction}\n\n"
        f"Task input:\n{json.dumps(item_input, ensure_ascii=False, sort_keys=True)}\n\n"
        f"Reference / expected output:\n{reference}\n\n"
        f"Submission to score:\n{produced_output}\n\n"
        "Respond with ONLY a JSON object of the form "
        '{"score": <number in [0,1]>, "rationale": "<short justification>"}. '
        "Do not include any other text."
    )


# =============================================================================
# Response parsing
# =============================================================================
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_judge_response(
    raw: str,
    *,
    criterion: EvalCriterion,
) -> CriterionScore:
    """Parse a judge's raw answer into a :class:`CriterionScore`.

    Accepts a bare JSON object or one embedded in surrounding prose (we pull
    the first ``{...}`` span). ``score`` is clamped to [0, 1]; ``passed`` is
    ``score >= criterion.pass_threshold``. A missing / non-numeric score or
    no JSON at all is a :class:`JudgeResponseError`.
    """
    match = _JSON_OBJECT_RE.search(raw)
    if match is None:
        raise JudgeResponseError(f"judge returned no JSON object: {raw!r}")
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise JudgeResponseError(f"judge returned invalid JSON: {raw!r}") from exc
    if not isinstance(payload, dict) or "score" not in payload:
        raise JudgeResponseError(f"judge JSON missing 'score': {raw!r}")
    try:
        raw_score = Decimal(str(payload["score"]))
    except (ArithmeticError, ValueError) as exc:
        raise JudgeResponseError(f"judge 'score' is not numeric: {raw!r}") from exc

    score = _clamp_unit(raw_score)
    rationale = str(payload.get("rationale", "")).strip()
    passed = score >= criterion.pass_threshold
    return CriterionScore(
        criterion_id=criterion.id,
        score=score,
        passed=passed,
        rationale=rationale,
    )


# =============================================================================
# Aggregation — weighted overall score + verdict
# =============================================================================
def aggregate_scores(
    scores: Sequence[CriterionScore],
    weights: dict[UUID, Decimal],
) -> tuple[Decimal, EvalResultVerdict]:
    """Combine per-criterion scores into a weighted overall + a verdict.

    The overall score is the weight-weighted mean of the per-criterion
    scores (weights from each criterion; a zero/absent weight falls back to
    1). The verdict is ``pass`` iff EVERY criterion passed — a single failing
    criterion drives a ``fail`` (a criterion is the unit of acceptance; a
    high average must not mask one hard failure). No scores at all (a dataset
    with no criteria) is an ``error``.
    """
    if not scores:
        return Decimal("0"), EvalResultVerdict.ERROR

    total_weight = Decimal("0")
    weighted_sum = Decimal("0")
    all_passed = True
    for s in scores:
        weight = weights.get(s.criterion_id, Decimal("1"))
        if weight <= 0:
            weight = Decimal("1")
        weighted_sum += s.score * weight
        total_weight += weight
        if not s.passed:
            all_passed = False

    overall = (weighted_sum / total_weight) if total_weight > 0 else Decimal("0")
    overall = _quantize_unit(overall)
    verdict = EvalResultVerdict.PASS if all_passed else EvalResultVerdict.FAIL
    return overall, verdict


# =============================================================================
# Judge one item against all its criteria
# =============================================================================
async def judge_item(
    *,
    judge: JudgeModel,
    criteria: Sequence[EvalCriterion],
    item_id: UUID | None,
    item_input: dict[str, Any],
    expected_output: str | None,
    produced_output: str,
    subject_tokens: int = 0,
    subject_cost_usd: Decimal = Decimal("0"),
    subject_latency_ms: int = 0,
) -> JudgedItem:
    """Judge ONE produced output against every criterion, then aggregate.

    Asks the judge once per criterion (the rubric is per-criterion), parses
    each answer, and rolls them up into a verdict + weighted overall score.
    The per-item usage is the subject's usage plus the sum of the judge calls'
    usage (latency/tokens/cost) — what the run's roll-up averages over.
    """
    scores: list[CriterionScore] = []
    weights: dict[UUID, Decimal] = {}
    judge_tokens = 0
    judge_cost = Decimal("0")
    judge_latency = 0
    for criterion in criteria:
        prompt = build_judge_prompt(
            criterion,
            item_input=item_input,
            expected_output=expected_output,
            produced_output=produced_output,
        )
        answer = await judge.judge(prompt)
        judge_tokens += answer.tokens
        judge_cost += answer.cost_usd
        judge_latency += answer.latency_ms
        scores.append(parse_judge_response(answer.text, criterion=criterion))
        weights[criterion.id] = Decimal(criterion.weight)

    overall, verdict = aggregate_scores(scores, weights)
    return JudgedItem(
        item_id=item_id,
        produced_output=produced_output,
        criterion_scores=tuple(scores),
        verdict=verdict,
        overall_score=overall,
        latency_ms=subject_latency_ms + judge_latency,
        tokens=subject_tokens + judge_tokens,
        cost_usd=subject_cost_usd + judge_cost,
    )


# =============================================================================
# Run a whole eval — orchestrate + persist (tenant-scoped session)
# =============================================================================
async def run_eval(
    session: AsyncSession,
    run: EvalRun,
    *,
    judge: JudgeModel,
    subject_model: str,
    subject: SubjectModel | None = None,
    produced_outputs: dict[UUID, str] | None = None,
) -> list[EvalResult]:
    """Execute ``run`` end-to-end: judge each dataset item, persist results.

    ``subject_model`` is the model the run evaluated; it MUST differ from
    ``judge.model`` (avoid self-bias) — otherwise :class:`SameModelJudgeError`
    is raised before any judging. Subject outputs come from
    ``produced_outputs`` (item_id -> output, pre-produced) when given,
    otherwise from the injected ``subject`` seam; one of the two must be
    provided.

    The ``session`` is already tenant-bound (RLS) by the caller, so reading
    the run's criteria/items and writing the ``EvalResult`` rows + the run
    roll-up all stay inside the caller's tenant. Returns the persisted
    results (flushed, not committed — the caller owns the transaction).
    """
    if judge.model == subject_model:
        raise SameModelJudgeError(
            f"judge model {judge.model!r} must differ from the subject model "
            "(LLM-as-judge avoids self-bias)"
        )
    if subject is None and produced_outputs is None:
        raise ValueError("run_eval needs either a subject model seam or produced_outputs")

    run.judge_model = judge.model
    run.status = EvalRunStatus.RUNNING.value
    run.started_at = datetime.now(UTC)

    criteria = await _load_criteria(session, run.dataset_id)
    items = await _load_items(session, run.dataset_id)

    results: list[EvalResult] = []
    for item in items:
        item_input = dict(item.input)
        if produced_outputs is not None and item.id in produced_outputs:
            produced = produced_outputs[item.id]
            s_tokens = s_latency = 0
            s_cost = Decimal("0")
        elif subject is not None:
            s_out = await subject.produce(item_input)
            produced = s_out.output
            s_tokens, s_latency = s_out.tokens, s_out.latency_ms
            s_cost = s_out.cost_usd
        else:
            raise ValueError(f"no produced output for item {item.id!r}")

        judged = await judge_item(
            judge=judge,
            criteria=criteria,
            item_id=item.id,
            item_input=item_input,
            expected_output=item.expected_output,
            produced_output=produced,
            subject_tokens=s_tokens,
            subject_cost_usd=s_cost,
            subject_latency_ms=s_latency,
        )
        result = EvalResult(
            id=uuid7(),
            tenant_id=run.tenant_id,
            run_id=run.id,
            item_id=judged.item_id,
            produced_output=judged.produced_output,
            criterion_scores=[s.to_json() for s in judged.criterion_scores],
            verdict=judged.verdict.value,
            overall_score=judged.overall_score,
            latency_ms=judged.latency_ms,
            tokens=judged.tokens,
            cost_usd=judged.cost_usd,
        )
        session.add(result)
        results.append(result)

    _roll_up_run(run, results)
    run.status = EvalRunStatus.COMPLETED.value
    run.finished_at = datetime.now(UTC)
    await session.flush()
    return results


# =============================================================================
# Internals
# =============================================================================
async def _load_criteria(session: AsyncSession, dataset_id: UUID) -> list[EvalCriterion]:
    stmt = (
        select(EvalCriterion)
        .where(
            EvalCriterion.dataset_id == dataset_id,
            EvalCriterion.deleted_at.is_(None),
        )
        .order_by(EvalCriterion.created_at, EvalCriterion.id)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _load_items(session: AsyncSession, dataset_id: UUID) -> list[EvalDatasetItem]:
    stmt = (
        select(EvalDatasetItem)
        .where(
            EvalDatasetItem.dataset_id == dataset_id,
            EvalDatasetItem.deleted_at.is_(None),
        )
        .order_by(EvalDatasetItem.created_at, EvalDatasetItem.id)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


def _roll_up_run(run: EvalRun, results: Sequence[EvalResult]) -> None:
    """Denormalise the per-item results onto the run (the dashboards read it).

    Delegates to :mod:`api_server.evals.metrics` (task_14_05): the standard
    roll-up — pass rate, p50/p95 latency, mean latency/tokens/cost over the
    items that reported each metric — is computed once and written onto the
    run's scalar columns + ``aggregate_metrics`` JSONB. Empty results are
    well-defined (NULLs, no divide-by-zero).
    """
    apply_to_run(run, compute_run_metrics(results))


def _clamp_unit(value: Decimal) -> Decimal:
    if value < 0:
        return Decimal("0")
    if value > 1:
        return Decimal("1")
    return value


def _quantize_unit(value: Decimal) -> Decimal:
    """Quantise to 3 decimals (the Numeric(4,3) columns) clamped to [0,1]."""
    return _clamp_unit(value).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


__all__ = [
    "CriterionScore",
    "JudgeCallResult",
    "JudgeModel",
    "JudgeResponseError",
    "JudgedItem",
    "SameModelJudgeError",
    "ScriptedJudgeModel",
    "ScriptedSubjectModel",
    "SubjectModel",
    "SubjectOutput",
    "aggregate_scores",
    "build_judge_prompt",
    "judge_item",
    "parse_judge_response",
    "run_eval",
]
