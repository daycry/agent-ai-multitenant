"""`AGENT_TRACKED_PATHS` — el worker le dice al runtime qué árbol está versionado.

Medido en vivo el 2026-08-31 (proyecto «Hello World CI4 v3», tenant mediapro): un
agente ejecutó `delete_file` recursivo sobre `app/` y se llevó por delante **85
ficheros** que eran el entregable YA COMMITEADO de la tarea anterior. Desde dentro
del sandbox `app/` y `vendor/` son el mismo directorio: el ADR 0163 esconde el
`.git` mientras corre el agente, así que no hay a quién preguntar si un árbol es
un entregable versionado o un artefacto reconstruible.

El worker SÍ lo sabe — es el único punto que tiene worktree y git a la vez, igual
que con el diff del reviewer (`workers/review_diff.py`). Aquí se prueba SU mitad
del contrato:

  1. calcular las entradas de PRIMER NIVEL versionadas en la rama del plan
     (primer nivel y no el árbol entero: en ese proyecto eran 5.192 ficheros, y
     el env de un contenedor no es sitio para eso);
  2. publicarlas en `AGENT_TRACKED_PATHS`, separadas por saltos de línea;
  3. que ese valor llegue de verdad al `ContainerSpec` del run — el «último
     tramo» que `docs/03-guides/verificar-antes-de-implementar.md` §5 documenta
     como el sitio donde estas features se quedan sin cablear.

La otra mitad —qué hace el runtime con la lista— vive en
`docker/agent-runtimes/agent-runtime/tests/`.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from workers import execution
from workers.config import Settings
from workers.container import ContainerResult, ContainerSpec
from workers.execution import _build_runtime_env, _PreparedRun, _Workspace
from workers.run_contract import ExecutionRequest
from workers.tracked_paths import compute_tracked_top_level_paths

pytestmark = pytest.mark.unit

_API_URL = "http://api-server:8000"


# ---------------------------------------------------------------------------
# Un worktree de verdad: git es justo lo que se está ejercitando
# ---------------------------------------------------------------------------
def _git(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
            "PATH": os.environ.get("PATH", ""),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
        },
    )


@pytest.fixture()
def worktree(tmp_path: Path) -> Path:
    """Un CodeIgniter 4 en miniatura: el caso real que motivó la protección."""
    work = tmp_path / "worktree"
    (work / "app" / "Controllers").mkdir(parents=True)
    (work / "app" / "Config").mkdir(parents=True)
    (work / "system").mkdir()
    (work / "public").mkdir()
    (work / "app" / "Controllers" / "Home.php").write_text("<?php\n", encoding="utf-8")
    (work / "app" / "Config" / "App.php").write_text("<?php\n", encoding="utf-8")
    (work / "system" / "Boot.php").write_text("<?php\n", encoding="utf-8")
    (work / "public" / "index.php").write_text("<?php\n", encoding="utf-8")
    (work / "composer.json").write_text("{}\n", encoding="utf-8")
    _git("init", "-q", "-b", "main", cwd=work)
    _git("add", "-A", cwd=work)
    _git("commit", "-qm", "feat: instala CI4", cwd=work)
    # Basura NO versionada: el artefacto reconstruible que el agente sí puede
    # borrar, y un fichero suelto que aún no ha commiteado nadie.
    (work / "vendor" / "codeigniter4").mkdir(parents=True)
    (work / "vendor" / "codeigniter4" / "x.php").write_text("<?php\n", encoding="utf-8")
    (work / "writable").mkdir()
    (work / ".env").write_text("APP=1\n", encoding="utf-8")
    return work


# ---------------------------------------------------------------------------
# 1. El cálculo
# ---------------------------------------------------------------------------
def test_solo_las_entradas_de_primer_nivel_versionadas(worktree: Path) -> None:
    """Lo versionado, entero, y NADA más: ni el árbol recursivo ni lo no versionado.

    Las dos mitades importan. Si faltara `app`, el borrado de 85 ficheros del
    2026-08-31 volvería a pasar. Si sobrara `vendor`, el agente no podría
    reinstalar dependencias y el ADR 0163 (que existe porque un agente necesitó
    vaciar el directorio para `composer create-project`) quedaría inservible.
    """
    entries = compute_tracked_top_level_paths(str(worktree))

    assert sorted(entries) == ["app", "composer.json", "public", "system"]
    # Primer nivel: nada de las 5.192 rutas anidadas del caso real.
    assert not [e for e in entries if "/" in e or "\\" in e]


def test_un_worktree_sin_commit_todavia_no_protege_nada(tmp_path: Path) -> None:
    """Primera tarea de un plan sobre un proyecto vacío: la lista vacía es la
    respuesta CORRECTA, no un error. Ahí no hay entregable previo que perder."""
    work = tmp_path / "vacio"
    work.mkdir()
    _git("init", "-q", "-b", "main", cwd=work)

    assert compute_tracked_top_level_paths(str(work)) == []


def test_sin_worktree_no_hay_nada_que_calcular() -> None:
    """Runs sin worktree (análisis, diseño, tmpfs legacy): `None` → lista vacía."""
    assert compute_tracked_top_level_paths(None) == []
    assert compute_tracked_top_level_paths("") == []


def test_un_fallo_de_git_degrada_a_lista_vacia_sin_tumbar_el_run(
    worktree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Perder la protección es malo; perder la ejecución es peor.

    Se rompe git de la forma más violenta posible (una excepción cualquiera, no
    la `GitCommandError` esperada) para fijar que el degradado no depende de
    acertar con el tipo del fallo."""

    def _boom(*_args: str, **_kwargs: Any) -> str:
        raise RuntimeError("git se ha ido a dar un paseo")

    monkeypatch.setattr("workers.git_repos._run_git", _boom)

    assert compute_tracked_top_level_paths(str(worktree)) == []


