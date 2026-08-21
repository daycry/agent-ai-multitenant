"""Integration test — prod-06 task_prod06_dag_03 (parte B).

``workers.sample_queue_metrics`` samples Celery queue depth (Redis LLEN per
queue) + task counts per lifecycle status (DB, all tenants) and writes the
node-exporter textfile. This drives the async core against the real test DB +
test Redis and asserts the rendered ``.prom`` file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from redis.asyncio import Redis

from ._redis_url import TEST_REDIS_URL  # con credencial; ver _redis_url.py

pytestmark = pytest.mark.integration


@pytest.fixture()
def schema_at_head(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


@pytest.fixture()
def workers_settings(monkeypatch: pytest.MonkeyPatch, migrations_pg_dsn: str, tmp_path: Any) -> Any:
    async_dsn = migrations_pg_dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    monkeypatch.setenv("WORKERS_DATABASE_URL", async_dsn)
    monkeypatch.setenv("WORKERS_BROKER_URL", TEST_REDIS_URL)
    monkeypatch.setenv("WORKERS_QUEUE_METRICS_TEXTFILE_PATH", str(tmp_path / "q.prom"))
    from workers.config import reset_settings_cache

    reset_settings_cache()
    from workers.config import get_settings

    yield get_settings()
    reset_settings_cache()


async def _seed_tasks(dsn: str) -> UUID:
    tenant_id = uuid4()
    project_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE executions, task_dependencies, tasks, plans, agents, projects,"
            " organizations RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T', 't-qmetrics')",
            tenant_id,
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status, worker_config)"
            " VALUES ($1, $2, 'P', 'active', '{}'::jsonb)",
            project_id,
            tenant_id,
        )
        # 2 in_review, 1 backlog, 1 ready (tasks is not soft-deletable).
        for status, n in [("in_review", 2), ("backlog", 1), ("ready", 1)]:
            for _ in range(n):
                await conn.execute(
                    "INSERT INTO tasks (id, tenant_id, project_id, title, status, priority)"
                    " VALUES ($1, $2, $3, 't', $4, 'medium')",
                    uuid4(),
                    tenant_id,
                    project_id,
                    status,
                )
        return tenant_id
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_sample_writes_queue_and_status_metrics(
    schema_at_head: None, migrations_pg_dsn: str, workers_settings: Any
) -> None:
    await _seed_tasks(migrations_pg_dsn)

    # Put 2 messages on the `default` Celery queue list in the test broker.
    redis = Redis.from_url(TEST_REDIS_URL)
    await redis.delete("default", "ingestion", "test", "review", "privileged")
    await redis.rpush("default", "msg-a", "msg-b")
    try:
        from workers.maintenance import _sample_queue_metrics_async

        result = await _sample_queue_metrics_async(workers_settings)
    finally:
        await redis.delete("default")
        await redis.aclose()

    assert result["written"] is True
    assert result["queue_depths"]["default"] == 2
    # The other declared queues are present and empty.
    assert result["queue_depths"]["review"] == 0
    assert result["status_counts"]["in_review"] == 2
    assert result["status_counts"]["backlog"] == 1
    assert result["status_counts"]["ready"] == 1

    body = Path(workers_settings.queue_metrics_textfile_path).read_text(encoding="utf-8")
    assert 'agentic_celery_queue_depth{queue="default"} 2' in body
    assert 'agentic_tasks_by_status{status="in_review"} 2' in body
    assert 'agentic_tasks_by_status{status="backlog"} 1' in body


@pytest.mark.asyncio
async def test_sample_publishes_the_celery_outcome_counters(
    schema_at_head: None, migrations_pg_dsn: str, workers_settings: Any
) -> None:
    """prod-08 task_prod08_metrics_workers_05, contra Redis de verdad.

    Las señales de Celery corren en los N procesos hijos del pool prefork, así
    que acumulan en Redis y el sampler los publica una vez por pasada. Este test
    recorre ese camino entero con el cliente síncrono que usan las señales y el
    async que usa el sampler — que son clientes distintos sobre las mismas
    claves, justo donde un cambio de nombre de clave pasaría desapercibido en un
    unitario con dobles.
    """
    from redis import Redis as SyncRedis
    from workers.task_metrics import DURATION_HASH_KEY, TASKS_HASH_KEY, record_task_outcome

    writer = SyncRedis.from_url(TEST_REDIS_URL)
    writer.delete(TASKS_HASH_KEY, DURATION_HASH_KEY)
    record_task_outcome(writer, queue="default", status="success", duration_s=2.0)
    record_task_outcome(writer, queue="review", status="failure", duration_s=1.5)

    try:
        from workers.maintenance import _sample_queue_metrics_async

        await _sample_queue_metrics_async(workers_settings)
    finally:
        writer.delete(TASKS_HASH_KEY, DURATION_HASH_KEY)
        writer.close()

    body = Path(workers_settings.queue_metrics_textfile_path).read_text(encoding="utf-8")
    assert 'agentic_celery_tasks_total{queue="default",status="success"} 1' in body
    assert 'agentic_celery_tasks_total{queue="review",status="failure"} 1' in body
    assert 'agentic_celery_task_duration_seconds_total{queue="default"} 2' in body
    # Y el colector nuevo declara que corrió bien: sin esto, un fallo suyo sería
    # una familia ausente y muda.
    assert 'agentic_sampler_collector_up{collector="celery_tasks"} 1' in body


@pytest.mark.asyncio
async def test_sample_publishes_pending_human_approvals(
    schema_at_head: None, migrations_pg_dsn: str, workers_settings: Any
) -> None:
    """El gauge sale SIEMPRE, también valiendo cero.

    Cero pendientes es el estado sano y es un dato: omitirlo dejaría a Prometheus
    sin poder distinguirlo de «el colector se cayó», y `HumanApprovalsStale`
    dejaría de evaluarse en silencio.
    """
    from workers.maintenance import _sample_queue_metrics_async

    await _sample_queue_metrics_async(workers_settings)

    body = Path(workers_settings.queue_metrics_textfile_path).read_text(encoding="utf-8")
    assert "agentic_human_approvals_pending 0" in body
    assert "agentic_human_approvals_oldest_age_seconds 0" in body
    assert 'agentic_sampler_collector_up{collector="approvals"} 1' in body
