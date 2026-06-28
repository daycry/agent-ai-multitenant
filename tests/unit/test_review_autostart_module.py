"""Unit tests for the shared review-autostart module (``api_server.review_autostart``).

This module is the SINGLE source of truth for the review-runtime autostart, invoked
by BOTH the orchestrator's live ``_on_task_done`` and the convergence reconciler's
``_reconcile_complete_plans``. Here we pin, without a DB:

  * the shared constants (task name / queue / verdict window / notify event);
  * the async builder's idempotent decision (``None`` on an active session) and its
    payload assembly, driven by a tiny fake session;
  * the reconciler's autostart wiring (``workers.maintenance._autostart_review_
    runtime``): it enqueues ``compose_review_runtime`` when the builder yields a
    request, no-ops when the builder returns ``None`` (idempotency), and swallows a
    broker blip so the reconciler pass never breaks.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import api_server.review_autostart as ra
import pytest
import workers.maintenance as m
from api_server.review_autostart import (
    ACTIVE_REVIEW_STATUSES,
    COMPOSE_REVIEW_RUNTIME_TASK,
    HUMAN_VALIDATION_NEEDED_EVENT,
    REVIEW_QUEUE,
    REVIEW_VERDICT_TIMEOUT_S,
    build_review_autostart_request,
)

pytestmark = pytest.mark.unit


# --- shared constants -------------------------------------------------------


def test_shared_constants_have_expected_values() -> None:
    assert COMPOSE_REVIEW_RUNTIME_TASK == "workers.compose_review_runtime"
    assert REVIEW_QUEUE == "review"
    assert ACTIVE_REVIEW_STATUSES == ("running", "suspended")
    assert REVIEW_VERDICT_TIMEOUT_S == 48 * 60 * 60
    assert HUMAN_VALIDATION_NEEDED_EVENT == "human_validation_needed"


# --- builder (fake session, no DB) -----------------------------------------


class _Result:
    """Minimal stand-in for a SQLAlchemy ``Result`` — only ``scalar_one_or_none``."""

    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class _FakeSession:
    """Returns canned ``scalar_one_or_none`` results in call order (1 per execute)."""

    def __init__(self, values: list[Any]) -> None:
        self._values = list(values)

    async def execute(self, *_a: Any, **_k: Any) -> _Result:
        return _Result(self._values.pop(0))


@pytest.mark.asyncio
async def test_builder_returns_none_when_active_session_exists() -> None:
    """IDEMPOTENT: an active (running/suspended) session ⇒ no second runtime. This is
    exactly the property the reconciler leans on when it re-drives the transition."""
    plan = SimpleNamespace(id=uuid4(), project_id=uuid4())
    # First execute (the active-session probe) returns a non-None id ⇒ short-circuit.
    session = _FakeSession([uuid4()])
    result = await build_review_autostart_request(
        session,  # type: ignore[arg-type]
        plan=plan,  # type: ignore[arg-type]
        tenant_id=uuid4(),
    )
    assert result is None


@pytest.mark.asyncio
async def test_builder_assembles_payload_for_fresh_plan() -> None:
    tenant_id = uuid4()
    plan = SimpleNamespace(
        id=uuid4(),
        project_id=uuid4(),
        slug="my-plan",
        title="My plan",
        created_by=uuid4(),
        specification={"tests_humans": [{"id": "h1", "description": "login works"}]},
    )
    project = SimpleNamespace(
        id=uuid4(),
        slug="backend",
        name="Backend",
        repository_config={"review_image": "backend:plan-1"},
        worker_config=None,
    )
    org = SimpleNamespace(slug="org-1")
    # execute order: no active session, project found, org found.
    session = _FakeSession([None, project, org])

    request = await build_review_autostart_request(
        session,  # type: ignore[arg-type]
        plan=plan,  # type: ignore[arg-type]
        tenant_id=tenant_id,
    )

    assert request is not None
    assert request["tenant_id"] == str(tenant_id)
    assert request["plan_id"] == str(plan.id)
    assert request["repo_name"] == "backend"
    assert request["tenant_slug"] == "org-1"
    assert request["project_slug"] == "backend"
    assert request["plan_slug"] == "my-plan"
    assert request["main_image"] == "backend:plan-1"
    assert request["expires_in_seconds"] == REVIEW_VERDICT_TIMEOUT_S
    assert request["human_checklist"] == [{"id": "h1", "description": "login works"}]
    assert request["owner_user_id"] == str(plan.created_by)
    assert request["notify_event"] == HUMAN_VALIDATION_NEEDED_EVENT


@pytest.mark.asyncio
async def test_builder_returns_none_when_project_deleted() -> None:
    """A soft-deleted project ⇒ nothing to review ⇒ ``None`` (never a stuck spawn)."""
    plan = SimpleNamespace(id=uuid4(), project_id=uuid4())
    # No active session, then the project lookup misses (soft-deleted).
    session = _FakeSession([None, None])
    result = await build_review_autostart_request(
        session,  # type: ignore[arg-type]
        plan=plan,  # type: ignore[arg-type]
        tenant_id=uuid4(),
    )
    assert result is None


# --- reconciler autostart wiring (fakes, no DB) ----------------------------


class _RecordingCelery:
    """Records ``send_task`` calls without a broker."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def send_task(self, name: str, **kwargs: Any) -> None:
        self.calls.append({"name": name, **kwargs})


