"""The agent loop nudges itself off a research rut (regression 2026-06-27).

A claude_sdk run burned all 25 iterations on reads/searches (list_files 11 times,
with repeated dirs, plus rag_search 9 times) and wrote NOTHING. `reflect` injects guidance
into the working context — on a repeated research call or a long research-only
streak — pushing the model to produce the deliverable.
"""

from __future__ import annotations

from typing import Any

from agent_runtime.graph import (
    _DISTINCT_READ_LIMIT,
    _PATH_CHURN_THRESHOLD,
    _REREAD_CHURN_NUDGE_LIMIT,
    _RESEARCH_HARD_LIMIT,
    _RESEARCH_STREAK_LIMIT,
    STATUS_NEEDS_HUMAN_REVIEW,
    AgentDeps,
    _abort_or_escalate_status,
    _AgentLoop,
    _path_churn_nudge,
    _read_target,
    _repetition_nudge,
    _reread_churn_nudge,
    _research_exhausted,
    _research_nudge,
)
from agent_runtime.loop_detection import LoopDetector
from agent_runtime.safeguards import Budgets, SafeguardTracker


def _exhausted(**kw: Any) -> bool:
    """Call ``_research_exhausted`` with sensible defaults so each test varies only
    the axis it cares about (new distinct-path signature has 8 kwargs)."""
    kw.setdefault("churn_streak", 0)
    kw.setdefault("distinct_reads", 0)
    kw.setdefault("distinct_limit", _DISTINCT_READ_LIMIT)
    kw.setdefault("research_streak", 0)
    kw.setdefault("hard_limit", _RESEARCH_HARD_LIMIT)
    kw.setdefault("has_produced", False)
    kw.setdefault("review_retries", 0)
    kw.setdefault("is_review", False)
    return _research_exhausted(**kw)


def test_nudge_on_repeated_research_tool() -> None:
    msg = _research_nudge(tool="list_files", research_streak=1, repeat_count=3)
    assert msg is not None and "list_files" in msg and "Do not repeat" in msg


def test_nudge_on_long_research_streak() -> None:
    msg = _research_nudge(tool="rag_search", research_streak=_RESEARCH_STREAK_LIMIT, repeat_count=1)
    assert msg is not None and "STOP researching" in msg


def test_no_nudge_for_normal_research() -> None:
    assert _research_nudge(tool="list_files", research_streak=2, repeat_count=1) is None


def test_no_nudge_for_producing_tool() -> None:
    assert _research_nudge(tool="write_file", research_streak=0, repeat_count=1) is None


# --- ADR 0095: reviewer-aware safeguards -----------------------------------


def test_review_nudge_says_emit_verdict_not_write_file() -> None:
    # A reviewer is forbidden to write_file; the streak nudge must push it to
    # FINISH with its <verdict>, not to produce a deliverable.
    msg = _research_nudge(
        tool="read_file", research_streak=_RESEARCH_STREAK_LIMIT, repeat_count=1, is_review=True
    )
    assert msg is not None
    assert "verdict" in msg.lower()
    assert "write_file" not in msg


def test_review_research_exhausted_cuts_sterile_reviewer() -> None:
    # ADR 0095 carve-out: a reviewer never "produces" and its reads are DISTINCT
    # (churn_streak stays 0), so the distinct-path split would let it leak — the
    # `is_review and research_streak >= hard_limit` trigger MUST still cut it.
    assert _exhausted(research_streak=_RESEARCH_HARD_LIMIT, churn_streak=0, is_review=True) is True
    # Non-review sterile run is still NOT cut (D3 invariant preserved).
    assert _exhausted(research_streak=_RESEARCH_HARD_LIMIT, is_review=False) is False


def test_review_safeguard_escalates_not_aborts() -> None:
    # A review run that trips a safeguard escalates to a human (so the worker can
    # converge the task), instead of a silent hard abort.
    assert _abort_or_escalate_status(False, is_review=True) == STATUS_NEEDS_HUMAN_REVIEW


