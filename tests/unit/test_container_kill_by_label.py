"""AgentContainerRunner.kill_by_label — cooperative cancellation primitive
(auditoría zona 'ejecuciones'; slice 2 de cancel-execution).

Kills containers tagged with the execution id so an operator cancel stops the
agent container (the LLM-cost source). Tested with a fake docker client — no
real Docker needed.
"""

from __future__ import annotations

import pytest
from workers.config import Settings
from workers.container import AgentContainerRunner

pytestmark = pytest.mark.unit


class _FakeContainer:
    def __init__(self) -> None:
        self.killed = False

    def kill(self) -> None:
        self.killed = True


class _FakeContainers:
    def __init__(self, containers: list[_FakeContainer]) -> None:
        self._containers = containers
        self.list_filters: dict | None = None

    def list(self, *, filters: dict) -> list[_FakeContainer]:
        self.list_filters = filters
        return self._containers


class _FakeClient:
    def __init__(self, containers: list[_FakeContainer]) -> None:
        self.containers = _FakeContainers(containers)


def test_kill_by_label_kills_matching_containers() -> None:
    c1, c2 = _FakeContainer(), _FakeContainer()
    client = _FakeClient([c1, c2])
    runner = AgentContainerRunner(Settings(), client=client)

    killed = runner.kill_by_label("exec-123")

    assert killed == 2
    assert c1.killed and c2.killed
    # Filters by the exact per-execution label.
    assert client.containers.list_filters == {"label": "com.agentic-platform.execution-id=exec-123"}


def test_kill_by_label_is_a_noop_when_nothing_matches() -> None:
    client = _FakeClient([])
    runner = AgentContainerRunner(Settings(), client=client)
    assert runner.kill_by_label("exec-gone") == 0
