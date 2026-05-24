"""Unit tests for the CopilotProvider — device flow, JWT mint, chat."""

from __future__ import annotations

import time

import httpx
import pytest
from shared_llm.exceptions import AuthError
from shared_llm.providers import CopilotProvider
from shared_llm.types import Message


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
