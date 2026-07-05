"""g1 step 7 (ADR 0102 D4): the worker persists runtime guardrail events.

Covers the NEW mapping logic in `_persist_guardrail_events` — event dict →
`record_guardrail_event` args, the empty-events short-circuit (no project load),
and the best-effort SAVEPOINT (a persist failure never propagates). The DB
persistence itself is covered by tests/integration/test_guardrail_events.py.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

from workers import execution
from workers.execution import _persist_guardrail_events, _RuntimeResult


class _FakeNested:
    async def __aenter__(self) -> _FakeNested:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False  # never suppress — a failure propagates to the caller's try


class _FakeSession:
    def begin_nested(self) -> _FakeNested:
        return _FakeNested()


def _result(events: list[dict]) -> _RuntimeResult:
    return _RuntimeResult(
        status="done",
        abort_code=None,
        output=None,
        iterations=1,
        steps=[],
        usage={},
        guardrail_events=events,
    )


def _run(coro: object) -> None:
    asyncio.run(coro)  # type: ignore[arg-type]


def test_persist_maps_each_event(monkeypatch) -> None:
    calls: list[dict] = []

    async def fake_record(session: object, **kwargs: object) -> None:
        calls.append(kwargs)

    async def fake_load_project(session: object, task_id: object) -> object:
        return SimpleNamespace(id=uuid4())

    monkeypatch.setattr(execution, "_load_project", fake_load_project)
    monkeypatch.setattr("api_server.guardrails.events.record_guardrail_event", fake_record)

    events = [
        {
            "guardrail_type": "prompt_injection",
            "hook_point": "post_tool",
            "severity": "high",
            "action": "warn",
            "detail": "[instruction_override] in post_tool text.",
            "detail_payload": {"match_count": 1},
        }
    ]
    _run(
        _persist_guardrail_events(
            _FakeSession(),
            _result(events),
            tenant_id=uuid4(),
            task_id=uuid4(),
            execution_id=uuid4(),
            agent_id=None,
        )
    )
    assert len(calls) == 1
    assert calls[0]["guardrail_type"] == "prompt_injection"
    assert calls[0]["hook_point"] == "post_tool"
    assert calls[0]["action"] == "warn"


def test_persist_empty_short_circuits(monkeypatch) -> None:
    loaded: list[int] = []

    async def fake_load_project(session: object, task_id: object) -> object:
        loaded.append(1)
        return None

    monkeypatch.setattr(execution, "_load_project", fake_load_project)
    _run(
        _persist_guardrail_events(
            _FakeSession(),
            _result([]),
            tenant_id=uuid4(),
            task_id=uuid4(),
            execution_id=uuid4(),
            agent_id=None,
        )
    )
    assert loaded == []  # no events → never even loads the project


def test_persist_is_best_effort_on_failure(monkeypatch) -> None:
    async def fake_record(session: object, **kwargs: object) -> None:
        raise RuntimeError("db boom")

    async def fake_load_project(session: object, task_id: object) -> object:
        return SimpleNamespace(id=uuid4())

    monkeypatch.setattr(execution, "_load_project", fake_load_project)
    monkeypatch.setattr("api_server.guardrails.events.record_guardrail_event", fake_record)

    # Must NOT raise — a persist failure can never fail an otherwise-finished run.
    _run(
        _persist_guardrail_events(
            _FakeSession(),
            _result(
                [
                    {
                        "guardrail_type": "prompt_injection",
                        "hook_point": "post_tool",
                        "severity": "high",
                        "action": None,
                        "detail": "d",
                        "detail_payload": {},
                    }
                ]
            ),
            tenant_id=uuid4(),
            task_id=uuid4(),
            execution_id=uuid4(),
            agent_id=None,
        )
    )
