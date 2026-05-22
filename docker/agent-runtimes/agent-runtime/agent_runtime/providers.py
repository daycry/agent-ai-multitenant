"""Real ModelClient implementations (Plan 02 Fase G / task_02_32).

The LangGraph loop talks to an LLM only through the `ModelClient`
protocol (ADR 0013): `decide()` returns one decision, `review()` one
verdict. This module plugs the three provider paths of CLAUDE.md §9
behind that protocol:

  * `LiteLLMModelClient`   — the LiteLLM gateway, OpenAI-compatible.
  * `CopilotModelClient`   — GitHub Copilot, OpenAI-compatible, with a
                             JWT minted from a GitHub OAuth token.
  * `ClaudeSDKModelClient` — the Claude Agent SDK, run one turn per
                             `decide()` (ADR 0018, option A) so the
                             LangGraph loop — not the SDK's own loop —
                             stays in charge.

The HTTP transport (`httpx.Client`) and the SDK `query` function are
injectable, so the tests exercise every parser with no network and no
real credentials.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx

from agent_runtime.model import (
    DecisionKind,
    ModelClient,
    ModelDecision,
    ModelResponse,
    ReviewResponse,
)

# ---------------------------------------------------------------------------
# Prompts + message construction
# ---------------------------------------------------------------------------
_DECIDE_SYSTEM = (
    "You are an autonomous agent executing one task inside a loop. On each "
    "turn either call exactly one tool to make progress, or — when the task "
    "is complete — reply with the final result as plain text and no tool call."
)
_REVIEW_SYSTEM = (
    "You are a reviewer. Decide whether the candidate output satisfies the "
    "task. Reply with a JSON object and nothing else: "
    '{"passed": <true|false>, "feedback": "<short reason>"}.'
)

# How many context fragments to feed the model — the loop's context list
# grows unbounded; the tail is the relevant part.
_CONTEXT_WINDOW = 8


def _decide_messages(state: dict[str, Any]) -> list[dict[str, str]]:
    """Turn the agent-loop state into the chat messages for a decision."""
    task = state.get("task") or {}
    lines = [f"Task: {task.get('title', '')}".strip()]
    if task.get("description"):
        lines.append(str(task["description"]))
    context = state.get("context") or []
    if context:
        lines.append("Context so far:")
        lines += [f"- {json.dumps(item, default=str)}" for item in context[-_CONTEXT_WINDOW:]]
    observation = state.get("last_observation")
    if observation:
        lines.append(f"Last observation: {json.dumps(observation, default=str)}")
    return [
        {"role": "system", "content": _DECIDE_SYSTEM},
        {"role": "user", "content": "\n".join(line for line in lines if line)},
    ]


def _review_messages(state: dict[str, Any]) -> list[dict[str, str]]:
    """Turn the agent-loop state into the chat messages for a review."""
    task = state.get("task") or {}
    body = (
        f"Task: {task.get('title', '')}\n{task.get('description', '')}\n\n"
        f"Candidate output:\n{state.get('output') or ''}"
    )
    return [
        {"role": "system", "content": _REVIEW_SYSTEM},
        {"role": "user", "content": body},
    ]


# ---------------------------------------------------------------------------
# Response parsing — shared by every provider
# ---------------------------------------------------------------------------
def _loads_args(raw: Any) -> dict[str, Any]:
    """Parse a tool-call `arguments` payload into a dict, leniently."""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    with contextlib.suppress(json.JSONDecodeError, TypeError):
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    return {}


def _extract_json(text: str) -> Any:
    """Best-effort: parse `text` as JSON, or the first `{...}` span in it."""
    with contextlib.suppress(json.JSONDecodeError):
        return json.loads(text)
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        with contextlib.suppress(json.JSONDecodeError):
            return json.loads(text[start : end + 1])
    return None


def _parse_verdict(content: str) -> tuple[bool, str]:
    """Turn a review reply into a (passed, feedback) pair.

    Prefers the documented JSON object; falls back to keyword sniffing
    so a model that ignores the format instruction still yields a verdict.
    """
    obj = _extract_json(content.strip())
    if isinstance(obj, dict) and "passed" in obj:
        return bool(obj["passed"]), str(obj.get("feedback", ""))
    lowered = content.lower()
    passed = "fail" not in lowered and ("pass" in lowered or "approve" in lowered)
    return passed, content.strip()


def _decision_from_chat(raw: dict[str, Any], model: str) -> ModelResponse:
    """Parse an OpenAI-compatible `/chat/completions` reply into a decision."""
    message = raw["choices"][0]["message"]
    tool_calls = message.get("tool_calls") or []
    if tool_calls:
        fn = tool_calls[0]["function"]
        decision = ModelDecision(
            kind=DecisionKind.ACT,
            tool=fn.get("name"),
            tool_args=_loads_args(fn.get("arguments")),
            rationale=message.get("content") or "",
        )
    else:
        decision = ModelDecision(kind=DecisionKind.FINISH, output=message.get("content") or "")
    return _model_response(decision, raw, model)


def _review_from_chat(raw: dict[str, Any], model: str) -> ReviewResponse:
    """Parse an OpenAI-compatible reply into a review verdict."""
    content = raw["choices"][0]["message"].get("content") or ""
    passed, feedback = _parse_verdict(content)
    usage = raw.get("usage") or {}
    return ReviewResponse(
        passed=passed,
        feedback=feedback,
        model=raw.get("model", model),
        tokens_in=int(usage.get("prompt_tokens", 0)),
        tokens_out=int(usage.get("completion_tokens", 0)),
        cost_usd=float(usage.get("cost", 0.0) or 0.0),
    )


def _model_response(decision: ModelDecision, raw: dict[str, Any], model: str) -> ModelResponse:
    usage = raw.get("usage") or {}
    return ModelResponse(
        decision=decision,
        model=raw.get("model", model),
        tokens_in=int(usage.get("prompt_tokens", 0)),
        tokens_out=int(usage.get("completion_tokens", 0)),
        cost_usd=float(usage.get("cost", 0.0) or 0.0),
    )


# ---------------------------------------------------------------------------
# OpenAI-compatible client — LiteLLM and Copilot both speak this
# ---------------------------------------------------------------------------
class OpenAICompatModelClient:
    """A `ModelClient` over an OpenAI-compatible `/chat/completions` API."""

    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        api_key: str | None = None,
        token_provider: Callable[[], str] | None = None,
        extra_headers: dict[str, str] | None = None,
        tools: list[dict[str, Any]] | None = None,
        http_client: httpx.Client | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        # A dynamic bearer source (Copilot mints a short-lived JWT); when
        # absent the static api_key is used.
        self._token_provider = token_provider
        self._extra_headers = dict(extra_headers or {})
        self._tools = tools
        self._timeout = timeout
        self._http = http_client

    def _client(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(timeout=self._timeout)
        return self._http

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", **self._extra_headers}
        headers["X-Request-Id"] = str(uuid.uuid4())
        token = self._token_provider() if self._token_provider is not None else self._api_key
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _post_chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._client().post(
            f"{self._base_url}/chat/completions", json=payload, headers=self._headers()
        )
        response.raise_for_status()
        return response.json()  # type: ignore[no-any-return]

    def decide(self, state: dict[str, Any]) -> ModelResponse:
        payload: dict[str, Any] = {"model": self.model, "messages": _decide_messages(state)}
        if self._tools:
            payload["tools"] = self._tools
        return _decision_from_chat(self._post_chat(payload), self.model)

    def review(self, state: dict[str, Any]) -> ReviewResponse:
        payload = {"model": self.model, "messages": _review_messages(state)}
        return _review_from_chat(self._post_chat(payload), self.model)


class LiteLLMModelClient(OpenAICompatModelClient):
    """The LiteLLM gateway — the primary provider path (CLAUDE.md §9)."""

    def __init__(
        self,
        *,
        model: str,
        base_url: str = "http://litellm:4000",
        api_key: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        super().__init__(
            model=model,
            base_url=base_url,
            api_key=api_key,
            tools=tools,
            http_client=http_client,
        )


# ---------------------------------------------------------------------------
# GitHub Copilot — OpenAI-compatible, but the bearer is a minted JWT
# ---------------------------------------------------------------------------
# Headers that make the request look like VS Code Copilot Chat — GitHub's
# internal endpoint rejects anything else (see the Fase G reference doc).
COPILOT_HEADERS: dict[str, str] = {
    "User-Agent": "GitHubCopilotChat/0.24.0",
    "Editor-Version": "vscode/1.96.2",
    "Editor-Plugin-Version": "copilot-chat/0.24.0",
    "Copilot-Integration-Id": "vscode-chat",
    "Openai-Organization": "github-copilot",
    "Openai-Intent": "conversation-panel",
}
_COPILOT_API = "https://api.githubcopilot.com"
_COPILOT_TOKEN_URL = "https://api.github.com/copilot_internal/v2/token"
# Re-mint the JWT once it has under this many seconds of life left.
_JWT_REFRESH_MARGIN_S = 60.0


class CopilotAuth:
    """Exchanges a GitHub OAuth token for a short-lived Copilot JWT.

    The OAuth token (`gho_*` / `ghu_*`) is never sent to the model
    endpoint — it is traded at `copilot_internal/v2/token` for a JWT
    (~30 min TTL) that is cached and re-minted just before it expires.
    """

    def __init__(self, oauth_token: str, *, http_client: httpx.Client | None = None) -> None:
        self._oauth_token = oauth_token
        self._http = http_client
        self._jwt: str | None = None
        self._expires_at = 0.0

    def _client(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(timeout=30.0)
        return self._http

    def jwt(self) -> str:
        """A valid Copilot JWT — cached, re-minted when near expiry."""
        now = time.time()
        if self._jwt is not None and now < self._expires_at - _JWT_REFRESH_MARGIN_S:
            return self._jwt
        response = self._client().get(
            _COPILOT_TOKEN_URL,
            headers={
                "Authorization": f"token {self._oauth_token}",
                "Accept": "application/json",
                "User-Agent": COPILOT_HEADERS["User-Agent"],
            },
        )
        response.raise_for_status()
        data = response.json()
        self._jwt = str(data["token"])
        self._expires_at = float(data.get("expires_at", now + 1500.0))
        return self._jwt


class CopilotModelClient(OpenAICompatModelClient):
    """GitHub Copilot behind the `ModelClient` protocol."""

    def __init__(
        self,
        *,
        model: str,
        auth: CopilotAuth,
        tools: list[dict[str, Any]] | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.auth = auth
        super().__init__(
            model=model,
            base_url=_COPILOT_API,
            token_provider=auth.jwt,
            extra_headers=COPILOT_HEADERS,
            tools=tools,
            http_client=http_client,
        )


# ---------------------------------------------------------------------------
# Claude Agent SDK — one turn per decide() (ADR 0018, option A)
# ---------------------------------------------------------------------------
# A `query`-shaped callable: (prompt, options) -> async iterator of messages.
SdkQuery = Callable[..., AsyncIterator[Any]]


async def _drain(query_fn: SdkQuery, prompt: str, options: Any) -> list[Any]:
    """Collect every message the SDK `query` yields for one turn."""
    messages: list[Any] = []
    async for message in query_fn(prompt=prompt, options=options):
        messages.append(message)
    return messages


def _sdk_text_and_tool(messages: list[Any]) -> tuple[list[str], Any, dict[str, Any], float]:
    """Walk SDK messages: collect text, the first tool use, usage, cost.

    Blocks are duck-typed — a `ToolUseBlock` has `name`/`input`, a
    `TextBlock` has `text`, a `ResultMessage` has `total_cost_usd`."""
    text_parts: list[str] = []
    tool_use: Any = None
    usage: dict[str, Any] = {}
    cost = 0.0
    for message in messages:
        content = getattr(message, "content", None)
        if isinstance(content, list):
            for block in content:
                if hasattr(block, "name") and hasattr(block, "input"):
                    if tool_use is None:
                        tool_use = block
                elif hasattr(block, "text"):
                    text_parts.append(block.text)
        if hasattr(message, "total_cost_usd"):
            cost = float(message.total_cost_usd or 0.0)
            usage = getattr(message, "usage", {}) or {}
    return text_parts, tool_use, usage, cost


class ClaudeSDKModelClient:
    """The Claude Agent SDK as a single-decision `ModelClient`.

    Each `decide()` runs exactly one SDK turn (`max_turns=1`): the
    LangGraph loop drives the iterations, the SDK contributes one
    decision. Authentication is the operator's Claude Pro/Max
    subscription — no API key (see the Fase G reference doc).
    """

    def __init__(
        self,
        *,
        model: str,
        query_fn: SdkQuery | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_turns: int = 1,
    ) -> None:
        self.model = model
        self._query_fn = query_fn
        self._tools = tools
        self._max_turns = max_turns

    def _query(self) -> SdkQuery:
        if self._query_fn is not None:
            return self._query_fn
        from claude_agent_sdk import query  # lazy — optional dependency

        return query  # type: ignore[no-any-return]

    def _options(self) -> Any:
        # An injected query (the tests) ignores options; the real SDK
        # needs ClaudeAgentOptions — built lazily so the package stays
        # an optional dependency.
        if self._query_fn is not None:
            return None
        from claude_agent_sdk import ClaudeAgentOptions

        return ClaudeAgentOptions(
            model=self.model,
            max_turns=self._max_turns,
            tools=[],
            strict_mcp_config=True,
            setting_sources=[],
            permission_mode="bypassPermissions",
        )

    def decide(self, state: dict[str, Any]) -> ModelResponse:
        messages = asyncio.run(
            _drain(
                self._query(), "\n\n".join(_message_texts(_decide_messages(state))), self._options()
            )
        )
        text_parts, tool_use, usage, cost = _sdk_text_and_tool(messages)
        if tool_use is not None:
            decision = ModelDecision(
                kind=DecisionKind.ACT,
                tool=tool_use.name,
                tool_args=dict(tool_use.input or {}),
            )
        else:
            decision = ModelDecision(kind=DecisionKind.FINISH, output="".join(text_parts))
        return ModelResponse(
            decision=decision,
            model=self.model,
            tokens_in=int(usage.get("input_tokens", 0)),
            tokens_out=int(usage.get("output_tokens", 0)),
            cost_usd=cost,
        )

    def review(self, state: dict[str, Any]) -> ReviewResponse:
        messages = asyncio.run(
            _drain(
                self._query(), "\n\n".join(_message_texts(_review_messages(state))), self._options()
            )
        )
        text_parts, _tool, usage, cost = _sdk_text_and_tool(messages)
        passed, feedback = _parse_verdict("".join(text_parts))
        return ReviewResponse(
            passed=passed,
            feedback=feedback,
            model=self.model,
            tokens_in=int(usage.get("input_tokens", 0)),
            tokens_out=int(usage.get("output_tokens", 0)),
            cost_usd=cost,
        )


def _message_texts(messages: list[dict[str, str]]) -> list[str]:
    """Flatten chat messages to plain text — the SDK takes a string prompt."""
    return [f"{m['role'].upper()}: {m['content']}" for m in messages]


# ---------------------------------------------------------------------------
# Factory — model_from_spec delegates here for non-scripted kinds
# ---------------------------------------------------------------------------
def build_provider_client(spec: dict[str, Any]) -> ModelClient:
    """Build a real `ModelClient` from a JSON model spec.

    Kinds: `litellm`, `copilot`, `claude_sdk` (alias `claude`). The
    `scripted` kind is handled by `agent_runtime.model.model_from_spec`.
    """
    kind = spec.get("kind")
    model = spec.get("model", "")
    tools = spec.get("tools")
    if kind == "litellm":
        return LiteLLMModelClient(
            model=model,
            base_url=spec.get("base_url", "http://litellm:4000"),
            api_key=spec.get("api_key"),
            tools=tools,
        )
    if kind == "copilot":
        return CopilotModelClient(
            model=model,
            auth=CopilotAuth(spec["oauth_token"]),
            tools=tools,
        )
    if kind in ("claude_sdk", "claude"):
        return ClaudeSDKModelClient(
            model=model,
            tools=tools,
            max_turns=int(spec.get("max_turns", 1)),
        )
    raise ValueError(f"unknown provider kind: {kind!r}")
