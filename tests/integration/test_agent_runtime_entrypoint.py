"""Integration tests: the agent-runtime container runs the agent loop
(task_02_29).

The entrypoint reads a task spec from `AGENT_TASK_SPEC`, runs the
LangGraph agent loop, and emits one JSON line per step on stdout plus a
final result line. These tests launch the real `agent-runtime:v1` image
through the hardened `AgentContainerRunner` and assert on that stream.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from workers.config import Settings
from workers.container import AgentContainerRunner, ContainerSpec

import docker

from ._docker_helpers import docker_client, requires_docker

pytestmark = [pytest.mark.integration, requires_docker]

_IMAGE = "agent-runtime:v1"

_DESCRIPTION = "exercise the containerised agent loop"


@pytest.fixture(scope="module", autouse=True)
def _agent_runtime_image() -> None:
    """Skip cleanly if agent-runtime:v1 has not been built on this host."""
    client = docker_client()
    try:
        client.images.get(_IMAGE)
    except docker.errors.ImageNotFound:  # pragma: no cover - env-dependent
        pytest.skip(f"{_IMAGE} not built — run: docker build -t {_IMAGE} ...")
    finally:
        client.close()


def _spec(*, model: dict[str, Any], budgets: dict[str, Any] | None = None) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "task": {"id": "t-faseG", "title": "Write a sea poem", "description": _DESCRIPTION},
        "model": model,
    }
    if budgets is not None:
        spec["budgets"] = budgets
    return spec


def _run(spec: dict[str, Any] | None) -> tuple[int, list[dict[str, Any]]]:
    """Launch the agent-runtime container; return (exit_code, json_lines)."""
    env = {"AGENT_TASK_SPEC": json.dumps(spec)} if spec is not None else {}
    result = AgentContainerRunner(Settings()).run(ContainerSpec(image=_IMAGE, env=env))
    lines = [json.loads(line) for line in result.logs.splitlines() if line.strip().startswith("{")]
    return result.exit_code, lines


# ---------------------------------------------------------------------------
# A loop run
# ---------------------------------------------------------------------------
_ACT_THEN_FINISH = {
    "kind": "scripted",
    "decisions": [
        {
            "kind": "act",
            "tool": "echo",
            "tool_args": {"text": "draft"},
            "tokens_in": 100,
            "tokens_out": 20,
            "cost_usd": 0.001,
        },
        {"kind": "finish", "output": "the sea poem"},
    ],
}


def test_runs_the_loop_and_streams_step_events() -> None:
    exit_code, lines = _run(_spec(model=_ACT_THEN_FINISH))
    assert exit_code == 0

    events = [line["event"] for line in lines]
    assert events[0] == "execution.started"
    assert events[-1] == "execution.finished"
    assert events.count("step") >= 1

    # Every streamed step carries the steps_log shape.
    step_events = [line["step"] for line in lines if line["event"] == "step"]
    assert all({"index", "kind", "node"} <= set(step) for step in step_events)
    assert any(step["kind"] == "model_call" for step in step_events)


def test_finished_result_carries_status_and_output() -> None:
    _exit, lines = _run(_spec(model=_ACT_THEN_FINISH))
    finished = next(line for line in lines if line["event"] == "execution.finished")
    result = finished["result"]
    assert result["status"] == "done"
    assert result["output"] == "the sea poem"
    assert result["iterations"] == 2
    assert result["usage"]["model_calls"] >= 1


def test_aborted_run_is_reported() -> None:
    # Two distinct actions + max_iterations=2: the loop aborts on the
    # third planning turn before loop detection can fire.
    model = {
        "kind": "scripted",
        "decisions": [
            {"kind": "act", "tool": "echo", "tool_args": {"text": "a"}},
            {"kind": "act", "tool": "echo", "tool_args": {"text": "b"}},
        ],
    }
    exit_code, lines = _run(_spec(model=model, budgets={"max_iterations": 2}))
    assert exit_code == 0  # the loop ran; an abort is not a crash

    finished = next(line for line in lines if line["event"] == "execution.finished")
    assert finished["result"]["status"] == "aborted"
    assert finished["result"]["abort_code"] == "max_iterations_exceeded"


def test_no_spec_falls_back_to_the_selftest() -> None:
    exit_code, lines = _run(None)
    assert exit_code == 0
    assert len(lines) == 1
    banner = lines[0]
    # The Fase B health banner — not an execution stream.
    assert banner["runtime"] == "agent-runtime"
    assert banner["status"] == "ready"
    assert "event" not in banner
