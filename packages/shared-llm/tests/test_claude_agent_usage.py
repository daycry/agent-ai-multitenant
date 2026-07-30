"""_usage_get reads SDK usage whether it's an object or a dict (regression 2026-06-27).

claude_sdk runs showed cost>0 but tokens=0: the SDK's ResultMessage carries
``total_cost_usd`` as an attribute but its ``usage`` arrived as a plain dict, so
``getattr(u, "input_tokens")`` silently returned 0. ``_usage_get`` reads both shapes.
"""

from __future__ import annotations

from typing import Any

from shared_llm.providers.claude_agent import _usage_get


class _Obj:
    def __init__(self, **kw: Any) -> None:
        for k, v in kw.items():
            setattr(self, k, v)


def test_reads_object_attributes() -> None:
    u = _Obj(input_tokens=10, output_tokens=5)
    assert _usage_get(u, "input_tokens") == 10
    assert _usage_get(u, "output_tokens") == 5
    assert _usage_get(u, "missing") == 0


def test_reads_dict_keys() -> None:
    u = {"input_tokens": 7, "output_tokens": 3}
    assert _usage_get(u, "input_tokens") == 7
    assert _usage_get(u, "output_tokens") == 3
    assert _usage_get(u, "missing", 99) == 99


def test_none_and_falsy_coerce_to_int() -> None:
    assert _usage_get(None, "input_tokens") == 0
    assert _usage_get({"input_tokens": None}, "input_tokens") == 0
    assert _usage_get(_Obj(input_tokens="12"), "input_tokens") == 12


# ---------------------------------------------------------------------------
# _harvest — auditoría 2026-07-02 (F1.4): en turnos con tool call interrumpido
# el ResultMessage llega sin `usage` (o no llega), así que 4 runs con 20+ tool
# calls persistieron total_tokens=0 pese a costar dinero (cost>0 sí llegaba).
# La cosecha debe recuperar tokens de los canales que SÍ están: el `usage` de
# cada AssistantMessage (sumado) y el `model_usage` del ResultMessage.
# ---------------------------------------------------------------------------

from shared_llm.providers.claude_agent import ClaudeAgentProvider  # noqa: E402


def test_interrupted_tool_turn_sums_assistant_usage() -> None:
    msgs = [
        _Obj(content=[], usage={"input_tokens": 100, "output_tokens": 10}),
        _Obj(content=[], usage={"input_tokens": 200, "output_tokens": 20}),
        # sin ResultMessage: el interrupt cortó el stream
    ]
    _, usage = ClaudeAgentProvider._harvest(msgs)
    assert usage.input_tokens == 300
    assert usage.output_tokens == 30


def test_result_model_usage_is_the_fallback_channel() -> None:
    msgs = [
        _Obj(content=[], usage=None),
        _Obj(  # ResultMessage: usage vacío pero model_usage poblado (camelCase CLI)
            content=None,
            usage={},
            total_cost_usd=0.03,
            model_usage={"claude-opus-4-8": {"inputTokens": 1500, "outputTokens": 250}},
        ),
    ]
    _, usage = ClaudeAgentProvider._harvest(msgs)
    assert usage.input_tokens == 1500
    assert usage.output_tokens == 250
    assert usage.cost_usd == 0.03


# ---------------------------------------------------------------------------
# _harvest_stop_reason — hallazgo #10c: el claude_sdk expone el motivo de parada
# (AssistantMessage.stop_reason / ResultMessage.stop_reason) pero la cosecha lo
# tiraba, así que la detección de truncado (F32, `max_tokens`) solo protegía a los
# providers HTTP. El campo tipado CompletionResponse.stop_reason lo transporta.
# ---------------------------------------------------------------------------

from shared_llm.providers.claude_agent import _harvest_stop_reason  # noqa: E402


def test_stop_reason_of_the_last_assistant_message() -> None:
    msgs = [_Obj(content=[], usage=None, stop_reason="max_tokens")]
    assert _harvest_stop_reason(msgs) == "max_tokens"


def test_stop_reason_end_turn() -> None:
    msgs = [_Obj(content=[], usage=None, stop_reason="end_turn")]
    assert _harvest_stop_reason(msgs) == "end_turn"


def test_stop_reason_none_when_absent() -> None:
    """Fakes viejos sin el atributo → None (retrocompatible)."""
    assert _harvest_stop_reason([_Obj(content=[], usage=None)]) is None
    assert _harvest_stop_reason([]) is None


def test_last_assistant_stop_reason_wins() -> None:
    """Un turno con mensajes intermedios tool_use y final max_tokens devuelve el
    ÚLTIMO — es el que decide si el turno acabó truncado."""
    msgs = [
        _Obj(content=[], usage=None, stop_reason="tool_use"),
        _Obj(content=[], usage=None, stop_reason="max_tokens"),
    ]
    assert _harvest_stop_reason(msgs) == "max_tokens"


def test_result_message_stop_reason_is_the_fallback() -> None:
    """Si ningún AssistantMessage trae stop_reason pero el ResultMessage sí, se usa
    ese como respaldo."""
    msgs = [
        _Obj(content=[], usage=None),  # assistant sin stop_reason
        _Obj(content=None, usage={}, total_cost_usd=0.01, stop_reason="end_turn"),  # result
    ]
    assert _harvest_stop_reason(msgs) == "end_turn"


def test_result_aggregate_usage_still_wins_when_present() -> None:
    msgs = [
        _Obj(content=[], usage={"input_tokens": 100, "output_tokens": 10}),
        _Obj(  # ResultMessage con usage agregado autoritativo del turno
            content=None,
            usage={"input_tokens": 1761, "output_tokens": 800},
            total_cost_usd=0.09,
        ),
    ]
    _, usage = ClaudeAgentProvider._harvest(msgs)
    assert usage.input_tokens == 1761
    assert usage.output_tokens == 800
    assert usage.cost_usd == 0.09
