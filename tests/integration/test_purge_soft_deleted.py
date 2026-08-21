"""prod-13 · task_prod13_14 — purga física de filas soft-borradas vencidas.

Hallazgo db-4. Borrar una KB o un proyecto solo ponía `deleted_at`: las filas, los
chunks con su `vector(768)` y los `steps_log` de sus runs se quedaban en disco
para siempre. La promesa que sí está escrita en los docstrings —«recuperable
durante la ventana de gracia»— implica su otra mitad: **pasada la gracia, se
borra de verdad**. Esa mitad no existía.

Es la task más peligrosa del plan (riesgo 3: «la purga borra datos que un tenant
quería recuperar»), así que los tests van justo a lo que puede salir caro:

  * **dentro de la gracia NO se toca nada** — el test que hay que escribir
    primero, porque el fallo es irreversible;
  * **una fila viva (`deleted_at IS NULL`) jamás entra**, ni por descuido de un
    `WHERE` mal puesto;
  * **dry-run cuenta pero no borra**, y es el default;
  * la cascada llega hasta el fondo (KB → documents → chunks;
    proyecto → plans/tasks/executions), que es lo que de verdad libera disco;
  * los **blobs** de los documentos purgados se van con ellos: si no, el borrado
    de filas solo mueve el problema a MinIO, donde nadie lo ve.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


class _FakeStorage:
    """Almacén de objetos de mentira: recuerda qué claves le mandaron borrar."""

    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def delete_object(self, *, key: str) -> None:
        self.deleted.append(key)


def _as_async_dsn(dsn: str) -> str:
    if dsn.startswith("postgresql+asyncpg://"):
        return dsn
    return dsn.replace("postgres://", "postgresql+asyncpg://", 1).replace(
        "postgresql://", "postgresql+asyncpg://", 1
    )


async def _reset(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE chunks, documents, knowledge_bases, executions, tasks, plans, "
            "projects, organizations CASCADE"
        )
    finally:
        await conn.close()


async def _seed(dsn: str, *, kb_deleted_at: datetime | None, project_deleted_at: datetime | None):
    """Un tenant con una KB (1 doc, 2 chunks) y un proyecto (1 plan, 1 task, 1 run)."""
    tenant = uuid4()
    kb = uuid4()
    doc = uuid4()
    project = uuid4()
    plan = uuid4()
    task = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T', $2)",
            tenant,
            f"t-{str(tenant)[:8]}",
        )
        await conn.execute(
            "INSERT INTO knowledge_bases (id, tenant_id, name, deleted_at)"
            " VALUES ($1, $2, 'KB', $3)",
            kb,
            tenant,
            kb_deleted_at,
        )
        await conn.execute(
            "INSERT INTO documents (id, tenant_id, kb_id, title, source_filename,"
            " source_mime_type, source_storage_key)"
            " VALUES ($1, $2, $3, 'D', 'd.pdf', 'application/pdf', $4)",
            doc,
            tenant,
            kb,
            f"kb/{tenant}/{kb}/{doc}/d.pdf",
        )
        for ordinal in (0, 1):
            await conn.execute(
                "INSERT INTO chunks (id, tenant_id, document_id, ordinal, content)"
                " VALUES ($1, $2, $3, $4, 'x')",
                uuid4(),
                tenant,
                doc,
                ordinal,
            )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, slug, deleted_at)"
            " VALUES ($1, $2, 'P', $3, $4)",
            project,
            tenant,
            f"p-{str(project)[:8]}",
            project_deleted_at,
        )
        await conn.execute(
            "INSERT INTO plans (id, tenant_id, project_id, title) VALUES ($1, $2, $3, 'PL')",
            plan,
            tenant,
            project,
        )
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, plan_id, title)"
            " VALUES ($1, $2, $3, $4, 'TK')",
            task,
            tenant,
            project,
            plan,
        )
        await conn.execute(
            "INSERT INTO executions (id, tenant_id, task_id) VALUES ($1, $2, $3)",
            uuid4(),
            tenant,
            task,
        )
    finally:
        await conn.close()
    return {"tenant": tenant, "kb": kb, "doc": doc, "project": project}


async def _counts(dsn: str) -> dict[str, int]:
    conn = await asyncpg.connect(dsn)
    try:
        out = {}
        for table in (
            "knowledge_bases",
            "documents",
            "chunks",
            "projects",
            "plans",
            "tasks",
            "executions",
        ):
            out[table] = int(await conn.fetchval(f"SELECT count(*) FROM {table}"))
        return out
    finally:
        await conn.close()


def _run_purge(dsn: str, *, storage: Any, **kwargs: Any) -> dict[str, Any]:
    from workers.maintenance.purge import purge_soft_deleted

    async def _go() -> dict[str, Any]:
        engine = create_async_engine(_as_async_dsn(dsn), pool_pre_ping=False)
        try:
            sm = async_sessionmaker(engine, expire_on_commit=False)
            return await purge_soft_deleted(sm, storage, **kwargs)
        finally:
            await engine.dispose()

    return asyncio.run(_go())


_LONG_AGO = datetime(2020, 1, 1, tzinfo=UTC)


# ---------------------------------------------------------------------------
# 1. Lo irreversible primero: la ventana de gracia
# ---------------------------------------------------------------------------
def test_rows_inside_the_grace_window_are_never_touched(
    alembic_config: Any, migrations_pg_dsn: str
) -> None:
    command.upgrade(alembic_config, "head")
    asyncio.run(_reset(migrations_pg_dsn))
    recent = datetime.now(UTC) - timedelta(days=5)
    asyncio.run(_seed(migrations_pg_dsn, kb_deleted_at=recent, project_deleted_at=recent))

    before = asyncio.run(_counts(migrations_pg_dsn))
    report = _run_purge(migrations_pg_dsn, storage=_FakeStorage(), grace_days=30, dry_run=False)

    assert asyncio.run(_counts(migrations_pg_dsn)) == before
    assert report["by_table"] == {}


def test_live_rows_are_never_touched(alembic_config: Any, migrations_pg_dsn: str) -> None:
    """Una fila viva no tiene `deleted_at`: ningún corte de fecha puede alcanzarla."""
    command.upgrade(alembic_config, "head")
    asyncio.run(_reset(migrations_pg_dsn))
    asyncio.run(_seed(migrations_pg_dsn, kb_deleted_at=None, project_deleted_at=None))

    before = asyncio.run(_counts(migrations_pg_dsn))
    # Gracia 0 días: el corte es AHORA. Ni así.
    _run_purge(migrations_pg_dsn, storage=_FakeStorage(), grace_days=0, dry_run=False)
    assert asyncio.run(_counts(migrations_pg_dsn)) == before


# ---------------------------------------------------------------------------
# 2. Dry-run: cuenta, no borra — y es el default
# ---------------------------------------------------------------------------
def test_dry_run_counts_but_deletes_nothing(alembic_config: Any, migrations_pg_dsn: str) -> None:
    command.upgrade(alembic_config, "head")
    asyncio.run(_reset(migrations_pg_dsn))
    asyncio.run(_seed(migrations_pg_dsn, kb_deleted_at=_LONG_AGO, project_deleted_at=_LONG_AGO))

    before = asyncio.run(_counts(migrations_pg_dsn))
    storage = _FakeStorage()
    report = _run_purge(migrations_pg_dsn, storage=storage, grace_days=30)

    assert report["dry_run"] is True, "el default DEBE ser dry-run: el borrado es irreversible"
    assert report["by_table"]["knowledge_bases"] == 1
    assert report["by_table"]["chunks"] == 2
    assert asyncio.run(_counts(migrations_pg_dsn)) == before
    assert storage.deleted == [], "un dry-run tampoco puede tocar los blobs"


# ---------------------------------------------------------------------------
# 3. La cascada de verdad
# ---------------------------------------------------------------------------
def test_expired_rows_are_purged_with_their_whole_cascade(
    alembic_config: Any, migrations_pg_dsn: str
) -> None:
    command.upgrade(alembic_config, "head")
    asyncio.run(_reset(migrations_pg_dsn))
    seeded = asyncio.run(
        _seed(migrations_pg_dsn, kb_deleted_at=_LONG_AGO, project_deleted_at=_LONG_AGO)
    )

    storage = _FakeStorage()
    report = _run_purge(migrations_pg_dsn, storage=storage, grace_days=30, dry_run=False)

    after = asyncio.run(_counts(migrations_pg_dsn))
    assert after == {
        "knowledge_bases": 0,
        "documents": 0,
        "chunks": 0,
        "projects": 0,
        "plans": 0,
        "tasks": 0,
        "executions": 0,
    }
    assert report["by_table"]["documents"] == 1
    assert report["by_table"]["executions"] == 1

    # Los blobs se van con las filas: si no, la purga solo esconde el problema.
    doc: UUID = seeded["doc"]
    assert any(str(doc) in key for key in storage.deleted), storage.deleted


def test_purging_a_kb_leaves_a_live_project_alone(
    alembic_config: Any, migrations_pg_dsn: str
) -> None:
    """Cada raíz se purga por su cuenta: una KB vencida no arrastra al proyecto."""
    command.upgrade(alembic_config, "head")
    asyncio.run(_reset(migrations_pg_dsn))
    asyncio.run(_seed(migrations_pg_dsn, kb_deleted_at=_LONG_AGO, project_deleted_at=None))

    _run_purge(migrations_pg_dsn, storage=_FakeStorage(), grace_days=30, dry_run=False)
    after = asyncio.run(_counts(migrations_pg_dsn))
    assert after["knowledge_bases"] == 0
    assert after["chunks"] == 0
    assert after["projects"] == 1
    assert after["executions"] == 1


# ---------------------------------------------------------------------------
# 4. Un storage caído no deja las filas sin purgar
# ---------------------------------------------------------------------------
def test_a_failing_storage_does_not_block_the_row_purge(
    alembic_config: Any, migrations_pg_dsn: str
) -> None:
    command.upgrade(alembic_config, "head")
    asyncio.run(_reset(migrations_pg_dsn))
    asyncio.run(_seed(migrations_pg_dsn, kb_deleted_at=_LONG_AGO, project_deleted_at=None))

    class _BrokenStorage:
        async def delete_object(self, *, key: str) -> None:
            raise RuntimeError("MinIO caído")

    report = _run_purge(migrations_pg_dsn, storage=_BrokenStorage(), grace_days=30, dry_run=False)
    assert asyncio.run(_counts(migrations_pg_dsn))["knowledge_bases"] == 0
    assert report["blob_failures"] == 1
