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
    assert set(QUEUE_NAMES) == {"default", "ingestion", "test", "review", "privileged"}


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
