"""Córtex F2 — producer ``enqueue_cortex_distill_affect`` (api-server side).

El api-server sólo PRODUCE la tarea por nombre (frontera de app limpia; no importa
el paquete ``workers``). Verifica que (a) encola ``workers.cortex_distill_affect``
en la cola ``default`` con el ``turn_id`` como arg, y (b) un fallo del broker se
traga y devuelve False (el appraisal nunca rompe el turno)."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_enqueue_sends_named_task(monkeypatch: pytest.MonkeyPatch) -> None:
    from api_server import celery_client

    captured: dict[str, Any] = {}

    class _FakeClient:
        def send_task(self, name: str, *, args: list[str], queue: str) -> None:
            captured["name"] = name
            captured["args"] = args
            captured["queue"] = queue

    monkeypatch.setattr(celery_client, "get_celery_client", _FakeClient)

    turn_id = uuid4()
    ok = await celery_client.enqueue_cortex_distill_affect(turn_id)
    assert ok is True
    assert captured["name"] == "workers.cortex_distill_affect"
    assert captured["args"] == [str(turn_id)]
    assert captured["queue"] == "default"


@pytest.mark.asyncio
async def test_enqueue_swallows_broker_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from api_server import celery_client

    class _BoomClient:
        def send_task(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("broker down")

    monkeypatch.setattr(celery_client, "get_celery_client", _BoomClient)
    ok = await celery_client.enqueue_cortex_distill_affect(uuid4())
    assert ok is False
