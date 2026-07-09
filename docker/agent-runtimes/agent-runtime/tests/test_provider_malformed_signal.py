"""F32 — the malformed/truncated robustness signal is CONSUMED (2026-06-27).

Phase 1 exposed, in `shared_llm`'s OpenAI-compatible parse path, a signal that
distinguishes "the model gave us nothing" from "we LOST what the model gave us"
(`CompletionSignals.truncated` / `.malformed_tool_args`, derivable from
`CompletionResponse.raw`). This unit closes the gap the audit left: `providers.py`
now CONSUMES that signal so a corrupt/truncated outcome is not silently degraded.

  * `_decision_from`: a `submit_result` whose `arguments` were corrupt (present but
    undecodable → summary/status LOST) or a response cut off at the token cap
    (finish_reason=length) no longer becomes a FINISH with empty output that looks
    legitimate — it becomes a no-op ACT so the loop takes another (bounded) turn.
    A GENUINELY ABSENT summary still keeps the historical empty-FINISH wrap.
  * `_review_from`: a corrupt/truncated `submit_verdict` stays INCONCLUSIVE but its
    feedback is relabelled "corrupt/truncated, retry" — distinct from ambiguous
    prose. A WELL-FORMED verdict and the no-tool-call prose path are untouched.

Payloads are built in the real `/chat/completions` shape and run through the real
`parse_chat_completion`, so the parsed `tool_calls` and the `raw` signal stay
consistent (a faithful fake of what the HTTP providers hand `_decision_from`).
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from agent_runtime.model import DecisionKind
from agent_runtime.providers import (
    _CORRUPT_VERDICT_FEEDBACK,
    _completion_signals,
    _decision_from,
    _review_from,
)
from shared_llm.providers._openai_compat import parse_chat_completion


# --- helpers ------------------------------------------------------------------
def _tc(name: str, arguments: Any) -> dict[str, Any]:
    """One raw `tool_calls` entry; `arguments` is the JSON STRING the model emits."""
    return {"id": "call_1", "type": "function", "function": {"name": name, "arguments": arguments}}


def _payload(
    *,
    tool_calls: list[dict[str, Any]] | None = None,
    content: str = "",
    finish_reason: str = "stop",
) -> dict[str, Any]:
    """An openai `/chat/completions` payload — the shape `CompletionResponse.raw` holds."""
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {
        "model": "m",
        "choices": [{"index": 0, "finish_reason": finish_reason, "message": message}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 2},
    }


def _resp(payload: dict[str, Any]) -> Any:
    """A real `CompletionResponse` (parsed tool_calls + the raw signal, consistent)."""
    return parse_chat_completion(payload, provider="fake", fallback_model="m")


# --- _completion_signals: defensive on every shape ----------------------------
def test_signals_default_when_no_raw() -> None:
    # A response with no `raw` attribute (the test fakes elsewhere) → all-False.
    s = _completion_signals(SimpleNamespace())
    assert s.truncated is False and s.malformed_tool_args is False


def test_signals_default_on_claude_sdk_list_raw() -> None:
    # claude_sdk stores a LIST of SDK messages in `raw`, not an openai dict → all-False,
    # so the claude_sdk path can never flag truncation / malformed args.
    s = _completion_signals(SimpleNamespace(raw=["msg-a", "msg-b"]))
    assert s.truncated is False and s.malformed_tool_args is False


# --- _decision_from: well-formed FINISH is EXACTLY as before -------------------
def test_well_formed_submit_result_finishes_unchanged() -> None:
    args = json.dumps({"status": "success", "summary": "todo hecho"})
    decision = _decision_from(_resp(_payload(tool_calls=[_tc("submit_result", args)])), model="m")
    d = decision.decision
    assert d.kind == DecisionKind.FINISH
    assert d.output == "todo hecho"
    assert d.finish_status == "success"


def test_absent_summary_keeps_historical_empty_finish() -> None:
    # The model called submit_result with NO args (empty arguments) — genuinely
    # ABSENT, not corrupt (malformed=False). Historical behaviour: empty FINISH,
    # status None — NOT a retry. This is the "distinguish corrupt from absent" line.
    decision = _decision_from(_resp(_payload(tool_calls=[_tc("submit_result", "")])), model="m")
    d = decision.decision
    assert d.kind == DecisionKind.FINISH
    assert d.finish_status is None
    assert d.output == ""


# --- _decision_from: corrupt / truncated FINISH retries instead of degrading ---
def test_malformed_submit_result_args_retries_not_empty_finish() -> None:
    # arguments present but undecodable (truncated mid-string) → summary/status LOST.
    # Must NOT become a FINISH that masquerades as legitimate; emit a no-op ACT.
    bad = '{"status": "success", "summary": "todo he'
    decision = _decision_from(_resp(_payload(tool_calls=[_tc("submit_result", bad)])), model="m")
    d = decision.decision
    assert d.kind == DecisionKind.ACT
    assert d.tool == "noop"
    assert d.finish_status is None


def test_truncated_response_with_submit_result_retries() -> None:
    # finish_reason=length: the body (incl. the args JSON) may be cut off → retry,
    # even when the args we managed to parse look complete.
    args = json.dumps({"status": "success", "summary": "parcial"})
    payload = _payload(tool_calls=[_tc("submit_result", args)], finish_reason="length")
    d = _decision_from(_resp(payload), model="m").decision
    assert d.kind == DecisionKind.ACT
    assert d.tool == "noop"


def test_legit_finish_not_blocked_by_unrelated_corrupt_call() -> None:
    # A DISCARDED action call has corrupt args, but submit_result itself is clean —
    # the finish is legitimate and must NOT be turned into a retry.
    good = json.dumps({"status": "success", "summary": "ok"})
    payload = _payload(tool_calls=[_tc("write_file", "{bad json"), _tc("submit_result", good)])
    d = _decision_from(_resp(payload), model="m").decision
    assert d.kind == DecisionKind.FINISH
    assert d.output == "ok"
    assert d.finish_status == "success"


# --- _decision_from: the ACT and prose branches are untouched -----------------
def test_action_only_corrupt_call_still_acts() -> None:
    # The F32 guard applies ONLY to the submit_result FINISH branch. A corrupt
    # action-only call still ACTs with degraded ({}) args, exactly as before.
    d = _decision_from(_resp(_payload(tool_calls=[_tc("write_file", "{bad")])), model="m").decision
    assert d.kind == DecisionKind.ACT
    assert d.tool == "write_file"
    assert d.tool_args == {}


def test_prose_finish_unchanged() -> None:
    d = _decision_from(_resp(_payload(content="Terminé la tarea.")), model="m").decision
    assert d.kind == DecisionKind.FINISH
    assert d.output == "Terminé la tarea."
    assert d.finish_status is None


# --- hallazgo #10c: la señal de truncado del claude_sdk (stop_reason) protege ---
#     también el FINISH en prosa (antes solo protegía a los providers HTTP vía raw)
from shared_llm.types import CompletionResponse, Usage  # noqa: E402


def _sdk_resp(content: str, *, stop_reason: str | None) -> CompletionResponse:
    """Un CompletionResponse como el que devuelve claude_sdk: `raw` es una LISTA de
    mensajes SDK (de la que completion_signals NO deriva truncado) y la señal viaja
    en el campo tipado `stop_reason`."""
    return CompletionResponse(
        content=content,
        model="m",
        provider="claude_sdk",
        usage=Usage(),
        tool_calls=None,
        raw=["msg-a", "msg-b"],
        stop_reason=stop_reason,
    )


def test_completion_signals_maps_max_tokens_stop_reason_to_truncated() -> None:
    # claude_sdk: aunque `raw` sea una lista (all-False), stop_reason=max_tokens
    # marca truncado — la señal que antes solo tenían los providers HTTP.
    s = _completion_signals(_sdk_resp("cortado a mit", stop_reason="max_tokens"))
    assert s.truncated is True


def test_completion_signals_end_turn_stop_reason_not_truncated() -> None:
    s = _completion_signals(_sdk_resp("respuesta completa", stop_reason="end_turn"))
    assert s.truncated is False
    # sin stop_reason (fakes viejos / SDK antiguo) tampoco marca truncado
    assert _completion_signals(_sdk_resp("x", stop_reason=None)).truncated is False


def test_prose_finish_truncated_retries_instead_of_finishing() -> None:
    # El FINISH en prosa de claude_sdk cortado en el tope de salida NO se acepta como
    # entregable legítimo: se emite un noop ACT para reintentar (bounded).
    d = _decision_from(_sdk_resp("He implementado la funci", stop_reason="max_tokens"), model="m")
    assert d.decision.kind == DecisionKind.ACT
    assert d.decision.tool == "noop"


def test_prose_finish_not_truncated_still_finishes() -> None:
    # Regresión: una prosa completa (end_turn) sigue cerrando como FINISH.
    d = _decision_from(_sdk_resp("Tarea terminada.", stop_reason="end_turn"), model="m")
    assert d.decision.kind == DecisionKind.FINISH
    assert d.decision.output == "Tarea terminada."


# --- _review_from: well-formed verdict is EXACTLY as before --------------------
def test_well_formed_submit_verdict_unchanged() -> None:
    args = json.dumps({"passed": True, "feedback": "ok"})
    r = _review_from(_resp(_payload(tool_calls=[_tc("submit_verdict", args)])), model="m")
    assert r.passed is True and r.inconclusive is False and r.feedback == "ok"


def test_truncated_but_valid_passed_is_still_honored() -> None:
    # finish_reason=length but `passed` parsed as a real boolean → the verdict bit
    # is intact; honour it (do NOT manufacture inconclusive from truncation alone).
    args = json.dumps({"passed": True, "feedback": "ok"})
    payload = _payload(tool_calls=[_tc("submit_verdict", args)], finish_reason="length")
    r = _review_from(_resp(payload), model="m")
    assert r.passed is True and r.inconclusive is False


# --- _review_from: corrupt verdict → inconclusive WITH a distinct label --------
def test_malformed_submit_verdict_is_inconclusive_with_corrupt_label() -> None:
    payload = _payload(tool_calls=[_tc("submit_verdict", '{"passed": tr')])  # corrupt JSON
    r = _review_from(_resp(payload), model="m")
    assert r.passed is False and r.inconclusive is True
    assert r.feedback == _CORRUPT_VERDICT_FEEDBACK


def test_truncated_submit_verdict_losing_passed_is_labelled_corrupt() -> None:
    # finish_reason=length AND the args JSON is cut off before `passed` → inconclusive,
    # relabelled corrupt (distinct from ambiguous prose, feeds a bounded review retry).
    payload = _payload(tool_calls=[_tc("submit_verdict", '{"feedba')], finish_reason="length")
    r = _review_from(_resp(payload), model="m")
    assert r.passed is False and r.inconclusive is True
    assert r.feedback == _CORRUPT_VERDICT_FEEDBACK


def test_genuinely_missing_passed_keeps_original_feedback() -> None:
    # Valid JSON object, no corruption, but `passed` is genuinely absent → inconclusive
    # but NOT relabelled (it is not corruption — the model just omitted the field).
    args = json.dumps({"feedback": "hmm"})
    r = _review_from(_resp(_payload(tool_calls=[_tc("submit_verdict", args)])), model="m")
    assert r.passed is False and r.inconclusive is True
    assert r.feedback == "hmm"


def test_ambiguous_prose_verdict_not_relabelled() -> None:
    # The no-tool-call prose path is untouched: ambiguous → inconclusive, original
    # content as feedback, never the corrupt label.
    r = _review_from(_resp(_payload(content="he mirado el código")), model="m")
    assert r.passed is False and r.inconclusive is True
    assert r.feedback != _CORRUPT_VERDICT_FEEDBACK
