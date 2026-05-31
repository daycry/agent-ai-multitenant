"""Unit tests for the temporary installer backend (Plan 15 task_15_01).

Asserts the Phase-A shell:
  * the FastAPI app imports and serves /healthz;
  * the 9-step wizard flow metadata is correct and ordered;
  * the wizard state machine advances / goes back / refuses illegal moves;
  * the prereq route runs an INJECTED checker (mocked — no Docker host) and
    computes the install gate from required probes only.

No host access: every host-touching action is behind a seam and faked here.
The real install/uninstall is a HUMAN test in the plan; nothing here shells
out to docker or writes to /data.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from installer_backend.main import create_app, get_prereq_checker
from installer_backend.seams import PrereqResult, StubPrereqChecker
from installer_backend.wizard import (
    CONFIRMATION_STEP,
    STEP_ORDER,
    WizardError,
    WizardState,
    WizardStep,
    next_step,
    previous_step,
)


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


# ---------------------------------------------------------------------------
# Health + import
# ---------------------------------------------------------------------------
def test_healthz_ok(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "installer"


# ---------------------------------------------------------------------------
# 9-step flow metadata
# ---------------------------------------------------------------------------
def test_steps_route_lists_nine_ordered_steps(client: TestClient) -> None:
    resp = client.get("/api/wizard/steps")
    assert resp.status_code == 200
    body = resp.json()
    steps = body["steps"]

    assert len(steps) == 9
    assert [s["id"] for s in steps] == [s.value for s in STEP_ORDER]
    # index is 0-based and monotonically increasing
    assert [s["index"] for s in steps] == list(range(9))
    # bilingual titles present (ES + EN per docs_language)
    assert all(s["title_es"] and s["title_en"] for s in steps)
    # exactly one confirmation step, and it is the summary
    confirmation = [s for s in steps if s["is_confirmation"]]
    assert len(confirmation) == 1
    assert confirmation[0]["id"] == CONFIRMATION_STEP.value == "summary"
    assert body["confirmation_step"] == "summary"


def test_first_and_last_steps_are_welcome_and_done() -> None:
    assert STEP_ORDER[0] is WizardStep.WELCOME
    assert STEP_ORDER[-1] is WizardStep.DONE


# ---------------------------------------------------------------------------
# Pure state machine
# ---------------------------------------------------------------------------
def test_next_and_previous_step_helpers() -> None:
    assert next_step(WizardStep.WELCOME) is WizardStep.BASICS
    assert next_step(WizardStep.DONE) is None
    assert previous_step(WizardStep.WELCOME) is None
    assert previous_step(WizardStep.BASICS) is WizardStep.WELCOME


def test_advance_walks_the_whole_flow() -> None:
    state = WizardState()
    visited = [state.current]
    while state.can_advance:
        state = state.advance()
        visited.append(state.current)
    assert visited == list(STEP_ORDER)
    assert state.is_last
    assert state.furthest is WizardStep.DONE


def test_advance_past_terminal_raises() -> None:
    state = WizardState(current=WizardStep.DONE, furthest=WizardStep.DONE)
    with pytest.raises(WizardError):
        state.advance()


def test_go_back_from_first_raises() -> None:
    with pytest.raises(WizardError):
        WizardState().go_back()


def test_advance_stores_payload_without_secrets_in_state() -> None:
    state = WizardState()
    state = state.advance({"system_name": "acme"})
    assert state.current is WizardStep.BASICS
    assert state.data["welcome"] == {"system_name": "acme"}


def test_goto_forbids_forward_skip_but_allows_visited() -> None:
    state = WizardState().advance().advance()  # at RESOURCES, furthest=RESOURCES
    # can jump back to a visited step
    back = state.goto(WizardStep.BASICS)
    assert back.current is WizardStep.BASICS
    # cannot jump forward to an unvisited step
    with pytest.raises(WizardError):
        state.goto(WizardStep.SUMMARY)


# ---------------------------------------------------------------------------
# Wizard transition routes (stateless server transitions)
# ---------------------------------------------------------------------------
def test_advance_route_moves_forward(client: TestClient) -> None:
    resp = client.post(
        "/api/wizard/advance",
        json={"state": {"current": "welcome", "furthest": "welcome"}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"]["current"] == "basics"
    assert body["can_go_back"] is True


def test_advance_route_rejects_terminal(client: TestClient) -> None:
    resp = client.post(
        "/api/wizard/advance",
        json={"state": {"current": "done", "furthest": "done"}},
    )
    assert resp.status_code == 409


def test_back_route_moves_backward(client: TestClient) -> None:
    resp = client.post(
        "/api/wizard/back",
        json={"state": {"current": "basics", "furthest": "basics"}},
    )
    assert resp.status_code == 200
    assert resp.json()["state"]["current"] == "welcome"


# ---------------------------------------------------------------------------
# Prereq route with an INJECTED (mocked) checker — no real Docker host
# ---------------------------------------------------------------------------
def test_prereq_route_uses_injected_checker() -> None:
    app = create_app()

    def fake_checker() -> StubPrereqChecker:
        return StubPrereqChecker(
            results=[
                PrereqResult(key="docker", label="Docker", ok=True, detail="27.0"),
                PrereqResult(key="ram", label="RAM >= 8GB", ok=False, detail="4GB"),
                # informational GPU probe failing must NOT block the install
                PrereqResult(key="gpu", label="NVIDIA GPU", ok=False, required=False),
            ]
        )

    app.dependency_overrides[get_prereq_checker] = fake_checker
    client = TestClient(app)

    resp = client.get("/api/prereqs")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["results"]) == 3
    # a REQUIRED probe (ram) failed -> install gate is closed
    assert body["all_required_ok"] is False


def test_prereq_gate_open_when_required_pass_even_if_optional_fails() -> None:
    app = create_app()

    def fake_checker() -> StubPrereqChecker:
        return StubPrereqChecker(
            results=[
                PrereqResult(key="docker", label="Docker", ok=True),
                PrereqResult(key="gpu", label="GPU", ok=False, required=False),
            ]
        )

    app.dependency_overrides[get_prereq_checker] = fake_checker
    client = TestClient(app)

    body = client.get("/api/prereqs").json()
    assert body["all_required_ok"] is True
