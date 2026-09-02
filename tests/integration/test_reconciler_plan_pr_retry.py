"""El reconciler reencola el auto-PR de un plan `completed` que se quedó sin él.

Auditoría 2026-09-01 (D-01), `task_cv_14`. El auto-PR se encola UNA vez al
validar el plan; si el broker no estaba o el worker murió con la task en la
mano, el plan queda `completed` sin `pr_url` ni `pr_error` para siempre. La
pasada (e) del reconciler lo reencola pidiendo el MISMO PR que el cierre por
veredicto, y deja en paz a los planes que ya tienen URL, a los que ya tienen un
motivo de fallo visible y a los recién cerrados.

Exige PostgreSQL con migraciones (stack de pruebas).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from api_server.celery_client import auto_pr_request
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


async def _seed(dsn: str) -> dict[str, UUID]:
    ids = {
        "tenant": uuid4(),
        "project": uuid4(),
        "plan_lost": uuid4(),
        "plan_recent": uuid4(),
        "plan_with_pr": uuid4(),
        "plan_with_error": uuid4(),
        "plan_ancient": uuid4(),
        "plan_open": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("TRUNCATE plans, projects, organizations RESTART IDENTITY CASCADE")
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T', 't-plan-pr-retry')",
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status, is_template)"
            " VALUES ($1, $2, 'P', 'active', false)",
            ids["project"],
            ids["tenant"],
        )
        rows: list[tuple[str, str, str, str | None, str | None]] = [
            ("plan_lost", "completed", "20 minutes", None, None),
            ("plan_recent", "completed", "3 minutes", None, None),
            ("plan_with_pr", "completed", "20 minutes", "https://x/pull/1", None),
            ("plan_with_error", "completed", "20 minutes", None, "GitHub PR falló (401)"),
            ("plan_ancient", "completed", "30 days", None, None),
            ("plan_open", "in_progress", "20 minutes", None, None),
        ]
        for key, status, age, pr_url, pr_error in rows:
            await conn.execute(
                "INSERT INTO plans (id, tenant_id, project_id, title, status, pr_url, pr_error,"
                " updated_at) VALUES ($1, $2, $3, $4, $5, $6, $7,"
                f" now() - interval '{age}')",
                ids[key],
                ids["tenant"],
                ids["project"],
                key,
                status,
                pr_url,
                pr_error,
            )
        return ids
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_only_the_lost_enqueue_is_retried_and_asks_for_the_same_pr(
    _migrated: None, workers_settings: object, migrations_pg_dsn: str
) -> None:
    from workers.maintenance.reconciler import _reconcile_plans_without_pr

    ids = await _seed(migrations_pg_dsn)
    sent: list[dict[str, Any]] = []

    async def _record(project_id: UUID, plan_id: UUID, *, title: str, body: str) -> bool:
        sent.append({"project_id": project_id, "plan_id": plan_id, "title": title, "body": body})
        return True

    engine = create_async_engine(workers_settings.database_url)  # type: ignore[attr-defined]
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        retried = await _reconcile_plans_without_pr(
            sessionmaker,
            now=datetime.now(UTC),
            min_age=timedelta(minutes=10),
            max_age=timedelta(days=7),
            enqueue=_record,
        )
    finally:
        await engine.dispose()

    assert retried == 1
    assert [s["plan_id"] for s in sent] == [ids["plan_lost"]]
    assert sent[0]["project_id"] == ids["project"]
    title, body = auto_pr_request(ids["plan_lost"], "plan_lost")
    assert (sent[0]["title"], sent[0]["body"]) == (
        title,
        body,
    ), "el reencolado tiene que pedir el MISMO PR que el cierre por veredicto"


@pytest.mark.asyncio
async def test_a_broker_failure_is_not_counted_and_is_retried_next_sweep(
    _migrated: None, workers_settings: object, migrations_pg_dsn: str
) -> None:
    from workers.maintenance.reconciler import _reconcile_plans_without_pr

    await _seed(migrations_pg_dsn)

    async def _broker_down(*_a: Any, **_k: Any) -> bool:
        return False

    engine = create_async_engine(workers_settings.database_url)  # type: ignore[attr-defined]
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        retried = await _reconcile_plans_without_pr(
            sessionmaker, now=datetime.now(UTC), enqueue=_broker_down
        )
    finally:
        await engine.dispose()
    assert retried == 0
