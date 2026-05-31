"""Eval execution engine (Plan 14 Fase B).

The data foundation (datasets / criteria / items / runs / results) is
Fase A (``api_server.db.evals`` + the CRUD router). This package adds the
*behaviour*: the LLM-as-judge that turns a run + dataset into persisted
``EvalResult`` rows (task_14_04), and the pure metric / diff functions
over those rows (task_14_05 / task_14_06).
"""

from __future__ import annotations

from api_server.evals.ci_run import (
    EXIT_GATE_BLOCKED,
    EXIT_GATE_PASSED,
    CiRunArgs,
    DiffProvider,
    GateDecision,
    gate_decision,
    main,
    resolve_threshold,
)
from api_server.evals.constants import (
    DEFAULT_DRIFT_DROP_THRESHOLD,
    DEFAULT_DRIFT_WINDOW,
    DEFAULT_PASS_RATE_REGRESSION_THRESHOLD,
    DEFAULT_SHADOW_SAMPLE_RATE,
    DRIFT_DROP_THRESHOLD_ENV_VAR,
    DRIFT_WINDOW_ENV_VAR,
    REGRESSION_THRESHOLD_ENV_VAR,
    SHADOW_SAMPLE_RATE_ENV_VAR,
)
from api_server.evals.diff import (
    DatasetMismatchError,
    DiffVerdict,
    ItemChange,
    MetricDelta,
    RunDiff,
    diff_metrics,
    diff_runs,
)
from api_server.evals.drift import (
    DEFAULT_DRIFT_DEBOUNCE_SECONDS,
    QUALITY_DRIFT_ALERT_EVENT_TYPE,
    CeleryDriftDispatcher,
    DriftConfig,
    DriftDecision,
    DriftDispatcher,
    DriftEvaluationResult,
    detect_drift,
    evaluate_quality_drift,
    resolve_drift_config,
)
from api_server.evals.judge import (
    CriterionScore,
    JudgedItem,
    JudgeModel,
    JudgeResponseError,
    SameModelJudgeError,
    ScriptedJudgeModel,
    ScriptedSubjectModel,
    SubjectModel,
    SubjectOutput,
    build_judge_prompt,
    judge_item,
    parse_judge_response,
    run_eval,
)
from api_server.evals.metrics import (
    RunMetrics,
    apply_to_run,
    compute_run_metrics,
    mean,
    pass_rate,
    percentile,
)
from api_server.evals.shadow import (
    DeterministicSampler,
    FixedSampler,
    Sampler,
    record_shadow_eval,
    resolve_sample_rate,
    select_shadow_sample,
)

__all__ = [
    "DEFAULT_DRIFT_DEBOUNCE_SECONDS",
    "DEFAULT_DRIFT_DROP_THRESHOLD",
    "DEFAULT_DRIFT_WINDOW",
    "DEFAULT_PASS_RATE_REGRESSION_THRESHOLD",
    "DEFAULT_SHADOW_SAMPLE_RATE",
    "DRIFT_DROP_THRESHOLD_ENV_VAR",
    "DRIFT_WINDOW_ENV_VAR",
    "EXIT_GATE_BLOCKED",
    "EXIT_GATE_PASSED",
    "QUALITY_DRIFT_ALERT_EVENT_TYPE",
    "REGRESSION_THRESHOLD_ENV_VAR",
    "SHADOW_SAMPLE_RATE_ENV_VAR",
    "CeleryDriftDispatcher",
    "CiRunArgs",
    "CriterionScore",
    "DatasetMismatchError",
    "DeterministicSampler",
    "DiffProvider",
    "DiffVerdict",
    "DriftConfig",
    "DriftDecision",
    "DriftDispatcher",
    "DriftEvaluationResult",
    "FixedSampler",
    "GateDecision",
    "ItemChange",
    "JudgeModel",
    "JudgeResponseError",
    "JudgedItem",
    "MetricDelta",
    "RunDiff",
    "RunMetrics",
    "Sampler",
    "SameModelJudgeError",
    "ScriptedJudgeModel",
    "ScriptedSubjectModel",
    "SubjectModel",
    "SubjectOutput",
    "apply_to_run",
    "build_judge_prompt",
    "compute_run_metrics",
    "detect_drift",
    "diff_metrics",
    "diff_runs",
    "evaluate_quality_drift",
    "gate_decision",
    "judge_item",
    "main",
    "mean",
    "parse_judge_response",
    "pass_rate",
    "percentile",
    "record_shadow_eval",
    "resolve_drift_config",
    "resolve_sample_rate",
    "resolve_threshold",
    "run_eval",
    "select_shadow_sample",
]
