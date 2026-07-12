"""Unit tests for orchestrator dispatch helpers (C3 F01/F02/F05).

Pure-ish logic exercised without a DB or broker:
  - ``_is_transient_db_error`` classifies a DB connectivity blip vs a
    deterministic fault (C3 F05);
  - ``_transient_db_guard`` re-raises only transient DB errors as
    ``TransientHandlerError`` (so the consumer retries via reclaim);
  - ``_publish_status_changed`` emits the right ``old_status -> new_status``
    pair — the revert path emits ``in_progress -> ready`` so the Kanban
    re-syncs (C3 F02).
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from orchestrator import dispatch as dispatch_mod
from orchestrator.assignment import Candidate
from orchestrator.config import Settings
from orchestrator.consumer import TransientHandlerError
from orchestrator.dispatch import TaskDispatcher, _is_transient_db_error
from orchestrator.events import EVENT_TASK_STATUS_CHANGED, TaskEvent
from sqlalchemy.exc import DBAPIError, IntegrityError, InterfaceError, OperationalError

pytestmark = pytest.mark.unit

_TENANT = "11111111-1111-1111-1111-111111111111"
_PROJECT = "22222222-2222-2222-2222-222222222222"
_TASK = "33333333-3333-3333-3333-333333333333"


def _event() -> TaskEvent:
    return TaskEvent(
        stream_id="1-0",
        type=EVENT_TASK_STATUS_CHANGED,
        tenant_id=_TENANT,
        project_id=_PROJECT,
        task_id=_TASK,
        occurred_at="2026-06-27T00:00:00+00:00",
        payload={"old_status": "ready", "new_status": "in_progress"},
    )


# ---------------------------------------------------------------------------
# _is_transient_db_error
# ---------------------------------------------------------------------------
def test_operational_and_interface_errors_are_transient() -> None:
    assert _is_transient_db_error(OperationalError("SELECT 1", {}, Exception("reset")))
    assert _is_transient_db_error(InterfaceError("SELECT 1", {}, Exception("gone")))


def test_invalidated_connection_dbapi_error_is_transient() -> None:
    err = DBAPIError("SELECT 1", {}, Exception("dropped"))
    err.connection_invalidated = True
    assert _is_transient_db_error(err)


def test_deterministic_db_error_is_not_transient() -> None:
    # An integrity violation would fail again on retry → dead-letter, not reclaim.
    assert not _is_transient_db_error(IntegrityError("INSERT", {}, Exception("dup")))
    assert not _is_transient_db_error(ValueError("not a db error"))


# ---------------------------------------------------------------------------
# _transient_db_guard
# ---------------------------------------------------------------------------
def _dispatcher(redis: object | None = None) -> TaskDispatcher:
    return TaskDispatcher(
        sessionmaker=object(),  # type: ignore[arg-type]
        celery_app=object(),  # type: ignore[arg-type]
        settings=Settings(),
        redis=redis,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_guard_reraises_transient_as_transient_handler_error() -> None:
    disp = _dispatcher()
    with pytest.raises(TransientHandlerError):
        async with disp._transient_db_guard("op"):
            raise OperationalError("SELECT 1", {}, Exception("reset"))


@pytest.mark.asyncio
async def test_guard_passes_through_non_transient_error() -> None:
    disp = _dispatcher()
    with pytest.raises(ValueError):
        async with disp._transient_db_guard("op"):
            raise ValueError("real bug")


@pytest.mark.asyncio
async def test_guard_passes_through_existing_transient_handler_error() -> None:
    disp = _dispatcher()
    with pytest.raises(TransientHandlerError):
        async with disp._transient_db_guard("op"):
            raise TransientHandlerError("already transient")


# ---------------------------------------------------------------------------
# _publish_status_changed (C3 F02 — revert re-syncs the board)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_publish_status_changed_defaults_old_status_ready(monkeypatch: object) -> None:
    captured: dict[str, str] = {}

    async def _fake_publish(_redis: object, _task: object, *, old_status: str, new_status: str):
        captured["old"] = old_status
        captured["new"] = new_status

    monkeypatch.setattr(dispatch_mod, "publish_task_status_changed", _fake_publish)  # type: ignore[attr-defined]
    disp = _dispatcher(redis=object())

    await disp._publish_status_changed(_event(), "in_progress")

    assert captured == {"old": "ready", "new": "in_progress"}


@pytest.mark.asyncio
async def test_publish_status_changed_revert_emits_in_progress_to_ready(
    monkeypatch: object,
) -> None:
    captured: dict[str, str] = {}

    async def _fake_publish(_redis: object, _task: object, *, old_status: str, new_status: str):
        captured["old"] = old_status
        captured["new"] = new_status

    monkeypatch.setattr(dispatch_mod, "publish_task_status_changed", _fake_publish)  # type: ignore[attr-defined]
    disp = _dispatcher(redis=object())

    await disp._publish_status_changed(_event(), "ready", old_status="in_progress")

    assert captured == {"old": "in_progress", "new": "ready"}


@pytest.mark.asyncio
async def test_publish_status_changed_noop_without_redis(monkeypatch: object) -> None:
    called = False

    async def _fake_publish(*_a: object, **_k: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(dispatch_mod, "publish_task_status_changed", _fake_publish)  # type: ignore[attr-defined]
    disp = _dispatcher(redis=None)

    await disp._publish_status_changed(_event(), "in_progress")

    assert called is False


# ---------------------------------------------------------------------------
# _pick — the plan's per-task assignment (preset assigned_agent_id) is
# authoritative; the load policy is only a fallback (Track 2 / R3).
# ---------------------------------------------------------------------------
def _task(*, assigned_agent_id: object = None) -> object:
    return SimpleNamespace(id=uuid4(), assigned_agent_id=assigned_agent_id)


def _project(worker_config: object = None) -> object:
    return SimpleNamespace(worker_config=worker_config)


def test_pick_honors_preset_assigned_agent_over_load_policy() -> None:
    # Default policy is LOAD_BALANCED; a preset from the plan must win regardless,
    # and the (different) candidate pool must be ignored.
    preset = uuid4()
    disp = _dispatcher()
    chosen = disp._pick(
        _project(),  # no worker_config → LOAD_BALANCED default
        _task(assigned_agent_id=preset),
        [Candidate(agent_id=str(uuid4()), active_task_count=0)],
    )
    assert chosen == str(preset)


def test_pick_without_preset_falls_back_to_load_balanced() -> None:
    disp = _dispatcher()
    least_loaded = str(uuid4())
    chosen = disp._pick(
        _project(),
        _task(assigned_agent_id=None),
        [
            Candidate(agent_id=str(uuid4()), active_task_count=3),
            Candidate(agent_id=least_loaded, active_task_count=0),
        ],
    )
    assert chosen == least_loaded


# ---------------------------------------------------------------------------
# P1-7 (investigación 2026-07-11): el reviewer ve el histórico de intentos.
# ---------------------------------------------------------------------------
def test_single_output_passes_verbatim() -> None:
    from orchestrator.dispatch import _format_prior_outputs

    assert _format_prior_outputs(["único intento"]) == "único intento"


def test_multiple_outputs_are_labelled_newest_first() -> None:
    from orchestrator.dispatch import _format_prior_outputs

    out = _format_prior_outputs(["tercero (último)", "segundo", "primero"])
    assert out.index("latest") < out.index("earlier")
    assert "attempt 3" in out and "attempt 1" in out
    assert out.index("tercero (último)") < out.index("segundo") < out.index("primero")


def test_earlier_outputs_are_tail_capped() -> None:
    from orchestrator.dispatch import _REVIEW_PRIOR_OUTPUT_TAIL, _format_prior_outputs

    huge = "x" * (_REVIEW_PRIOR_OUTPUT_TAIL * 3)
    out = _format_prior_outputs(["reciente", huge])
    assert len(out) < _REVIEW_PRIOR_OUTPUT_TAIL * 2


def test_empty_outputs_render_empty() -> None:
    from orchestrator.dispatch import _format_prior_outputs

    assert _format_prior_outputs(["", "   "]) == ""
