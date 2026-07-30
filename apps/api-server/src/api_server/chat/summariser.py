"""El `Summariser` de producción del chat (task_wf_06 b).

``db/conversation_compression.py`` define el summariser como un ``Protocol`` para
quedarse libre de SDK de LLM, y su docstring lleva desde el Plan 03 diciendo que
«el bucle de agente enchufará uno real respaldado por ``shared_llm.LLMProvider``».
Nunca se enchufó: el único implementador era el ``ScriptedSummariser`` de los
tests, así que el subsistema de compresión estaba completo y apagado. Este módulo
es ese implementador.

Dos decisiones de diseño lo definen:

* **Doble representación.** El modelo devuelve un objeto con prosa y cuatro listas
  (requisitos / decisiones / descartado / abierto). La prosa es para el humano;
  las listas son el :class:`SummaryRecord` que el pliegue copia literal de un piso
  al siguiente sin volver a pasar por un modelo.
* **Modo de fallo explícito**, con la forma de ``DistillationResult``
  (``memorizer/distillation.py``): ``ok`` | ``llm_empty`` | ``llm_unparseable`` |
  ``llm_error``. Nunca lanza hacia el turno del chat; sin resumen, la conversación
  simplemente sigue sin comprimir.

El modelo es **el que el turno del chat ya resolvió** (``chat_model_config``, ADR
0065/0055): el operador ya tiene ahí la palanca para abaratar el chat, y añadir un
eje de configuración solo para el resumen sería una palanca más que mantener y
explicar.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import structlog
from shared_llm.base import LLMProvider
from shared_llm.types import Message as LLMMessage

from api_server.db.conversation import Message
from api_server.db.conversation_compression import (
    SummaryRecord,
    WindowSummary,
)

_log = structlog.get_logger("api_server.chat.summariser")

_MAX_TOKENS = 1200
_TEMPERATURE = 0.2

# Cuánto de cada mensaje entra en el prompt. Una ventana son 10-25 mensajes y
# alguno puede ser un volcado enorme; sin tope, el propio resumen desbordaría.
_MAX_CHARS_PER_MESSAGE = 4000

_ROLE_LABELS = {"user": "USUARIO", "agent": "EQUIPO", "system": "SISTEMA"}

_SYSTEM_PROMPT = (
    "Eres el archivista de una conversación de planificación de software. Te doy "
    "un tramo ANTIGUO de la conversación que va a sustituirse por tu resumen: a "
    "partir de ahora el equipo leerá tu resumen en lugar de estos mensajes, así "
    "que lo que no recojas se pierde.\n\n"
    "Responde SIEMPRE con un único objeto JSON, sin prosa alrededor y sin vallas "
    "de markdown, con exactamente estas cinco claves:\n"
    '  "resumen"    — un párrafo en markdown que cuente qué pasó en este tramo, '
    "para que lo lea una persona.\n"
    '  "requisitos" — lista de los requisitos que el usuario ha enunciado, cada '
    "uno con SUS PALABRAS y en una frase completa que se entienda sola.\n"
    '  "decisiones" — lista de las decisiones que el equipo ha cerrado.\n'
    '  "descartado" — lista de las opciones que se han RECHAZADO, y por qué. '
    "Esta lista es crítica: sin ella el equipo vuelve a proponer lo que ya se "
    "descartó.\n"
    '  "abierto"    — lista de las preguntas o puntos que quedan sin resolver.\n\n'
    "Reglas: no inventes nada que no esté en el tramo; no resumas una entrada "
    "hasta dejarla ambigua («varios requisitos» no vale, escribe cuáles); si una "
    "lista no aplica, déjala vacía. Escribe en el idioma de la conversación."
)


def _window_text(messages: Sequence[Message]) -> str:
    lines: list[str] = []
    for message in messages:
        speaker = _ROLE_LABELS.get(message.author_kind, message.author_kind.upper())
        content = (message.content or "").strip()
        if len(content) > _MAX_CHARS_PER_MESSAGE:
            content = content[:_MAX_CHARS_PER_MESSAGE] + " […]"
        lines.append(f"[{speaker}] {content}")
    return "\n\n".join(lines)


def _try_parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def parse_summary_response(text: str) -> WindowSummary:
    """Parse the model's answer into a :class:`WindowSummary`.

    Strategies, in order: the whole response as JSON; the first ``{...}`` block;
    give up (``llm_unparseable``). A parsed object with neither prose nor a single
    record entry is ``llm_empty`` — the one legitimate "nothing to record".
    """
    payload = _try_parse_json(text)
    if not isinstance(payload, dict):
        match = re.search(r"\{.*\}", text, re.DOTALL)
        payload = _try_parse_json(match.group(0)) if match is not None else None
    if not isinstance(payload, dict):
        _log.info("chat.summariser_parse_failed", preview=text[:200])
        return WindowSummary(content="", cause="llm_unparseable")

    record = SummaryRecord.from_attachment(payload)
    prose = payload.get("resumen")
    prose_text = prose.strip() if isinstance(prose, str) else ""
    if not prose_text and record.is_empty():
        return WindowSummary(content="", cause="llm_empty")
    if not prose_text:
        # El modelo se dejó la prosa pero acertó el registro. Comprimir sigue
        # siendo mejor que no comprimir: el registro es lo que hay que conservar.
        prose_text = "El modelo no redactó el resumen; el registro estructurado sí."
    return WindowSummary(content=prose_text, record=record, cause="ok")


@dataclass
class LLMSummariser:
    """A :class:`StructuredSummariser` backed by any catalog provider (ADR 0021)."""

    provider: LLMProvider
    model: str | None = None

    async def summarise_window(self, messages: list[Message]) -> WindowSummary:
        if not messages:
            # Ventana que solo pliega resúmenes ya estructurados: la fusión es
            # determinista, así que gastar una llamada sería tirar dinero.
            return WindowSummary(content="", cause="llm_empty")
        prompt = [
            LLMMessage(role="system", content=_SYSTEM_PROMPT),
            LLMMessage(role="user", content=_window_text(messages)),
        ]
        try:
            response = await self.provider.complete(
                prompt,
                model=self.model,
                max_tokens=_MAX_TOKENS,
                temperature=_TEMPERATURE,
            )
        except Exception as exc:
            # El tipo, no el texto: los errores de proveedor pueden traer cuerpos
            # de respuesta o credenciales dentro.
            _log.warning("chat.summariser_call_failed", error_type=exc.__class__.__name__)
            return WindowSummary(content="", cause="llm_error")
        return parse_summary_response(response.content or "")

    async def summarise(self, messages: list[Message]) -> str:
        """The original prose-only ``Summariser`` seam, kept working."""
        return (await self.summarise_window(messages)).content


__all__ = ["LLMSummariser", "parse_summary_response"]
