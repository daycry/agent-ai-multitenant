"""Unit tests for the shared-llm types (ADR 0021)."""

from __future__ import annotations

from shared_llm.types import (
    AgentRunEvent,
    CompletionResponse,
    Message,
    StreamChunk,
    ToolCall,
    Usage,
)


def test_usage_defaults_to_zero_costs() -> None:
    u = Usage()
    assert u.input_tokens == 0
    assert u.output_tokens == 0
    assert u.cost_usd == 0.0


def test_completion_response_carries_typed_tool_calls() -> None:
    tc = ToolCall(id="call_1", name="echo", arguments={"text": "hi"})
    resp = CompletionResponse(
        content="",
        model="m",
        provider="p",
        tool_calls=[tc],
    )
    assert resp.tool_calls is not None
    assert resp.tool_calls[0].name == "echo"
    assert resp.tool_calls[0].arguments == {"text": "hi"}


def test_message_supports_tool_reply_shape() -> None:
    m = Message(role="tool", content="ok", tool_call_id="call_1", name="echo")
    assert m.tool_call_id == "call_1"
    assert m.name == "echo"


def test_stream_chunk_final_carries_done_and_usage() -> None:
    final = StreamChunk(delta="", done=True, usage=Usage(input_tokens=10, output_tokens=20))
    assert final.done is True
    assert final.usage is not None
    assert final.usage.output_tokens == 20


def test_agent_run_event_kinds() -> None:
    for kind in ("text", "tool_use", "result", "other"):
        evt = AgentRunEvent(kind=kind)
        assert evt.kind == kind
