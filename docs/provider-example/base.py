"""Interfaz común que implementan todos los proveedores."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Protocol, runtime_checkable

from .types import CompletionResponse, Message, StreamChunk


@runtime_checkable
class LLMProvider(Protocol):
    name: str

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        **kwargs,
    ) -> CompletionResponse: ...

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        **kwargs,
    ) -> AsyncIterator[StreamChunk]: ...

    async def aclose(self) -> None: ...
