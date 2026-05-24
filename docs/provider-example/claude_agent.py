"""Wrapper sobre claude-agent-sdk.

Notas importantes:
- claude-agent-sdk NO es solo un cliente de chat: ejecuta un loop de agente
  con tool use, filesystem, MCP, subagentes, etc. Forzarlo a la interfaz
  común "completion" pierde parte de su valor.
- Aquí exponemos dos modos:
    * `complete` / `stream`: para encajar en LLMProvider, modo no-agente
      (turno único, sin herramientas, sin permisos de fs).
    * `run_agent`: vía de escape para usar el SDK con todas sus capacidades.

Asume `pip install claude-agent-sdk` y que tienes ANTHROPIC_API_KEY en el
entorno, o pasas `api_key` al constructor.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Sequence
from typing import Any

from ..exceptions import AuthError, ProviderError
from ..types import CompletionResponse, Message, StreamChunk, Usage


class ClaudeAgentProvider:
    name = "claude_agent"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        default_model: str = "claude-sonnet-4-5",
        # Para modo "completion" desactivamos herramientas peligrosas por defecto
        default_allowed_tools: list[str] | None = None,
        default_system_prompt: str | None = None,
    ):
        # Import perezoso para no obligar a tenerlo instalado si no se usa
        try:
            from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient  # noqa: F401
        except ImportError as e:
            raise ImportError("Instala claude-agent-sdk: pip install claude-agent-sdk") from e

        if api_key:
            os.environ["ANTHROPIC_API_KEY"] = api_key
        elif not os.environ.get("ANTHROPIC_API_KEY"):
            raise AuthError("Falta ANTHROPIC_API_KEY")

        self._default_model = default_model
        self._default_allowed_tools = default_allowed_tools or []  # sin tools por defecto
        self._default_system_prompt = default_system_prompt

    def _build_options(
        self,
        *,
        model: str | None,
        system: str | None,
        allowed_tools: list[str] | None,
        max_turns: int = 1,
        **extra,
    ):
        from claude_agent_sdk import ClaudeAgentOptions

        return ClaudeAgentOptions(
            model=model or self._default_model,
            system_prompt=system if system is not None else self._default_system_prompt,
            allowed_tools=(
                allowed_tools if allowed_tools is not None else self._default_allowed_tools
            ),
            max_turns=max_turns,
            **extra,
        )

    def _flatten_messages(self, messages: Sequence[Message]) -> tuple[str | None, str]:
        """Convierte una conversación a (system, prompt) para SDK simple.
        El SDK acepta también historiales más ricos vía streaming input,
        pero para un wrapper de 'completion' lo aplanamos."""
        system_parts = [m.content for m in messages if m.role == "system"]
        system = "\n\n".join(system_parts) if system_parts else None

        convo = []
        for m in messages:
            if m.role == "system":
                continue
            tag = "Human" if m.role == "user" else "Assistant"
            convo.append(f"{tag}: {m.content}")
        prompt = "\n\n".join(convo)
        return system, prompt

    # ---------- Interfaz común ----------

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        **kwargs,
    ) -> CompletionResponse:
        from claude_agent_sdk import query

        system, prompt = self._flatten_messages(messages)
        options = self._build_options(
            model=model,
            system=system,
            allowed_tools=kwargs.pop("allowed_tools", None),
            max_turns=1,
        )

        text_chunks: list[str] = []
        usage = Usage()
        raw_messages: list[Any] = []

        try:
            async for msg in query(prompt=prompt, options=options):
                raw_messages.append(msg)
                # claude-agent-sdk emite varios tipos de mensaje; nos quedamos
                # con el contenido textual del asistente y el usage final.
                content = getattr(msg, "content", None)
                if isinstance(content, list):
                    for block in content:
                        text = getattr(block, "text", None)
                        if text:
                            text_chunks.append(text)
                u = getattr(msg, "usage", None)
                if u:
                    usage.input_tokens = getattr(u, "input_tokens", usage.input_tokens)
                    usage.output_tokens = getattr(u, "output_tokens", usage.output_tokens)
                    usage.cache_read_tokens = getattr(u, "cache_read_input_tokens", 0) or 0
                    usage.cache_write_tokens = getattr(u, "cache_creation_input_tokens", 0) or 0
        except Exception as e:
            raise ProviderError(str(e)) from e

        return CompletionResponse(
            content="".join(text_chunks),
            model=model or self._default_model,
            provider=self.name,
            usage=usage,
            raw=raw_messages,
        )

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        **kwargs,
    ) -> AsyncIterator[StreamChunk]:
        from claude_agent_sdk import query

        system, prompt = self._flatten_messages(messages)
        options = self._build_options(
            model=model,
            system=system,
            allowed_tools=kwargs.pop("allowed_tools", None),
            max_turns=1,
        )

        last_usage: Usage | None = None
        async for msg in query(prompt=prompt, options=options):
            content = getattr(msg, "content", None)
            if isinstance(content, list):
                for block in content:
                    text = getattr(block, "text", None)
                    if text:
                        yield StreamChunk(delta=text)
            u = getattr(msg, "usage", None)
            if u:
                last_usage = Usage(
                    input_tokens=getattr(u, "input_tokens", 0) or 0,
                    output_tokens=getattr(u, "output_tokens", 0) or 0,
                    cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
                    cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
                )
        yield StreamChunk(delta="", done=True, usage=last_usage)

    # ---------- Vía de escape para uso AGENTE completo ----------

    async def run_agent(
        self,
        prompt: str,
        *,
        model: str | None = None,
        system_prompt: str | None = None,
        allowed_tools: list[str] | None = None,
        max_turns: int = 10,
        **extra_options,
    ):
        """Devuelve el iterador crudo del SDK para que uses tool use, MCP, etc.

        Ejemplo:
            async for msg in provider.run_agent("Analiza el repo", allowed_tools=["Read","Grep","Bash"]):
                ...
        """
        from claude_agent_sdk import query

        options = self._build_options(
            model=model,
            system=system_prompt,
            allowed_tools=allowed_tools,
            max_turns=max_turns,
            **extra_options,
        )
        async for msg in query(prompt=prompt, options=options):
            yield msg

    async def aclose(self) -> None:
        # El SDK gestiona su propio cliente; nada que cerrar aquí.
        return None
