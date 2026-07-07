"""Container-result parsing and folding (refactor P2).

The agent-runtime emits one JSON event per stdout line; this module parses
those lines and folds the streamed steps + the terminal event into the
`_RuntimeResult` that `finalize_execution` duck-types. Pure module — no DB,
no docker, no api_server imports — so the pre-commit mypy gate covers it.

`workers.execution` re-exports everything here (its historical home).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# A zeroed usage roll-up — used when a run produces no result line
# (the container crashed or timed out before `execution.finished`).
_EMPTY_USAGE: dict[str, Any] = {
    "iterations": 0,
    "total_tokens": 0,
    "cost_usd": 0.0,
    "tool_calls": 0,
    "model_calls": 0,
}


@dataclass
class _RuntimeResult:
    """The agent run's result, in the shape `finalize_execution`
    duck-types (`ExecutionResultLike`)."""

    status: str
    abort_code: str | None
    output: str | None
    iterations: int
    steps: list[dict[str, Any]]
    usage: dict[str, Any]
    # ADR 0087: the agent's structured finish status (success|failed|partial) or
    # None — carried from the runtime's execution.finished result.
    finish_status: str | None = None
    # Guardrail events (ADR 0102 / g1): triggered post_tool guardrails from the
    # runtime's execution.finished result; persisted tenant-scoped after finalize.
    guardrail_events: list[dict[str, Any]] = field(default_factory=list)


def _parse_line(line: str) -> dict[str, Any] | None:
    """Parse one stdout line into a JSON event, or None if it isn't one.

    The agent-runtime emits one JSON object per line; LangGraph (and the
    occasional library) may also print free text — those are ignored.
    """
    if not line.startswith("{"):
        return None
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _scan_logs_for_terminal(logs: str) -> tuple[dict[str, Any] | None, str | None]:
    """Re-parse the COMPLETE captured container logs for a terminal event the live
    stream dropped (F16/P1.1).

    The live drain pumps lines as they arrive and a torn follow read can lose the
    final `execution.finished`/`execution.error` line even though the container
    emitted it (Fase 1 guarantees ``ContainerResult.logs`` holds the full capture).
    Returns ``(finished_result, error)`` — the LAST ``execution.finished`` ``result``
    payload found (or ``None``) and the LAST ``execution.error`` message (or ``None``).
    """
    finished: dict[str, Any] | None = None
    error: str | None = None
    for line in logs.splitlines():
        event = _parse_line(line)
        if event is None or not event.get("event"):
            continue
        kind = str(event["event"])
        if kind == "execution.finished":
            result = event.get("result")
            if isinstance(result, dict):
                finished = result
        elif kind == "execution.error":
            error = event.get("error")
    return finished, error


def _assemble_result(
    final_result: dict[str, Any] | None,
    steps: list[dict[str, Any]],
    *,
    timed_out: bool,
    exit_code: int,
    runtime_error: str | None,
    logs: str | None = None,
) -> _RuntimeResult:
    """Fold the streamed steps + final result line into a `_RuntimeResult`.

    When the container produced an `execution.finished` line, that is
    the result. Otherwise the run failed (crash, timeout, or an
    `execution.error` line) — keep whatever steps streamed and mark it
    `failed`.

    F16/P1.1: before declaring a clean exit (exit 0, no timeout, no
    `execution.error`) a failure, re-parse the COMPLETE captured ``logs`` for a
    terminal line the live drain missed — the container DID emit
    `execution.finished`, the worker just lost it on the wire.
    """
    if final_result is not None:
        return _RuntimeResult(
            status=final_result.get("status", "failed"),
            abort_code=final_result.get("abort_code"),
            output=final_result.get("output"),
            iterations=int(final_result.get("iterations", 0)),
            steps=steps,
            usage=final_result.get("usage") or dict(_EMPTY_USAGE),
            finish_status=final_result.get("finish_status"),
            guardrail_events=final_result.get("guardrail_events") or [],
        )

    # F16/P1.1: a clean exit with no result on the live stream — recover from the
    # full log capture before treating it as a failure. Only for an otherwise-clean
    # exit (exit 0, no timeout, no live error); a crash/timeout keeps the hard path.
    if logs and exit_code == 0 and not timed_out and runtime_error is None:
        recovered, recovered_error = _scan_logs_for_terminal(logs)
        if recovered is not None:
            return _RuntimeResult(
                status=recovered.get("status", "failed"),
                abort_code=recovered.get("abort_code"),
                output=recovered.get("output"),
                iterations=int(recovered.get("iterations", 0)),
                steps=steps,
                usage=recovered.get("usage") or dict(_EMPTY_USAGE),
                finish_status=recovered.get("finish_status"),
                guardrail_events=recovered.get("guardrail_events") or [],
            )
        if recovered_error is not None:
            runtime_error = recovered_error

    if runtime_error is not None:
        detail = f"agent-runtime error: {runtime_error}"
    elif timed_out:
        detail = "agent-runtime container timed out"
    else:
        detail = f"agent-runtime container exited {exit_code} with no result"
    return _RuntimeResult(
        status="failed",
        abort_code=None,
        output=detail,
        iterations=0,
        steps=steps,
        usage=dict(_EMPTY_USAGE),
    )
