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
from collections.abc import Sequence
from typing import Any

import httpx

from shared_llm.exceptions import AuthError, ProviderError, RateLimitError
from shared_llm.types import CompletionResponse, Message, ToolCall, Usage


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
    """Raise the right typed error for a non-2xx response."""
    if resp.status_code in (401, 403):
        raise AuthError(f"{provider}: auth failed ({resp.status_code}) {resp.text}")
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
    "parse_chat_completion",
    "parse_sse_delta",
    "to_openai_messages",
]
