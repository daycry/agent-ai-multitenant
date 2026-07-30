"""ADR 0098 (eje 3, «lo nuevo aprobado»): beat periódico de fetch de remotos.

El re-sync del remoto tenía solo el botón manual (`POST /projects/{id}/git/sync`);
este beat (`workers.sweep_project_git_remotes`) recorre los proyectos con
`git_config.remote_url` y reutiliza el clone/fetch autenticado de ADR 0072
(`_clone_project_repo_async`) por proyecto, best-effort. Gated por el platform
setting `git_fetch_sweep_enabled` (default OFF: el poll de remotos lo enciende
un System Admin conscientemente) con cadencia cron configurable
(WORKERS_GIT_FETCH_CRON).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

import pytest
from workers.git_remote_sweep import GIT_FETCH_BEAT_ENTRY, _sweep_project_git_remotes_async

pytestmark = pytest.mark.unit


class _FakeRows:
    def __init__(self, ids: list[UUID]) -> None:
        self._ids = ids

    def all(self) -> list[tuple[UUID]]:
        return [(pid,) for pid in self._ids]


class _FakeSession:
    def __init__(self, ids: list[UUID]) -> None:
        self._ids = ids

    async def execute(self, stmt: Any) -> _FakeRows:
        return _FakeRows(self._ids)


def _factory(ids: list[UUID]):
    @asynccontextmanager
    async def _ctx():
        yield _FakeSession(ids)

    return _ctx


@pytest.mark.asyncio
async def test_sweep_noop_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _disabled(session: Any) -> bool:
        return False

    monkeypatch.setattr("api_server.db.platform_settings.get_git_fetch_sweep_enabled", _disabled)
    called: list[UUID] = []

    async def _clone(pid: UUID, *, settings: Any) -> dict[str, Any]:
        called.append(pid)
        return {"status": "fetched"}

    result = await _sweep_project_git_remotes_async(
        object(), session_factory=_factory([uuid4()]), clone=_clone
    )
    assert result == {"enabled": False, "skipped": True}
    assert called == []


@pytest.mark.asyncio
async def test_sweep_fetches_each_project_best_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _enabled(session: Any) -> bool:
        return True

    monkeypatch.setattr("api_server.db.platform_settings.get_git_fetch_sweep_enabled", _enabled)
    ok_id, bad_id = uuid4(), uuid4()
    called: list[UUID] = []

    async def _clone(pid: UUID, *, settings: Any) -> dict[str, Any]:
        called.append(pid)
        if pid == bad_id:
            raise RuntimeError("credencial caducada")
        return {"status": "fetched"}

    result = await _sweep_project_git_remotes_async(
        object(), session_factory=_factory([ok_id, bad_id]), clone=_clone
    )
    # Un proyecto que falla NO aborta el resto (best-effort por proyecto).
    assert called == [ok_id, bad_id]
    assert result["enabled"] is True
    assert result["projects"] == 2
    assert result["fetched"] == 1
    assert result["failed"] == 1


def test_beat_schedule_includes_git_fetch_entry() -> None:
    from workers.beat_schedule import build_beat_schedule
    from workers.config import Settings

    sched = build_beat_schedule(Settings())
    entry = sched[GIT_FETCH_BEAT_ENTRY]
    assert entry["task"] == "workers.sweep_project_git_remotes"
    assert entry["options"] == {"queue": "default"}
