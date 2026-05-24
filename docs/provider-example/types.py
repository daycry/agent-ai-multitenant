"""Tipos comunes para todos los proveedores."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class Message:
    role: Role
    content: str
    name: str | None = None  # para tool messages
    tool_call_id: str | None = None


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    # Solo Anthropic, lo dejamos opcional
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass
class CompletionResponse:
    content: str
    model: str
    provider: str
    usage: Usage = field(default_factory=Usage)
    raw: Any = None  # respuesta cruda por si necesitas algo específico


@dataclass
class StreamChunk:
    delta: str
    done: bool = False
    usage: Usage | None = None  # solo en el último chunk normalmente
