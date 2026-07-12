"""ADR 0102 cierre — pre_tool + enforce de block + truncado D6 en el runtime.

El slice g1 solo escaneaba OUTPUTS (post_tool) en modo LOG. Ahora: (1) el nodo
``act`` corre el hook ``pre_tool`` ANTES de ejecutar la tool — un ``block``
configurado rechaza la llamada (la tool no corre; la observación explica el
motivo); (2) un ``block`` en ``post_tool`` sustituye el output antes de que
re-entre al contexto del modelo; (3) ``run_hook`` trunca el input a
``_HOOK_INPUT_MAX`` chars (D6) para acotar el coste del escaneo. El baseline
sigue siendo warn/LOG: sin config del operador nada cambia.
"""

from __future__ import annotations

from typing import Any

from agent_runtime.graph import AgentDeps, _AgentLoop
from agent_runtime.guardrails import _HOOK_INPUT_MAX, run_hook
from agent_runtime.loop_detection import LoopDetector
from agent_runtime.model import ScriptedModelClient
from agent_runtime.safeguards import Budgets, SafeguardTracker
from agent_runtime.state import initial_state
from agent_runtime.tools import ToolRegistry, ToolResult


class _Pipeline:
    """Pipeline fake: dispara `action` en los hooks indicados."""

    def __init__(self, *, block_hooks: tuple[str, ...] = ()) -> None:
        self.block_hooks = block_hooks
        self.contexts: list[Any] = []

    def run(self, ctx: Any) -> Any:
        from shared_guardrails.types import (
            Action,
            GuardrailOutcome,
            PipelineDecision,
            Severity,
        )

        self.contexts.append(ctx)
        triggered = ctx.hook in self.block_hooks
        outcomes = (
            [
                GuardrailOutcome(
                    type="keyword",
                    triggered=True,
                    severity=Severity.HIGH,
                    detail="matched forbidden pattern",
                    action=Action.BLOCK,
                )
            ]
            if triggered
            else []
        )
        return PipelineDecision(
            hook=ctx.hook,
            triggered=triggered,
            action=Action.BLOCK if triggered else None,
            outcomes=outcomes,
        )


def _loop(pipeline: Any) -> tuple[_AgentLoop, list[str]]:
    calls: list[str] = []
    registry = ToolRegistry()

    def _echo(args: dict[str, Any]) -> ToolResult:
        calls.append(str(args.get("text")))
        return ToolResult(ok=True, output=f"echo:{args.get('text')}")

    registry.register("echo", _echo)
    deps = AgentDeps(
        model=ScriptedModelClient(decisions=[], reviews=[]),
        tools=registry,
        guardrails=pipeline,
    )
    return _AgentLoop(deps, SafeguardTracker(Budgets()), LoopDetector()), calls


def _act(loop: _AgentLoop, tool: str = "echo") -> dict[str, Any]:
    state = initial_state({"id": "t", "title": "T", "description": ""})
    state["last_decision"] = {"tool": tool, "tool_args": {"text": "hola"}}
    return loop.act(state)


def test_pre_tool_block_rejects_the_call() -> None:
    pipeline = _Pipeline(block_hooks=("pre_tool",))
    loop, calls = _loop(pipeline)
    delta = _act(loop)
    # La tool NO se ejecutó y la observación explica el bloqueo.
    assert calls == []
    observation = delta["last_observation"]
    assert observation["ok"] is False
    assert "guardrail" in str(observation["error"]).lower()
    # El evento del guardrail viaja al envelope (D4).
    assert any(e["hook_point"] == "pre_tool" for e in delta["guardrail_events"])


def test_post_tool_block_replaces_the_output() -> None:
    pipeline = _Pipeline(block_hooks=("post_tool",))
    loop, calls = _loop(pipeline)
    delta = _act(loop)
    # La tool corrió, pero su output NO re-entra al contexto.
    assert calls == ["hola"]
    observation = delta["last_observation"]
    assert "echo:hola" not in str(observation["output"])
    assert "blocked" in str(observation["output"]).lower()


def test_warn_baseline_still_advisory() -> None:
    pipeline = _Pipeline(block_hooks=())
    loop, calls = _loop(pipeline)
    delta = _act(loop)
    assert calls == ["hola"]
    assert delta["last_observation"]["output"] == "echo:hola"


def test_run_hook_truncates_huge_inputs() -> None:
    # D6: el escaneo no paga outputs de MB — se trunca a _HOOK_INPUT_MAX.
    pipeline = _Pipeline()
    run_hook(
        pipeline,
        hook="post_tool",
        tool_name="echo",
        tool_result="x" * (_HOOK_INPUT_MAX + 5000),
    )
    seen = pipeline.contexts[-1]
    assert len(seen.tool_result) == _HOOK_INPUT_MAX
    assert seen.metadata.get("truncated") is True