def test_los_nombres_coinciden_con_los_del_disco(tmp_path: Path) -> None:
    """La única propiedad que importa: lo devuelto CASA con lo que hay en disco.

    El runtime compara la lista contra rutas reales, así que una entrada que no
    sea byte a byte el nombre del fichero no protege nada — y falla en SILENCIO,
    que es lo peor: la guarda parece puesta.

    Dos deformaciones distintas conspiran aquí, y por eso el árbol de prueba
    lleva un nombre con espacio Y otro con acentos:

      * `git ls-tree` sin `-z` entrecomilla y C-escapa los no-ASCII
        (`"\\303\\261andu.txt"`) — la mata `documentación`;
      * el runner de git decodificando con el locale del host (cp1252 en un dev
        Windows) en vez de UTF-8 devuelve mojibake (`documentaciÃ³n`) — esa NO la
        mata `mi carpeta`, que es ASCII, y es la que se coló hasta producción.

    Comparar contra `iterdir()` las mata a las dos sin tener que enumerarlas.
    """
    work = tmp_path / "acentos"
    work.mkdir()
    _git("init", "-q", "-b", "main", cwd=work)
    (work / "documentación").mkdir()
    (work / "documentación" / "guía.md").write_text("# x\n", encoding="utf-8")
    (work / "mi carpeta").mkdir()
    (work / "mi carpeta" / "a.txt").write_text("x\n", encoding="utf-8")
    (work / "diseño.md").write_text("# x\n", encoding="utf-8")
    _git("add", "-A", cwd=work)
    _git("commit", "-qm", "docs", cwd=work)

    entries = compute_tracked_top_level_paths(str(work))

    en_disco = sorted(p.name for p in work.iterdir() if p.name != ".git")
    assert sorted(entries) == en_disco
    assert "documentación" in entries  # explícito: el caso que se perdía
    assert not [e for e in entries if e.startswith('"')]


# ---------------------------------------------------------------------------
# 2. El env del contenedor (`_build_runtime_env` sigue siendo PURA)
# ---------------------------------------------------------------------------
def _request() -> ExecutionRequest:
    return ExecutionRequest(
        tenant_id=str(uuid4()),
        task_id=str(uuid4()),
        # Sin agente asignado no se mintea token: la función queda pura y el test
        # no necesita el jwt_secret del api-server.
        agent_id=None,
        task={"title": "instala CI4", "description": ""},
        model={"kind": "scripted"},
    )


def _env(tracked: list[str] | None) -> dict[str, str]:
    return _build_runtime_env(
        _request(),
        None,
        agent_internal_api_url=_API_URL,
        tracked_paths=tracked,
    )


def test_el_env_lleva_las_entradas_separadas_por_saltos_de_linea() -> None:
    """El formato ES el contrato: el runtime hace `split('\\n')`."""
    env = _env(["app", "system", "public", "composer.json"])

    assert env["AGENT_TRACKED_PATHS"] == "app\nsystem\npublic\ncomposer.json"


def test_sin_entradas_no_se_publica_la_variable() -> None:
    """Compat hacia atrás: un proyecto vacío y un worker viejo se ven IGUAL desde
    el runtime, que sin la variable no aplica la protección nueva."""
    assert "AGENT_TRACKED_PATHS" not in _env(None)
    assert "AGENT_TRACKED_PATHS" not in _env([])


