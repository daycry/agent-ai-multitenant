"""GitHub Copilot via OAuth Device Flow + minted JWT (ADR 0021).

Three-step authentication:

  1. **Device Flow**: `start_device_flow()` returns a `user_code` for
     the operator to enter at `verification_uri`. `poll_device_flow()`
     polls until the operator authorises and returns the long-lived
     GitHub OAuth token. `authenticate_interactive()` wraps both.
  2. **JWT mint**: the OAuth token is exchanged at
     `api.github.com/copilot_internal/v2/token` for a short-lived JWT
     (~30 min TTL). The provider re-mints it with 60 seconds of
     margin before expiry — never on the back of a 401.
  3. **Chat**: `complete()` / `stream()` use the JWT against
     `api.githubcopilot.com/chat/completions` with the editor headers
     GitHub's internal endpoint expects.

WARNING: Copilot has no public API for third parties. The endpoints
used here are the ones the official VS Code plugin uses; they can
change without notice and using them outside the IDE may violate
GitHub's Terms of Service. The operator opts in by configuring this
provider (see ADR 0021 and the docs/context/github-copilot-* note).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from shared_llm.exceptions import AuthError, ProviderError
from shared_llm.providers._openai_compat import (
    check_status,
    iter_sse_chunks,
    parse_chat_completion,
    to_openai_messages,
)
from shared_llm.types import CompletionResponse, Message, StreamChunk

# The VS Code Copilot plugin's public OAuth client id. Using your own
# would require registering a GitHub App with Copilot scope, which
# GitHub does not grant to third-party apps.
VSCODE_CLIENT_ID = "01ab8ac9400c4e429b23"

# Headers that make every request look like VS Code Copilot Chat.
EDITOR_HEADERS: dict[str, str] = {
    "User-Agent": "GitHubCopilotChat/0.24.0",
    "Editor-Version": "vscode/1.96.2",
    "Editor-Plugin-Version": "copilot-chat/0.24.0",
    "Copilot-Integration-Id": "vscode-chat",
}

_DEVICE_CODE_URL = "https://github.com/login/device/code"
_OAUTH_TOKEN_URL = "https://github.com/login/oauth/access_token"
_COPILOT_TOKEN_URL = "https://api.github.com/copilot_internal/v2/token"
_COPILOT_API = "https://api.githubcopilot.com"

# Re-mint the JWT once it has under this many seconds of life left.
# 120s (was 60s) buys headroom on high-latency / proxied networks where
# the mint round-trip itself can take 10-30s: with the old 60s margin a
# token that had exactly 60s left when the call started could expire
# in-flight (llm-providers-7). Override per-instance via the constructor.
_JWT_REFRESH_MARGIN_S = 120.0


@dataclass
class DeviceCodeInfo:
    """Returned by `start_device_flow` — what the UI shows the operator."""

    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int


@dataclass
class DevicePollResult:
    """Outcome of a SINGLE device-flow poll attempt (`poll_device_flow_once`).

    Unlike :meth:`CopilotProvider.poll_device_flow`, which blocks an event
    loop until the operator authorises (a fine fit for a CLI), this models
    one non-blocking poll so a web backend can be driven by the browser:
    each HTTP poll request maps to one attempt and the UI keeps polling
    while ``status`` is ``pending`` / ``slow_down``.

    ``status`` is one of:

      * ``authorized``  — the operator approved; ``token`` carries the
                          long-lived GitHub OAuth token (the ONLY status
                          that ever carries a token).
      * ``pending``     — still waiting (GitHub's ``authorization_pending``);
                          poll again after ``interval`` seconds.
      * ``slow_down``   — poll too fast; GitHub asks us to back off. The
                          caller should add 5s to its interval.
      * ``expired``     — the device code expired (``expired_token``).
      * ``denied``      — the operator declined (``access_denied``).
    """

    status: str
    token: str | None = None
    # GitHub's suggested new interval after a slow_down (interval + 5s).
    interval: int | None = None


# Single-poll status constants (mirror GitHub's device-flow `error` codes).
POLL_AUTHORIZED = "authorized"
POLL_PENDING = "pending"
POLL_SLOW_DOWN = "slow_down"
POLL_EXPIRED = "expired"
POLL_DENIED = "denied"


class CopilotProvider:
    name = "github_copilot"

    def __init__(
        self,
        *,
        github_token: str | None = None,
        timeout: float = 60.0,
        http_client: httpx.AsyncClient | None = None,
        jwt_refresh_margin_s: float = _JWT_REFRESH_MARGIN_S,
    ) -> None:
        """Initialise the provider.

        `github_token` is the long-lived OAuth token (`gho_*` / `ghu_*`)
        the device flow returns. If you don't have one yet, leave it
        None and call `authenticate_interactive()` first.

        `jwt_refresh_margin_s` is how early (seconds before expiry) the
        minted Copilot JWT is pre-emptively re-minted. Defaults to 120s;
        raise it further on very high-latency links.
        """
        self._github_token = github_token
        self._jwt_refresh_margin_s = jwt_refresh_margin_s
        self._jwt: str | None = None
        self._jwt_expires_at = 0.0
        if http_client is not None:
            self._client = http_client
            self._owns_client = False
        else:
            self._client = httpx.AsyncClient(timeout=timeout)
            self._owns_client = True

    # ------------------------------------------------------------------
    # Device flow — interactive auth bootstrap
    # ------------------------------------------------------------------
    async def start_device_flow(self) -> DeviceCodeInfo:
        """Step 1 — request the user_code the operator types into GitHub."""
        resp = await self._client.post(
            _DEVICE_CODE_URL,
            headers={"Accept": "application/json", **EDITOR_HEADERS},
            data={"client_id": VSCODE_CLIENT_ID, "scope": "read:user"},
        )
        if resp.status_code >= 400:
            raise AuthError(f"device/code failed: {resp.text}")
        d = resp.json()
        return DeviceCodeInfo(
            device_code=d["device_code"],
            user_code=d["user_code"],
            verification_uri=d["verification_uri"],
            expires_in=int(d["expires_in"]),
            interval=int(d["interval"]),
        )

    async def poll_device_flow(self, info: DeviceCodeInfo) -> str:
        """Step 2-3 — poll until the operator authorises; returns the
        long-lived GitHub OAuth token and stores it on the provider."""
        deadline = time.time() + info.expires_in
        interval = info.interval
        while time.time() < deadline:
            await asyncio.sleep(interval)
            resp = await self._client.post(
                _OAUTH_TOKEN_URL,
                headers={"Accept": "application/json", **EDITOR_HEADERS},
                data={
                    "client_id": VSCODE_CLIENT_ID,
                    "device_code": info.device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
            )
            d = resp.json()
            if "access_token" in d:
                token = str(d["access_token"])
                self._github_token = token
                return token
            err = d.get("error")
            if err == "authorization_pending":
                continue
            if err == "slow_down":
                interval += 5
                continue
            if err in ("expired_token", "access_denied"):
                raise AuthError(f"Device flow aborted: {err}")
        raise AuthError("Device flow expired without authorisation")

    async def poll_device_flow_once(
        self, device_code: str, *, interval: int = 5
    ) -> DevicePollResult:
        """Single, non-blocking device-flow poll (the web-friendly variant).

        Hits GitHub's OAuth token endpoint exactly ONCE with *device_code*
        and classifies the reply into a :class:`DevicePollResult` instead of
        looping until a deadline like :meth:`poll_device_flow`. A web backend
        calls this once per browser poll so it never blocks a worker for the
        whole authorisation window.

        On ``authorized`` the long-lived GitHub OAuth token is returned in
        ``result.token`` AND stored on the provider (so a subsequent
        ``_ensure_jwt`` works). Pending/slow_down are non-error states; the
        caller keeps polling. Expired/denied are terminal. ``interval`` is
        only used to compute the suggested back-off on ``slow_down``.
        """
        resp = await self._client.post(
            _OAUTH_TOKEN_URL,
            headers={"Accept": "application/json", **EDITOR_HEADERS},
            data={
                "client_id": VSCODE_CLIENT_ID,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
        )
        d = resp.json()
        if "access_token" in d:
            token = str(d["access_token"])
            self._github_token = token
            return DevicePollResult(status=POLL_AUTHORIZED, token=token)
        err = d.get("error")
        if err == "authorization_pending":
            return DevicePollResult(status=POLL_PENDING)
        if err == "slow_down":
            return DevicePollResult(status=POLL_SLOW_DOWN, interval=interval + 5)
        if err == "expired_token":
            return DevicePollResult(status=POLL_EXPIRED)
        if err == "access_denied":
            return DevicePollResult(status=POLL_DENIED)
        # Any other error code is an auth failure we cannot recover from.
        raise AuthError(f"Device flow poll failed: {err or resp.text}")

    async def authenticate_interactive(
        self,
        on_user_code: Callable[[DeviceCodeInfo], Awaitable[None]] | None = None,
    ) -> str:
        """Helper that runs both steps. `on_user_code` is your hook to
        show the user_code in whatever UI you have (CLI, web banner, …)."""
        info = await self.start_device_flow()
        if on_user_code is not None:
            await on_user_code(info)
        return await self.poll_device_flow(info)

    # ------------------------------------------------------------------
    # JWT mint — refreshed with margin, never on the back of a 401
    # ------------------------------------------------------------------
    async def _ensure_jwt(self) -> str:
        now = time.time()
        if self._jwt is not None and now < self._jwt_expires_at - self._jwt_refresh_margin_s:
            return self._jwt
        if not self._github_token:
            raise AuthError("no GitHub token — run authenticate_interactive() first")
        resp = await self._client.get(
            _COPILOT_TOKEN_URL,
            headers={
                "Authorization": f"token {self._github_token}",
                "Accept": "application/json",
                "User-Agent": EDITOR_HEADERS["User-Agent"],
            },
        )
        if resp.status_code == 401:
            raise AuthError("GitHub token invalid or lacks Copilot access")
        if resp.status_code >= 400:
            raise ProviderError(
                f"copilot_internal/v2/token: {resp.text}",
                status_code=resp.status_code,
            )
        d = resp.json()
        self._jwt = str(d["token"])
        self._jwt_expires_at = float(d.get("expires_at", now + 1500.0))
        return self._jwt

    async def _chat_headers(self) -> dict[str, str]:
        jwt = await self._ensure_jwt()
        return {
            "Authorization": f"Bearer {jwt}",
            "Content-Type": "application/json",
            "Openai-Intent": "conversation-panel",
            "Openai-Organization": "github-copilot",
            **EDITOR_HEADERS,
        }

    # ------------------------------------------------------------------
    # LLMProvider Protocol
    # ------------------------------------------------------------------
    async def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> CompletionResponse:
        model_id = model or "gpt-4o"
        body: dict[str, Any] = {
            "model": model_id,
            "messages": to_openai_messages(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
            **kwargs,
        }
        if tools:
            body["tools"] = tools
        resp = await self._client.post(
            f"{_COPILOT_API}/chat/completions",
            headers=await self._chat_headers(),
            json=body,
        )
        # 401 here means the JWT expired between mint and call — re-mint
        # once and retry. Anything else is a real provider error.
        if resp.status_code == 401:
            self._jwt = None
            resp = await self._client.post(
                f"{_COPILOT_API}/chat/completions",
                headers=await self._chat_headers(),
                json=body,
            )
        check_status(resp, provider=self.name)
        return parse_chat_completion(resp.json(), provider=self.name, fallback_model=model_id)

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        model_id = model or "gpt-4o"
        body: dict[str, Any] = {
            "model": model_id,
            "messages": to_openai_messages(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
            **kwargs,
        }
        if tools:
            body["tools"] = tools
        # Same 401 handling as complete(): a 401 on the first attempt
        # means the JWT expired between mint and call. Drop the cached
        # JWT, re-mint, and retry the stream exactly once. We must close
        # the first response body before re-opening, so the retry happens
        # outside the first `async with` block.
        async with self._client.stream(
            "POST",
            f"{_COPILOT_API}/chat/completions",
            headers=await self._chat_headers(),
            json=body,
        ) as resp:
            if resp.status_code != 401:
                check_status(resp, provider=self.name)
                async for chunk in iter_sse_chunks(resp, provider=self.name):
                    yield chunk
                return
            self._jwt = None
        # Retry once with a freshly minted JWT.
        async with self._client.stream(
            "POST",
            f"{_COPILOT_API}/chat/completions",
            headers=await self._chat_headers(),
            json=body,
        ) as retry_resp:
            check_status(retry_resp, provider=self.name)
            async for chunk in iter_sse_chunks(retry_resp, provider=self.name):
                yield chunk

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


__all__ = [
    "EDITOR_HEADERS",
    "POLL_AUTHORIZED",
    "POLL_DENIED",
    "POLL_EXPIRED",
    "POLL_PENDING",
    "POLL_SLOW_DOWN",
    "VSCODE_CLIENT_ID",
    "CopilotProvider",
    "DeviceCodeInfo",
    "DevicePollResult",
]
