"""Unit tests for the ClaudeAgentProvider — no real SDK needed.

The provider accepts an injected `query_fn` shaped like
`claude_agent_sdk.query`. We feed it a fake that yields a sequence of
SDK-shaped messages so the parsing / `AgentRunEvent` translation is
exercised without importing the real package.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
from shared_llm.providers import ClaudeAgentProvider
from shared_llm.types import AgentRunEvent, Message


# ----------------------------------------------------------------------
# Fake SDK message shapes — duck-typed to match what the real SDK emits.
# ----------------------------------------------------------------------
@dataclass
class _TextBlock:
    text: str


@dataclass
class _ToolUseBlock:
    name: str
    input: dict[str, Any]
    id: str = "tu_1"


@dataclass
class _AssistantMessage:
    content: list[Any]


@dataclass
class _UsageBlock:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class _ResultMessage:
    total_cost_usd: float
    usage: _UsageBlock


def _make_query(*messages: Any):  # type: ignore[no-untyped-def]
    """Build a fake query callable that yields the given SDK messages."""

    async def _q(prompt: str, options: Any) -> AsyncIterator[Any]:
        for m in messages:
            yield m

    return _q


def test_api_key_is_exported_to_anthropic_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """API-key mode: the key lands in ANTHROPIC_API_KEY so the SDK authenticates."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    ClaudeAgentProvider(api_key="sk-ant-test-DO-NOT-LEAK", query_fn=_make_query())
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-test-DO-NOT-LEAK"


def test_subscription_token_is_exported_to_claude_code_oauth_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Subscription Pro/Max mode (ADR 0063): a `claude setup-token` OAuth token
    lands in CLAUDE_CODE_OAUTH_TOKEN — the env var the Claude Agent SDK reads to
    authenticate against a subscription WITHOUT an API key."""
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    ClaudeAgentProvider(oauth_token="sk-ant-oat-test-DO-NOT-LEAK", query_fn=_make_query())
    assert os.environ["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat-test-DO-NOT-LEAK"


@pytest.mark.asyncio
async def test_complete_collects_text_blocks_and_usage() -> None:
    fake_query = _make_query(
        _AssistantMessage(content=[_TextBlock(text="Hello, ")]),
        _AssistantMessage(content=[_TextBlock(text="world.")]),
        _ResultMessage(
            total_cost_usd=0.005,
            usage=_UsageBlock(input_tokens=10, output_tokens=20),
        ),
    )
    p = ClaudeAgentProvider(query_fn=fake_query, default_model="claude-sonnet-4-5")
    resp = await p.complete([Message(role="user", content="hi")])
    assert resp.content == "Hello, world."
    assert resp.provider == "claude_agent"
    assert resp.usage.input_tokens == 10
    assert resp.usage.output_tokens == 20
    assert resp.usage.cost_usd == 0.005


@pytest.mark.asyncio
async def test_stream_yields_text_deltas_and_a_final_done_chunk() -> None:
    fake_query = _make_query(
        _AssistantMessage(content=[_TextBlock(text="abc")]),
        _ResultMessage(
            total_cost_usd=0.001,
            usage=_UsageBlock(input_tokens=1, output_tokens=2),
        ),
    )
    p = ClaudeAgentProvider(query_fn=fake_query, default_model="claude-sonnet-4-5")
    chunks = []
    async for c in p.stream([Message(role="user", content="hi")]):
        chunks.append(c)
    assert chunks[0].delta == "abc"
    assert chunks[-1].done is True
    assert chunks[-1].usage is not None
    assert chunks[-1].usage.cost_usd == 0.001


@pytest.mark.asyncio
async def test_run_agent_yields_typed_agent_run_events() -> None:
    fake_query = _make_query(
        _AssistantMessage(content=[_ToolUseBlock(name="Read", input={"path": "file.txt"})]),
        _AssistantMessage(content=[_TextBlock(text="done")]),
        _ResultMessage(
            total_cost_usd=0.002,
            usage=_UsageBlock(input_tokens=3, output_tokens=4),
        ),
    )
    p = ClaudeAgentProvider(query_fn=fake_query, default_model="claude-sonnet-4-5")
    events = []
    async for evt in p.run_agent("Read file.txt", allowed_tools=["Read"], max_turns=2):
        events.append(evt)

    assert all(isinstance(e, AgentRunEvent) for e in events)
    assert events[0].kind == "tool_use"
    assert events[0].tool_use == {"name": "Read", "input": {"path": "file.txt"}, "id": "tu_1"}
    assert events[1].kind == "text"
    assert events[1].text == "done"
    assert events[2].kind == "result"
    assert events[2].usage is not None
    assert events[2].usage.cost_usd == 0.002


@pytest.mark.asyncio
async def test_flatten_collapses_chat_into_human_assistant_transcript() -> None:
    """The SDK's `query()` takes a string prompt; we collapse the chat
    history into a `Human:`/`Assistant:` transcript with system prepended."""
    captured_prompts: list[str] = []

    async def _q(prompt: str, options: Any) -> AsyncIterator[Any]:
        captured_prompts.append(prompt)
        yield _AssistantMessage(content=[_TextBlock(text="ok")])

    p = ClaudeAgentProvider(query_fn=_q)
    await p.complete(
        [
            Message(role="system", content="Be concise."),
            Message(role="user", content="hi"),
            Message(role="assistant", content="hello"),
            Message(role="user", content="goodbye"),
        ]
    )
    transcript = captured_prompts[0]
    assert transcript == "Human: hi\n\nAssistant: hello\n\nHuman: goodbye"