def test_la_funcion_sigue_siendo_pura_y_no_llama_a_git(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_build_runtime_env` recibe la lista YA calculada. Si llamara a git aquí,
    dejaría de poder testearse en aislamiento y correría subprocesos dentro del
    armado del spec."""

    def _boom(*_args: str, **_kwargs: Any) -> str:
        raise AssertionError("_build_runtime_env no puede tocar git")

    monkeypatch.setattr("workers.git_repos._run_git", _boom)

    assert _env(["app"])["AGENT_TRACKED_PATHS"] == "app"


# ---------------------------------------------------------------------------
# 3. El cableado: worktree → _Workspace → ContainerSpec
# ---------------------------------------------------------------------------
def _prepared(
    *,
    worktree_inputs: tuple[str, str, str, str, str] | None = None,
    review_worktree: tuple[str, str] | None = None,
) -> _PreparedRun:
    return _PreparedRun(
        execution_id=uuid4(),
        approval_policy=None,
        approved_actions=[],
        guardrails=None,
        worktree_inputs=worktree_inputs,
        review_worktree=review_worktree,
        task_acceptance_criteria=[],
        plan_has_prior_work=True,
        resolved_model={"kind": "scripted"},
        resolution_error=None,
    )


@pytest.mark.asyncio
async def test_la_provision_del_workspace_calcula_las_rutas_versionadas(
    worktree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Se calcula en la provisión porque es el ÚNICO punto con worktree + git, y
    porque `conduct_execution` esconde el `.git` (ADR 0163) inmediatamente
    después: dentro del sandbox ya no hay a quién preguntar."""

    async def _fake_provision(_settings: Settings, **_kwargs: Any) -> str | None:
        return str(worktree)

    monkeypatch.setattr(execution, "_provision_worktree", _fake_provision)

    ws = await execution._provision_workspace(
        Settings(),
        _prepared(worktree_inputs=("mediapro", "hello-world-ci4-v3", "p1", "plan1", "slug")),
        task_id=uuid4(),
    )

    assert ws.host_path == str(worktree)
    assert sorted(ws.tracked_paths) == ["app", "composer.json", "public", "system"]


@pytest.mark.asyncio
async def test_un_run_de_review_no_publica_proteccion(
    worktree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El worktree del review se monta READ-ONLY (ADR 0095): el agente no puede
    borrar nada, así que no hay nada que proteger y no se paga el git."""
    monkeypatch.setattr(execution, "_resolve_review_worktree", lambda *_a, **_k: str(worktree))

    ws = await execution._provision_workspace(
        Settings(),
        _prepared(review_worktree=("mediapro", "hello-world-ci4-v3")),
        task_id=uuid4(),
    )

    assert ws.read_only is True
    assert ws.tracked_paths == []


class _CapturingRunner:
    """Doble del `AgentContainerRunner`: se queda el spec y cierra el run."""

    def __init__(self) -> None:
        self.spec: ContainerSpec | None = None

    def run_streamed(self, spec: ContainerSpec, on_line: Any, timeout: float) -> ContainerResult:
        self.spec = spec
        on_line(
            json.dumps(
                {
                    "event": "execution.finished",
                    "result": {"status": "completed", "output": "ok", "iterations": 1},
                }
            )
        )
        return ContainerResult(
            container_id="c1",
            exit_code=0,
            logs="",
            timed_out=False,
            host_config={},
            config_env=(),
            networks=(),
        )

    def kill_by_label(self, *_args: Any) -> None:
        raise AssertionError("este run no se cancela")


class _FakeTxn:
    async def __aenter__(self) -> _FakeTxn:
        return self

    async def __aexit__(self, *_exc: Any) -> bool:
        return False


class _FakeSession:
    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_exc: Any) -> bool:
        return False

    def begin(self) -> _FakeTxn:
        return _FakeTxn()


@pytest.mark.asyncio
async def test_el_contenedor_arranca_con_la_variable_puesta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El último tramo: lo que calculó la provisión llega al `ContainerSpec`.

    Sin esta atadura el mecanismo entero puede existir, estar testeado por
    separado y no proteger NADA en producción, que es el modo de fallo dominante
    en esta base (`verificar-antes-de-implementar.md` §5)."""

    async def _no_execution(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def _no_publish(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(execution, "get_execution", _no_execution)
    monkeypatch.setattr(execution, "publish_execution_event", _no_publish)
    runner = _CapturingRunner()

    result, _approval, _decls = await execution._launch_and_stream(
        _request(),
        settings=Settings(),
        # La clase ES el sessionmaker: llamarla devuelve una sesión, igual que
        # `async_sessionmaker`. Redis no se toca: `publish_execution_event`
        # está sustituido más arriba.
        sessionmaker=_FakeSession,
        redis=None,
        prepared=_prepared(),
        workspace=_Workspace(
            host_path="/data/agent-platform/wt",
            tracked_paths=["app", "system", "composer.json"],
        ),
        exec_id="exec-1",
        runner=runner,
        # Alto a propósito: el vigía de cancelación no debe despertar en el test.
        cancel_poll_interval_s=3600.0,
    )

    assert result.status == "completed"
    assert runner.spec is not None
    assert runner.spec.env["AGENT_TRACKED_PATHS"] == "app\nsystem\ncomposer.json"
