"""Unit — per-task run lock (prod-18 A6).

A concurrent re-delivery of the same task must fail to acquire the lock (so it
is skipped), and release must be token-guarded (a run whose lock expired + was
re-acquired by another run cannot free the newer holder's lock).
"""

from __future__ import annotations

import pytest
from workers.run_lock import acquire_run_lock, release_run_lock, run_lock_key

pytestmark = pytest.mark.unit


class _FakeRedis:
    """Minimal async Redis supporting SET NX EX, GET and the release Lua (CAS-del)."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(
        self, key: str, value: str, *, nx: bool = False, ex: int | None = None
    ) -> bool | None:
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def eval(self, _script: str, _numkeys: int, key: str, token: str) -> int:
        # Emulate the compare-and-delete: del only if the value matches the token.
        if self.store.get(key) == token:
            del self.store[key]
            return 1
        return 0


@pytest.mark.asyncio
async def test_first_run_acquires_second_is_blocked() -> None:
    redis = _FakeRedis()
    assert await acquire_run_lock(redis, "task-1", ttl_s=60, token="run-A") is True
    # A concurrent re-delivery of the SAME task cannot acquire.
    assert await acquire_run_lock(redis, "task-1", ttl_s=60, token="run-B") is False
    # A different task is independent.
    assert await acquire_run_lock(redis, "task-2", ttl_s=60, token="run-C") is True


@pytest.mark.asyncio
async def test_release_frees_the_lock_for_the_next_run() -> None:
    redis = _FakeRedis()
    await acquire_run_lock(redis, "task-1", ttl_s=60, token="run-A")
    await release_run_lock(redis, "task-1", token="run-A")
    # After release the next run can claim it.
    assert await acquire_run_lock(redis, "task-1", ttl_s=60, token="run-B") is True


@pytest.mark.asyncio
async def test_release_is_token_guarded() -> None:
    redis = _FakeRedis()
    # run-A's lock "expired" and run-B re-acquired it (simulated: B overwrites).
    redis.store[run_lock_key("task-1")] = "run-B"
    # run-A (late) must NOT free run-B's lock.
    await release_run_lock(redis, "task-1", token="run-A")
    assert redis.store.get(run_lock_key("task-1")) == "run-B"


# --- Integración ligera: _run_execution SALTA si el lock ya está tomado (A6).
@pytest.mark.asyncio
async def test_run_execution_skips_when_lock_held(monkeypatch: pytest.MonkeyPatch) -> None:
    # El ciclo del run vive en el submódulo run_cycle (split 2026-07-08):
    # se parchea AHÍ (el lookup site real), no en la façade del paquete.
    import workers.tasks.run_cycle as tasks_mod
    from workers.run_lock import run_lock_key

    task_id = "11111111-1111-1111-1111-111111111111"

    fake = _FakeRedis()
    # Otro run YA tiene el lock de esta tarea (re-entrega concurrente).
    fake.store[run_lock_key(task_id)] = "run-original"

    class _FakeRedisFactory:
        @staticmethod
        def from_url(*_a: object, **_k: object) -> _FakeRedis:
            return fake

    class _FakeEngine:
        async def dispose(self) -> None: ...

    monkeypatch.setattr(tasks_mod, "Redis", _FakeRedisFactory)
    # `task_audit14_06`: el engine ya no se construye aquí a mano; lo da la
    # factoría común (`workers.db.worker_engine`), que es el nombre importado en
    # este módulo y por tanto el lookup site que hay que parchear.
    monkeypatch.setattr(tasks_mod, "worker_engine", lambda *_a, **_k: _FakeEngine())

    async def _boom(*_a: object, **_k: object) -> object:
        raise AssertionError("conduct_execution NO debe llamarse con el lock tomado")

    monkeypatch.setattr(tasks_mod, "conduct_execution", _boom)

    req = type("_Req", (), {"task_id": task_id})()

    class _Settings:
        database_url = "postgresql+asyncpg://x:y@h/db"
        events_redis_url = "redis://h/0"

        def container_timeout_with_grace_for_kind(
            self, _kind: str, *, is_review: bool = False
        ) -> int:
            return 7320

    aclose_called = {"v": False}

    async def _aclose() -> None:
        aclose_called["v"] = True

    fake.aclose = _aclose  # type: ignore[attr-defined]

    out = await tasks_mod._run_execution(req, _Settings(), celery_task_id="run-B")  # type: ignore[arg-type]

    assert out["status"] == "skipped"
    assert out["abort_code"] == "concurrent_run_locked"
    # El lock del run original SIGUE intacto (no lo liberó el run saltado).
    assert fake.store.get(run_lock_key(task_id)) == "run-original"
    assert aclose_called["v"] is True


class _LockTestSettings:
    database_url = "postgresql+asyncpg://x:y@h/db"
    events_redis_url = "redis://h/0"

    def container_timeout_with_grace_for_kind(self, _kind: str, *, is_review: bool = False) -> int:
        return 7320


# --- H1 (carrera lock A6 ↔ evento diferido): el evento de finish que devuelve
# conduct_execution se publica DESPUÉS de soltar el run-lock, para que el
# despacho inmediato del orchestrator (review / re-dispatch tras reject) no
# choque con el lock aún vivo (`concurrent_run_locked` → 6 min de reconciler).
@pytest.mark.asyncio
async def test_pending_task_event_publishes_after_lock_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import workers.run_lock as run_lock_mod
    import workers.tasks.run_cycle as tasks_mod
    from workers.run_contract import ExecutionOutcome

    task_id = "22222222-2222-2222-2222-222222222222"
    calls: list[str] = []

    fake = _FakeRedis()

    async def _aclose() -> None: ...

    fake.aclose = _aclose  # type: ignore[attr-defined]

    class _FakeRedisFactory:
        @staticmethod
        def from_url(*_a: object, **_k: object) -> _FakeRedis:
            return fake

    class _FakeEngine:
        async def dispose(self) -> None: ...

    monkeypatch.setattr(tasks_mod, "Redis", _FakeRedisFactory)
    monkeypatch.setattr(tasks_mod, "worker_engine", lambda *_a, **_k: _FakeEngine())

    sentinel_task = object()
    outcome = ExecutionOutcome(
        execution_id="e-1",
        status="in_review",
        abort_code=None,
        pending_task_event=(sentinel_task, "in_progress", "in_review"),
    )

    async def _fake_conduct(*_a: object, **_k: object) -> ExecutionOutcome:
        return outcome

    monkeypatch.setattr(tasks_mod, "conduct_execution", _fake_conduct)

    real_release = run_lock_mod.release_run_lock

    async def _spy_release(redis: object, tid: str, *, token: str) -> None:
        calls.append("release")
        await real_release(redis, tid, token=token)  # type: ignore[arg-type]

    monkeypatch.setattr(run_lock_mod, "release_run_lock", _spy_release)

    async def _spy_publish(
        _redis: object, task_obj: object, *, old_status: str, new_status: str
    ) -> None:
        assert task_obj is sentinel_task
        assert (old_status, new_status) == ("in_progress", "in_review")
        # El lock DEBE estar ya libre cuando se publica el evento.
        assert run_lock_key(task_id) not in fake.store
        calls.append("publish")

    monkeypatch.setattr(tasks_mod, "publish_task_status_changed", _spy_publish)

    req = type("_Req", (), {"task_id": task_id})()
    out = await tasks_mod._run_execution(req, _LockTestSettings(), celery_task_id="run-A")  # type: ignore[arg-type]

    assert out["status"] == "in_review"
    # El orden es el contrato: primero release, después publish.
    assert calls == ["release", "publish"]


@pytest.mark.asyncio
async def test_outcome_without_pending_event_publishes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import workers.tasks.run_cycle as tasks_mod
    from workers.run_contract import ExecutionOutcome

    task_id = "33333333-3333-3333-3333-333333333333"
    fake = _FakeRedis()

    async def _aclose() -> None: ...

    fake.aclose = _aclose  # type: ignore[attr-defined]

    class _FakeRedisFactory:
        @staticmethod
        def from_url(*_a: object, **_k: object) -> _FakeRedis:
            return fake

    class _FakeEngine:
        async def dispose(self) -> None: ...

    monkeypatch.setattr(tasks_mod, "Redis", _FakeRedisFactory)
    monkeypatch.setattr(tasks_mod, "worker_engine", lambda *_a, **_k: _FakeEngine())

    async def _fake_conduct(*_a: object, **_k: object) -> ExecutionOutcome:
        return ExecutionOutcome(execution_id="e-2", status="done", abort_code=None)

    monkeypatch.setattr(tasks_mod, "conduct_execution", _fake_conduct)

    async def _boom_publish(*_a: object, **_k: object) -> None:
        raise AssertionError("sin pending_task_event NO debe publicarse nada")

    monkeypatch.setattr(tasks_mod, "publish_task_status_changed", _boom_publish)

    req = type("_Req", (), {"task_id": task_id})()
    out = await tasks_mod._run_execution(req, _LockTestSettings(), celery_task_id="run-A")  # type: ignore[arg-type]
    assert out["status"] == "done"