# --- D4 (ADR 0089 addendum): the HARD backstop, now keyed on RE-reads ----------
# Trips (when eligible) on ANY of: a re-read churn streak, an absolute distinct-read
# ceiling, or a reviewer with a long raw research streak.
def test_research_exhausted_true_on_reread_churn_after_produced() -> None:
    # A genuine re-read loop (same targets over and over) after producing → fast cut.
    assert _exhausted(churn_streak=_RESEARCH_HARD_LIMIT, distinct_reads=2, has_produced=True)


def test_research_exhausted_true_after_failed_review_reread() -> None:
    # Re-read churn AFTER a rejected self-review: no new write, but there IS work.
    assert _exhausted(churn_streak=_RESEARCH_HARD_LIMIT, review_retries=1)


def test_research_exhausted_true_on_distinct_ceiling() -> None:
    # Verdict fix: "produced, then read a NEW path every turn" keeps churn_streak at 0
    # forever — the absolute distinct ceiling closes that evasion.
    assert _exhausted(churn_streak=0, distinct_reads=_DISTINCT_READ_LIMIT, has_produced=True)


def test_research_exhausted_false_for_distinct_exploration_after_produce() -> None:
    # THE FALSE POSITIVE WE FIX: a task that produced then legitimately reads ~15 NEW
    # files (churn_streak=0, distinct below the ceiling) must NOT be cut.
    assert not _exhausted(churn_streak=0, distinct_reads=15, has_produced=True)


def test_research_exhausted_false_for_sterile_analysis_run() -> None:
    # INVARIANT (D3): a sterile analysis-only run (no production, no failed review,
    # not a review) is NOT cut even with huge churn/distinct — bounded by max_iterations.
    assert not _exhausted(
        churn_streak=_RESEARCH_HARD_LIMIT + 5,
        distinct_reads=_DISTINCT_READ_LIMIT + 5,
        research_streak=_RESEARCH_HARD_LIMIT + 5,
    )


def test_research_exhausted_false_below_both_limits() -> None:
    assert not _exhausted(
        churn_streak=_RESEARCH_HARD_LIMIT - 1,
        distinct_reads=_DISTINCT_READ_LIMIT - 1,
        has_produced=True,
    )


# --- B1: the repetition nudge fires by tool class at the detector threshold ----
def test_repetition_nudge_fires_at_threshold_for_mutator() -> None:
    # threshold=3 → a write seen 3 times warns on the turn BEFORE the 4th aborts.
    msg = _repetition_nudge(tool="write_file", repeat_count=3, threshold=3, has_produced=True)
    assert msg is not None
    assert "write_file" in msg and "submit_result" in msg  # producer wording → FINISH


def test_repetition_nudge_not_before_threshold() -> None:
    nudge = _repetition_nudge(tool="write_file", repeat_count=2, threshold=3, has_produced=True)
    assert nudge is None


def test_repetition_nudge_readonly_wording() -> None:
    msg = _repetition_nudge(tool="read_file", repeat_count=3, threshold=3, has_produced=False)
    assert msg is not None
    assert "read_file" in msg and "result you already have" in msg
    assert "submit_result" not in msg  # read-only → reuse, NOT finish


def test_repetition_nudge_namespaced_mutator() -> None:
    # An MCP/custom writer (namespaced) still classifies as a mutator → producer wording.
    msg = _repetition_nudge(tool="fs.write_file", repeat_count=4, threshold=3, has_produced=True)
    assert msg is not None and "write_file" in msg and "submit_result" in msg


def test_repetition_nudge_none_for_no_tool() -> None:
    assert _repetition_nudge(tool=None, repeat_count=9, threshold=3, has_produced=True) is None


def _loop() -> _AgentLoop:
    # reflect() never touches deps.model, so a dummy object is fine.
    return _AgentLoop(AgentDeps(model=object()), SafeguardTracker(Budgets()), LoopDetector())  # type: ignore[arg-type]


def _state(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "last_observation": {"tool": tool, "ok": True},
        "last_decision": {"tool": tool, "tool_args": args},
        "steps": [],
    }


def test_reflect_injects_guidance_after_research_streak() -> None:
    loop = _loop()
    out: dict[str, Any] = {}
    for i in range(_RESEARCH_STREAK_LIMIT):
        out = loop.reflect(_state("rag_search", {"query": f"q{i}"}))  # vary args → not a repeat
    assert loop.research_streak == _RESEARCH_STREAK_LIMIT
    assert "context" in out and out["context"][0]["role"] == "guidance"
    assert "STOP researching" in out["context"][0]["note"]


