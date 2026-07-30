"""Integration tests: the real ModelClient adapters (ADR 0021).

Plan 02 Fase G plugged real LLM providers behind the one
`ModelClient` protocol the LangGraph loop already depends on
(ADR 0013, ADR 0018). ADR 0021 simplified the catalog to four
providers and moved their HTTP/SDK logic into `shared_llm`; the
adapters here are thin sync wrappers over that async layer:

  * `AzureFoundryModelClient` — Azure AI Foundry behind APIM
                                (the enterprise gateway path, replaces
                                 the LiteLLM gateway that was retired
                                 in ADR 0021).
  * `CopilotModelClient`      — GitHub Copilot (JWT minted from a
                                 GitHub OAuth token, OpenAI-compat).
  * `ClaudeSDKModelClient`    — Claude Agent SDK, run one turn per
                                 `decide()` so our loop stays in
                                 charge (ADR 0018).
  * `OllamaModelClient`       — Ollama, local or cloud.

The transports are mocked — `httpx.MockTransport` on an
`httpx.AsyncClient` for the HTTP clients, an injected fake `query` for
the SDK — so the suite needs no network and no real credentials.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from agent_runtime.model import DecisionKind, ModelClient
from agent_runtime.providers import (
    AzureFoundryModelClient,
    ClaudeSDKModelClient,
    CopilotModelClient,
    OllamaModelClient,
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


def _mock_async_http(handler: Any) -> httpx.AsyncClient:
    """An httpx.AsyncClient whose transport is a recording mock."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# AUD16-01 — wire-format helpers: the HTTP providers pass `tools` VERBATIM to
# the OpenAI-compatible body, so EVERY entry must carry the
# {"type": "function", "function": {...}} envelope. A bare
# {name, description, parameters} dict is a 400 on strict endpoints
# (Azure/Copilot) and a nameless husk on Ollama.
# ---------------------------------------------------------------------------
def _assert_openai_tool_envelope(tools: list[dict[str, Any]]) -> None:
    assert isinstance(tools, list) and tools
    for entry in tools:
        assert entry.get("type") == "function", f"tool without envelope: {entry}"
        fn = entry.get("function")
        assert isinstance(fn, dict) and fn.get("name"), f"tool without function/name: {entry}"


def _tool_names(tools: list[dict[str, Any]]) -> list[str]:
    return [entry["function"]["name"] for entry in tools]


def _capture_body(seen: dict[str, Any]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/copilot_internal/v2/token"):
            return httpx.Response(200, json={"token": "jwt-1", "expires_at": 9_999_999_999})
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_chat_text("done"))

    return handler


# ===========================================================================
# Azure Foundry — OpenAI-compatible gateway (replaces LiteLLM)
# ===========================================================================
def _azure(handler: Any) -> AzureFoundryModelClient:
    return AzureFoundryModelClient(
        model="gpt-4o-foundry",
        apim_base_url="https://x.azure-api.net/foundry",
        deployment="gpt-4o",
        subscription_key="sub-test",
        tools=_TOOLS,
        http_client=_mock_async_http(handler),
    )


def test_azure_decide_parses_a_tool_call() -> None:
    client = _azure(lambda _req: httpx.Response(200, json=_chat_tool_call("echo", {"text": "hi"})))
    response = client.decide(_STATE)

    assert response.decision.kind == DecisionKind.ACT
    assert response.decision.tool == "echo"
    assert response.decision.tool_args == {"text": "hi"}
    assert response.tokens_in == 50
    assert response.tokens_out == 12


def test_azure_decide_parses_a_final_answer() -> None:
    client = _azure(lambda _req: httpx.Response(200, json=_chat_text("the sea poem")))
    response = client.decide(_STATE)

    assert response.decision.kind == DecisionKind.FINISH
    assert response.decision.output == "the sea poem"


def test_azure_decide_targets_apim_url_with_subscription_key() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["sub"] = request.headers.get("ocp-apim-subscription-key")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_chat_text("done"))

    _azure(handler).decide(_STATE)

    # APIM URL shape: <base>/openai/deployments/<dep>/chat/completions?api-version=...
    assert "/openai/deployments/gpt-4o/chat/completions" in seen["url"]
    assert "api-version=" in seen["url"]
    assert seen["sub"] == "sub-test"
    assert isinstance(seen["body"]["messages"], list) and seen["body"]["messages"]
    # decide() offers the tool catalog (plus the ADR 0087 `submit_result`
    # finisher) so the model can act and close with a structured outcome.
    assert seen["body"]["tools"][: len(_TOOLS)] == _TOOLS
    _assert_openai_tool_envelope(seen["body"]["tools"])
    assert _tool_names(seen["body"]["tools"]) == ["echo", "submit_result"]


