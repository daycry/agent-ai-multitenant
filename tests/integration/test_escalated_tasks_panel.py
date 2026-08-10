"""Integration test: GET /plans/{id}/escalated-tasks panel (F44).

The escalated-tasks panel must surface BOTH human-escalation paths:

  * the ADR 0020 approval state ``awaiting_human_approval``;
  * production review-exhaustion / self-reported failure, which escalates to
    ``blocked`` with a review abort code on the LATEST execution
    (``review_inconclusive`` / ``max_review_retries_exhausted`` /
    ``agent_reported_failure``).

A plain ``blocked`` task (no review abort code on its latest execution) is a
different kind of block and must STAY OUT of the panel — including a task whose
latest execution is clean even though an OLDER execution carried a review code.

DB-backed: applies migrations (incl. 0101 CHECK) and seeds via asyncpg. The
operator runs it; this module is marked ``integration`` and is not part of the
unit run.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

from ._partitions import ensure_partition_for

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


async def _seed_tenant(dsn: str) -> dict[str, UUID]:
    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE executions, task_dependencies, tasks, plans, conversations,"
            " projects, agents, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            tenant_id,
            "Tenant Esc",
            "tenant-esc",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-esc",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3)",
            user_id,
            "alice@esc.test",
            "argon2-placeholder",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, $4)",
            uuid4(),
            tenant_id,
            user_id,
            "tenant_admin",
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, $3)",
            project_id,
            tenant_id,
            "Esc Project",
        )
    finally:
        await conn.close()
    return {"tenant_id": tenant_id, "user_id": user_id, "project_id": project_id}


async def _insert_task(
    dsn: str,
    *,
    tenant_id: UUID,
    project_id: UUID,
    plan_id: UUID,
    status: str,
    title: str,
) -> UUID:
    task_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, plan_id, title, status, priority)"
            " VALUES ($1, $2, $3, $4, $5, $6, 'medium')",
            task_id,
            tenant_id,
            project_id,
            plan_id,
            title,
            status,
        )
    finally:
        await conn.close()
    return task_id


async def _insert_execution(
    dsn: str,
    *,
    tenant_id: UUID,
    task_id: UUID,
    abort_code: str | None,
    created_at: datetime,
    status: str | None = None,
) -> None:
    # `executions` está particionada por mes y SIN DEFAULT (ADR 0151): el llamante
    # retrofecha (`now - 5 min`), que cae en el mes anterior si el test corre en los
    # primeros minutos del mes. Ver
    # docs/03-guides/gotchas/sembrar-filas-retrofechadas-en-tabla-particionada.md
    await ensure_partition_for(dsn, "executions", created_at)
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO executions (id, tenant_id, task_id, status, abort_code, created_at)"
            " VALUES ($1, $2, $3, $4, $5, $6)",
            uuid4(),
            tenant_id,
            task_id,
            status or ("aborted" if abort_code else "completed"),
            abort_code,
            created_at,
        )
    finally:
        await conn.close()


@pytest.fixture()
def configured_app(
    alembic_config,
    app_database_url: str,
    admin_database_url: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
):
    command.upgrade(alembic_config, "head")
    from tests.integration.conftest import _flush_redis, _grant_app_user_existing_tables

    asyncio.run(_grant_app_user_existing_tables())
    asyncio.run(_flush_redis(test_redis_url))

    monkeypatch.setenv("API_SERVER_DATABASE_URL", app_database_url)
    monkeypatch.setenv("API_SERVER_ADMIN_DATABASE_URL", admin_database_url)
    monkeypatch.setenv("API_SERVER_REDIS_URL", test_redis_url)
    monkeypatch.setenv("API_SERVER_JWT_SECRET", "test-secret")

    from api_server.auth.deps import reset_redis_cache
    from api_server.config import get_settings
    from api_server.db.session import reset_engine_cache

    get_settings.cache_clear()
    reset_engine_cache()
    reset_redis_cache()

    from api_server.main import create_app

    app = create_app()
    try:
        yield app
    finally:
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()


async def _mint_token(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


@pytest.mark.asyncio
async def test_escalated_panel_includes_blocked_with_review_abort_code(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed_tenant(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}
    tenant_id = seeded["tenant_id"]
    project_id = seeded["project_id"]

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        create = await client.post(
            f"/projects/{project_id}/plans",
            json={"title": "Esc plan"},  # no spec → no auto tasks
            headers=headers,
        )
        assert create.status_code == 201, create.text
        plan_id = UUID(create.json()["id"])

        now = datetime.now(UTC)

        # 1. blocked + latest execution review_inconclusive → IN panel.
        t_review = await _insert_task(
            migrations_pg_dsn,
            tenant_id=tenant_id,
            project_id=project_id,
            plan_id=plan_id,
            status="blocked",
            title="review-escalated",
        )
        await _insert_execution(
            migrations_pg_dsn,
            tenant_id=tenant_id,
            task_id=t_review,
            abort_code="review_inconclusive",
            created_at=now,
        )

        # 2. blocked + latest execution agent_reported_failure → IN panel.
        t_failed = await _insert_task(
            migrations_pg_dsn,
            tenant_id=tenant_id,
            project_id=project_id,
            plan_id=plan_id,
            status="blocked",
            title="agent-failed",
        )
        await _insert_execution(
            migrations_pg_dsn,
            tenant_id=tenant_id,
            task_id=t_failed,
            abort_code="agent_reported_failure",
            created_at=now,
        )

        # 3. awaiting_human_approval (ADR 0020) → IN panel, reason None.
        t_approval = await _insert_task(
            migrations_pg_dsn,
            tenant_id=tenant_id,
            project_id=project_id,
            plan_id=plan_id,
            status="awaiting_human_approval",
            title="approval",
        )

        # 4. blocked + non-review abort code (commit_failed) → OUT.
        t_plain = await _insert_task(
            migrations_pg_dsn,
            tenant_id=tenant_id,
            project_id=project_id,
            plan_id=plan_id,
            status="blocked",
            title="plain-block",
        )
        await _insert_execution(
            migrations_pg_dsn,
            tenant_id=tenant_id,
            task_id=t_plain,
            abort_code="commit_failed",
            created_at=now,
        )

        # 5. blocked, OLD review code but LATEST execution clean → OUT
        #    (validates the latest-execution-wins ordering).
        t_recovered = await _insert_task(
            migrations_pg_dsn,
            tenant_id=tenant_id,
            project_id=project_id,
            plan_id=plan_id,
            status="blocked",
            title="recovered",
        )
        await _insert_execution(
            migrations_pg_dsn,
            tenant_id=tenant_id,
            task_id=t_recovered,
            abort_code="review_inconclusive",
            created_at=now - timedelta(minutes=5),
        )
        await _insert_execution(
            migrations_pg_dsn,
            tenant_id=tenant_id,
            task_id=t_recovered,
            abort_code=None,
            created_at=now,
        )

        # 6. done → OUT.
        await _insert_task(
            migrations_pg_dsn,
            tenant_id=tenant_id,
            project_id=project_id,
            plan_id=plan_id,
            status="done",
            title="done",
        )

        # 7. Auditoría 2026-07-02 (F1.1): el runtime escala con execution.status
        #    = needs_human_review y abort_codes que el panel NO enumeraba
        #    (max_iterations_exceeded / repetitive_loop_detected /
        #    research_exhausted / self_review_stalemate) → la task quedaba
        #    blocked e INVISIBLE, sin acciones. El criterio nuevo es el ESTADO
        #    del último run, no la lista de códigos.
        t_runtime_esc: dict[str, UUID] = {}
        for code in (
            "max_iterations_exceeded",
            "repetitive_loop_detected",
            "research_exhausted",
            "self_review_stalemate",
        ):
            tid = await _insert_task(
                migrations_pg_dsn,
                tenant_id=tenant_id,
                project_id=project_id,
                plan_id=plan_id,
                status="blocked",
                title=f"runtime-esc-{code}",
            )
            await _insert_execution(
                migrations_pg_dsn,
                tenant_id=tenant_id,
                task_id=tid,
                abort_code=code,
                created_at=now,
                status="needs_human_review",
            )
            t_runtime_esc[code] = tid

        resp = await client.get(f"/plans/{plan_id}/escalated-tasks", headers=headers)
        assert resp.status_code == 200, resp.text
        tasks = resp.json()["tasks"]
        by_id = {t["id"]: t for t in tasks}

        expected = {str(t_review), str(t_failed), str(t_approval)} | {
            str(tid) for tid in t_runtime_esc.values()
        }
        assert set(by_id) == expected
        assert by_id[str(t_review)]["escalation_reason"] == "review_inconclusive"
        assert by_id[str(t_review)]["status"] == "blocked"
        assert by_id[str(t_failed)]["escalation_reason"] == "agent_reported_failure"
        assert by_id[str(t_approval)]["escalation_reason"] is None
        assert by_id[str(t_approval)]["status"] == "awaiting_human_approval"
        for code, tid in t_runtime_esc.items():
            assert by_id[str(tid)]["escalation_reason"] == code
            assert by_id[str(tid)]["status"] == "blocked"
