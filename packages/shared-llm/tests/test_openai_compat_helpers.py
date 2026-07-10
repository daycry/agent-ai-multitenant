"""Unit tests for the OpenAI-compat parsing helpers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
from shared_llm.exceptions import AuthError, ProviderError, RateLimitError
from shared_llm.providers._openai_compat import (
    _loads_args,
    check_status,
    completion_signals,
    iter_sse_chunks,
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


def test_check_status_maps_403_to_provider_error_not_auth() -> None:
    """403 (Forbidden) is a permission problem, not an auth one: a fresh
    token will not help, so it must NOT be an AuthError (the caller's
    re-mint-and-retry path keys off AuthError)."""
    resp = httpx.Response(403, text="forbidden")
    with pytest.raises(ProviderError) as info:
        check_status(resp, provider="test")
    assert info.value.status_code == 403
    # And it is specifically NOT an AuthError.
    assert not isinstance(info.value, AuthError)


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


def test_parse_chat_completion_populates_typed_stop_reason() -> None:
    """M-4 (auditoría 2026-07-10): el campo tipado ``stop_reason`` prometía estar
    «normalizado del provider» pero los providers HTTP nunca lo poblaban (solo
    claude_sdk, #10c) — trampa para quien confíe en él. Ahora viaja el
    ``finish_reason`` del payload; ausente → None (fakes/shapes viejos)."""

    def _with(finish_reason: str | None) -> dict[str, object]:
        choice: dict[str, object] = {"message": {"role": "assistant", "content": "x"}}
        if finish_reason is not None:
            choice["finish_reason"] = finish_reason
        return {"model": "m", "choices": [choice], "usage": {}}

    assert (
        parse_chat_completion(_with("length"), provider="x", fallback_model="m").stop_reason
        == "length"
    )
    assert (
        parse_chat_completion(_with("stop"), provider="x", fallback_model="m").stop_reason == "stop"
    )
    assert parse_chat_completion(_with(None), provider="x", fallback_model="m").stop_reason is None


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


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"choices": []},
        {"choices": [{}]},
        {"choices": [{"message": None}]},
        {"choices": "nope"},
    ],
)
def test_parse_chat_completion_raises_provider_error_on_malformed_body(
    body: dict[str, object],
) -> None:
    """An HTTP 200 with a malformed/empty body (a flaky OpenAI-compat gateway, an APIM
    policy returning an error-shape) must surface as a typed ProviderError, not a raw
    KeyError/IndexError escaping the LLM layer."""
    with pytest.raises(ProviderError):
        parse_chat_completion(body, provider="x", fallback_model="default")


# ---------------------------------------------------------------------------
# F32 — robustness signals: tell "corrupt/truncated args" from "no args"
# ---------------------------------------------------------------------------
def test_loads_args_still_degrades_malformed_to_empty_for_execution() -> None:
    """The parse path always needs *a* dict, so a corrupt payload still degrades
    to {} here (best-effort) — backward-compatible. The distinction lives in
    `completion_signals`, not in this helper."""
    assert _loads_args('{"a": 1}') == {"a": 1}
    assert _loads_args(None) == {}
    assert _loads_args("") == {}
    assert _loads_args('{"a": 1') == {}  # truncated/corrupt -> still {}
    assert _loads_args("not json") == {}


def test_completion_signals_flags_truncated_response() -> None:
    """finish_reason == 'length' means the body (incl. tool-call args JSON) may be
    cut off — exposed so the caller does not trust a half-baked tool call."""
    data = {
        "choices": [{"finish_reason": "length", "message": {"role": "assistant", "content": "x"}}],
    }
    sig = completion_signals(data)
    assert sig.truncated is True
    assert sig.malformed_tool_args is False


def test_completion_signals_flags_malformed_tool_args_not_absent_args() -> None:
    """A tool call whose `arguments` is present-but-corrupt is flagged; a tool call
    with NO/empty args is NOT flagged (the key distinction the audit asked for)."""
    corrupt = {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "tool_calls": [
                        {"id": "1", "function": {"name": "submit_result", "arguments": '{"a": 1'}}
                    ]
                },
            }
        ]
    }
    assert completion_signals(corrupt).malformed_tool_args is True

    empty = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {"id": "1", "function": {"name": "submit_result", "arguments": ""}}
                    ]
                },
            }
        ]
    }
    sig_empty = completion_signals(empty)
    assert sig_empty.malformed_tool_args is False
    assert sig_empty.truncated is False


def test_completion_signals_is_safe_on_unexpected_shapes() -> None:
    """`raw` can be any shape across providers; signals must never raise."""
    for bad in (None, "nope", {}, {"choices": "x"}, {"choices": [None]}):
        sig = completion_signals(bad)
        assert sig.truncated is False
        assert sig.malformed_tool_args is False


def test_parse_chat_completion_keeps_raw_for_signal_extraction() -> None:
    """The caller derives signals from `CompletionResponse.raw`, which is the
    original payload — verify a truncated/corrupt tool call round-trips."""
    data = {
        "model": "m1",
        "choices": [
            {
                "finish_reason": "length",
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {"id": "c1", "function": {"name": "submit_result", "arguments": '{"a"'}}
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 2},
    }
    resp = parse_chat_completion(data, provider="x", fallback_model="default")
    # Execution still gets a (best-effort empty) dict...
    assert resp.tool_calls is not None
    assert resp.tool_calls[0].arguments == {}
    # ...but the signal recovers the lost information.
    sig = completion_signals(resp.raw)
    assert sig.truncated is True
    assert sig.malformed_tool_args is True


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


# ---------------------------------------------------------------------------
# iter_sse_chunks — body iteration + mid-stream error wrapping
# ---------------------------------------------------------------------------
class _RaisingStream(httpx.AsyncByteStream):
    """A response body that yields a few good SSE lines, then raises the
    given exception mid-stream — simulating a dropped connection / read
    timeout after the headers were already accepted."""

    def __init__(self, *, good: list[bytes], exc: BaseException) -> None:
        self._good = good
        self._exc = exc

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._good:
            yield chunk
        raise self._exc

    async def aclose(self) -> None:
        return None


def _streaming_response(stream: httpx.AsyncByteStream) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"Content-Type": "text/event-stream"},
        stream=stream,
    )


@pytest.mark.asyncio
async def test_iter_sse_chunks_yields_deltas_then_done() -> None:
    body = b"".join(
        [
            b'data: {"choices":[{"delta":{"content":"he"}}]}\n\n',
            b'data: {"choices":[{"delta":{"content":"llo"}}]}\n\n',
            b"data: [DONE]\n\n",
        ]
    )
    resp = httpx.Response(200, content=body, headers={"Content-Type": "text/event-stream"})
    chunks = [c async for c in iter_sse_chunks(resp, provider="test")]
    assert "".join(c.delta for c in chunks if not c.done) == "hello"
    assert chunks[-1].done is True


@pytest.mark.asyncio
async def test_iter_sse_chunks_wraps_midstream_httpx_error_as_provider_error() -> None:
    """A network error raised by aiter_lines() mid-stream is converted to
    ProviderError instead of escaping as a raw httpx error."""
    stream = _RaisingStream(
        good=[b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'],
        exc=httpx.ReadError("connection reset"),
    )
    resp = _streaming_response(stream)

    seen: list[str] = []
    with pytest.raises(ProviderError, match="stream interrupted"):
        async for chunk in iter_sse_chunks(resp, provider="test"):
            if chunk.delta:
                seen.append(chunk.delta)
    # The partial delta before the failure was still delivered.
    assert seen == ["partial"]


@pytest.mark.asyncio
async def test_iter_sse_chunks_wraps_oserror_as_provider_error() -> None:
    """A bare OSError (e.g. socket-level failure) is also wrapped."""
    stream = _RaisingStream(good=[], exc=OSError("socket closed"))
    resp = _streaming_response(stream)
    with pytest.raises(ProviderError, match="stream interrupted"):
        async for _chunk in iter_sse_chunks(resp, provider="test"):
            pass


@pytest.mark.asyncio
async def test_iter_sse_chunks_does_not_swallow_cancellation() -> None:
    """CancelledError must propagate untouched — it is not a provider
    error and the narrow except tuple must not catch it."""
    stream = _RaisingStream(good=[], exc=asyncio.CancelledError())
    resp = _streaming_response(stream)
    with pytest.raises(asyncio.CancelledError):
        async for _chunk in iter_sse_chunks(resp, provider="test"):
            pass
