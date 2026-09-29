"""Una tool call de acción truncada o corrupta no se ejecuta (ADR 0169 D5).

`task_cm_02` del plan `compat-modelos-2026-09-29`. F32 protegía `submit_result` y la
prosa, pero una ACCIÓN con los argumentos cortados por el tope de salida llegaba al
registro con ``{}``: un ``write_file`` sin ``path`` ni ``content``. El modelo veía un
error de validación que no explicaba nada, repetía la misma llamada demasiado larga y
el detector de bucles acababa el run. Ahora la llamada no se ejecuta y el ``noop``
le dice por qué y cómo evitarlo.

La guarda es POR LLAMADA, no por respuesta: una llamada descartada con argumentos
corruptos no invalida la que sí se eligió.
"""

from __future__ import annotations

import json
from typing import Any

from agent_runtime.model import DecisionKind
from agent_runtime.providers import _decision_from
from shared_llm.providers._openai_compat import parse_chat_completion


def _tc(name: str, arguments: str, idx: int = 1) -> dict[str, Any]:
    return {
        "id": f"call_{idx}",
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def _resp(tool_calls: list[dict[str, Any]], finish_reason: str = "tool_calls") -> Any:
    payload = {
        "model": "m",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": "", "tool_calls": tool_calls},
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 2},
    }
    return parse_chat_completion(payload, provider="fake", fallback_model="m")


_CUT = '{"path": "src/app.py", "content": "def main():\\n    print('  # cortado a mitad


def test_a_truncated_write_is_not_executed_with_empty_args() -> None:
    d = _decision_from(_resp([_tc("write_file", _CUT)], "length"), model="m").decision
    assert d.kind == DecisionKind.ACT
    assert d.tool == "noop"
    reason = d.tool_args["reason"]
    assert "write_file" in reason, "el modelo debe saber QUÉ llamada se descartó"
    assert "NOT executed" in reason


def test_corrupt_args_without_truncation_are_not_executed_either() -> None:
    d = _decision_from(_resp([_tc("write_file", "{not json")]), model="m").decision
    assert d.tool == "noop"


def test_the_last_call_of_a_truncated_turn_is_suspect_even_if_it_parses() -> None:
    """Cortado justo antes de los argumentos, el JSON queda vacío y NO parece corrupto."""
    d = _decision_from(_resp([_tc("write_file", "")], "length"), model="m").decision
    assert d.tool == "noop"


def test_a_clean_call_is_executed_despite_a_corrupt_discarded_one() -> None:
    calls = [
        _tc("write_file", json.dumps({"path": "a.py", "content": "x = 1\n"}), 1),
        _tc("write_file", "{broken", 2),
    ]
    d = _decision_from(_resp(calls), model="m").decision
    assert d.tool == "write_file"
    assert d.tool_args == {"path": "a.py", "content": "x = 1\n"}


def test_the_read_only_batch_stops_before_a_corrupt_call() -> None:
    calls = [
        _tc("read_file", json.dumps({"path": "a.py"}), 1),
        _tc("read_file", json.dumps({"path": "b.py"}), 2),
        _tc("read_file", "{broken", 3),
    ]
    d = _decision_from(_resp(calls), model="m").decision
    assert d.tool == "read_file"
    assert [c["args"] for c in d.batch_calls] == [{"path": "b.py"}]


def test_a_clean_single_call_is_untouched() -> None:
    d = _decision_from(_resp([_tc("read_file", json.dumps({"path": "a.py"}))]), model="m").decision
    assert d.tool == "read_file"
    assert d.tool_args == {"path": "a.py"}
