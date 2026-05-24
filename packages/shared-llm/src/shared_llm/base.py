"""The `LLMProvider` Protocol every provider implements (ADR 0021)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any, Protocol, runtime_checkable

from shared_llm.types import CompletionResponse, Message, StreamChunk


@runtime_checkable
class LLMProvider(Protocol):
    """The single seam between the platform and any LLM backend.

    `name` is a stable string the host can log / persist with the
    execution to know which provider answered.

    `complete()` does a one-shot call: build a prompt from `messages`,
    return one `CompletionResponse`. Implementations honor `tools`
    when the underlying API supports tool calling.

    `stream()` does the same but yields `StreamChunk` incrementally;
    the last chunk carries `done=True` and (if the provider reports
    it) the final `usage`.

    `aclose()` releases any sockets / sessions the provider holds.
    Idempotent.
    """

    name: str

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> CompletionResponse: ...

    def stream(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]: ...

    async def aclose(self) -> None: ...


__all__ = ["LLMProvider"]