def test_reflect_injects_guidance_on_repeat() -> None:
    loop = _loop()
    action = {"tool": "list_files", "args": {"path": "."}}
    loop.detector.record(action)  # seen twice → count_of == 2 in reflect
    loop.detector.record(action)
    out = loop.reflect(_state("list_files", {"path": "."}))
    assert "context" in out and "Do not repeat" in out["context"][0]["note"]


def test_reflect_resets_streak_on_producing_tool() -> None:
    loop = _loop()
    loop.research_streak = 4
    loop.reflect(_state("write_file", {"path": "a.py", "content": "x"}))
    assert loop.research_streak == 0


def test_reflect_sets_repetition_warning_scalar_not_context() -> None:
    # A write_file repeated to the threshold sets the SCALAR repetition_warning —
    # never `context` (which operator.add would reorder, burying it / breaking
    # context[0] ordering). Record it threshold times so reflect's count_of == 3.
    loop = _loop()
    action = {"tool": "write_file", "args": {"path": "a.py", "content": "x"}}
    for _ in range(loop.detector.threshold):
        loop.detector.record(action)
    out = loop.reflect(_state("write_file", {"path": "a.py", "content": "x"}))
    assert out.get("repetition_warning") is not None
    assert "submit_result" in out["repetition_warning"]
    assert "context" not in out  # a producing tool emits no research guidance


def test_reflect_no_repetition_warning_below_threshold() -> None:
    loop = _loop()
    action = {"tool": "write_file", "args": {"path": "a.py", "content": "x"}}
    loop.detector.record(action)  # count_of == 1 in reflect, < threshold
    out = loop.reflect(_state("write_file", {"path": "a.py", "content": "x"}))
    assert "repetition_warning" not in out


# --- the over-verification trap: once produced, the nudge says FINISH ----------
def test_finish_nudge_when_already_produced_and_streak() -> None:
    msg = _research_nudge(
        tool="list_files", research_streak=_RESEARCH_STREAK_LIMIT, repeat_count=1, has_produced=True
    )
    # C0 (ADR 0087): the nudge must NOT prescribe "NO tool call" — under the
    # structured-finish contract, FINISH on HTTP providers IS a submit_result tool
    # call. The guidance is provider-neutral: report the result and stop.
    assert msg is not None and "FINISH" in msg
    assert "NO tool call" not in msg and "no tool call" not in msg.lower()


# --- ADR 0089: same-path CHURN nudge (varying content, never byte-identical) ----
def test_path_churn_nudge_fires_at_threshold() -> None:
    msg = _path_churn_nudge(
        path="app/Mig.php", write_count=_PATH_CHURN_THRESHOLD, threshold=_PATH_CHURN_THRESHOLD
    )
    assert msg is not None
    assert "app/Mig.php" in msg and "FINISH" in msg and "submit_result" in msg


def test_path_churn_nudge_not_before_threshold() -> None:
    assert (
        _path_churn_nudge(
            path="a.php", write_count=_PATH_CHURN_THRESHOLD - 1, threshold=_PATH_CHURN_THRESHOLD
        )
        is None
    )


def test_path_churn_nudge_none_without_path() -> None:
    assert _path_churn_nudge(path=None, write_count=99, threshold=_PATH_CHURN_THRESHOLD) is None


def test_reflect_churn_nudge_on_repeated_same_path_varying_content() -> None:
    # The model re-writes the SAME path with DIFFERENT content each turn: the loop
    # detector NEVER trips (content-aware fingerprint) and the identical-args nudge
    # never fires (count_of stays 1) — but the path-churn nudge does, pushing it to
    # FINISH. This is exactly the case that burned 50 iterations re-writing a migration.
    loop = _loop()
    out: dict[str, Any] = {}
    for i in range(_PATH_CHURN_THRESHOLD):
        out = loop.reflect(
            _state("write_file", {"path": "app/Mig.php", "content": f"<?php // v{i}"})
        )
    assert loop.path_write_counts["app/Mig.php"] == _PATH_CHURN_THRESHOLD
    assert out.get("repetition_warning") is not None
    assert "app/Mig.php" in out["repetition_warning"] and "FINISH" in out["repetition_warning"]
    # The detector did NOT count these as a loop (distinct content → distinct fingerprint).
    assert (
        loop.detector.count_of(
            {"tool": "write_file", "args": {"path": "app/Mig.php", "content": "<?php // v0"}}
        )
        <= 1
    )


