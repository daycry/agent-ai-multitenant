"""Integration test — prod-06 task_prod06_zombi_01 (``workers.sweep_stale_executions``).

A ``running`` execution older than the stale threshold (its Celery child was
SIGKILLed by OOM/hard-limit, leaving the row dangling) must be closed ``failed``
with ``abort_code=stale_after_worker_loss``, its task moved off ``in_progress``
(dag_01 policy → ``blocked``), and its container reaped by label. A fresh
``running`` execution is left untouched.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from api_server.db.domain import Execution, Task, TaskStatus
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


class _FakeRunner:
    """Records ``kill_by_label`` / reaper calls instead of touching Docker."""

    def __init__(
        self,
        exited: list[tuple[str, str]] | None = None,
        managed_ids: set[str] | None = None,
    ) -> None:
        self.killed: list[str] = []
        self.removed: list[str] = []
        self._exited = list(exited or [])
        # None = daemon sin respuesta (el sweep de huérfanos no debe barrer).
        self._managed_ids = managed_ids

    def kill_by_label(self, execution_id: str) -> int:
        self.killed.append(execution_id)
        return 1

    def list_exited_managed(self) -> list[tuple[str, str]]:
        return list(self._exited)

    def list_managed_execution_ids(self) -> set[str] | None:
        return None if self._managed_ids is None else set(self._managed_ids)

    def remove_container(self, container_id: str) -> bool:
        self.removed.append(container_id)
        return True


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


@pytest.fixture()
def workers_settings(monkeypatch: pytest.MonkeyPatch, migrations_pg_dsn: str):
    async_dsn = migrations_pg_dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    monkeypatch.setenv("WORKERS_DATABASE_URL", async_dsn)
    from workers.config import get_settings, reset_settings_cache

    reset_settings_cache()
    yield get_settings()
    reset_settings_cache()


async def _seed(dsn: str) -> dict[str, UUID]:
    ids = {
        "tenant": uuid4(),
        "project": uuid4(),
        "task_stale": uuid4(),
        "task_fresh": uuid4(),
        "exec_stale": uuid4(),
        "exec_fresh": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE executions, tasks, projects, organizations RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T zombi', 't-zombi01')",
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status, is_template)"
            " VALUES ($1, $2, 'P', 'active', false)",
            ids["project"],
            ids["tenant"],
        )
        for tid in (ids["task_stale"], ids["task_fresh"]):
            await conn.execute(
                "INSERT INTO tasks (id, tenant_id, project_id, title, status, priority)"
                " VALUES ($1, $2, $3, 'task', 'in_progress', 'medium')",
                tid,
                ids["tenant"],
                ids["project"],
            )
        # stale execution: started 8h ago (> the 7h threshold). fresh: just now.
        await conn.execute(
            "INSERT INTO executions (id, tenant_id, task_id, status, started_at)"
            " VALUES ($1, $2, $3, 'running', now() - interval '8 hours')",
            ids["exec_stale"],
            ids["tenant"],
            ids["task_stale"],
        )
        await conn.execute(
            "INSERT INTO executions (id, tenant_id, task_id, status, started_at)"
            " VALUES ($1, $2, $3, 'running', now())",
            ids["exec_fresh"],
            ids["tenant"],
            ids["task_fresh"],
        )
        return ids
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_sweep_stale_executions(
    _migrated: None, workers_settings: object, migrations_pg_dsn: str
) -> None:
    from workers.maintenance import _sweep_stale_executions_async

    ids = await _seed(migrations_pg_dsn)
    runner = _FakeRunner()

    result = await _sweep_stale_executions_async(
        workers_settings,  # type: ignore[arg-type]
        runner=runner,
        stale_after=timedelta(hours=7),
        now=datetime.now(UTC),
    )

    engine = create_async_engine(workers_settings.database_url)  # type: ignore[attr-defined]
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessionmaker() as session:
            exec_stale = await session.get(Execution, ids["exec_stale"])
            exec_fresh = await session.get(Execution, ids["exec_fresh"])
            task_stale = await session.get(Task, ids["task_stale"])
            task_fresh = await session.get(Task, ids["task_fresh"])

        # Stale execution closed failed with the worker-loss code; its task blocked.
        assert exec_stale is not None and exec_stale.status == "failed"
        assert exec_stale.abort_code == "stale_after_worker_loss"
        assert exec_stale.completed_at is not None
        assert task_stale is not None and task_stale.status == TaskStatus.BLOCKED.value
        # Fresh execution + its task untouched.
        assert exec_fresh is not None and exec_fresh.status == "running"
        assert task_fresh is not None and task_fresh.status == TaskStatus.IN_PROGRESS.value
        # The stale container was reaped by label.
        assert runner.killed == [str(ids["exec_stale"])]
        assert result["swept"] == 1
        assert result["reaped"] == 1

        # AUD16-21: el sello administrativo deja rastro reconstruible desde BD —
        # un task_audit_event con actor/motivo, y el skip_reason del memorizer.
        assert exec_stale.memorize_skip_reason == "administrative_finalize"
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            events = await conn.fetch(
                "SELECT kind, actor, payload FROM task_audit_events WHERE task_id = $1",
                ids["task_stale"],
            )
        finally:
            await conn.close()
        sweeper_events = [e for e in events if e["kind"] == "execution_sealed_by_sweeper"]
        assert len(sweeper_events) == 1
        assert sweeper_events[0]["actor"] == "system:stale_sweeper"
        assert str(ids["exec_stale"]) in str(sweeper_events[0]["payload"])
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sweep_closes_orphaned_running_rows_whose_container_is_gone(
    _migrated: None, workers_settings: object, migrations_pg_dsn: str
) -> None:
    """Sweep de huérfanos (2026-07-03, gotcha engine-restart): una fila `running`
    cuyo contenedor YA NO EXISTE (engine-restart, rm externo) no puede terminar
    jamás — con solo el umbral de 7 h quedaba horas de zombi vetando el
    re-despacho de su task. Si el daemon responde y el contenedor no está, se
    cierra al pasar la gracia; una fila con contenedor VIVO no se toca aunque
    lleve horas; y si el daemon no responde (None) no se barre nada."""
    from workers.maintenance import _sweep_stale_executions_async

    ids = await _seed(migrations_pg_dsn)
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        # El "stale" pasa a: 30 min de antigüedad (muy por debajo de 7h), SIN
        # contenedor pero CON container_launched_at (huérfano genuino de
        # engine-restart: el contenedor existió y se perdió). El "fresh" pasa a:
        # 2h de antigüedad CON contenedor vivo → run legítimo largo, intocable.
        await conn.execute(
            "UPDATE executions SET started_at = now() - interval '30 minutes',"
            " container_launched_at = now() - interval '30 minutes' WHERE id=$1",
            ids["exec_stale"],
        )
        await conn.execute(
            "UPDATE executions SET started_at = now() - interval '2 hours',"
            " container_launched_at = now() - interval '2 hours' WHERE id=$1",
            ids["exec_fresh"],
        )
    finally:
        await conn.close()

    runner = _FakeRunner(managed_ids={str(ids["exec_fresh"])})
    result = await _sweep_stale_executions_async(
        workers_settings,  # type: ignore[arg-type]
        runner=runner,
        stale_after=timedelta(hours=7),
        now=datetime.now(UTC),
    )

    engine = create_async_engine(workers_settings.database_url)  # type: ignore[attr-defined]
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessionmaker() as session:
            orphan = await session.get(Execution, ids["exec_stale"])
            alive = await session.get(Execution, ids["exec_fresh"])
            orphan_task = await session.get(Task, ids["task_stale"])
        assert orphan is not None and orphan.status == "failed"
        assert orphan.abort_code == "stale_after_worker_loss"
        assert orphan_task is not None and orphan_task.status == TaskStatus.BLOCKED.value
        assert alive is not None and alive.status == "running"
        assert result["swept"] == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_orphan_sweep_respects_grace_and_daemon_silence(
    _migrated: None, workers_settings: object, migrations_pg_dsn: str
) -> None:
    """Ni una fila DENTRO de la gracia (contenedor aún arrancando) ni ninguna
    fila cuando el daemon no responde (list → None) se barren."""
    from workers.maintenance import _sweep_stale_executions_async

    ids = await _seed(migrations_pg_dsn)
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        # 2 min de antigüedad: dentro de la gracia aunque no tenga contenedor.
        await conn.execute(
            "UPDATE executions SET started_at = now() - interval '2 minutes' WHERE id=$1",
            ids["exec_stale"],
        )
        # 30 min, huérfano genuino (container_launched_at puesto), pero el daemon NO
        # responde en este test → aun así no se barre nada.
        await conn.execute(
            "UPDATE executions SET started_at = now() - interval '30 minutes',"
            " container_launched_at = now() - interval '30 minutes' WHERE id=$1",
            ids["exec_fresh"],
        )
    finally:
        await conn.close()

    in_grace = await _sweep_stale_executions_async(
        workers_settings,  # type: ignore[arg-type]
        runner=_FakeRunner(managed_ids=set()),
        stale_after=timedelta(hours=7),
        now=datetime.now(UTC),
    )
    # Solo el de 30 min sin contenedor cae; el de 2 min sobrevive a la gracia.
    assert in_grace["swept"] == 1

    ids2 = await _seed(migrations_pg_dsn)
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "UPDATE executions SET started_at = now() - interval '30 minutes' WHERE id=$1",
            ids2["exec_stale"],
        )
    finally:
        await conn.close()

    daemon_down = await _sweep_stale_executions_async(
        workers_settings,  # type: ignore[arg-type]
        runner=_FakeRunner(managed_ids=None),
        stale_after=timedelta(hours=7),
        now=datetime.now(UTC),
    )
    assert daemon_down["swept"] == 0


@pytest.mark.asyncio
async def test_orphan_sweep_skips_row_still_provisioning(
    _migrated: None, workers_settings: object, migrations_pg_dsn: str
) -> None:
    """M1: una fila `running` que AÚN NO lanzó contenedor (container_launched_at
    NULL — pull en frío / checkout git grande / Vault lento) NO es huérfana: no hay
    contenedor que se haya perdido, solo provisión lenta. Con la gracia fija de 5
    min el sweep la mataba (y su resultado sano se descartaba al finalizar). Tras el
    fix queda protegida del reap temprano y solo caería por el umbral de 7 h."""
    from workers.maintenance import _sweep_stale_executions_async

    ids = await _seed(migrations_pg_dsn)
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        # 30 min de antigüedad (muy por encima de la gracia de 5 min, por debajo de
        # 7h), container_launched_at = NULL → todavía provisionando.
        await conn.execute(
            "UPDATE executions SET started_at = now() - interval '30 minutes',"
            " container_launched_at = NULL WHERE id=$1",
            ids["exec_stale"],
        )
    finally:
        await conn.close()

    # Daemon vivo, SIN contenedores (managed_ids=set()): con el código viejo esto la
    # marcaría huérfana; con el fix, container_launched_at NULL la protege.
    result = await _sweep_stale_executions_async(
        workers_settings,  # type: ignore[arg-type]
        runner=_FakeRunner(managed_ids=set()),
        stale_after=timedelta(hours=7),
        now=datetime.now(UTC),
    )

    engine = create_async_engine(workers_settings.database_url)  # type: ignore[attr-defined]
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessionmaker() as session:
            provisioning = await session.get(Execution, ids["exec_stale"])
            task = await session.get(Task, ids["task_stale"])
        assert provisioning is not None and provisioning.status == "running"
        assert task is not None and task.status == TaskStatus.IN_PROGRESS.value
        assert result["swept"] == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sweep_removes_exited_containers_of_terminal_executions(
    _migrated: None, workers_settings: object, migrations_pg_dsn: str
) -> None:
    """F0.6 (auditoría 2026-07-02): los contenedores agent-runtime `exited` de
    runs superseded/crasheados no los limpiaba nadie (run_streamed solo limpia
    si el proceso worker sigue vivo; kill_by_label solo mata running) — en un
    host que duerme a diario se acumulan. El sweep los elimina cuando su
    execution ya es terminal (o su fila no existe); NUNCA los de un run vivo."""
    from workers.maintenance import _sweep_stale_executions_async

    ids = await _seed(migrations_pg_dsn)
    # Cierra la execution "stale" como terminal ANTES del sweep para simular un
    # run superseded con contenedor exited abandonado.
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "UPDATE executions SET status='failed', abort_code='superseded',"
            " completed_at=now() WHERE id=$1",
            ids["exec_stale"],
        )
    finally:
        await conn.close()

    runner = _FakeRunner(
        exited=[
            ("c-terminal", str(ids["exec_stale"])),  # row terminal → remove
            ("c-live", str(ids["exec_fresh"])),  # row running → conservar
            ("c-orphan", str(uuid4())),  # fila inexistente → remove (basura)
        ]
    )

    result = await _sweep_stale_executions_async(
        workers_settings,  # type: ignore[arg-type]
        runner=runner,
        stale_after=timedelta(hours=7),
        now=datetime.now(UTC),
    )

    assert sorted(runner.removed) == ["c-orphan", "c-terminal"]
    assert result["containers_removed"] == 2
