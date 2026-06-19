"""Traducción del ``reasoning_effort`` (ADR 0070) al kwarg nativo de cada
proveedor para pasar a ``LLMProvider.complete()``.

Fuente única de la correspondencia kind → parámetro, reutilizada por el
agent-runtime (adaptadores ``ModelClient``) y por el asistente personal
(``LLMAssistantModel``):

  * ``claude_sdk``                 → ``effort`` (el SDK lo consume en
    ``ClaudeAgentOptions``; ``complete()`` hace ``kwargs.pop("effort")``).
  * ``azure_foundry`` / ``copilot``→ ``reasoning_effort`` (OpenAI o-series);
    ``off`` ⇒ no enviar nada.
  * ``ollama``                     → ``reasoning_effort`` en el endpoint
    OpenAI-compat ``/v1`` (NO el ``think`` de ``/api/chat``, que ``/v1``
    ignora — ollama#14820); ``off`` ⇒ ``"none"`` explícito, porque omitirlo
    deja el thinking auto-ON en los modelos que razonan.

Un valor vacío/``None`` (sin configurar) no envía nada en ningún proveedor.
"""

from __future__ import annotations

from typing import Any

__all__ = ["reasoning_call_kwargs"]


def reasoning_call_kwargs(kind: str | None, reasoning_effort: str | None) -> dict[str, Any]:
    """kwargs a pasar a ``complete()`` para aplicar el esfuerzo de razonamiento."""
    if not reasoning_effort:
        return {}
    if reasoning_effort == "off":
        # Solo Ollama necesita el "off" explícito ("none"); en OpenAI/Claude
        # omitirlo equivale a no razonar.
        return {"reasoning_effort": "none"} if kind == "ollama" else {}
    if kind == "claude_sdk":
        return {"effort": reasoning_effort}
    return {"reasoning_effort": reasoning_effort}
