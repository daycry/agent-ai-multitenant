"""Unit tests for the CopilotProvider — device flow, JWT mint, chat."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

import httpx
import pytest
from shared_llm.exceptions import AuthError, ProviderError
from shared_llm.providers import CopilotProvider
from shared_llm.types import Message

_TOKEN_URL = "https://api.github.com/copilot_internal/v2/token"
_CHAT_URL = "https://api.githubcopilot.com/chat/completions"


class _RaisingStream(httpx.AsyncByteStream):
    """SSE body that yields good lines then raises mid-stream."""

    def __init__(self, *, good: list[bytes], exc: BaseException) -> None:
        self._good = good
        self._exc = exc

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._good:
            yield chunk
        raise self._exc

    async def aclose(self) -> None:
        return None


def _sse_body(*deltas: str) -> bytes:
    lines = [('data: {"choices":[{"delta":{"content":"' + d + '"}}]}\n\n').encode() for d in deltas]
    lines.append(b"data: [DONE]\n\n")
    return b"".join(lines)


def _mint_response() -> httpx.Response:
    return httpx.Response(200, json={"token": "jwt-xyz", "expires_at": time.time() + 1500})


def _mock_client(handler) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        timeout=5.0,
    )


@pytest.mark.asyncio
async def test_start_device_flow_returns_user_code() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert str(req.url) == "https://github.com/login/device/code"
        return httpx.Response(
            200,
            json={
                "device_code": "dev-abc",
                "user_code": "AB12-CD34",
                "verification_uri": "https://github.com/login/device",
                "expires_in": 900,
                "interval": 5,
            },
        )

    p = CopilotProvider(http_client=_mock_client(handler))
    info = await p.start_device_flow()
    assert info.user_code == "AB12-CD34"
    assert info.verification_uri == "https://github.com/login/device"


@pytest.mark.asyncio
async def test_complete_mints_jwt_and_posts_chat() -> None:
    """A fresh provider with a github_token mints a JWT, then POSTs
    chat to Copilot with the Bearer JWT + editor headers."""
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        calls.append(url)
        if url == "https://api.github.com/copilot_internal/v2/token":
            assert req.headers["Authorization"] == "token gho_test"
            return httpx.Response(
                200,
                json={"token": "jwt-xyz", "expires_at": time.time() + 1500},
            )
        if url == "https://api.githubcopilot.com/chat/completions":
            assert req.headers["Authorization"] == "Bearer jwt-xyz"
            # Editor headers must be present.
            assert req.headers["Editor-Version"].startswith("vscode/")
            assert req.headers["Copilot-Integration-Id"] == "vscode-chat"
            return httpx.Response(
                200,
                json={
                    "model": "gpt-4o",
                    "choices": [{"message": {"role": "assistant", "content": "hi"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 2},
                },
            )
        raise AssertionError(f"unexpected url {url}")

    p = CopilotProvider(github_token="gho_test", http_client=_mock_client(handler))
    resp = await p.complete([Message(role="user", content="hi")])
    assert resp.content == "hi"
    # The JWT mint hit happens before chat.
    assert calls[0].endswith("/copilot_internal/v2/token")
    assert calls[1].endswith("/chat/completions")


@pytest.mark.asyncio
async def test_complete_without_github_token_raises_auth_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise AssertionError("must not call HTTP without a token")

    p = CopilotProvider(http_client=_mock_client(handler))
    with pytest.raises(AuthError, match="no GitHub token"):
        await p.complete([Message(role="user", content="hi")])


@pytest.mark.asyncio
async def test_jwt_is_cached_until_close_to_expiry() -> None:
    """A second call within the JWT's validity must NOT re-hit the
    mint endpoint."""
    mint_calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal mint_calls
        url = str(req.url)
        if url.endswith("/copilot_internal/v2/token"):
            mint_calls += 1
            return httpx.Response(
                200,
                # 30 min TTL; refresh margin is 60s, so we have ~29 min.
                json={"token": "jwt-xyz", "expires_at": time.time() + 1800},
            )
        return httpx.Response(
            200,
            json={
                "model": "gpt-4o",
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {},
            },
        )

    p = CopilotProvider(github_token="gho_test", http_client=_mock_client(handler))
    await p.complete([Message(role="user", content="hi")])
    await p.complete([Message(role="user", content="hi again")])
    assert mint_calls == 1, "JWT should be minted only once when still valid"


@pytest.mark.asyncio
async def test_jwt_is_remined_when_inside_refresh_margin() -> None:
    """When the JWT has under 60s of life left, the next call mints again."""
    mint_calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal mint_calls
        url = str(req.url)
        if url.endswith("/copilot_internal/v2/token"):
            mint_calls += 1
            return httpx.Response(
                200,
                # Expires in 30s — strictly within the 60s refresh margin.
                json={"token": f"jwt-{mint_calls}", "expires_at": time.time() + 30},
            )
        return httpx.Response(
            200,
            json={
                "model": "gpt-4o",
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {},
            },
        )

    p = CopilotProvider(github_token="gho_test", http_client=_mock_client(handler))
    await p.complete([Message(role="user", content="hi")])
    await p.complete([Message(role="user", content="hi again")])
    assert mint_calls == 2, "JWT inside the refresh margin should be re-minted"


# ---------------------------------------------------------------------------
# stream() — JWT 401 retry parity with complete(), mid-stream error wrapping
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_stream_mints_jwt_and_yields_deltas() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if url == _TOKEN_URL:
            return _mint_response()
        assert url == _CHAT_URL
        assert req.headers["Authorization"] == "Bearer jwt-xyz"
        return httpx.Response(
            200, content=_sse_body("he", "llo"), headers={"Content-Type": "text/event-stream"}
        )

    p = CopilotProvider(github_token="gho_test", http_client=_mock_client(handler))
    chunks = [c async for c in p.stream([Message(role="user", content="hi")])]
    assert "".join(c.delta for c in chunks if not c.done) == "hello"
    assert chunks[-1].done is True


@pytest.mark.asyncio
async def test_stream_retries_once_on_401_after_reminting_jwt() -> None:
    """Parity with complete(): a 401 on the first stream attempt drops the
    cached JWT, re-mints, and retries the stream exactly once."""
    mint_calls = 0
    chat_calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal mint_calls, chat_calls
        url = str(req.url)
        if url == _TOKEN_URL:
            mint_calls += 1
            return httpx.Response(
                200, json={"token": f"jwt-{mint_calls}", "expires_at": time.time() + 1500}
            )
        chat_calls += 1
        # First chat attempt: stale JWT -> 401. Second attempt: success.
        if chat_calls == 1:
            assert req.headers["Authorization"] == "Bearer jwt-1"
            return httpx.Response(401, text="expired jwt")
        assert req.headers["Authorization"] == "Bearer jwt-2"
        return httpx.Response(
            200, content=_sse_body("ok"), headers={"Content-Type": "text/event-stream"}
        )

    p = CopilotProvider(github_token="gho_test", http_client=_mock_client(handler))
    chunks = [c async for c in p.stream([Message(role="user", content="hi")])]
    assert "".join(c.delta for c in chunks if not c.done) == "ok"
    assert chat_calls == 2, "stream() must retry the chat call once on 401"
    assert mint_calls == 2, "the retry must re-mint the JWT"


@pytest.mark.asyncio
async def test_stream_does_not_retry_more_than_once_on_persistent_401() -> None:
    """If the second attempt is also 401, stream() raises AuthError rather
    than looping forever."""
    chat_calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal chat_calls
        if str(req.url) == _TOKEN_URL:
            return _mint_response()
        chat_calls += 1
        return httpx.Response(401, text="still unauthorised")

    p = CopilotProvider(github_token="gho_test", http_client=_mock_client(handler))
    with pytest.raises(AuthError):
        async for _c in p.stream([Message(role="user", content="hi")]):
            pass
    assert chat_calls == 2, "exactly one retry, then surface the error"


@pytest.mark.asyncio
async def test_stream_403_is_provider_error_no_retry() -> None:
    """A 403 is a permission problem, not stale-auth: no retry, and it
    surfaces as ProviderError (not AuthError)."""
    chat_calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal chat_calls
        if str(req.url) == _TOKEN_URL:
            return _mint_response()
        chat_calls += 1
        return httpx.Response(403, text="forbidden")

    p = CopilotProvider(github_token="gho_test", http_client=_mock_client(handler))
    with pytest.raises(ProviderError) as info:
        async for _c in p.stream([Message(role="user", content="hi")]):
            pass
    assert not isinstance(info.value, AuthError)
    assert info.value.status_code == 403
    assert chat_calls == 1, "403 must not trigger the 401 retry path"


@pytest.mark.asyncio
async def test_stream_midstream_error_becomes_provider_error() -> None:
    """A connection drop mid-body is wrapped into ProviderError."""

    def handler(req: httpx.Request) -> httpx.Response:
        if str(req.url) == _TOKEN_URL:
            return _mint_response()
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            stream=_RaisingStream(
                good=[b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'],
                exc=httpx.ReadError("connection reset"),
            ),
        )

    p = CopilotProvider(github_token="gho_test", http_client=_mock_client(handler))
    deltas: list[str] = []
    with pytest.raises(ProviderError, match="stream interrupted"):
        async for c in p.stream([Message(role="user", content="hi")]):
            if c.delta:
                deltas.append(c.delta)
    assert deltas == ["partial"]