class _FakeDBSession:
    """Async-context session whose ``get`` yields a non-None plan sentinel."""

    async def __aenter__(self) -> _FakeDBSession:
        return self

    async def __aexit__(self, *_exc: Any) -> bool:
        return False

    async def get(self, _model: Any, _pk: Any) -> Any:
        return SimpleNamespace()


class _FakeSessionmaker:
    def __call__(self) -> _FakeDBSession:
        return _FakeDBSession()


@pytest.mark.asyncio
async def test_reconciler_autostart_enqueues_when_builder_returns_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reconciler fires ``compose_review_runtime`` on the review queue with the
    builder's request as ``kwargs={'request': ...}`` (the worker signature)."""

    async def fake_builder(_db: Any, *, plan: Any, tenant_id: Any) -> dict[str, Any]:
        return {"plan_id": "p1", "main_image": "app:1"}

    celery = _RecordingCelery()
    monkeypatch.setattr(ra, "build_review_autostart_request", fake_builder)
    monkeypatch.setattr(m, "app", celery)

    await m._autostart_review_runtime(
        _FakeSessionmaker(),  # type: ignore[arg-type]
        plan_id=uuid4(),
        tenant_id=uuid4(),
    )

    assert len(celery.calls) == 1
    call = celery.calls[0]
    assert call["name"] == COMPOSE_REVIEW_RUNTIME_TASK
    assert call["queue"] == REVIEW_QUEUE
    assert call["kwargs"] == {"request": {"plan_id": "p1", "main_image": "app:1"}}


@pytest.mark.asyncio
async def test_reconciler_autostart_noops_when_builder_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No second runtime when an active session already exists (builder → None)."""

    async def fake_builder(_db: Any, *, plan: Any, tenant_id: Any) -> None:
        return None

    celery = _RecordingCelery()
    monkeypatch.setattr(ra, "build_review_autostart_request", fake_builder)
    monkeypatch.setattr(m, "app", celery)

    await m._autostart_review_runtime(
        _FakeSessionmaker(),  # type: ignore[arg-type]
        plan_id=uuid4(),
        tenant_id=uuid4(),
    )

    assert celery.calls == []


@pytest.mark.asyncio
async def test_reconciler_autostart_swallows_broker_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broker blip must NEVER break the reconciler pass / the committed transition."""

    async def fake_builder(_db: Any, *, plan: Any, tenant_id: Any) -> dict[str, Any]:
        return {"plan_id": "p1"}

    class _BoomCelery:
        def send_task(self, *_a: Any, **_k: Any) -> None:
            raise RuntimeError("broker down")

    monkeypatch.setattr(ra, "build_review_autostart_request", fake_builder)
    monkeypatch.setattr(m, "app", _BoomCelery())

    # Must not raise.
    await m._autostart_review_runtime(
        _FakeSessionmaker(),  # type: ignore[arg-type]
        plan_id=uuid4(),
        tenant_id=uuid4(),
    )
