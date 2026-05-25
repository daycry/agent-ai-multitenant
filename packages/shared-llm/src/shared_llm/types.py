"""Common types used by every provider (ADR 0021).

Kept simple on purpose — the provider Protocol is `complete()` and
`stream()`, the inner provider can return as much detail as it likes
in `CompletionResponse.raw`. The four typed fields the platform always
needs are:

  - text content (`CompletionResponse.content`)
  - tool calls if any (`CompletionResponse.tool_calls`)
  - token + cost usage (`CompletionResponse.usage`)
  - which provider answered (`CompletionResponse.provider`)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class Message:
    """One chat message — the lowest common denominator across providers."""

    role: Role
    content: str
    # For role="tool" — which call this is the result of.
    tool_call_id: str | None = None
    # Some providers (Azure OpenAI) accept a `name` on tool replies.
    name: str | None = None


@dataclass
class ToolCall:
    """One tool the model wants the host to invoke.

    `arguments` is the parsed JSON dict the model produced (not the raw
    string) — every provider that returns tool calls in this shape
    parses it for the host. Keeping it pre-parsed means the agent loop
    doesn't have to know about each provider's JSON encoding quirks.
    """

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class Usage:
    """Token + cost accounting for one provider call.

    `cost_usd` is best-effort: Claude SDK reports it directly,
    Copilot is computed from a local catalog, APIM exposes it only if
    the policy enables it. Ollama local is `0.0` (no API cost; the
    GPU bill is the operator's problem).
    """

    input_tokens: int = 0
    output_tokens: int = 0
    # Anthropic-specific; left at zero for the other providers.
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    # Best-effort cost in USD — see class docstring for caveats.
    cost_usd: float = 0.0


@dataclass
class CompletionResponse:
    """Result of a non-streaming `LLMProvider.complete()` call."""

    content: str
    model: str
    provider: str
    usage: Usage = field(default_factory=Usage)
    # When the model decides to call tools instead of (or alongside)
    # producing text. None when no tool calls were emitted.
    tool_calls: list[ToolCall] | None = None
    # Raw provider payload, kept for callers that need provider-specific
    # bits we did not surface in typed fields. Not part of the contract;
    # do not rely on its shape across providers.
    raw: Any = None


@dataclass
class StreamChunk:
    """One incremental piece of a streamed completion.

    `done=True` chunks carry the final `usage` if the provider reports
    it at the end of the stream (most OpenAI-compatible endpoints do).
    """

    delta: str
    done: bool = False
    usage: Usage | None = None


@dataclass
class AgentRunEvent:
    """One event from `ClaudeAgentProvider.run_agent()` (multi-turn).

    A typed wrapper around the SDK's heterogeneous messages so the rest
    of the platform doesn't import claude-agent-sdk just to read them.
    """

    # "text"      -> the assistant produced a text block (in `text`)
    # "tool_use"  -> the assistant called a tool (`tool_use` dict)
    # "result"    -> the SDK finished a turn (`usage` + `cost_usd`)
    # "other"     -> anything we did not flatten (full message in `raw`)
    kind: Literal["text", "tool_use", "result", "other"]
    text: str | None = None
    tool_use: dict[str, Any] | None = None
    usage: Usage | None = None
    raw: Any = None


__all__ = [
    "AgentRunEvent",
    "CompletionResponse",
    "Message",
    "Role",
    "StreamChunk",
    "ToolCall",
    "Usage",
]
