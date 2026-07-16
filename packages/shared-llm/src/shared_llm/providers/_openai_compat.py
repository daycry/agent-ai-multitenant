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

import contextlib
import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
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


@contextlib.asynccontextmanager
async def typed_transport_errors(*, provider: str) -> AsyncIterator[None]:
    """Convert raw httpx/OS transport errors into the layer's typed error.

    AUD16 (2026-07-16): ``stream()`` ya envolvía los errores de red mid-stream
    (``iter_sse_chunks``), pero un ``httpx.ReadTimeout``/``ConnectError`` en
    ``complete()`` escapaba CRUDO hasta el caller (el córtex lo convirtió en un
    500 sin manejar el 07-13). Los errores YA tipados del layer (``AuthError``,
    ``RateLimitError``, ``ProviderError`` de ``check_status``) pasan intactos.
    """
    try:
        yield
    except (AuthError, RateLimitError, ProviderError):
        raise
    except (httpx.HTTPError, OSError) as exc:
        raise ProviderError(f"{provider}: transport error — {exc}") from exc


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
    # A 200 can still carry a malformed/empty body (a flaky gateway, an APIM policy
    # returning an error-shape). Guard so a typed ProviderError surfaces instead of a raw
    # KeyError/IndexError/TypeError escaping the LLM layer. The body goes in `raw`, not the
    # message, to avoid leaking it into logs.
    choices = data.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices else None
    message = choice.get("message") if isinstance(choice, dict) else None
    if not isinstance(message, dict):
        raise ProviderError(f"{provider}: respuesta sin 'choices[0].message'", raw=data)
    content = message.get("content") or ""
    raw_tool_calls = message.get("tool_calls") or []
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
    # M-4 (auditoría 2026-07-10): el campo tipado `stop_reason` viaja también en
    # los providers HTTP (antes solo claude_sdk lo poblaba, #10c) — el
    # finish_reason verbatim del payload; ausente → None. `completion_signals`
    # sigue derivando el truncado del raw (misma señal, sin cambio de conducta).
    finish_reason = choice.get("finish_reason") if isinstance(choice, dict) else None
    return CompletionResponse(
        content=content,
        model=str(data.get("model") or fallback_model),
        provider=provider,
        usage=usage,
        tool_calls=tool_calls,
        raw=data,
        stop_reason=str(finish_reason) if finish_reason is not None else None,
    )


def parse_sse_delta(line: str) -> tuple[str | None, bool]:
    """Parse one Server-Sent-Event line from `/chat/completions?stream=true`.

    Returns `(delta_text, done)`:
      - `(None, False)` for irrelevant lines (keep-alive, comment, ...).
      - `(text, False)` for a real content delta.
      - `(None, True)` for the terminator `[DONE]`.

    Tool-call deltas are NOT surfaced here (text-only API, kept stable);
    `iter_sse_chunks` accumulates them separately (AUD16-06).
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


def _sse_tool_call_deltas(line: str) -> list[dict[str, Any]]:
    """The raw ``delta.tool_calls`` entries of one SSE line (``[]`` if none)."""
    if not line or not line.startswith("data: "):
        return []
    payload = line[6:].strip()
    if payload == "[DONE]":
        return []
    try:
        chunk = json.loads(payload)
    except json.JSONDecodeError:
        return []
    delta = chunk.get("choices", [{}])[0].get("delta", {})
    raw = delta.get("tool_calls")
    return [tc for tc in raw if isinstance(tc, dict)] if isinstance(raw, list) else []


def _merge_tool_call_delta(acc: dict[int, dict[str, Any]], tc: dict[str, Any]) -> None:
    """Fold one streamed tool-call delta into the per-index accumulator.

    OpenAI streams a tool call as: first delta with ``index``/``id``/
    ``function.name`` (+ an ``arguments`` fragment), then more deltas whose
    ``function.arguments`` fragments concatenate into the JSON args string.
    """
    index = int(tc.get("index") or 0)
    slot = acc.setdefault(index, {"id": "", "name": "", "arguments": ""})
    if tc.get("id"):
        slot["id"] = str(tc["id"])
    fn = tc.get("function")
    if isinstance(fn, dict):
        if fn.get("name"):
            slot["name"] = str(fn["name"])
        fragment = fn.get("arguments")
        if isinstance(fragment, str):
            slot["arguments"] += fragment


def _accumulated_tool_calls(acc: dict[int, dict[str, Any]]) -> list[ToolCall] | None:
    """The finished `ToolCall`s from the accumulator (None when none arrived)."""
    if not acc:
        return None
    calls = [
        ToolCall(id=slot["id"], name=slot["name"], arguments=_loads_args(slot["arguments"]))
        for _, slot in sorted(acc.items())
        if slot["name"]
    ]
    return calls or None


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

    AUD16-06: los deltas de `tool_calls` ya no se descartan en silencio — se
    acumulan por índice y viajan parseados en el chunk final `done=True`
    (`StreamChunk.tool_calls`); sin tool calls el campo queda en None.
    """
    tool_call_acc: dict[int, dict[str, Any]] = {}
    try:
        async for line in resp.aiter_lines():
            for tc_delta in _sse_tool_call_deltas(line):
                _merge_tool_call_delta(tool_call_acc, tc_delta)
            delta, done = parse_sse_delta(line)
            if done:
                yield StreamChunk(
                    delta="", done=True, tool_calls=_accumulated_tool_calls(tool_call_acc)
                )
                return
            if delta:
                yield StreamChunk(delta=delta)
    except _STREAM_ERRORS as exc:
        raise ProviderError(f"{provider}: stream interrupted — {exc}") from exc


