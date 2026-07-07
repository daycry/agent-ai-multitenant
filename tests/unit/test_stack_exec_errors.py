"""Unit — auditoría runs 2026-07-02 (F0.3): errores accionables en stack_exec.

Incidente /data del 2026-07-02: con el worktree arrasado, `_run_stack_command`
reconstruía la ruta sin comprobar que existe y `containers/create` reventaba con
docker 400 «bind source path does not exist»; la excepción mataba la task Celery
y el agente recibía un 502 genérico y ENGAÑOSO («failed to reach the worker»)
que alimentaba reintentos inútiles. Ahora:

  1. worktree inexistente → respuesta estructurada accionable SIN lanzar contenedor;
  2. docker.errors.APIError → capturado y devuelto como error estructurado.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

import docker

pytestmark = pytest.mark.unit


def _wire_db(
    monkeypatch: pytest.MonkeyPatch, *, project_slug: str = "api-ci", org_slug: str = "demo"
) -> None:
    """Mockea engine+session: task→project(php-phpunit, composer permitido)→org.

    ``project_slug``/``org_slug`` son configurables (defaults iguales a los reales)
    para ejercitar la guarda de slug vacío de ADR 0093 sin tocar los tests previos."""
    from workers import tasks

    project = SimpleNamespace(
        id=uuid4(),
        slug=project_slug,
        allowed_commands=["composer"],
        default_runtime_template="php-phpunit",
    )
    task = SimpleNamespace(id=uuid4(), project_id=project.id)
    org = SimpleNamespace(slug=org_slug)

    class _Res:
        def __init__(self, obj: object) -> None:
            self._obj = obj

        def scalar_one_or_none(self) -> object:
            return self._obj

    class _Session:
        def __init__(self) -> None:
            self._seq = [task, project]
            self._i = 0

        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *a: object) -> bool:
            return False

        async def execute(self, _stmt: object) -> _Res:
            obj = self._seq[self._i]
            self._i += 1
            return _Res(obj)

        async def get(self, _model: object, _pk: object) -> object:
            return org

    class _Engine:
        async def dispose(self) -> None: ...

    monkeypatch.setattr(tasks, "create_async_engine", lambda _url: _Engine())
    monkeypatch.setattr(tasks, "async_sessionmaker", lambda _engine, **_k: (lambda: _Session()))
    monkeypatch.setattr("docker.from_env", lambda: MagicMock())


def _request() -> dict:
    return {"tenant_id": str(uuid4()), "task_id": str(uuid4()), "command": "composer install"}


@pytest.mark.asyncio
async def test_missing_worktree_returns_actionable_error_without_launch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from workers import tasks
    from workers.config import Settings

    _wire_db(monkeypatch)
    missing = tmp_path / "nope" / "task"
    monkeypatch.setattr(
        "workers.git_repos.BareRepoLayout",
        lambda **_k: SimpleNamespace(worktree_path=lambda _t: str(missing)),
    )
    launched: dict = {}

    class _NeverRunner:
        def __init__(self, settings: object) -> None: ...

        def run_command(self, spec: object, command: str, *, timeout_s: int) -> tuple[int, str]:
            launched["yes"] = True
            return 0, "ok"

    monkeypatch.setattr("workers.test_runtime.TestRuntimeRunner", _NeverRunner)

    result = await tasks._run_stack_command(_request(), Settings())

    assert launched == {}  # no se llegó a lanzar el runtime
    assert result["exit_code"] == -1
    assert result["timed_out"] is False
    # Mensaje accionable: el problema es el workspace, no el worker/proxy.
    assert "worktree" in result["logs"] or "workspace" in result["logs"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("project_slug", "org_slug"), [("", "demo"), ("api-ci", "")])
async def test_empty_slug_returns_not_resolvable_without_launch(
    monkeypatch: pytest.MonkeyPatch, project_slug: str, org_slug: str
) -> None:
    """ADR 0093 (bug del slug vacío): un project/org con slug '' NO debe resolver un
    worktree — el path colapsaría (``projects/<tenant>//worktrees/<task>``) apuntando a
    un workspace ajeno o inexistente. La guarda corta ANTES de tocar Docker/el runtime.

    Regresión: si se elimina la parte de slug de la guarda (tasks.py:541), el flujo
    continúa, BareRepoLayout colapsa el path y el mensaje cambia → esta aserción falla.
    """
    from workers import tasks
    from workers.config import Settings

    _wire_db(monkeypatch, project_slug=project_slug, org_slug=org_slug)
    launched: dict = {}

    class _NeverRunner:
        def __init__(self, settings: object) -> None: ...

        def run_command(self, spec: object, command: str, *, timeout_s: int) -> tuple[int, str]:
            launched["yes"] = True
            return 0, "ok"

    monkeypatch.setattr("workers.test_runtime.TestRuntimeRunner", _NeverRunner)

    result = await tasks._run_stack_command(_request(), Settings())

    assert launched == {}  # la guarda cortó antes de lanzar el runtime
    assert result == {"exit_code": -1, "logs": "project/org not resolvable", "timed_out": False}


@pytest.mark.asyncio
async def test_docker_apierror_is_returned_structured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from workers import tasks
    from workers.config import Settings

    _wire_db(monkeypatch)
    wt = tmp_path / "task"
    wt.mkdir()
    monkeypatch.setattr(
        "workers.git_repos.BareRepoLayout",
        lambda **_k: SimpleNamespace(worktree_path=lambda _t: str(wt)),
    )

    class _ExplodingRunner:
        def __init__(self, settings: object) -> None: ...

        def run_command(self, spec: object, command: str, *, timeout_s: int) -> tuple[int, str]:
            raise docker.errors.APIError(
                "400 Client Error",
                explanation="invalid mount config: bind source path does not exist",
            )

    monkeypatch.setattr("workers.test_runtime.TestRuntimeRunner", _ExplodingRunner)

    result = await tasks._run_stack_command(_request(), Settings())

    assert result["exit_code"] == -1
    assert result["timed_out"] is False
    assert "docker" in result["logs"].lower()
    assert "bind source path does not exist" in result["logs"]
