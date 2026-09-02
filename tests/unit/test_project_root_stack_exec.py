"""ADR 0162 (decisión 1) — `stack_exec` lee la raíz del proyecto y la propaga.

El dato vive en `projects.repository_config.project_root` (JSONB, sin migración)
y es DEL OPERADOR. Este módulo fija el tramo que va de la fila de proyecto a las
dos cosas que dependen de ella: el `TestRuntimeSpec` con el que corre el comando
y la búsqueda del lockfile para la caché de dependencias.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

pytestmark = pytest.mark.unit


def _wire_db(monkeypatch: pytest.MonkeyPatch, *, repository_config: dict[str, Any] | None) -> None:
    """Mockea engine+session: task→project(php-phpunit, `php` permitido)→org."""
    from workers.tasks import stack_exec_task as tasks

    project = SimpleNamespace(
        id=uuid4(),
        slug="api-ci",
        allowed_commands=["php"],
        default_runtime_template="php-phpunit",
        repository_config=repository_config,
    )
    task = SimpleNamespace(id=uuid4(), project_id=project.id)
    org = SimpleNamespace(slug="demo")

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

    monkeypatch.setattr(tasks, "worker_engine", lambda _settings: _Engine())
    monkeypatch.setattr(tasks, "async_sessionmaker", lambda _engine, **_k: _Session)
    monkeypatch.setattr("docker.from_env", MagicMock)


def _capture_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """Sustituye el runner y el worktree; devuelve el dict donde se anota todo."""
    seen: dict[str, Any] = {}
    wt = tmp_path / "task"
    wt.mkdir()
    monkeypatch.setattr(
        "workers.git_repos.BareRepoLayout",
        lambda **_k: SimpleNamespace(worktree_path=lambda _t: str(wt)),
    )

    class _Runner:
        def __init__(self, settings: object) -> None: ...

        def run_command(
            self, spec: Any, command: str, *, timeout_s: int, cwd: str | None = None
        ) -> tuple[int, str]:
            seen["spec"] = spec
            seen["cwd"] = cwd
            return 0, "ok"

    monkeypatch.setattr("workers.test_runtime.TestRuntimeRunner", _Runner)

    from workers.tasks import stack_exec_task as tasks

    def _dep_cache(
        template: Any,
        worktree_host_path: str,
        data_root: str,
        project_root: str | None = None,
        *,
        tenant_slug: str | None = None,
    ) -> str | None:
        seen["dep_cache_root"] = project_root
        seen["dep_cache_tenant"] = tenant_slug
        return None

    monkeypatch.setattr(tasks, "_resolve_stack_dep_cache", _dep_cache)
    return seen


def _request(**extra: Any) -> dict[str, Any]:
    base = {"tenant_id": str(uuid4()), "task_id": str(uuid4()), "command": "php spark migrate"}
    base.update(extra)
    return base


@pytest.mark.asyncio
async def test_the_project_root_reaches_the_spec(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from workers.config import Settings
    from workers.tasks import stack_exec_task as tasks

    _wire_db(monkeypatch, repository_config={"project_root": "ci4build"})
    seen = _capture_run(monkeypatch, tmp_path)

    result = await tasks._run_stack_command(_request(), Settings())

    assert result["exit_code"] == 0
    assert seen["spec"].project_root == "ci4build"
    # El `cwd` del agente se pasa tal cual: la precedencia la resuelve el runner
    # (un único sitio), no cada llamador por su cuenta.
    assert seen["cwd"] is None


@pytest.mark.asyncio
async def test_a_project_without_root_keeps_todays_behaviour(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No-regresión: `repository_config` a NULL es el caso de casi todos los
    proyectos vivos."""
    from workers.config import Settings
    from workers.tasks import stack_exec_task as tasks

    _wire_db(monkeypatch, repository_config=None)
    seen = _capture_run(monkeypatch, tmp_path)

    await tasks._run_stack_command(_request(), Settings())

    assert seen["spec"].project_root is None
    assert seen["dep_cache_root"] is None


