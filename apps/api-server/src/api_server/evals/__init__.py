"""Eval execution engine (Plan 14 Fase B).

The data foundation (datasets / criteria / items / runs / results) is
Fase A (``api_server.db.evals`` + the CRUD router). This package adds the
*behaviour*: the LLM-as-judge that turns a run + dataset into persisted
``EvalResult`` rows (task_14_04), and the pure metric / diff functions
over those rows (task_14_05 / task_14_06).
"""

from __future__ import annotations

from api_server.evals.diff import (
    DatasetMismatchError,
    DiffVerdict,
    ItemChange,
    MetricDelta,
    RunDiff,
    diff_metrics,
    diff_runs,
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

__all__ = [
    "CriterionScore",
    "DatasetMismatchError",
    "DiffVerdict",
    "ItemChange",
    "JudgeModel",
    "JudgeResponseError",
    "JudgedItem",
    "MetricDelta",
    "RunDiff",
    "RunMetrics",
    "SameModelJudgeError",
    "ScriptedJudgeModel",
    "ScriptedSubjectModel",
    "SubjectModel",
    "SubjectOutput",
    "apply_to_run",
    "build_judge_prompt",
    "compute_run_metrics",
    "diff_metrics",
    "diff_runs",
    "judge_item",
    "mean",
    "parse_judge_response",
    "pass_rate",
    "percentile",
    "run_eval",
]
