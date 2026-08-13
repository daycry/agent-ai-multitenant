"""AUD16 menor C/H7 (auditoría 2026-07-16): topología de colas del dispatcher.

``Queue(name)`` sin exchange/routing_key hacía que la cola priority quedara
ligada al exchange de la default con la routing key de la default (kombu asigna
el default-exchange del app a las colas sin exchange propio): el aislamiento de
la lane priority era nominal. Antes de separar workers por lane, cada cola debe
declarar exchange y routing key PROPIOS (``Queue(name, Exchange(name),
routing_key=name)``).
"""

from __future__ import annotations

import pytest
from notification_dispatcher.celery_app import build_celery_app
from notification_dispatcher.config import Settings

pytestmark = pytest.mark.unit


def _settings() -> Settings:
    return Settings(
        broker_url="redis://localhost:6379/1",
        result_backend="redis://localhost:6379/1",
    )


def test_each_lane_declares_its_own_exchange_and_routing_key() -> None:
    app = build_celery_app(_settings())
    queues = {q.name: q for q in app.conf.task_queues}

    assert set(queues) == {"notifications.default", "notifications.priority"}
    for name, queue in queues.items():
        assert queue.routing_key == name, f"{name} routing_key={queue.routing_key!r}"
        assert queue.exchange is not None and queue.exchange.name == name, (
            f"{name} exchange={queue.exchange!r}"
        )
