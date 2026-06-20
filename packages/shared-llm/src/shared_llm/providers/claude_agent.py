"""Claude Agent SDK wrapper (ADR 0021).

The SDK is not just a chat client — it runs an agent loop with tool
use, filesystem access, MCP, sub-agents, etc. Two modes are exposed
here:

  * **`complete()` / `stream()`** — fits the `LLMProvider` Protocol.
    Runs the SDK with `max_turns=1`, no tools, no host-side state.
    Use when you want "Claude as a chat backend".
  * **`run_agent()`** — escape hatch that yields `AgentRunEvent`s
    (typed wrapper around the SDK's heterogeneous messages). Use when
    you want the SDK's full agent capabilities.

`claude-agent-sdk` is an optional dependency (extra `claude` in the
package's pyproject). The provider only imports it when actually used,
so a deployment without Claude doesn't drag the SDK + the Node CLI in.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Sequence
from typing import Any

from shared_llm.exceptions import AuthError, ProviderError
from shared_llm.types import (
    AgentRunEvent,
    CompletionResponse,
    Message,
    StreamChunk,
    Usage,
)


class ClaudeAgentProvider:
    name = "claude_agent"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        # Pro/Max subscription token from `claude setup-token` (ADR 0063). The
        # Claude Agent SDK reads CLAUDE_CODE_OAUTH_TOKEN to authenticate against
        # a subscription WITHOUT an API key — the alternative auth mode for the
        # same `claude_sdk` provider kind.
        oauth_token: str | None = None,
        default_model: str = "claude-sonnet-4-5",
        # The completion-shaped path keeps tools off by default — the
        # `run_agent()` path is where tools belong.
        default_allowed_tools: list[str] | None = None,
        default_system_prompt: str | None = None,
        # Injectable for tests: a callable with the same shape as
        # claude_agent_sdk.query. None -> real SDK is loaded lazily.
        query_fn: Any | None = None,
    ) -> None:
        if api_key:
            os.environ["ANTHROPIC_API_KEY"] = api_key
        if oauth_token:
            os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = oauth_token
        if (
            not (api_key or oauth_token)
            and not os.environ.get("ANTHROPIC_API_KEY")
            and query_fn is None
        ):
            # Pro/Max subscription users may rely on ambient auth (a token
            # already in the environment / the SDK's own credentials). We do
            # NOT fail here — the SDK surfaces an auth error at call time.
            pass
        self._default_model = default_model
        self._default_allowed_tools = default_allowed_tools or []
        self._default_system_prompt = default_system_prompt
        self._query_fn = query_fn

    # ------------------------------------------------------------------
    # Internals — lazy import keeps the SDK optional
    # ------------------------------------------------------------------
    def _query(self) -> Any:
        if self._query_fn is not None:
            return self._query_fn
        # Lazy import — claude-agent-sdk is an optional extra of this
        # package. Loading it at module import time would force every
        # deployment (Azure / Copilot / Ollama) to carry the SDK and
        # its Node CLI dependency.
        try:
            from claude_agent_sdk import query
        except ImportError as exc:
            raise ImportError(
                "claude-agent-sdk is not installed. " "Run `pip install 'shared-llm[claude]'`."
            ) from exc
        return query

    def _build_options(
        self,
        *,
        model: str | None,
        system: str | None,
        allowed_tools: list[str] | None,
        max_turns: int,
        effort: str | None = None,
    ) -> Any:
        if self._query_fn is not None:
            return None  # the injected fake accepts whatever we pass
        try:
            from claude_agent_sdk import ClaudeAgentOptions
        except ImportError as exc:
            raise ImportError(
                "claude-agent-sdk is not installed. " "Run `pip install 'shared-llm[claude]'`."
            ) from exc
        # ADR 0070: extended-thinking effort (EffortLevel: low/medium/high/xhigh/max).
        # Solo se pasa cuando hay valor — así seguimos compatibles con SDKs sin el
        # campo `effort`; `None` (off) reproduce el comportamiento previo.
        extra: dict[str, Any] = {}
        if effort:
            extra["effort"] = effort
        return ClaudeAgentOptions(
            model=model or self._default_model,
            system_prompt=system if system is not None else self._default_system_prompt,
            allowed_tools=(
                allowed_tools if allowed_tools is not None else self._default_allowed_tools
            ),
            max_turns=max_turns,
            **extra,
        )

    @staticmethod
    def _flatten(messages: Sequence[Message]) -> tuple[str | None, str]:
        """Join `messages` into (system, prompt). The SDK takes a string
        prompt and an optional system block; the chat history collapses
        into a `Human:`/`Assistant:` transcript."""
        system_parts = [m.content for m in messages if m.role == "system"]
        system = "\n\n".join(system_parts) if system_parts else None
        convo: list[str] = []
        for m in messages:
            if m.role == "system":
                continue
            tag = "Human" if m.role == "user" else "Assistant"
            convo.append(f"{tag}: {m.content}")
        return system, "\n\n".join(convo)

    @staticmethod
    def _harvest(messages: list[Any]) -> tuple[list[str], Usage]:
        """Walk SDK messages: collect text blocks + the final usage."""
        text_parts: list[str] = []
        usage = Usage()
        for msg in messages:
            content = getattr(msg, "content", None)
            if isinstance(content, list):
                for block in content:
                    text = getattr(block, "text", None)
                    if text:
                        text_parts.append(text)
            u = getattr(msg, "usage", None)
            if u:
                usage.input_tokens = int(getattr(u, "input_tokens", usage.input_tokens) or 0)
                usage.output_tokens = int(getattr(u, "output_tokens", usage.output_tokens) or 0)
                usage.cache_read_tokens = int(getattr(u, "cache_read_input_tokens", 0) or 0)
                usage.cache_write_tokens = int(getattr(u, "cache_creation_input_tokens", 0) or 0)
            cost = getattr(msg, "total_cost_usd", None)
            if cost is not None:
                usage.cost_usd = float(cost)
        return text_parts, usage

    # ------------------------------------------------------------------
    # LLMProvider Protocol
    # ------------------------------------------------------------------
    async def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        max_tokens: int = 1024,  # noqa: ARG002 — SDK does not expose this
        temperature: float = 0.7,  # noqa: ARG002 — same
        tools: list[dict[str, Any]] | None = None,  # noqa: ARG002
        **kwargs: Any,
    ) -> CompletionResponse:
        system, prompt = self._flatten(messages)
        options = self._build_options(
            model=model,
            system=system,
            allowed_tools=kwargs.pop("allowed_tools", None),
            # `max_turns=1` agota el loop interno del Claude Code CLI (incluso una
            # respuesta simple cuenta como >1 turno) → "Reached maximum number of
            # turns (1)". 8 deja responder + algún paso interno; overridable.
            max_turns=int(kwargs.pop("max_turns", 8)),
            effort=kwargs.pop("effort", None),
        )
        query_fn = self._query()
        collected: list[Any] = []
        try:
            async for msg in query_fn(prompt=prompt, options=options):
                collected.append(msg)
        except Exception as exc:  # — wrap into typed error
            raise ProviderError(str(exc)) from exc
        text_parts, usage = self._harvest(collected)
        return CompletionResponse(
            content="".join(text_parts),
            model=model or self._default_model,
            provider=self.name,
            usage=usage,
            tool_calls=None,  # complete() runs SDK with no tools
            raw=collected,
        )

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        max_tokens: int = 1024,  # noqa: ARG002
        temperature: float = 0.7,  # noqa: ARG002
        tools: list[dict[str, Any]] | None = None,  # noqa: ARG002
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        system, prompt = self._flatten(messages)
        options = self._build_options(
            model=model,
            system=system,
            allowed_tools=kwargs.pop("allowed_tools", None),
            # `max_turns=1` agota el loop interno del Claude Code CLI (incluso una
            # respuesta simple cuenta como >1 turno) → "Reached maximum number of
            # turns (1)". 8 deja responder + algún paso interno; overridable.
            max_turns=int(kwargs.pop("max_turns", 8)),
            effort=kwargs.pop("effort", None),
        )
        query_fn = self._query()
        last_usage: Usage | None = None
        try:
            async for msg in query_fn(prompt=prompt, options=options):
                content = getattr(msg, "content", None)
                if isinstance(content, list):
                    for block in content:
                        text = getattr(block, "text", None)
                        if text:
                            yield StreamChunk(delta=text)
                u = getattr(msg, "usage", None)
                if u or getattr(msg, "total_cost_usd", None) is not None:
                    last_usage = Usage(
                        input_tokens=int(getattr(u, "input_tokens", 0) or 0) if u else 0,
                        output_tokens=int(getattr(u, "output_tokens", 0) or 0) if u else 0,
                        cache_read_tokens=(
                            int(getattr(u, "cache_read_input_tokens", 0) or 0) if u else 0
                        ),
                        cache_write_tokens=(
                            int(getattr(u, "cache_creation_input_tokens", 0) or 0) if u else 0
                        ),
                        cost_usd=float(getattr(msg, "total_cost_usd", 0.0) or 0.0),
                    )
        except Exception as exc:
            raise ProviderError(str(exc)) from exc
        yield StreamChunk(delta="", done=True, usage=last_usage)

    # ------------------------------------------------------------------
    # Escape hatch — full agent run, typed AgentRunEvent stream
    # ------------------------------------------------------------------
    async def run_agent(
        self,
        prompt: str,
        *,
        model: str | None = None,
        system_prompt: str | None = None,
        allowed_tools: list[str] | None = None,
        max_turns: int = 10,
    ) -> AsyncIterator[AgentRunEvent]:
        """Multi-turn SDK run; yields typed events.

        Use when you want the SDK's full capabilities (tool use, MCP,
        sub-agents) but don't want the rest of the codebase to import
        `claude-agent-sdk` types.
        """
        options = self._build_options(
            model=model,
            system=system_prompt,
            allowed_tools=allowed_tools,
            max_turns=max_turns,
        )
        query_fn = self._query()
        async for msg in query_fn(prompt=prompt, options=options):
            yield _to_agent_event(msg)

    async def aclose(self) -> None:
        return None

    # Convenience for tests that want to verify api_key handling.
    @staticmethod
    def assert_api_key_present() -> None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise AuthError("ANTHROPIC_API_KEY is not set")


def _to_agent_event(msg: Any) -> AgentRunEvent:
    """Translate one SDK message into a typed `AgentRunEvent`."""
    # ResultMessage carries total_cost_usd and the final usage.
    cost = getattr(msg, "total_cost_usd", None)
    raw_usage = getattr(msg, "usage", None)
    if cost is not None:
        usage = Usage(
            input_tokens=int(getattr(raw_usage, "input_tokens", 0) or 0) if raw_usage else 0,
            output_tokens=int(getattr(raw_usage, "output_tokens", 0) or 0) if raw_usage else 0,
            cache_read_tokens=(
                int(getattr(raw_usage, "cache_read_input_tokens", 0) or 0) if raw_usage else 0
            ),
            cache_write_tokens=(
                int(getattr(raw_usage, "cache_creation_input_tokens", 0) or 0) if raw_usage else 0
            ),
            cost_usd=float(cost),
        )
        return AgentRunEvent(kind="result", usage=usage, raw=msg)

    content = getattr(msg, "content", None)
    if isinstance(content, list):
        for block in content:
            if hasattr(block, "name") and hasattr(block, "input"):
                return AgentRunEvent(
                    kind="tool_use",
                    tool_use={
                        "name": getattr(block, "name", ""),
                        "input": dict(getattr(block, "input", {}) or {}),
                        "id": getattr(block, "id", None),
                    },
                    raw=msg,
                )
            text = getattr(block, "text", None)
            if text:
                return AgentRunEvent(kind="text", text=text, raw=msg)

    return AgentRunEvent(kind="other", raw=msg)


__all__ = ["AgentRunEvent", "ClaudeAgentProvider"]
