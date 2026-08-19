"""Unit test — prod-06 task_prod06_colas_02 (ADR 0083 Option B).

``heavy`` and ``gpu`` were DEAD queues: declared in ``QUEUE_NAMES`` but no
producer ever routed to them (the dispatcher always uses ``dispatch_queue`` =
``default``), and on a single-host deployment there is no separate worker to
drain them. Option B trims the topology to the lanes that actually have a
producer AND a consumer. These tests pin that heavy/gpu are gone and that every
queue a producer / beat entry targets is a declared (consumed) queue.
"""

from __future__ import annotations

import pytest
from orchestrator.config import Settings as OrchestratorSettings
from workers.beat_schedule import build_beat_schedule
from workers.celery_app import DEFAULT_QUEUE, QUEUE_NAMES
from workers.config import Settings as WorkerSettings

pytestmark = pytest.mark.unit


def test_heavy_and_gpu_queues_removed() -> None:
    assert "heavy" not in QUEUE_NAMES
    assert "gpu" not in QUEUE_NAMES


def test_topology_is_exactly_the_consumed_lanes() -> None:
    # The lanes that have a producer AND a consumer on a single host.
    #
    # `marketplace` entro el 2026-08-19 (prod-13 task_prod13_01): las puertas de
    # seguridad de una instalacion, que corrian DENTRO del request del api-server
    # (bandit + semgrep + prueba de humo del sandbox, hasta ~4 min). Cumple la
    # condicion del ADR 0083 que heavy/gpu no cumplian, y esa es la razon por la
    # que esta linea puede crecer sin traicionar la decision que la puso:
    # PRODUCTOR real (`api_server.celery_client.enqueue_marketplace_install_gates`,
    # llamado por POST /marketplace/installations con `async_gates=true`) y
    # CONSUMIDOR real (`workers-marketplace`, en el compose de dev y en el que
    # genera el instalador). Quien la comprueba es
    # `tests/unit/test_marketplace_no_sync_subprocess_in_async.py`, que mira los
    # dos composes, y `tests/unit/test_compose_generator.py`, que compara las
    # colas drenadas con esta misma tupla.
    assert set(QUEUE_NAMES) == {
        "default",
        "ingestion",
        "test",
        "review",
        "privileged",
        "marketplace",
    }


def test_dispatch_queue_is_a_declared_queue() -> None:
    # The orchestrator enqueues run_execution onto this lane — it must be drained.
    assert OrchestratorSettings().dispatch_queue in QUEUE_NAMES
    assert DEFAULT_QUEUE in QUEUE_NAMES


def test_every_beat_entry_targets_a_declared_queue() -> None:
    # No scheduled task may target a queue no worker drains (no dead lanes).
    sched = build_beat_schedule(WorkerSettings())
    for name, entry in sched.items():
        options = entry.get("options") or {}
        queue = options.get("queue")
        assert queue in QUEUE_NAMES, f"beat entry {name!r} targets undeclared queue {queue!r}"
