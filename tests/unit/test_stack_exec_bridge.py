"""Unit: the `stack_exec` api→worker bridge helper (ADR 0093).

`run_stack_command_and_wait` enqueues `workers.run_stack_command` on the `test`
queue and blocks on the result backend for rc+logs — the synchronous half of the
bridge the `/internal/agent/run-stack` endpoint delegates to.
"""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import api_server.celery_client as cc
import pytest

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_run_stack_command_and_wait_sends_to_test_queue_and_returns_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def _send_task(name: str, *, args: list[object], queue: str) -> MagicMock:
        captured["name"] = name
        captured["args"] = args
        captured["queue"] = queue
        async_result = MagicMock()
        async_result.get = MagicMock(
            return_value={"exit_code": 0, "logs": "composer ok", "timed_out": False}
        )
        return async_result

    fake_client = MagicMock()
    fake_client.send_task = _send_task
    monkeypatch.setattr(cc, "get_celery_client", lambda: fake_client)

    tenant_id, task_id = uuid4(), uuid4()
    result = await cc.run_stack_command_and_wait(
        tenant_id=tenant_id, task_id=task_id, command="composer install", timeout_s=300
    )

    assert result == {"exit_code": 0, "logs": "composer ok", "timed_out": False}
    assert captured["name"] == "workers.run_stack_command"
    assert captured["queue"] == "test"  # separate lane → no deadlock with the agent run
    payload = captured["args"][0]  # type: ignore[index]
    assert payload["command"] == "composer install"
    assert payload["tenant_id"] == str(tenant_id)
    assert payload["task_id"] == str(task_id)
    assert payload["timeout_s"] == 300


@pytest.mark.asyncio
async def test_run_stack_command_and_wait_coerces_non_dict_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = MagicMock()
    fake_client.send_task.return_value.get.return_value = "not-a-dict"
    monkeypatch.setattr(cc, "get_celery_client", lambda: fake_client)

    result = await cc.run_stack_command_and_wait(
        tenant_id=uuid4(), task_id=uuid4(), command="php -v", timeout_s=60
    )
    assert result == {}