def test_azure_decide_wire_format_wraps_every_tool() -> None:
    # AUD16-01: submit_result travelled BARE next to the wrapped agent tools,
    # which is a 400 on APIM's strict schema — every entry must be enveloped.
    seen: dict[str, Any] = {}
    _azure(_capture_body(seen)).decide(_STATE)
    _assert_openai_tool_envelope(seen["body"]["tools"])
    assert _tool_names(seen["body"]["tools"]) == ["echo", "submit_result"]


def test_azure_review_wire_format_wraps_submit_verdict() -> None:
    seen: dict[str, Any] = {}
    _azure(_capture_body(seen)).review(_STATE)
    _assert_openai_tool_envelope(seen["body"]["tools"])
    assert _tool_names(seen["body"]["tools"]) == ["submit_verdict"]
    # F34: the verdict is still FORCED via tool_choice.
    assert seen["body"]["tool_choice"] == {
        "type": "function",
        "function": {"name": "submit_verdict"},
    }


def test_azure_review_parses_a_pass() -> None:
    verdict = json.dumps({"passed": True, "feedback": "matches the task"})
    client = _azure(lambda _req: httpx.Response(200, json=_chat_text(verdict)))
    review = client.review(_STATE)

    assert review.passed is True
    assert review.feedback == "matches the task"


def test_azure_review_parses_a_fail() -> None:
    verdict = json.dumps({"passed": False, "feedback": "off topic"})
    client = _azure(lambda _req: httpx.Response(200, json=_chat_text(verdict)))
    review = client.review(_STATE)

    assert review.passed is False
    assert review.feedback == "off topic"


# ===========================================================================
# Ollama — OpenAI-compatible local / cloud
# ===========================================================================
def _ollama(handler: Any) -> OllamaModelClient:
    return OllamaModelClient(
        model="llama3.1",
        base_url="http://localhost:11434/v1",
        api_key=None,
        tools=_TOOLS,
        http_client=_mock_async_http(handler),
    )


def test_ollama_decide_parses_a_tool_call() -> None:
    client = _ollama(
        lambda _req: httpx.Response(200, json=_chat_tool_call("echo", {"text": "ahoy"}))
    )
    response = client.decide(_STATE)

    assert response.decision.kind == DecisionKind.ACT
    assert response.decision.tool == "echo"


def test_ollama_cloud_sends_bearer_token() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=_chat_text("ok"))

    client = OllamaModelClient(
        model="gpt-oss:120b",
        base_url="https://ollama.com/v1",
        api_key="sk-cloud",
        http_client=_mock_async_http(handler),
    )
    client.decide(_STATE)
    assert seen["auth"] == "Bearer sk-cloud"


def test_ollama_decide_wire_format_wraps_every_tool() -> None:
    # AUD16-01: Ollama unmarshals a bare dict into a nameless tool husk — the
    # model would never see submit_result. Every entry must be enveloped.
    seen: dict[str, Any] = {}
    _ollama(_capture_body(seen)).decide(_STATE)
    _assert_openai_tool_envelope(seen["body"]["tools"])
    assert _tool_names(seen["body"]["tools"]) == ["echo", "submit_result"]


def test_ollama_review_wire_format_wraps_submit_verdict() -> None:
    seen: dict[str, Any] = {}
    _ollama(_capture_body(seen)).review(_STATE)
    _assert_openai_tool_envelope(seen["body"]["tools"])
    assert _tool_names(seen["body"]["tools"]) == ["submit_verdict"]


# ===========================================================================
# GitHub Copilot — OpenAI-compat + JWT auth (provider handles the mint)
# ===========================================================================
def test_copilot_decide_uses_the_jwt_and_vscode_headers() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/copilot_internal/v2/token"):
            # The provider exchanges the OAuth token for a JWT first.
            assert request.headers["authorization"] == "token gho_test"
            return httpx.Response(200, json={"token": "jwt-1", "expires_at": 9_999_999_999})
        seen["auth"] = request.headers.get("authorization")
        seen["agent"] = request.headers.get("user-agent")
        seen["integration"] = request.headers.get("copilot-integration-id")
        return httpx.Response(200, json=_chat_tool_call("echo", {"text": "ahoy"}))

    client = CopilotModelClient(
        model="gpt-4o",
        github_token="gho_test",
        tools=_TOOLS,
        http_client=_mock_async_http(handler),
    )
    response = client.decide(_STATE)

    assert response.decision.kind == DecisionKind.ACT
    assert response.decision.tool == "echo"
    assert seen["auth"] == "Bearer jwt-1"
    assert seen["agent"].startswith("GitHubCopilotChat/")
    assert seen["integration"] == "vscode-chat"


