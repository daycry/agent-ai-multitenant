"""Integration tests: the real ModelClient implementations (task_02_32).

Plan 02 Fase G plugs three real LLM providers behind the one
`ModelClient` protocol the LangGraph loop already depends on (ADR 0013,
ADR 0018):

  * `LiteLLMModelClient`  — the LiteLLM gateway (OpenAI-compatible).
  * `CopilotModelClient`  — GitHub Copilot (OpenAI-compatible + a JWT
                            minted from a GitHub OAuth token).
  * `ClaudeSDKModelClient`— the Claude Agent SDK, run one turn per
                            `decide()` so our loop stays in charge.

The transports are mocked — `httpx.MockTransport` for the HTTP clients,
an injected fake `query` for the SDK — so the suite needs no network
and no real credentials.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from agent_runtime.model import DecisionKind, ModelClient
from agent_runtime.providers import (
    ClaudeSDKModelClient,
    CopilotAuth,
    CopilotModelClient,
    LiteLLMModelClient,
)

pytestmark = pytest.mark.integration

_STATE: dict[str, Any] = {
    "task": {"id": "t-1", "title": "Write a sea poem", "description": "about the tide"},
    "context": [{"role": "task", "title": "Write a sea poem"}],
    "last_observation": None,
    "output": "the sea poem",
}

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "echo",
            "description": "Echo text back.",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    }
]


# ---------------------------------------------------------------------------
# OpenAI-compatible response builders
# ---------------------------------------------------------------------------
def _chat_tool_call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "model": "gpt-4o",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)},
                        }
                    ],
                }
            }
        ],
        "usage": {"prompt_tokens": 50, "completion_tokens": 12, "total_tokens": 62},
    }


def _chat_text(content: str) -> dict[str, Any]:
    return {
        "model": "gpt-4o",
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 40, "completion_tokens": 8, "total_tokens": 48},
    }


def _mock_http(handler: Any) -> httpx.Client:
    """An httpx.Client whose transport is a recording mock."""
    return httpx.Client(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# LiteLLM — OpenAI-compatible gateway
# ---------------------------------------------------------------------------
def _litellm(handler: Any) -> LiteLLMModelClient:
    return LiteLLMModelClient(
        model="gpt-4o",
        base_url="http://litellm:4000",
        api_key="sk-test",
        tools=_TOOLS,
        http_client=_mock_http(handler),
    )


def test_litellm_decide_parses_a_tool_call() -> None:
    client = _litellm(
        lambda _req: httpx.Response(200, json=_chat_tool_call("echo", {"text": "hi"}))
    )
    response = client.decide(_STATE)

    assert response.decision.kind == DecisionKind.ACT
    assert response.decision.tool == "echo"
    assert response.decision.tool_args == {"text": "hi"}
    assert response.tokens_in == 50
    assert response.tokens_out == 12


def test_litellm_decide_parses_a_final_answer() -> None:
    client = _litellm(lambda _req: httpx.Response(200, json=_chat_text("the sea poem")))
    response = client.decide(_STATE)

    assert response.decision.kind == DecisionKind.FINISH
    assert response.decision.output == "the sea poem"


def test_litellm_decide_sends_the_model_messages_and_tools() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_chat_text("done"))

    _litellm(handler).decide(_STATE)

    assert seen["url"].endswith("/chat/completions")
    assert seen["auth"] == "Bearer sk-test"
    assert seen["body"]["model"] == "gpt-4o"
    assert isinstance(seen["body"]["messages"], list) and seen["body"]["messages"]
    # decide() offers the tool catalog so the model can act.
    assert seen["body"]["tools"] == _TOOLS


def test_litellm_review_parses_a_pass() -> None:
    verdict = json.dumps({"passed": True, "feedback": "matches the task"})
    client = _litellm(lambda _req: httpx.Response(200, json=_chat_text(verdict)))
    review = client.review(_STATE)

    assert review.passed is True
    assert review.feedback == "matches the task"


def test_litellm_review_parses_a_fail() -> None:
    verdict = json.dumps({"passed": False, "feedback": "off topic"})
    client = _litellm(lambda _req: httpx.Response(200, json=_chat_text(verdict)))
    review = client.review(_STATE)

    assert review.passed is False
    assert review.feedback == "off topic"


# ---------------------------------------------------------------------------
# GitHub Copilot — OpenAI-compatible + JWT auth
# ---------------------------------------------------------------------------
def test_copilot_auth_exchanges_an_oauth_token_for_a_jwt() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        assert request.headers["authorization"] == "token gho_test"
        return httpx.Response(200, json={"token": "copilot-jwt-xyz", "expires_at": 9_999_999_999})

    auth = CopilotAuth("gho_test", http_client=_mock_http(handler))
    assert auth.jwt() == "copilot-jwt-xyz"
    # A second call inside the TTL is served from cache — no re-exchange.
    assert auth.jwt() == "copilot-jwt-xyz"
    assert calls["n"] == 1


def test_copilot_decide_uses_the_jwt_and_vscode_headers() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/copilot_internal/v2/token"):
            return httpx.Response(200, json={"token": "jwt-1", "expires_at": 9_999_999_999})
        seen["auth"] = request.headers.get("authorization")
        seen["agent"] = request.headers.get("user-agent")
        seen["integration"] = request.headers.get("copilot-integration-id")
        return httpx.Response(200, json=_chat_tool_call("echo", {"text": "ahoy"}))

    http = _mock_http(handler)
    client = CopilotModelClient(
        model="gpt-4o",
        auth=CopilotAuth("gho_test", http_client=http),
        tools=_TOOLS,
        http_client=http,
    )
    response = client.decide(_STATE)

    assert response.decision.kind == DecisionKind.ACT
    assert response.decision.tool == "echo"
    assert seen["auth"] == "Bearer jwt-1"
    assert seen["agent"].startswith("GitHubCopilotChat/")
    assert seen["integration"] == "vscode-chat"


# ---------------------------------------------------------------------------
# Claude Agent SDK — one turn per decide() (ADR 0018, option A)
# ---------------------------------------------------------------------------
class _TextBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _ToolUseBlock:
    def __init__(self, name: str, tool_input: dict[str, Any]) -> None:
        self.id = "tu_1"
        self.name = name
        self.input = tool_input


class _AssistantMessage:
    def __init__(self, content: list[Any]) -> None:
        self.content = content


class _ResultMessage:
    def __init__(self, *, usage: dict[str, int], total_cost_usd: float) -> None:
        self.usage = usage
        self.total_cost_usd = total_cost_usd


def _fake_query(*messages: Any) -> Any:
    """A stand-in for `claude_agent_sdk.query` — yields fixed messages."""

    async def _query(*_args: Any, **_kwargs: Any) -> AsyncIterator[Any]:
        for message in messages:
            yield message

    return _query


def test_claude_sdk_decide_parses_a_tool_use() -> None:
    query = _fake_query(
        _AssistantMessage([_ToolUseBlock("echo", {"text": "ahoy"})]),
        _ResultMessage(usage={"input_tokens": 70, "output_tokens": 15}, total_cost_usd=0.004),
    )
    client = ClaudeSDKModelClient(model="claude-opus-4-7", query_fn=query, tools=_TOOLS)
    response = client.decide(_STATE)

    assert response.decision.kind == DecisionKind.ACT
    assert response.decision.tool == "echo"
    assert response.decision.tool_args == {"text": "ahoy"}
    assert response.tokens_in == 70
    assert response.tokens_out == 15
    assert response.cost_usd == 0.004


def test_claude_sdk_decide_parses_a_text_finish() -> None:
    query = _fake_query(
        _AssistantMessage([_TextBlock("the sea poem")]),
        _ResultMessage(usage={"input_tokens": 60, "output_tokens": 9}, total_cost_usd=0.002),
    )
    client = ClaudeSDKModelClient(model="claude-opus-4-7", query_fn=query)
    response = client.decide(_STATE)

    assert response.decision.kind == DecisionKind.FINISH
    assert response.decision.output == "the sea poem"
    assert response.model == "claude-opus-4-7"


def test_claude_sdk_review_parses_the_verdict() -> None:
    verdict = json.dumps({"passed": True, "feedback": "good"})
    query = _fake_query(
        _AssistantMessage([_TextBlock(verdict)]),
        _ResultMessage(usage={"input_tokens": 30, "output_tokens": 6}, total_cost_usd=0.001),
    )
    client = ClaudeSDKModelClient(model="claude-haiku-4-5", query_fn=query)
    review = client.review(_STATE)

    assert review.passed is True
    assert review.feedback == "good"


# ---------------------------------------------------------------------------
# Protocol conformance + the model_from_spec factory
# ---------------------------------------------------------------------------
def test_model_from_spec_builds_each_real_client() -> None:
    from agent_runtime.model import model_from_spec

    litellm = model_from_spec({"kind": "litellm", "model": "gpt-4o", "api_key": "sk-x"})
    copilot = model_from_spec({"kind": "copilot", "model": "gpt-4o", "oauth_token": "gho_x"})
    claude = model_from_spec({"kind": "claude_sdk", "model": "claude-opus-4-7"})

    assert isinstance(litellm, LiteLLMModelClient)
    assert isinstance(copilot, CopilotModelClient)
    assert isinstance(claude, ClaudeSDKModelClient)


def test_all_three_clients_conform_to_the_model_client_protocol() -> None:
    clients = [
        LiteLLMModelClient(model="gpt-4o", base_url="http://x", api_key="k"),
        CopilotModelClient(model="gpt-4o", auth=CopilotAuth("gho_x")),
        ClaudeSDKModelClient(model="claude-opus-4-7"),
    ]
    for client in clients:
        assert isinstance(client, ModelClient)
