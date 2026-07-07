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
    import workers.tasks as tasks_mod
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
    monkeypatch.setattr(tasks_mod, "create_async_engine", lambda *_a, **_k: _FakeEngine())

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