def test_copilot_decide_wire_format_wraps_every_tool() -> None:
    seen: dict[str, Any] = {}
    client = CopilotModelClient(
        model="gpt-4o",
        github_token="gho_test",
        tools=_TOOLS,
        http_client=_mock_async_http(_capture_body(seen)),
    )
    client.decide(_STATE)
    _assert_openai_tool_envelope(seen["body"]["tools"])
    assert _tool_names(seen["body"]["tools"]) == ["echo", "submit_result"]


# ===========================================================================
# Claude Agent SDK — one turn per decide() (ADR 0018, option A)
# ===========================================================================
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


class _UsageBlock:
    def __init__(self, *, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_input_tokens = 0
        self.cache_creation_input_tokens = 0


class _ResultMessage:
    def __init__(self, *, usage: _UsageBlock, total_cost_usd: float) -> None:
        self.usage = usage
        self.total_cost_usd = total_cost_usd


def _fake_query(*messages: Any) -> Any:
    """A stand-in for `claude_agent_sdk.query` — yields fixed messages."""

    async def _query(*_args: Any, **_kwargs: Any) -> AsyncIterator[Any]:
        for message in messages:
            yield message

    return _query


def test_claude_sdk_decide_parses_a_text_finish() -> None:
    """The SDK adapter only emits FINISH on text (no OpenAI-style
    tool_calls path through complete() — ADR 0018)."""
    query = _fake_query(
        _AssistantMessage([_TextBlock("the sea poem")]),
        _ResultMessage(
            usage=_UsageBlock(input_tokens=60, output_tokens=9),
            total_cost_usd=0.002,
        ),
    )
    client = ClaudeSDKModelClient(model="claude-opus-4-7", query_fn=query)
    response = client.decide(_STATE)

    assert response.decision.kind == DecisionKind.FINISH
    assert response.decision.output == "the sea poem"
    assert response.tokens_in == 60
    assert response.tokens_out == 9
    assert response.cost_usd == 0.002


def test_claude_sdk_review_parses_the_verdict() -> None:
    verdict = json.dumps({"passed": True, "feedback": "good"})
    query = _fake_query(
        _AssistantMessage([_TextBlock(verdict)]),
        _ResultMessage(
            usage=_UsageBlock(input_tokens=30, output_tokens=6),
            total_cost_usd=0.001,
        ),
    )
    client = ClaudeSDKModelClient(model="claude-haiku-4-5", query_fn=query)
    review = client.review(_STATE)

    assert review.passed is True
    assert review.feedback == "good"


# ===========================================================================
# Protocol conformance + the model_from_spec factory
# ===========================================================================
def test_model_from_spec_builds_each_real_client() -> None:
    from agent_runtime.model import model_from_spec

    azure = model_from_spec(
        {
            "kind": "azure_foundry",
            "model": "gpt-4o-foundry",
            "apim_base_url": "https://x.azure-api.net/foundry",
            "deployment": "gpt-4o",
            "subscription_key": "sub-x",
        }
    )
    copilot = model_from_spec({"kind": "copilot", "model": "gpt-4o", "github_token": "gho_x"})
    claude = model_from_spec({"kind": "claude_sdk", "model": "claude-opus-4-7"})
    ollama = model_from_spec({"kind": "ollama", "model": "llama3.1"})

    assert isinstance(azure, AzureFoundryModelClient)
    assert isinstance(copilot, CopilotModelClient)
    assert isinstance(claude, ClaudeSDKModelClient)
    assert isinstance(ollama, OllamaModelClient)


def test_model_from_spec_rejects_litellm_kind() -> None:
    """ADR 0021 retired LiteLLM. The factory must say so explicitly."""
    from agent_runtime.model import model_from_spec

    with pytest.raises(ValueError, match="ADR 0021"):
        model_from_spec({"kind": "litellm", "model": "gpt-4o", "api_key": "sk-x"})


def test_all_four_clients_conform_to_the_model_client_protocol() -> None:
    clients = [
        AzureFoundryModelClient(
            model="gpt-4o-foundry",
            apim_base_url="https://x.azure-api.net/foundry",
            deployment="gpt-4o",
            subscription_key="k",
        ),
        CopilotModelClient(model="gpt-4o", github_token="gho_x"),
        ClaudeSDKModelClient(model="claude-opus-4-7"),
        OllamaModelClient(model="llama3.1"),
    ]
    for client in clients:
        assert isinstance(client, ModelClient)
