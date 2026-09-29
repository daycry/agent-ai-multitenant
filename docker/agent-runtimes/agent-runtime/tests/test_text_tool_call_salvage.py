"""Tool calls escritas como texto se rescatan si nombran una tool anunciada (ADR 0169 D5).

`task_cm_03` del plan `compat-modelos-2026-09-29`. Un modelo pequeño, o un servidor
cuya plantilla no reconoce la llamada, deja la tool call dentro de ``content``. Sin
rescate, `_decision_from` la trata como el FINISH en prosa de ``claude_sdk`` y el
run termina con el JSON como entregable: el peor fallo posible, porque parece un
éxito.

El rescate tiene un límite duro que estos tests fijan: sólo enruta una llamada cuyo
nombre es una tool ANUNCIADA en ese turno. Un JSON cualquiera en la prosa sigue
siendo prosa.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from agent_runtime.model import DecisionKind
from agent_runtime.providers import _ProviderModelClient
from agent_runtime.text_tool_calls import salvage_tool_calls, strip_think
from shared_llm import Message
from shared_llm.types import CompletionResponse, Usage

_WRITE = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": "Write a file.",
        "parameters": {"type": "object", "properties": {}},
    },
}
_READ = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read a file.",
        "parameters": {"type": "object", "properties": {}},
    },
}


class _Provider:
    name = "fake"

    def __init__(self, content: str) -> None:
        self._content = content

    async def complete(self, _messages: Sequence[Message], **_kwargs: Any) -> CompletionResponse:
        return CompletionResponse(content=self._content, model="m", provider="fake", usage=Usage())

    async def aclose(self) -> None:
        return None


def _decide(content: str, *, cls: type[_ProviderModelClient] = _ProviderModelClient) -> Any:
    client = cls(provider=_Provider(content), model="m", tools=[_WRITE, _READ])
    state = {"task": {"title": "t", "description": "d"}, "context": [], "iteration": 0}
    return client.decide(state).decision


_ARGS = {"path": "hola.txt", "content": "hola\n"}


# --- formatos reconocidos -------------------------------------------------------
def test_hermes_tool_call_tag_is_routed_as_act() -> None:
    call = json.dumps({"name": "write_file", "arguments": _ARGS})
    d = _decide(f"<tool_call>\n{call}\n</tool_call>")
    assert d.kind == DecisionKind.ACT
    assert d.tool == "write_file"
    assert d.tool_args == _ARGS


def test_bare_json_with_parameters_key_is_routed_as_act() -> None:
    """La forma de Llama 3.x: ``parameters`` en vez de ``arguments``."""
    d = _decide(json.dumps({"name": "read_file", "parameters": {"path": "a.py"}}))
    assert d.tool == "read_file"
    assert d.tool_args == {"path": "a.py"}


def test_fenced_json_block_is_routed_as_act() -> None:
    body = json.dumps({"name": "write_file", "arguments": _ARGS})
    d = _decide(f"Voy a escribir el fichero.\n```json\n{body}\n```")
    assert d.tool == "write_file"


def test_arguments_encoded_as_a_json_string_are_decoded() -> None:
    d = _decide(json.dumps({"name": "write_file", "arguments": json.dumps(_ARGS)}))
    assert d.tool_args == _ARGS


def test_submit_result_in_text_finishes_with_its_summary() -> None:
    call = {"name": "submit_result", "arguments": {"status": "success", "summary": "listo"}}
    d = _decide(f"<tool_call>{json.dumps(call)}</tool_call>")
    assert d.kind == DecisionKind.FINISH
    assert d.output == "listo"
    assert d.finish_status == "success"


def test_think_block_before_the_call_is_ignored() -> None:
    call = json.dumps({"name": "read_file", "arguments": {"path": "a.py"}})
    d = _decide(f"<think>Primero leo a.py para ver qué hay.</think>\n<tool_call>{call}</tool_call>")
    assert d.tool == "read_file"


# --- lo que NO se rescata --------------------------------------------------------
def test_an_unadvertised_name_stays_prose() -> None:
    text = json.dumps({"name": "rm_rf", "arguments": {"path": "/"}})
    d = _decide(text)
    assert d.kind == DecisionKind.FINISH
    assert d.output == text


def test_json_that_is_not_a_call_stays_prose() -> None:
    text = 'El fichero de config queda así: {"debug": true, "port": 8080}'
    d = _decide(text)
    assert d.kind == DecisionKind.FINISH
    assert d.output == text


def test_a_client_without_submit_result_never_salvages() -> None:
    """El camino claude_sdk: su FINISH real es prosa y no se reinterpreta."""

    class _SdkLike(_ProviderModelClient):
        _advertises_submit_result = False

    text = json.dumps({"name": "write_file", "arguments": _ARGS})
    d = _decide(text, cls=_SdkLike)
    assert d.kind == DecisionKind.FINISH


# --- el entregable no arrastra el razonamiento -----------------------------------
def test_think_block_is_stripped_from_a_prose_finish() -> None:
    d = _decide("<think>razonamiento interno largo</think>\nEl informe final.")
    assert d.kind == DecisionKind.FINISH
    assert d.output == "El informe final."


# --- el módulo puro -------------------------------------------------------------
def test_strip_think_handles_an_unclosed_block() -> None:
    assert strip_think("Respuesta.<think>se cortó aquí") == "Respuesta."


def test_several_tagged_calls_are_all_returned_in_order() -> None:
    a = json.dumps({"name": "read_file", "arguments": {"path": "a.py"}})
    b = json.dumps({"name": "read_file", "arguments": {"path": "b.py"}})
    text = f"<tool_call>{a}</tool_call><tool_call>{b}</tool_call>"
    calls = salvage_tool_calls(text, {"read_file"})
    assert [c.arguments["path"] for c in calls] == ["a.py", "b.py"]
