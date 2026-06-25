"""Integration test — prod-06 task_prod06_beat_02.

The ingestion sweep must CLAIM documents with an enqueue LEASE instead of
re-enqueueing every ``pending`` row past the age cutoff. A >5-min backlog on the
``ingestion`` queue used to make the sweep re-enqueue documents that were still
legitimately queued (duplicate work). Now the sweep re-enqueues only documents
whose lease (``enqueued_at``) is NULL (the enqueue never landed) or has expired,
and it stamps the lease atomically on claim so concurrent sweeps do not double up.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command

pytestmark = pytest.mark.integration


async def _seed(dsn: str) -> dict[str, UUID]:
    tenant_id = uuid4()
    kb_id = uuid4()
    ids = {
        "never_old": uuid4(),  # pending, enqueued_at NULL, created 10m ago  → claim
        "recent_lease": uuid4(),  # pending, enqueued 1m ago               → skip
        "expired_lease": uuid4(),  # pending, enqueued 20m ago             → claim
        "too_young": uuid4(),  # pending, enqueued_at NULL, created 10s ago → skip
        "deleted": uuid4(),  # pending but soft-deleted                    → skip
        "indexed": uuid4(),  # not pending                                 → skip
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE kb_projects, chunks, documents, knowledge_bases,"
            " memory_entries, plans, conversations, projects, agents, teams,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'Acme', 'acme-lease')",
            tenant_id,
        )
        await conn.execute(
            "INSERT INTO knowledge_bases (id, tenant_id, name) VALUES ($1, $2, 'KB')",
            kb_id,
            tenant_id,
        )

        async def _doc(
            doc_id: UUID,
            *,
            status: str,
            created_secs_ago: int,
            enqueued_secs_ago: int | None,
            deleted: bool,
        ) -> None:
            await conn.execute(
                "INSERT INTO documents"
                " (id, tenant_id, kb_id, title, source_filename, source_mime_type,"
                "  source_storage_key, source_size_bytes, status, created_at,"
                "  enqueued_at, deleted_at)"
                " VALUES ($1, $2, $3, 'Doc', 'm.pdf', 'application/pdf', $4, 1, $5,"
                "  now() - make_interval(secs => $6),"
                "  CASE WHEN $7::int IS NULL THEN NULL"
                "       ELSE now() - make_interval(secs => $7::int) END,"
                "  CASE WHEN $8 THEN now() - make_interval(secs => 100) ELSE NULL END)",
                doc_id,
                tenant_id,
                kb_id,
                f"kb/{doc_id}/source.pdf",
                status,
                created_secs_ago,
                enqueued_secs_ago,
                deleted,
            )

        await _doc(
            ids["never_old"],
            status="pending",
            created_secs_ago=600,
            enqueued_secs_ago=None,
            deleted=False,
        )
        await _doc(
            ids["recent_lease"],
            status="pending",
            created_secs_ago=600,
            enqueued_secs_ago=60,
            deleted=False,
        )
        await _doc(
            ids["expired_lease"],
            status="pending",
            created_secs_ago=1800,
            enqueued_secs_ago=1200,
            deleted=False,
        )
        await _doc(
            ids["too_young"],
            status="pending",
            created_secs_ago=10,
            enqueued_secs_ago=None,
            deleted=False,
        )
        await _doc(
            ids["deleted"],
            status="pending",
            created_secs_ago=600,
            enqueued_secs_ago=None,
            deleted=True,
        )
        await _doc(
            ids["indexed"],
            status="indexed",
            created_secs_ago=600,
            enqueued_secs_ago=None,
            deleted=False,
        )
        return ids
    finally:
        await conn.close()


@pytest.fixture()
def schema_at_head(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


@pytest.fixture()
def workers_settings(monkeypatch: pytest.MonkeyPatch, migrations_pg_dsn: str) -> Any:
    async_dsn = migrations_pg_dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    monkeypatch.setenv("WORKERS_DATABASE_URL", async_dsn)
    from workers.config import reset_settings_cache

    reset_settings_cache()
    from workers.config import get_settings

    yield get_settings()
    reset_settings_cache()


@pytest.mark.asyncio
async def test_sweep_claims_only_leaseless_or_expired(
    schema_at_head: None, migrations_pg_dsn: str, workers_settings: Any
) -> None:
    ids = await _seed(migrations_pg_dsn)
    enqueued: list[UUID] = []

    from workers.ingestion import _sweep_pending_documents_async

    result = await _sweep_pending_documents_async(
        settings=workers_settings,
        enqueue=lambda doc_id: (enqueued.append(doc_id), True)[1],
        older_than_seconds=300,
        lease_seconds=600,
    )

    # Only the leaseless-and-old and the expired-lease documents get re-enqueued.
    assert set(enqueued) == {ids["never_old"], ids["expired_lease"]}
    assert result["reenqueued"] == 2

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        rows = {
            r["id"]: r["enqueued_at"]
            for r in await conn.fetch("SELECT id, enqueued_at FROM documents")
        }
        # Claimed docs had their lease stamped fresh (within the last 30s).
        fresh = await conn.fetchval(
            "SELECT count(*) FROM documents"
            " WHERE id = ANY($1::uuid[])"
            "   AND enqueued_at > now() - make_interval(secs => 30)",
            [ids["never_old"], ids["expired_lease"]],
        )
        # The within-lease doc was NOT re-stamped (still ~60s ago).
        recent_still_old = await conn.fetchval(
            "SELECT enqueued_at < now() - make_interval(secs => 30)"
            " FROM documents WHERE id = $1",
            ids["recent_lease"],
        )
    finally:
        await conn.close()

    assert fresh == 2
    assert recent_still_old is True
    # The skipped docs keep their original lease state.
    assert rows[ids["too_young"]] is None
    assert rows[ids["deleted"]] is None


@pytest.mark.asyncio
async def test_concurrent_sweep_does_not_double_enqueue(
    schema_at_head: None, migrations_pg_dsn: str, workers_settings: Any
) -> None:
    """A second sweep right after the first claims nothing — the lease was
    stamped atomically on the first claim."""
    await _seed(migrations_pg_dsn)

    from workers.ingestion import _sweep_pending_documents_async

    first: list[UUID] = []
    await _sweep_pending_documents_async(
        settings=workers_settings,
        enqueue=lambda d: (first.append(d), True)[1],
        older_than_seconds=300,
        lease_seconds=600,
    )
    second: list[UUID] = []
    result2 = await _sweep_pending_documents_async(
        settings=workers_settings,
        enqueue=lambda d: (second.append(d), True)[1],
        older_than_seconds=300,
        lease_seconds=600,
    )

    assert len(first) == 2
    assert second == []
    assert result2["reenqueued"] == 0
