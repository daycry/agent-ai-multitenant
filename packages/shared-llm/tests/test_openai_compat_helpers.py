"""Unit tests for the OpenAI-compat parsing helpers."""

from __future__ import annotations

import httpx
import pytest
from shared_llm.exceptions import AuthError, ProviderError, RateLimitError
from shared_llm.providers._openai_compat import (
    check_status,
    parse_chat_completion,
    parse_sse_delta,
    to_openai_messages,
)
from shared_llm.types import Message


def test_to_openai_messages_keeps_tool_metadata() -> None:
    msgs = to_openai_messages(
        [
            Message(role="system", content="be nice"),
            Message(role="user", content="hi"),
            Message(role="tool", content="ok", tool_call_id="c1", name="echo"),
        ]
    )
    assert msgs[0] == {"role": "system", "content": "be nice"}
    assert msgs[2] == {
        "role": "tool",
        "content": "ok",
        "name": "echo",
        "tool_call_id": "c1",
    }


def test_check_status_maps_401_to_auth_error() -> None:
    resp = httpx.Response(401, text="nope")
    with pytest.raises(AuthError):
        check_status(resp, provider="test")


def test_check_status_maps_429_to_rate_limit() -> None:
    resp = httpx.Response(429, text="slow down")
    with pytest.raises(RateLimitError):
        check_status(resp, provider="test")


def test_check_status_maps_other_4xx_to_provider_error() -> None:
    resp = httpx.Response(500, text="boom")
    with pytest.raises(ProviderError) as info:
        check_status(resp, provider="test")
    assert info.value.status_code == 500


def test_parse_chat_completion_text_only() -> None:
    data = {
        "model": "m1",
        "choices": [{"message": {"role": "assistant", "content": "hello"}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 5},
    }
    resp = parse_chat_completion(data, provider="x", fallback_model="default")
    assert resp.content == "hello"
    assert resp.model == "m1"
    assert resp.tool_calls is None
    assert resp.usage.input_tokens == 3
    assert resp.usage.output_tokens == 5


def test_parse_chat_completion_with_tool_calls() -> None:
    data = {
        "model": "m1",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_42",
                            "type": "function",
                            "function": {
                                "name": "echo",
                                "arguments": '{"text": "hi"}',
                            },
                        }
                    ],
                }
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 2, "cost": 0.001},
    }
    resp = parse_chat_completion(data, provider="x", fallback_model="default")
    assert resp.content == ""
    assert resp.tool_calls is not None
    assert resp.tool_calls[0].id == "call_42"
    assert resp.tool_calls[0].name == "echo"
    assert resp.tool_calls[0].arguments == {"text": "hi"}
    assert resp.usage.cost_usd == 0.001


def test_parse_sse_delta_recognises_content_and_done() -> None:
    text, done = parse_sse_delta('data: {"choices":[{"delta":{"content":"hi"}}]}')
    assert text == "hi"
    assert done is False

    _, done2 = parse_sse_delta("data: [DONE]")
    assert done2 is True

    # Comments / blank lines are ignored.
    text3, done3 = parse_sse_delta(": keep-alive")
    assert text3 is None
    assert done3 is False
