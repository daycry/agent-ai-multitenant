"""Provider robustness + FINISH/verdict contract (C6/C2 cluster, 2026-06-27).

Pins the audit fixes that harden the provider adapters:

  * F35 — `_DECIDE_SYSTEM` tells the model to FINISH via `submit_result`, not in
    bare prose (which left HTTP runs with `finish_status=None`); prose stays the
    explicit fallback for the claude_sdk path that has no such tool.
  * F36 — `_decision_from` has EXPLICIT precedence when several tool calls arrive:
    `submit_result` wins (FINISH); otherwise the first action call (ACT). It no
    longer trusts a blind `tool_calls[0]`.
  * F34 — the HTTP review forces `tool_choice=submit_verdict` so the verdict is
    structured, not prose → inconclusive → escalation.
  * F25/F30 — `_run_with_retry` bounds each call by a wall-clock timeout (typed
    `ProviderTimeout`) and retries transient errors (rate-limit / 5xx / timeout)
    with backoff, RE-RAISING the typed error once the budget is spent.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any

import pytest
from agent_runtime.model import DecisionKind
from agent_runtime.providers import (
    _DECIDE_SYSTEM,
    _SUBMIT_RESULT_TOOL,
    _SUBMIT_VERDICT_TOOL,
    _SUBMIT_VERDICT_TOOL_CHOICE,
    ProviderTimeout,
    _decision_from,
    _pass_marker_present,
    _ProviderModelClient,
    _run_with_retry,
)
from shared_llm import AuthError, Message, ProviderError, RateLimitError


# --- helpers ------------------------------------------------------------------
def _resp(*, tool_calls: Any = None, content: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        tool_calls=tool_calls or [],
        content=content,
        model="m",
        usage=SimpleNamespace(input_tokens=1, output_tokens=2, cost_usd=0.0),
    )


def _call(name: str, **args: Any) -> SimpleNamespace:
    return SimpleNamespace(name=name, arguments=args)


class _RecordingProvider:
    """A fake `LLMProvider` that records the kwargs each `complete()` received."""

    name = "fake"

    def __init__(self, resp: SimpleNamespace) -> None:
        self._resp = resp
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        messages: Sequence[Message],  # noqa: ARG002 — must match the Protocol signature
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> SimpleNamespace:
        self.calls.append({"model": model, "tools": tools, "kwargs": kwargs})
        return self._resp

    def stream(self, *a: Any, **k: Any) -> Any:  # pragma: no cover - unused
        raise NotImplementedError

    async def aclose(self) -> None:  # pragma: no cover - unused
        return None


# --- F35: the decide system prompt finishes via submit_result -----------------
def test_decide_system_prompt_finishes_via_submit_result() -> None:
    lowered = _DECIDE_SYSTEM.lower()
    assert "submit_result" in _DECIDE_SYSTEM
    # It must NOT prescribe the old "plain text and NO tool call" finish, which
    # contradicted the structured-finish tool on the HTTP providers.
    assert "no tool call" not in lowered
    assert "plain text and no tool" not in lowered
    # Prose is offered only as the no-tool fallback (the claude_sdk path).
    assert "plain prose" in lowered


# --- F36: multi tool-call precedence in _decision_from ------------------------
def test_submit_result_wins_over_concurrent_action_call() -> None:
    # A real action call AND submit_result in one turn → submit_result wins.
    resp = _resp(
        tool_calls=[
            _call("write_file", path="a.py", content="x"),
            _call("submit_result", status="success", summary="todo hecho"),
        ]
    )
    decision = _decision_from(resp, model="m").decision
    assert decision.kind == DecisionKind.FINISH
    assert decision.output == "todo hecho"
    assert decision.finish_status == "success"


def test_first_action_call_taken_when_no_submit_result() -> None:
    # Several action calls, none submit_result → the FIRST is taken (ACT).
    resp = _resp(
        tool_calls=[
            _call("read_file", path="a.py"),
            _call("write_file", path="b.py", content="y"),
        ]
    )
    decision = _decision_from(resp, model="m").decision
    assert decision.kind == DecisionKind.ACT
    assert decision.tool == "read_file"


def test_submit_result_wins_even_when_not_first() -> None:
    # Precedence is by NAME, not position: submit_result last still finishes.
    resp = _resp(
        tool_calls=[
            _call("list_files"),
            _call("read_file", path="z"),
            _call("submit_result", status="partial", summary="parcial"),
        ]
    )
    decision = _decision_from(resp, model="m").decision
    assert decision.kind == DecisionKind.FINISH
    assert decision.finish_status == "partial"


# --- F34: HTTP review forces tool_choice=submit_verdict -----------------------
def test_http_review_forces_submit_verdict_tool_choice() -> None:
    prov = _RecordingProvider(_resp(tool_calls=[_call("submit_verdict", passed=True)]))
    client = _ProviderModelClient(provider=prov, model="m")
    client.review({"task": {"title": "T"}, "output": "x"})
    kwargs = prov.calls[0]["kwargs"]
    assert kwargs.get("tool_choice") == _SUBMIT_VERDICT_TOOL_CHOICE
    assert prov.calls[0]["tools"] == [_SUBMIT_VERDICT_TOOL]


def test_http_decide_does_not_force_tool_choice() -> None:
    # decide() must keep tool_choice free (it has to be able to ACT), and it
    # advertises submit_result alongside the agent's tools.
    prov = _RecordingProvider(_resp(content="done"))
    client = _ProviderModelClient(provider=prov, model="m", tools=[{"name": "write_file"}])
    client.decide({"task": {"title": "T"}})
    call = prov.calls[0]
    assert "tool_choice" not in call["kwargs"]
    assert _SUBMIT_RESULT_TOOL in (call["tools"] or [])


# --- H4: la consolidación decide/review preserva el protocolo claude_sdk -------
def test_claude_sdk_decide_never_advertises_submit_result() -> None:
    """claude_sdk NO anuncia submit_result (un tool call forzado deja content=''
    y pierde la prosa — su FINISH es prosa + <finish>) y pasa `effort` nativo."""
    from agent_runtime.providers import ClaudeSDKModelClient

    client = ClaudeSDKModelClient(
        model="claude-x", tools=[{"name": "write_file"}], reasoning_effort="high"
    )
    prov = _RecordingProvider(_resp(content='done <finish status="success"/>'))
    client.provider = prov
    client.decide({"task": {"title": "T"}})
    call = prov.calls[0]
    names = [t.get("name") for t in (call["tools"] or [])]
    assert "submit_result" not in names
    assert "write_file" in names
    assert call["kwargs"].get("effort") == "high"
    assert "tool_choice" not in call["kwargs"]


def test_claude_sdk_review_does_not_force_tool_choice() -> None:
    """El review del SDK ofrece submit_verdict pero sin tool_choice (no soportado
    por el camino CLI; la red de prosa de _review_from es el fallback)."""
    from agent_runtime.providers import ClaudeSDKModelClient

    client = ClaudeSDKModelClient(model="claude-x")
    prov = _RecordingProvider(_resp(tool_calls=[_call("submit_verdict", passed=True)]))
    client.provider = prov
    client.review({"task": {"title": "T"}, "output": "x"})
    call = prov.calls[0]
    assert call["tools"] == [_SUBMIT_VERDICT_TOOL]
    assert "tool_choice" not in call["kwargs"]


# --- F33: negation-aware pass matcher -----------------------------------------
def test_pass_marker_present_respects_negation() -> None:
    assert _pass_marker_present("el output satisface los criterios", "satisface los criterios")
    assert not _pass_marker_present("no satisface los criterios", "satisface los criterios")
    # An unnegated occurrence anywhere still counts as a pass.
    assert _pass_marker_present(
        "cumple los criterios; no cumple ninguna mala práctica", "cumple los criterios"
    )


# --- F25/F30: timeout + retry around the provider call ------------------------
def test_run_with_retry_returns_on_first_success() -> None:
    async def _ok() -> str:
        return "ok"

    assert _run_with_retry(_ok, attempts=3, backoff=0.0) == "ok"


def test_run_with_retry_retries_transient_then_succeeds() -> None:
    state = {"n": 0}

    async def _flaky() -> str:
        state["n"] += 1
        if state["n"] < 3:
            raise RateLimitError("slow down")
        return "ok"

    assert _run_with_retry(_flaky, attempts=3, backoff=0.0) == "ok"
    assert state["n"] == 3


def test_run_with_retry_reraises_after_exhausting_attempts() -> None:
    state = {"n": 0}

    async def _always_503() -> str:
        state["n"] += 1
        raise ProviderError("upstream 503", status_code=503)

    with pytest.raises(ProviderError):
        _run_with_retry(_always_503, attempts=2, backoff=0.0)
    assert state["n"] == 2  # exactly `attempts` tries, then re-raise


def test_run_with_retry_does_not_retry_permanent_errors() -> None:
    state = {"n": 0}

    async def _auth() -> str:
        state["n"] += 1
        raise AuthError("bad token")

    with pytest.raises(AuthError):
        _run_with_retry(_auth, attempts=3, backoff=0.0)
    assert state["n"] == 1  # AuthError is permanent → no retry


def test_run_with_retry_does_not_retry_4xx_provider_error() -> None:
    state = {"n": 0}

    async def _bad_request() -> str:
        state["n"] += 1
        raise ProviderError("bad request", status_code=400)

    with pytest.raises(ProviderError):
        _run_with_retry(_bad_request, attempts=3, backoff=0.0)
    assert state["n"] == 1  # a 4xx is not transient


def test_run_with_retry_times_out_to_typed_error() -> None:
    async def _hang() -> str:
        await asyncio.sleep(10)
        return "never"

    with pytest.raises(ProviderTimeout):
        _run_with_retry(_hang, timeout=0.05, attempts=1, backoff=0.0)


def test_run_with_retry_backoff_is_invoked_between_attempts() -> None:
    slept: list[float] = []

    async def _always_rl() -> str:
        raise RateLimitError("429")

    with pytest.raises(RateLimitError):
        # `jitter` pinned to its maximum (prod-07 task_prod07_01): the wait is now
        # jittered into [d/2, d] so N parallel agents don't all come back to a
        # rate-limited provider at the same instant. Pinning it keeps THIS test's
        # original point exact — 2 sleeps for 3 attempts, growing exponentially,
        # none after the last attempt.
        _run_with_retry(_always_rl, attempts=3, backoff=1.0, sleep=slept.append, jitter=lambda: 1.0)
    assert slept == [1.0, 2.0]
