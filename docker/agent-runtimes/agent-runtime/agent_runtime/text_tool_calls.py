"""Rescate de tool calls escritas como texto (ADR 0169 D5, `task_cm_03`).

Un modelo pequeño, o un servidor cuya plantilla de chat no reconoce la llamada, deja
la tool call dentro de ``content`` en vez de en ``tool_calls``. Este módulo la
reconoce en los tres formatos que se ven en la práctica:

* la etiqueta de Hermes y Qwen: ``<tool_call>{"name": …, "arguments": …}</tool_call>``;
* ese mismo objeto dentro de un bloque de código (```` ```json … ``` ````);
* el objeto desnudo como contenido entero.

La clave de los argumentos puede ser ``arguments`` o ``parameters`` (Llama 3.x), y su
valor un objeto o un JSON codificado como cadena.

**El límite que hace esto seguro**: sólo se devuelve una llamada cuyo nombre esté en
``allowed``, que el llamante rellena con las tools ANUNCIADAS en ese turno. Un JSON
cualquiera en la prosa —un ejemplo de configuración, un fragmento de respuesta— no
tiene ``name`` de una tool anunciada y sigue siendo prosa.

Módulo puro: sin red, sin estado, sin dependencias del runtime.
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection
from typing import Any

from shared_llm.types import ToolCall

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.S | re.I)
_UNCLOSED_THINK = re.compile(r"<think>.*\Z", re.S | re.I)
_TAGGED = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.S | re.I)
_FENCED = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S | re.I)


def strip_think(text: str) -> str:
    """El texto sin los bloques ``<think>…</think>``, incluido uno sin cerrar al final.

    Algunos modelos de razonamiento los dejan en ``content``. No son parte del
    entregable, y dentro de ellos el modelo a veces «ensaya» una tool call que no
    llegó a hacer, así que se retiran antes de buscar llamadas.
    """
    return _UNCLOSED_THINK.sub("", _THINK_BLOCK.sub("", text)).strip()


def _as_call(obj: Any, allowed: Collection[str], idx: int) -> ToolCall | None:
    """Un `ToolCall` si ``obj`` tiene forma de llamada a una tool permitida."""
    if not isinstance(obj, dict):
        return None
    name = obj.get("name")
    if not isinstance(name, str) or name not in allowed:
        return None
    raw_args = obj.get("arguments", obj.get("parameters", {}))
    if isinstance(raw_args, str):
        try:
            raw_args = json.loads(raw_args) if raw_args.strip() else {}
        except json.JSONDecodeError:
            return None
    if not isinstance(raw_args, dict):
        return None
    return ToolCall(id=f"text_call_{idx}", name=name, arguments=raw_args)


def _loads(fragment: str) -> Any:
    try:
        return json.loads(fragment)
    except json.JSONDecodeError:
        return None


def salvage_tool_calls(text: str, allowed: Collection[str]) -> list[ToolCall]:
    """Las tool calls que ``text`` contiene como texto, en orden; ``[]`` si ninguna.

    Se prueba cada formato por orden de especificidad y se para en el primero que da
    alguna llamada válida, para no mezclar, por ejemplo, una etiqueta real con un
    ejemplo en un bloque de código dentro de la misma respuesta.
    """
    body = strip_think(text)
    if not body or not allowed:
        return []
    for fragments in (_TAGGED.findall(body), _FENCED.findall(body), [body]):
        calls = [
            call
            for idx, fragment in enumerate(fragments)
            if (call := _as_call(_loads(fragment), allowed, idx)) is not None
        ]
        if calls:
            return calls
    return []


__all__ = ["salvage_tool_calls", "strip_think"]
