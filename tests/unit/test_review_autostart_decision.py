"""Unit tests: review-runtime autostart pure decisions (C8 F39 / ADR 0063).

The DB-bound autostart wiring (idempotency query, enqueue) is exercised by an
integration test; here we pin the pure helpers the orchestrator uses to build the
``compose_review_runtime`` payload — image/port resolution + the human checklist
parser — without a DB.
"""

from __future__ import annotations

from typing import Any

import pytest
from api_server.review_autostart import (
    DEFAULT_REVIEW_MAIN_IMAGE,
    DEFAULT_REVIEW_MAIN_PORT,
    build_review_human_checklist,
    resolve_review_main_image,
    resolve_review_main_port,
)
from orchestrator.dispatch import (
    _COMPOSE_REVIEW_RUNTIME_TASK,
    _REVIEW_QUEUE,
    TaskDispatcher,
)

pytestmark = pytest.mark.unit


# --- resolve_review_main_image --------------------------------------------


def test_main_image_prefers_repository_review_image() -> None:
    img = resolve_review_main_image(
        {"review_image": "backend:plan-1", "main_image": "ignored"}, None
    )
    assert img == "backend:plan-1"


def test_main_image_falls_back_through_chain() -> None:
    assert resolve_review_main_image({"main_image": "app:latest"}, None) == "app:latest"
    assert resolve_review_main_image(None, {"review_main_image": "svc:1"}) == "svc:1"


def test_main_image_default_when_unpinned() -> None:
    assert resolve_review_main_image(None, None) == DEFAULT_REVIEW_MAIN_IMAGE
    assert resolve_review_main_image({}, {}) == DEFAULT_REVIEW_MAIN_IMAGE
    # Blank / non-string never wins.
    assert resolve_review_main_image({"review_image": "  "}, None) == DEFAULT_REVIEW_MAIN_IMAGE
    assert resolve_review_main_image({"review_image": 123}, None) == DEFAULT_REVIEW_MAIN_IMAGE


# --- resolve_review_main_port ---------------------------------------------


def test_main_port_resolution_and_default() -> None:
    assert resolve_review_main_port({"review_port": 3000}, None) == 3000
    assert resolve_review_main_port({"main_port": "9090"}, None) == 9090
    assert resolve_review_main_port(None, {"review_main_port": 5000}) == 5000
    assert resolve_review_main_port(None, None) == DEFAULT_REVIEW_MAIN_PORT


def test_main_port_ignores_bool_and_garbage() -> None:
    # bool is an int subclass — must NOT be read as a port.
    assert resolve_review_main_port({"main_port": True}, None) == DEFAULT_REVIEW_MAIN_PORT
    assert resolve_review_main_port({"main_port": "abc"}, None) == DEFAULT_REVIEW_MAIN_PORT


# --- build_review_human_checklist -----------------------------------------


def test_checklist_parses_dict_entries() -> None:
    spec = {
        "tests_humans": [
            {"id": "h1", "description": "login works", "hint": "use admin"},
            {"title": "logout works", "checklist": ["click", "verify"]},
        ]
    }
    items = build_review_human_checklist(spec)
    assert items[0] == {"id": "h1", "description": "login works", "hint": "use admin"}
    assert items[1]["id"] == "human_02"
    assert items[1]["description"] == "logout works"
    assert items[1]["checklist"] == ["click", "verify"]


def test_checklist_accepts_string_entries_and_legacy_key() -> None:
    items = build_review_human_checklist({"tests_humanos": ["check the homepage"]})
    assert items == [{"id": "human_01", "description": "check the homepage"}]


def test_checklist_empty_on_missing_or_malformed() -> None:
    assert build_review_human_checklist(None) == []
    assert build_review_human_checklist({}) == []
    assert build_review_human_checklist({"tests_humans": "not-a-list"}) == []
    # Blank strings are dropped.
    assert build_review_human_checklist({"tests_humans": ["   "]}) == []


# --- enqueue routing (no DB) ----------------------------------------------


class _FakeCelery:
    """Records ``send_task`` calls without a broker."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def send_task(self, name: str, **kwargs: Any) -> None:
        self.calls.append({"name": name, **kwargs})


@pytest.mark.asyncio
async def test_enqueue_review_runtime_routes_to_review_queue() -> None:
    """The autostart enqueue must hit the review task on the review queue with the
    request as ``kwargs={'request': ...}`` (matches the worker signature)."""
    celery = _FakeCelery()
    dispatcher = TaskDispatcher(
        sessionmaker=None,  # type: ignore[arg-type]
        celery_app=celery,  # type: ignore[arg-type]
        settings=None,  # type: ignore[arg-type]
    )
    request = {"plan_id": "p1", "tenant_id": "t1", "main_image": "app:1"}
    await dispatcher._enqueue_review_runtime(request)

    assert len(celery.calls) == 1
    call = celery.calls[0]
    assert call["name"] == _COMPOSE_REVIEW_RUNTIME_TASK
    assert call["queue"] == _REVIEW_QUEUE
    assert call["kwargs"] == {"request": request}


@pytest.mark.asyncio
async def test_enqueue_review_runtime_swallows_broker_failure() -> None:
    """A broker blip must NOT raise into the done-handler (the plan transition is
    already committed; the autostart retries on a later trigger)."""

    class _BoomCelery:
        def send_task(self, *_a: Any, **_k: Any) -> None:
            raise RuntimeError("broker down")

    dispatcher = TaskDispatcher(
        sessionmaker=None,  # type: ignore[arg-type]
        celery_app=_BoomCelery(),  # type: ignore[arg-type]
        settings=None,  # type: ignore[arg-type]
    )
    # Must not raise.
    await dispatcher._enqueue_review_runtime({"plan_id": "p1"})
