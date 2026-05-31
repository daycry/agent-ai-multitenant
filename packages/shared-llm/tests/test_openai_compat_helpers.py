"""Unit tests for the OpenAI-compat parsing helpers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
from shared_llm.exceptions import AuthError, ProviderError, RateLimitError
from shared_llm.providers._openai_compat import (
    check_status,
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
