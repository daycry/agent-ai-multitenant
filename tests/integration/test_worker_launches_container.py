"""Integration tests: the Celery worker launches agent containers
through the Docker SDK (task_02_06).

These drive a real Docker daemon — `AgentContainerRunner` actually
starts python:3.12-slim containers, waits for them, and reaps them.
"""

from __future__ import annotations

import json

import pytest
from workers.config import Settings
from workers.container import AgentContainerRunner, ContainerSpec
from workers.tasks import run_agent_container

import docker

from ._docker_helpers import BASE_IMAGE, docker_client, ensure_base_image, requires_docker

pytestmark = [pytest.mark.integration, requires_docker]


@pytest.fixture(scope="module", autouse=True)
def _base_image() -> None:
    client = docker_client()
    try:
        ensure_base_image(client)
    finally:
        client.close()


def _runner() -> AgentContainerRunner:
    return AgentContainerRunner(Settings())


def test_runner_launches_and_captures_stdout() -> None:
    result = _runner().run(
        ContainerSpec(
            image=BASE_IMAGE,
            command=["python", "-c", "print('hello from agent container')"],
        )
    )
    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.succeeded() is True
    assert "hello from agent container" in result.logs
    assert result.container_id


def test_runner_reports_a_nonzero_exit_code() -> None:
    result = _runner().run(
        ContainerSpec(image=BASE_IMAGE, command=["python", "-c", "import sys; sys.exit(7)"])
    )
    assert result.exit_code == 7
    assert result.succeeded() is False


def test_runner_passes_environment_into_the_container() -> None:
    result = _runner().run(
        ContainerSpec(
            image=BASE_IMAGE,
            command=["python", "-c", "import os; print(os.environ['AGENT_TASK'])"],
            env={"AGENT_TASK": "task-42"},
        )
    )
    assert result.exit_code == 0
    assert "task-42" in result.logs


def test_runner_removes_the_container_afterwards() -> None:
    result = _runner().run(ContainerSpec(image=BASE_IMAGE, command=["python", "-c", "print(1)"]))
    client = docker_client()
    try:
        with pytest.raises(docker.errors.NotFound):
            client.containers.get(result.container_id)
    finally:
        client.close()


def test_runner_kills_a_container_past_its_time_budget() -> None:
    result = _runner().run(
        ContainerSpec(image=BASE_IMAGE, command=["python", "-c", "import time; time.sleep(60)"]),
        timeout=2,
    )
    assert result.timed_out is True
    assert result.succeeded() is False


def test_celery_task_returns_a_json_safe_result() -> None:
    out = run_agent_container(
        image=BASE_IMAGE, command=["python", "-c", "print('via celery task')"]
    )
    assert isinstance(out, dict)
    assert out["exit_code"] == 0
    assert out["timed_out"] is False
    assert "via celery task" in out["logs"]
    # The result backend serialises as JSON — this must not raise.
    assert json.loads(json.dumps(out)) == out


def test_ensure_network_creates_the_dedicated_internal_network() -> None:
    name = _runner().ensure_network()
    assert name == "agentic-agents"

    client = docker_client()
    try:
        net = client.networks.get(name)
        assert net.attrs["Internal"] is True
        assert net.attrs["Driver"] == "bridge"
    finally:
        client.close()


def test_ensure_network_is_idempotent() -> None:
    runner = _runner()
    assert runner.ensure_network() == runner.ensure_network() == "agentic-agents"
