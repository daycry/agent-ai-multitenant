"""prod-12 task_prod12_reaper_01 — ``workers.reap_orphans``.

Contenedores ``managed=true`` sin asociación VIVA (execution ``running`` /
review ``running``/``suspended``) se eliminan; los vivos y los frescos (gracia
anti-carrera) se respetan; las redes bridge de test-runtime vacías se borran.
Fake del docker SDK — sin daemon en CI.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 7, 8, 12, 0, 0, tzinfo=UTC)


def _iso(age: timedelta) -> str:
    return (_NOW - age).strftime("%Y-%m-%dT%H:%M:%S.123456789Z")


class _FakeContainer:
    def __init__(self, labels: dict[str, str], *, age: timedelta) -> None:
        self.labels = {"com.agentic-platform.managed": "true", **labels}
        self.attrs = {"Created": _iso(age)}
        self.removed = False

    def remove(self, force: bool = False) -> None:
        assert force is True
        self.removed = True


class _FakeNetwork:
    def __init__(self, *, age: timedelta, containers: dict[str, Any] | None = None) -> None:
        self.attrs = {"Created": _iso(age), "Containers": dict(containers or {})}
        self.removed = False

    def reload(self) -> None: ...

    def remove(self) -> None:
        self.removed = True


class _FakeClient:
    def __init__(self, containers: list[_FakeContainer], networks: list[_FakeNetwork]) -> None:
        self._containers = containers
        self._networks = networks
        self.containers = self
        self.networks = self

    def list(self, *args: Any, **kwargs: Any) -> list[Any]:
        filters = kwargs.get("filters") or {}
        label = str(filters.get("label", ""))
        if "managed" in label:
            return list(self._containers)
        return list(self._networks)


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
        "plan": uuid4(),
        "task": uuid4(),
        "exec_running": uuid4(),
        "exec_done": uuid4(),
        "review_running": uuid4(),
        "review_approved": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE review_sessions, executions, tasks, plans, projects, organizations"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T', 't-reaper')",
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status, is_template)"
            " VALUES ($1, $2, 'P', 'active', false)",
            ids["project"],
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO plans (id, tenant_id, project_id, title, slug, status)"
            " VALUES ($1, $2, $3, 'Plan', 'plan-reaper', 'pending_human_validation')",
            ids["plan"],
            ids["tenant"],
            ids["project"],
        )
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, title, status, priority)"
            " VALUES ($1, $2, $3, 'task', 'in_progress', 'medium')",
            ids["task"],
            ids["tenant"],
            ids["project"],
        )
        for eid, status in ((ids["exec_running"], "running"), (ids["exec_done"], "done")):
            await conn.execute(
                "INSERT INTO executions (id, tenant_id, task_id, status, started_at)"
                " VALUES ($1, $2, $3, $4, now() - interval '1 hour')",
                eid,
                ids["tenant"],
                ids["task"],
                status,
            )
        for sid, status in (
            (ids["review_running"], "running"),
            (ids["review_approved"], "approved"),
        ):
            await conn.execute(
                "INSERT INTO review_sessions (id, tenant_id, plan_id, spec, status, expires_at)"
                " VALUES ($1, $2, $3, '{}'::jsonb, $4, now() + interval '24 hours')",
                sid,
                ids["tenant"],
                ids["plan"],
                status,
            )
    finally:
        await conn.close()
    return ids


@pytest.mark.asyncio
async def test_reaps_only_containers_without_a_live_association(
    _migrated: None, workers_settings: Any, migrations_pg_dsn: str
) -> None:
    from workers.maintenance import _reap_orphans_async

    ids = await _seed(migrations_pg_dsn)
    age = timedelta(hours=1)
    c_live_exec = _FakeContainer(
        {"com.agentic-platform.execution-id": str(ids["exec_running"])}, age=age
    )
    c_dead_exec = _FakeContainer(
        {"com.agentic-platform.execution-id": str(ids["exec_done"])}, age=age
    )
    c_missing_exec = _FakeContainer({"com.agentic-platform.execution-id": str(uuid4())}, age=age)
    c_live_review = _FakeContainer(
        {"com.agentic-platform.review-session-id": str(ids["review_running"])}, age=age
    )
    c_dead_review = _FakeContainer(
        {"com.agentic-platform.review-session-id": str(ids["review_approved"])}, age=age
    )
    # Fresco (dentro de la gracia): jamás se toca, aunque su fila esté done.
    c_fresh = _FakeContainer(
        {"com.agentic-platform.execution-id": str(ids["exec_done"])},
        age=timedelta(minutes=2),
    )
    # Sin label de asociación: solo cae pasado el hard-limit + 25 %.
    c_untagged_old = _FakeContainer({}, age=timedelta(hours=8))
    c_untagged_recent = _FakeContainer({}, age=timedelta(hours=2))

    client = _FakeClient(
        [
            c_live_exec,
            c_dead_exec,
            c_missing_exec,
            c_live_review,
            c_dead_review,
            c_fresh,
            c_untagged_old,
            c_untagged_recent,
        ],
        [],
    )
    result = await _reap_orphans_async(workers_settings, client=client, now=_NOW)

    assert result["containers_removed"] == 4
    assert not c_live_exec.removed
    assert not c_live_review.removed
    assert not c_fresh.removed
    assert not c_untagged_recent.removed
    assert c_dead_exec.removed
    assert c_missing_exec.removed
    assert c_dead_review.removed
    assert c_untagged_old.removed


@pytest.mark.asyncio
async def test_reaps_empty_test_runtime_networks_only(
    _migrated: None, workers_settings: Any, migrations_pg_dsn: str
) -> None:
    from workers.maintenance import _reap_orphans_async

    await _seed(migrations_pg_dsn)
    n_empty_old = _FakeNetwork(age=timedelta(hours=1))
    n_occupied = _FakeNetwork(age=timedelta(hours=1), containers={"abc": {}})
    n_fresh = _FakeNetwork(age=timedelta(minutes=1))
    client = _FakeClient([], [n_empty_old, n_occupied, n_fresh])

    result = await _reap_orphans_async(workers_settings, client=client, now=_NOW)

    assert result["networks_removed"] == 1
    assert n_empty_old.removed
    assert not n_occupied.removed
    assert not n_fresh.removed


class _LabelledNetwork:
    """Network fake carrying a component label + distinct id, so the reaper's
    per-filter sweep + dedup can be exercised realistically (ADR 0129 fase 2)."""

    def __init__(self, net_id: str, component: str, *, age: timedelta, occupied: bool = False):
        self.id = net_id
        self._component = component
        self.attrs = {
            "Created": _iso(age),
            "Containers": {"c": {}} if occupied else {},
        }
        self.removed = False

    def reload(self) -> None: ...

    def remove(self) -> None:
        self.removed = True


class _LabelAwareClient:
    """Docker fake whose ``networks.list`` honours the component label filter."""

    def __init__(self, networks: list[_LabelledNetwork]) -> None:
        self._networks = networks
        self.containers = self
        self.networks = self

    def list(self, *args: Any, **kwargs: Any) -> list[Any]:
        label = str((kwargs.get("filters") or {}).get("label", ""))
        if "managed" in label:
            return []
        if label.startswith("com.agentic-platform.component="):
            want = label.split("=", 1)[1]
            return [n for n in self._networks if n._component == want]
        return list(self._networks)


@pytest.mark.asyncio
async def test_reaps_empty_review_bridges_too(
    _migrated: None, workers_settings: Any, migrations_pg_dsn: str
) -> None:
    from workers.maintenance import _reap_orphans_async

    await _seed(migrations_pg_dsn)
    review_empty = _LabelledNetwork("net-r1", "review-runtime", age=timedelta(hours=1))
    review_busy = _LabelledNetwork(
        "net-r2", "review-runtime", age=timedelta(hours=1), occupied=True
    )
    review_fresh = _LabelledNetwork("net-r3", "review-runtime", age=timedelta(minutes=1))
    test_empty = _LabelledNetwork("net-t1", "test-runtime", age=timedelta(hours=1))
    client = _LabelAwareClient([review_empty, review_busy, review_fresh, test_empty])

    result = await _reap_orphans_async(workers_settings, client=client, now=_NOW)

    # both an empty review bridge and an empty test bridge are reaped
    assert result["networks_removed"] == 2
    assert review_empty.removed
    assert test_empty.removed
    assert not review_busy.removed
    assert not review_fresh.removed


@pytest.mark.asyncio
async def test_daemon_unavailable_is_a_clean_noop(
    workers_settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import workers.docker_client as docker_client_mod
    from workers.maintenance import _reap_orphans_async

    # Sin daemon (get_docker_client → None): no-op honesto, jamás crashea beat.
    # Parcheado SIEMPRE — este host de dev sí tiene daemon y el test no debe
    # tocar contenedores reales.
    monkeypatch.setattr(docker_client_mod, "get_docker_client", lambda: None)
    result = await _reap_orphans_async(workers_settings, client=None, now=_NOW)
    assert result == {"containers_removed": 0, "networks_removed": 0, "note": "docker unavailable"}
