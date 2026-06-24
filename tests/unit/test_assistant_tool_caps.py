"""Per-tool call caps in the assistant sub-graph (auditoría zona 'asistente').

Regression: the cap used ``state.tools_called.count(name)``, which only reflects
PREVIOUS rounds, so an over-eager model emitting the same tool N times with
different args in ONE round bypassed the cap entirely (notably breaking the
"AT MOST ONE remember_about_me per turn" guarantee). The cap must count both
prior rounds AND calls already kept within the current round.
"""

from __future__ import annotations

from api_server.assistant.graph import (
    MAX_CALLS_PER_TOOL,
    AssistantState,
    ToolInvocation,
    _admissible_tool_calls,
    _signature,
)

WRITE_TOOL = "remember_about_me"  # capped to 1/turn (_PER_TOOL_CALL_CAP)
READ_TOOL = "tenant_projects_status"  # default cap (MAX_CALLS_PER_TOOL)


def _state(enabled: tuple[str, ...], **kw: object) -> AssistantState:
    return AssistantState(system_prompt="", enabled_tools=enabled, **kw)  # type: ignore[arg-type]


def test_write_tool_capped_to_one_within_a_single_round() -> None:
    state = _state((WRITE_TOOL,))
    calls = tuple(ToolInvocation(WRITE_TOOL, {"content": c}) for c in ("a", "b", "c"))
    kept = _admissible_tool_calls(state, calls)
    assert len(kept) == 1  # only ONE write survives, even with distinct args


def test_default_cap_applies_within_a_single_round() -> None:
    state = _state((READ_TOOL,))
    calls = tuple(ToolInvocation(READ_TOOL, {"i": i}) for i in range(5))
    kept = _admissible_tool_calls(state, calls)
    assert len(kept) == MAX_CALLS_PER_TOOL  # 5 requested, capped at 3


def test_cap_counts_prior_rounds_plus_current() -> None:
    # One call already ran this turn; the per-tool cap (1) is already spent.
    state = _state((WRITE_TOOL,), tools_called=[WRITE_TOOL])
    kept = _admissible_tool_calls(state, (ToolInvocation(WRITE_TOOL, {"content": "x"}),))
    assert kept == ()


def test_disabled_and_already_executed_calls_are_dropped() -> None:
    dup = ToolInvocation(READ_TOOL, {"i": 1})
    state = _state((READ_TOOL,), executed_signatures={_signature(dup)})
    calls = (
        ToolInvocation("not_enabled", {}),  # not in enabled_tools
        dup,  # same signature already executed
        ToolInvocation(READ_TOOL, {"i": 2}),  # fresh → kept
    )
    kept = _admissible_tool_calls(state, calls)
    assert [c.arguments for c in kept] == [{"i": 2}]
