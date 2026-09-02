"""Restos de menor riesgo de la auditoría 2026-09-01, tanda operativa (`task_cv_45`).

- B-08: el wrapper `timeout` del `exec_run` no mataba (sin `-k`): un proceso
  que ignora SIGTERM colgaba el hilo del worker sin techo.
- B-10: los consumidores de `worktree_host_path` aceptaban cualquier ruta de
  host; el socket-proxy filtra endpoints, no payloads. Ahora se exige que la
  ruta viva bajo `data_root`.
- E-10: el embedding de la query del recall fallaba en silencio y el recall
  degradaba a BM25 sin que nadie lo viera.
- G-09: `restore_reconcile` marcaba CRITICAL planes `approved` que
  legítimamente no tienen rama (nace en el primer `worktree add`).
- G-11: el watchdog resolvía cada contenedor una sola vez; tras un recreate
  quedaba ciego (`NotFound` en cada tick).
- E-06 / G-12: `plan_retro` insertaba por SQL crudo saltándose la persistencia
  común, con marker sólo en Redis y una ventana de 48 h: beat parado más de
  48 h → sin retro para siempre; Redis restaurado → retros duplicadas.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

pytestmark = pytest.mark.unit


# ------------------------------------------------------------- B-08 timeout -k


class _ExecContainer:
    def __init__(self) -> None:
        self.cmd: list[str] | None = None

    def exec_run(self, cmd: list[str], demux: bool = False) -> Any:
        self.cmd = list(cmd)
        return SimpleNamespace(exit_code=0, output=b"ok")


def test_the_exec_timeout_kills_a_process_that_ignores_sigterm() -> None:
    from workers.config import Settings
    from workers.test_runtime import TestRuntimeRunner

    runner = TestRuntimeRunner(Settings(), client=object())
    container = _ExecContainer()

    rc, _logs = runner._exec(container, "pytest -q", timeout_s=30)

    assert rc == 0
    assert container.cmd is not None
    wrapped = container.cmd[-1]
    assert wrapped.startswith("timeout -k 10 30 sh -c "), wrapped


# ------------------------------------------------------------- B-10 host paths


def test_a_host_path_under_data_root_is_accepted_normalised(tmp_path: Path) -> None:
    from workers.host_paths import ensure_under_data_root

    inside = tmp_path / "projects" / "t" / "p" / "worktrees" / "task-1"
    inside.mkdir(parents=True)

    got = ensure_under_data_root(
        str(tmp_path / "projects" / "t" / ".." / "t" / "p" / "worktrees" / "task-1"),
        data_root=str(tmp_path),
    )

    assert Path(got) == inside.resolve()


@pytest.mark.parametrize("relative", ["../../etc", "etc/passwd"])
def test_a_host_path_outside_data_root_is_rejected(tmp_path: Path, relative: str) -> None:
    from workers.host_paths import HostPathError, ensure_under_data_root

    outside = (tmp_path / relative) if not Path(relative).is_absolute() else Path(relative)
    with pytest.raises(HostPathError):
        ensure_under_data_root(
            str(outside if relative.startswith("..") else Path(relative)), data_root=str(tmp_path)
        )


def test_data_root_itself_is_not_a_valid_workspace(tmp_path: Path) -> None:
    from workers.host_paths import HostPathError, ensure_under_data_root

    with pytest.raises(HostPathError):
        ensure_under_data_root(str(tmp_path), data_root=str(tmp_path))


def test_the_review_resolver_ignores_an_explicit_path_outside_data_root(tmp_path: Path) -> None:
    from workers.config import Settings
    from workers.tasks.review_runtime_task import _resolve_review_worktree_host_path

    outside = tmp_path.parent / "elsewhere"
    request = {"worktree_host_path": str(outside)}

    assert _resolve_review_worktree_host_path(request, Settings(data_root=str(tmp_path))) == ""


def test_the_test_runtime_task_reports_a_foreign_worktree_as_infra_failure(tmp_path: Path) -> None:
    from workers.config import Settings
    from workers.tasks.test_runtime_task import INFRA_FAILURE_KEY, _validated_worktree_host_path

    path, outcome = _validated_worktree_host_path(
        {"worktree_host_path": str(tmp_path.parent / "elsewhere"), "task_id": "t"},
        Settings(data_root=str(tmp_path)),
    )

    assert path is None
    assert outcome is not None and outcome[INFRA_FAILURE_KEY] == "worktree_host_path_invalid"


def test_the_agent_container_task_refuses_a_foreign_workspace(tmp_path: Path) -> None:
    from workers.config import Settings
    from workers.tasks.run_cycle import _validated_workspace

    with pytest.raises(ValueError):
        _validated_workspace(str(tmp_path.parent / "elsewhere"), Settings(data_root=str(tmp_path)))
    assert _validated_workspace(None, Settings(data_root=str(tmp_path))) is None


# ------------------------------------------------------------- E-10 embedding


def test_a_failed_query_embedding_is_counted_and_logged() -> None:
    from api_server.ingestion.embeddings import EmbeddingError
    from api_server.routers.internal_agent import RECALL_EMBEDDING_FAILURES, _embed_query

    class _Embedder:
        async def embed(self, _texts: list[str]) -> list[list[float]]:
            raise EmbeddingError("ollama down")

    before = RECALL_EMBEDDING_FAILURES._value.get()

    assert asyncio.run(_embed_query(_Embedder(), "hola")) is None
    assert RECALL_EMBEDDING_FAILURES._value.get() == before + 1


# ------------------------------------------------------------- G-09 restore


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _Rows:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class _Session:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    async def execute(self, _stmt: Any, *_a: Any, **_k: Any) -> _Rows:
        return _Rows(self._rows)


class _NoBranchGit:
    def repo_exists(self, repo_path: Path) -> bool:
        return True

    def branch_exists(self, repo_path: Path, branch: str) -> bool:
        return False


def _plan_row(status: str, plan_id: str) -> dict[str, Any]:
    return {
        "id": plan_id,
        "title": f"plan {status}",
        "slug": "x",
        "project_slug": "p",
        "tenant_slug": "t",
        "status": status,
    }


def test_an_approved_plan_without_branch_is_a_warning_not_a_critical(tmp_path: Path) -> None:
    from workers.restore_reconcile import (
        SEVERITY_CRITICAL,
        SEVERITY_WARNING,
        RestoreReconciler,
    )

    (tmp_path / "projects" / "t" / "p" / "repos" / "p.git").mkdir(parents=True)
    reconciler = RestoreReconciler(git=_NoBranchGit(), data_root=tmp_path)
    session = _Session([_plan_row("approved", "a" * 8), _plan_row("in_progress", "b" * 8)])

    divergences = asyncio.run(reconciler._check_db_vs_git(session))  # type: ignore[arg-type]

    by_subject = {d.subject: d for d in divergences}
    assert by_subject["plan plan approved (aaaaaaaa)"].severity == SEVERITY_WARNING
    assert by_subject["plan plan in_progress (bbbbbbbb)"].severity == SEVERITY_CRITICAL


# ------------------------------------------------------------- G-11 watchdog


class _Gone:
    attrs: ClassVar[dict[str, Any]] = {}

    def reload(self) -> None:
        raise RuntimeError("404 Not Found: no such container")

    def restart(self, *, timeout: int = 10) -> None:
        raise AssertionError("no se reinicia un contenedor que ya no existe")


class _Healthy:
    def __init__(self) -> None:
        self.attrs: dict[str, Any] = {
            "State": {"Status": "running", "Health": {"Status": "healthy"}}
        }

    def reload(self) -> None:
        return None

    def restart(self, *, timeout: int = 10) -> None:
        raise AssertionError("un contenedor sano no se reinicia")


def test_the_watchdog_re_resolves_a_recreated_container() -> None:
    from watchdog.service_monitor import ServiceMonitor

    fresh = _Healthy()
    monitor = ServiceMonitor(name="postgres", container=_Gone(), resolver=lambda: fresh)

    assert monitor.check_and_recover(now=100.0) == "ok"
    assert monitor.container is fresh, "el watchdog sigue mirando el contenedor viejo"


def test_without_a_replacement_the_tick_still_reports_inspect_failed() -> None:
    from watchdog.service_monitor import ServiceMonitor

    monitor = ServiceMonitor(name="postgres", container=_Gone(), resolver=lambda: None)

    assert monitor.check_and_recover(now=100.0) == "inspect_failed"


def test_monitors_are_built_with_a_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    from watchdog import __main__ as main_mod

    class _Client:
        class containers:  # noqa: N801 - imita la API de docker-py
            @staticmethod
            def list(**_kw: Any) -> list[Any]:
                return [_Healthy()]

            @staticmethod
            def get(_name: str) -> Any:
                return _Healthy()

    monkeypatch.setattr(main_mod.docker, "from_env", _Client)
    monkeypatch.setenv("WATCHDOG_SERVICES", "postgres")

    monitors = main_mod._build_monitors()

    assert len(monitors) == 1
    assert monitors[0].resolver is not None
    assert monitors[0].resolver() is not None


# ------------------------------------------------------------- G-12 / E-06 retro


class _Scalar:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar(self) -> Any:
        return self._value

    def fetchall(self) -> list[Any]:
        return []


class _RetroSession:
    def __init__(self, sink: list[tuple[str, dict[str, Any]]], *, scalar: Any = None) -> None:
        self._sink = sink
        self._scalar = scalar

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Scalar:
        self._sink.append((str(stmt), dict(params or {})))
        return _Scalar(self._scalar)

    async def __aenter__(self) -> _RetroSession:
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None


def test_the_retro_marker_lives_in_the_database_not_in_redis() -> None:
    from workers.plan_retro import DbRetroMarker, retro_plan_tag

    seen: list[tuple[str, dict[str, Any]]] = []
    marker = DbRetroMarker(lambda: _RetroSession(seen, scalar=1))

    assert asyncio.run(marker.is_done("plan-1")) is True
    stmt, params = seen[0]
    assert "memory_entries" in stmt and params["tag"] == retro_plan_tag("plan-1")


def test_closed_plans_are_selected_by_missing_retro_not_by_a_48h_window() -> None:
    from workers.plan_retro import RETRO_LOOKBACK_DAYS, _load_closed_plans_without_retro

    seen: list[tuple[str, dict[str, Any]]] = []

    asyncio.run(_load_closed_plans_without_retro(lambda: _RetroSession(seen)))

    stmt, params = seen[0]
    assert "NOT EXISTS" in stmt and "memory_entries" in stmt
    assert params["d"] == RETRO_LOOKBACK_DAYS and RETRO_LOOKBACK_DAYS >= 30


def test_the_retro_is_persisted_through_the_common_memory_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api_server.memorizer import persistence
    from workers.plan_retro import ClosedPlan, DbRetroPersister, retro_plan_tag

    captured: dict[str, Any] = {}

    async def _persist(session: Any, candidates: Any, **kwargs: Any) -> list[Any]:
        captured["candidates"] = list(candidates)
        captured.update(kwargs)
        return []

    monkeypatch.setattr(persistence, "persist_memory_candidates", _persist)
    plan = ClosedPlan(
        plan_id="0199aa11-2233-4455-6677-889900aabbcc",
        tenant_id="0199aa11-2233-4455-6677-889900aabb01",
        project_id="0199aa11-2233-4455-6677-889900aabb02",
        title="Cierre",
        status="completed",
    )

    class _TxSession(_RetroSession):
        def begin(self) -> Any:
            return self

    asyncio.run(DbRetroPersister(lambda: _TxSession([])).save(plan=plan, content="Retro…"))

    candidate = captured["candidates"][0]
    assert candidate.content == "Retro…" and candidate.type == "semantic"
    assert list(candidate.tags) == ["plan_retro", retro_plan_tag(plan.plan_id)]
    assert captured["scope"] == "project_shared"
    assert str(captured["project_id"]) == plan.project_id
    assert str(captured["tenant_id"]) == plan.tenant_id
    assert captured["extra_metadata"]["plan_id"] == plan.plan_id
