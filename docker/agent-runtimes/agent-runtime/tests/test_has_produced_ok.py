"""G3/r4: ``has_produced`` latches only on a SUCCESSFUL producing tool.

Before, ANY producing tool (write_file / shell_exec / stack_exec …) latched
``has_produced`` without checking ``observation.ok``. A denied shell_exec
("command not allowed") or a write that errored produced nothing, yet flipped
every safeguard trip from ABORTED to needs_human_review (contaminating the human
queue with sterile runs) and switched the nudge to "FINISH" (audit 2026-07-03, r4).
"""

from __future__ import annotations

from types import SimpleNamespace

from agent_runtime.graph import _AgentLoop


def _loop() -> _AgentLoop:
    # _track_research only reads deps.is_review + its own read_* state; tracker /
    # detector are stored but unused here, so lightweight fakes suffice.
    deps = SimpleNamespace(is_review=False)
    return _AgentLoop(deps, tracker=SimpleNamespace(), detector=SimpleNamespace())  # type: ignore[arg-type]


def test_failed_producing_tool_does_not_latch_has_produced() -> None:
    loop = _loop()
    assert loop.has_produced is False
    _target, productive = loop._track_research(
        "shell_exec",
        {"tool_args": {"command": "sed -n '1,5p' vendor.php"}},
        {"ok": False, "error": "command not allowed: sed"},
    )
    assert loop.has_produced is False
    assert productive is False


def test_successful_producing_tool_latches_has_produced() -> None:
    loop = _loop()
    _target, productive = loop._track_research(
        "write_file", {"tool_args": {"path": "x.py"}}, {"ok": True}
    )
    assert loop.has_produced is True
    assert productive is True


def test_namespaced_producing_tool_respects_ok() -> None:
    loop = _loop()
    loop._track_research("fs.write_file", {"tool_args": {}}, {"ok": False})
    assert loop.has_produced is False
    loop._track_research("fs.write_file", {"tool_args": {}}, {"ok": True})
    assert loop.has_produced is True