@pytest.mark.asyncio
async def test_the_dep_cache_looks_at_the_project_root_not_at_the_command_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """El lockfile vive en la RAÍZ DEL PROYECTO, no donde corra un comando suelto.

    Este test afirmaba lo contrario —que la caché siguiera al `cwd` efectivo, «la
    misma precedencia que aplica el runner»— y sonaba coherente, pero era una
    regresión: en un proyecto EN LA RAÍZ (la inmensa mayoría), un agente que
    pidiera `cwd='tests'` mandaba a la caché a buscar `composer.lock` dentro de
    `tests/`. No lo encontraba y la caché se apagaba, cuando antes del ADR 0162
    acertaba siempre porque miraba la raíz del worktree.

    La precedencia del ADR gobierna DÓNDE SE EJECUTA el comando; dónde está el
    lockfile es otra pregunta y tiene otra respuesta.

    Lo que se pierde y consta a propósito: en un monorepo donde el agente corre
    en `apps/api` y ESE subproyecto tiene su propio lockfile, la caché fallará.
    Es un fallo de caché, no de corrección —el install corre en frío—, hoy queda
    LOGUEADO en vez de en silencio, y nunca funcionó antes. Se prefiere a
    regresar los proyectos que sí funcionan.
    """
    from workers.config import Settings
    from workers.tasks import stack_exec_task as tasks

    _wire_db(monkeypatch, repository_config={"project_root": "ci4build"})
    seen = _capture_run(monkeypatch, tmp_path)

    await tasks._run_stack_command(_request(), Settings())
    assert seen["dep_cache_root"] == "ci4build"

    # El `cwd` explícito del agente NO desvía la caché.
    await tasks._run_stack_command(_request(cwd="apps/api"), Settings())
    assert seen["dep_cache_root"] == "ci4build"


@pytest.mark.asyncio
async def test_a_root_project_with_an_explicit_cwd_keeps_its_warm_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """La no-regresión concreta que motivó el cambio de arriba: proyecto sin
    `project_root` (el caso normal) y un `cwd` explícito del agente. La caché
    tiene que seguir mirando la raíz del worktree, como antes del ADR 0162."""
    from workers.config import Settings
    from workers.tasks import stack_exec_task as tasks

    _wire_db(monkeypatch, repository_config={})
    seen = _capture_run(monkeypatch, tmp_path)

    await tasks._run_stack_command(_request(cwd="tests"), Settings())
    assert seen["dep_cache_root"] is None, (
        "con el proyecto en la raíz, un `cwd` explícito desvió la caché a un "
        "subdirectorio sin lockfile: se apaga una caché que antes acertaba"
    )


def test_resolve_stack_dep_cache_finds_a_nested_lockfile(tmp_path: Path) -> None:
    """Sin esto la caché se apagaba para todo proyecto anidado: `composer
    install` reinstalaba el árbol entero en cada run."""
    from shared_test_runtimes.catalog import get
    from workers.tasks.stack_exec_task import _resolve_stack_dep_cache

    worktree = tmp_path / "wt"
    (worktree / "ci4build").mkdir(parents=True)
    (worktree / "ci4build" / "composer.lock").write_bytes(b'{"hash":"abc"}\n')
    data_root = tmp_path / "data"

    warm = _resolve_stack_dep_cache(
        get("php-phpunit"),
        str(worktree),
        str(data_root),
        project_root="ci4build",
        tenant_slug="acme",
    )
    assert warm is not None
    assert Path(warm).is_dir()
    assert Path(warm).name.startswith("composer-")

    # Sin la raíz, el lockfile no aparece y la caché se queda apagada (el
    # comportamiento de hoy, que es justo el que el ADR 0162 mide).
    assert (
        _resolve_stack_dep_cache(
            get("php-phpunit"), str(worktree), str(data_root), tenant_slug="acme"
        )
        is None
    )
