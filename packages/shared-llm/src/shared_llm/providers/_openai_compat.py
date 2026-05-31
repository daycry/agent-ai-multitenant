"""Tiny helpers shared by the OpenAI-compatible providers.

ADR 0021 explicitly defers extracting a common `OpenAICompatibleProvider`
base class — three providers (Azure Foundry APIM, Ollama, Copilot)
speak `/chat/completions`, but Copilot has enough custom logic (JWT
mint, editor headers, status-401 retry) that the base would be leaky.

What we DO share are pure helpers: message conversion, response
parsing, error mapping. These live here so the provider modules stay
focused on the auth + endpoint layout that actually differs.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from shared_llm.exceptions import AuthError, ProviderError, RateLimitError
from shared_llm.types import CompletionResponse, Message, StreamChunk, ToolCall, Usage

# Errors that can surface *mid-stream* (after `check_status` has already
# accepted the response headers) while iterating the body: a dropped
# connection, a read timeout, a malformed chunk decode, etc. We catch the
# broad `httpx.HTTPError` family plus generic transport-level errors and
# re-raise them as the layer's typed `ProviderError`, mirroring the
# wrapping `claude_agent.ClaudeAgentProvider.stream()` already does. The
# narrow tuple keeps `KeyboardInterrupt` / `asyncio.CancelledError` from
# being swallowed.
_STREAM_ERRORS: tuple[type[BaseException], ...] = (httpx.HTTPError, OSError)


def to_openai_messages(messages: Sequence[Message]) -> list[dict[str, Any]]:
    """Render `Message`s in the OpenAI `/chat/completions` shape."""
    out: list[dict[str, Any]] = []
    for m in messages:
        entry: dict[str, Any] = {"role": m.role, "content": m.content}
        if m.name:
            entry["name"] = m.name
        if m.tool_call_id:
            entry["tool_call_id"] = m.tool_call_id
        out.append(entry)
    return out


def check_status(resp: httpx.Response, *, provider: str) -> None:
    """Raise the right typed error for a non-2xx response.

    401 and 403 are deliberately split: 401 (Unauthenticated) means the
    credential is missing / invalid / expired — the caller can react by
    re-minting a token and retrying (Copilot does exactly this). 403
    (Forbidden) means the credential is valid but lacks permission for
    this resource; retrying with a fresh token will not help, so it maps
    to a plain ProviderError instead of AuthError.
    """
    if resp.status_code == 401:
        raise AuthError(f"{provider}: auth failed (401) {resp.text}")
    if resp.status_code == 429:
        raise RateLimitError(f"{provider}: rate-limited — {resp.text}")
    if resp.status_code >= 400:
        raise ProviderError(
            f"{provider}: HTTP {resp.status_code} — {resp.text}",
            status_code=resp.status_code,
            raw=resp.text,
        )


def parse_chat_completion(
    data: dict[str, Any], *, provider: str, fallback_model: str
) -> CompletionResponse:
    """Parse one `/chat/completions` response into `CompletionResponse`.

    Handles both text content and tool calls. Token counts come from
    the standard `usage` block; `cost` is read if the provider added
    it (some APIM policies do, OpenAI itself does not).
    """
    choice = data["choices"][0]["message"]
    content = choice.get("content") or ""
    raw_tool_calls = choice.get("tool_calls") or []
    tool_calls: list[ToolCall] | None = None
    if raw_tool_calls:
        tool_calls = []
        for tc in raw_tool_calls:
            fn = tc.get("function") or {}
            args = _loads_args(fn.get("arguments"))
            tool_calls.append(
                ToolCall(
                    id=str(tc.get("id") or ""),
                    name=str(fn.get("name") or ""),
                    arguments=args,
                )
            )
    usage_d = data.get("usage") or {}
    usage = Usage(
        input_tokens=int(usage_d.get("prompt_tokens", 0)),
        output_tokens=int(usage_d.get("completion_tokens", 0)),
        cost_usd=float(usage_d.get("cost", 0.0) or 0.0),
    )
    return CompletionResponse(
        content=content,
        model=str(data.get("model") or fallback_model),
        provider=provider,
        usage=usage,
        tool_calls=tool_calls,
        raw=data,
    )


def parse_sse_delta(line: str) -> tuple[str | None, bool]:
    """Parse one Server-Sent-Event line from `/chat/completions?stream=true`.

    Returns `(delta_text, done)`:
      - `(None, False)` for irrelevant lines (keep-alive, comment, ...).
      - `(text, False)` for a real content delta.
      - `(None, True)` for the terminator `[DONE]`.
    """
    if not line or not line.startswith("data: "):
        return None, False
    payload = line[6:].strip()
    if payload == "[DONE]":
        return None, True
    try:
        chunk = json.loads(payload)
    except json.JSONDecodeError:
        return None, False
    delta = (chunk.get("choices", [{}])[0].get("delta", {}).get("content")) or ""
    if delta:
        return delta, False
    return None, False


async def iter_sse_chunks(resp: httpx.Response, *, provider: str) -> AsyncIterator[StreamChunk]:
    """Yield `StreamChunk`s from an open streaming `/chat/completions` body.

    `check_status` must have validated the response *before* calling this
    (it inspects the status line, which is available as soon as the
    headers arrive). This helper owns the body iteration only.

    A network drop, read timeout, or transport error *mid-stream* would
    otherwise escape `aiter_lines()` raw and leak an `httpx`/`OSError`
    type to callers that only catch the LLM layer's typed errors. We wrap
    the loop and convert such failures to `ProviderError`, matching the
    pattern in `claude_agent.ClaudeAgentProvider.stream()`. The terminal
    `done=True` chunk is emitted by this helper on the `[DONE]` marker.
    """
    try:
        async for line in resp.aiter_lines():
            delta, done = parse_sse_delta(line)
            if done:
                yield StreamChunk(delta="", done=True)
                return
            if delta:
                yield StreamChunk(delta=delta)
    except _STREAM_ERRORS as exc:
        raise ProviderError(f"{provider}: stream interrupted — {exc}") from exc


def _loads_args(raw: Any) -> dict[str, Any]:
    """Parse a tool-call arguments payload leniently."""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


__all__ = [
    "check_status",
    "iter_sse_chunks",
    "parse_chat_completion",
    "parse_sse_delta",
    "to_openai_messages",
]