def test_finish_nudge_on_repeat_after_producing() -> None:
    msg = _research_nudge(tool="read_file", research_streak=1, repeat_count=3, has_produced=True)
    assert msg is not None and "FINISH" in msg


def test_reflect_latches_has_produced_and_nudges_to_finish() -> None:
    loop = _loop()
    # Produce once → latches has_produced (and resets the streak).
    loop.reflect(_state("write_file", {"path": "a.php", "content": "x"}))
    assert loop.has_produced is True and loop.research_streak == 0
    # Then it slips back into verifying; after the streak the nudge pushes FINISH.
    out: dict[str, Any] = {}
    for i in range(_RESEARCH_STREAK_LIMIT):
        out = loop.reflect(_state("list_files", {"path": f"dir{i}"}))
    assert "context" in out and "FINISH" in out["context"][0]["note"]


# --- distinct-path exploration vs re-read churn (2026-07-01 hardening) ----------
def test_read_target_ignores_offset_and_limit() -> None:
    # Paging the SAME file must be the same target — else offset-varying re-reads
    # masquerade as exploration and evade the churn streak.
    a = _read_target("read_file", {"path": "x.php", "offset": 0})
    b = _read_target("read_file", {"path": "x.php", "offset": 200, "limit": 50})
    assert a == b == "read_file:x.php"


def test_read_target_shapes_and_none() -> None:
    assert _read_target("list_files", {}) == "list_files:."  # default path
    assert _read_target("fs.read_file", {"path": "y"}) == "read_file:y"  # namespace-stripped
    assert _read_target("rag_search", {"query": " q "}) == "rag_search:q"
    assert _read_target("memory_recall", {"query": ""}) is None  # empty query → untargetable
    assert _read_target("write_file", {"path": "y"}) is None  # not a research tool


def test_reread_churn_nudge_below_limit_is_none() -> None:
    assert (
        _reread_churn_nudge(
            churn_streak=_REREAD_CHURN_NUDGE_LIMIT - 1,
            limit=_REREAD_CHURN_NUDGE_LIMIT,
            has_produced=False,
        )
        is None
    )


def test_reread_churn_nudge_sterile_offers_finish_by_conclusion() -> None:
    msg = _reread_churn_nudge(
        churn_streak=_REREAD_CHURN_NUDGE_LIMIT, limit=_REREAD_CHURN_NUDGE_LIMIT, has_produced=False
    )
    assert msg is not None and "FINISH" in msg and "conclusion" in msg.lower()


def test_reread_churn_nudge_review_says_verdict() -> None:
    msg = _reread_churn_nudge(
        churn_streak=_REREAD_CHURN_NUDGE_LIMIT,
        limit=_REREAD_CHURN_NUDGE_LIMIT,
        has_produced=False,
        is_review=True,
    )
    assert msg is not None and "verdict" in msg.lower() and "write_file" not in msg


def test_reflect_distinct_reads_do_not_build_churn() -> None:
    # Reading 12 NEW files in a row is exploration: churn stays 0, targets accumulate.
    loop = _loop()
    for i in range(12):
        loop.reflect(_state("read_file", {"path": f"app/File{i}.php"}))
    assert loop.read_churn_streak == 0
    assert len(loop.read_targets) == 12


def test_reflect_reread_same_target_builds_churn_even_varying_offset() -> None:
    # Re-reading the SAME file (paging with different offsets) is churn, not exploration.
    loop = _loop()
    for off in range(6):
        loop.reflect(_state("read_file", {"path": "app/Routes.php", "offset": off * 100}))
    assert len(loop.read_targets) == 1
    assert loop.read_churn_streak == 5  # first is new (0), each re-read +1


def test_reflect_producing_tool_resets_churn_streak() -> None:
    loop = _loop()
    loop.read_churn_streak = 7
    loop.reflect(_state("write_file", {"path": "a.py", "content": "x"}))
    assert loop.read_churn_streak == 0