def _loads_args(raw: Any) -> dict[str, Any]:
    """Parse a tool-call arguments payload leniently (best-effort dict).

    Kept for the parse path: tool execution always needs *a* dict, so a
    malformed payload still degrades to ``{}`` here. To tell a *corrupt*
    payload apart from genuinely *absent* args, use `completion_signals`
    (F32) — this helper alone cannot, by design.
    """
    args, _ = _parse_args_checked(raw)
    return args


def _parse_args_checked(raw: Any) -> tuple[dict[str, Any], bool]:
    """Parse tool-call ``arguments``; return ``(args, malformed)``.

    ``malformed`` is ``True`` only when the payload was *present* but could
    not be decoded into a JSON object (corrupt or truncated mid-string).
    A genuinely absent/empty payload is NOT malformed — it yields
    ``({}, False)`` — so the caller never confuses "no args" with "the
    model produced garbage we silently dropped".
    """
    if isinstance(raw, dict):
        return raw, False
    if not raw:
        return {}, False
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}, True
    if isinstance(parsed, dict):
        return parsed, False
    # Valid JSON but not an object (a bare string/number/list) — not usable
    # as tool arguments, so it is just as corrupt as undecodable text.
    return {}, True


@dataclass
class CompletionSignals:
    """Robustness flags for one ``/chat/completions`` payload (F32).

    Without these, `_loads_args` collapses a corrupt tool-call into ``{}``
    and the layer above turns an empty ``submit_result`` into an empty
    deliverable / an empty ``submit_verdict`` into ``inconclusive`` — and
    runs *real* tools with empty args — all silently. These flags let the
    caller distinguish "the model gave us nothing" from "we lost what the
    model gave us".
    """

    # finish_reason == "length": the provider hit the token cap, so the body
    # (incl. any tool-call ``arguments`` JSON) may be cut off mid-string.
    truncated: bool = False
    # At least one tool call carried an ``arguments`` payload that was present
    # but not a valid JSON object (corrupt / truncated) — see `_parse_args_checked`.
    malformed_tool_args: bool = False


def completion_signals(data: Any) -> CompletionSignals:
    """Derive `CompletionSignals` from a raw ``/chat/completions`` payload.

    Safe on any shape (re-uses the same defensive walk as `parse_chat_completion`),
    so callers can pass ``CompletionResponse.raw`` directly. A non-dict / unexpected
    payload yields the all-``False`` default rather than raising.
    """
    if not isinstance(data, dict):
        return CompletionSignals()
    choices = data.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices else None
    if not isinstance(choice, dict):
        return CompletionSignals()
    truncated = choice.get("finish_reason") == "length"
    message = choice.get("message")
    malformed = False
    if isinstance(message, dict):
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function") if isinstance(tc, dict) else None
            _, tc_malformed = _parse_args_checked((fn or {}).get("arguments"))
            if tc_malformed:
                malformed = True
                break
    return CompletionSignals(truncated=truncated, malformed_tool_args=malformed)


__all__ = [
    "CompletionSignals",
    "check_status",
    "completion_signals",
    "iter_sse_chunks",
    "parse_chat_completion",
    "parse_sse_delta",
    "to_openai_messages",
    "typed_transport_errors",
]
