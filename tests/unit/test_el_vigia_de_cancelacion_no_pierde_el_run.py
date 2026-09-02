"""Un fallo del vigía de cancelación no puede perder un run que terminó bien.

Auditoría 2026-09-01 (A-03). `_watch_for_cancel` consulta `cancel_requested_at`
cada pocos segundos durante todo el run —unas 2.400 consultas en un run de dos
horas— sin capturar nada. El `finally` de `_launch_and_stream` hacía
`watcher.cancel(); with suppress(CancelledError): await watcher`, que re-lanza
cualquier OTRA excepción del vigía justo cuando el contenedor acaba de terminar:
el `execution.finished` se descartaba, la fila quedaba `running` hasta que el
sweeper la sellaba con una etiqueta falsa (`stale_after_worker_loss`), la tarea
acababa `blocked` y `staged_credentials.cleanup()` —que iba después— no se
alcanzaba: el fichero de credencial quedaba en disco.

Un blip de BD (reinicio de Postgres, `statement_timeout`, pool agotado) es
rutina en dos horas. El vigía es un accesorio del run, no su dueño.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from workers import execution
from workers.config import Settings
from workers.container import ContainerResult, ContainerSpec
from workers.execution import _PreparedRun, _Workspace
from workers.run_contract import ExecutionRequest

pytestmark = pytest.mark.unit


def _request() -> ExecutionRequest:
    return ExecutionRequest(
        tenant_id=str(uuid4()),
        task_id=str(uuid4()),
        agent_id=None,
        task={"title": "instala CI4", "description": ""},
        model={"kind": "scripted"},
    )


def _prepared() -> _PreparedRun:
    return _PreparedRun(
        execution_id=uuid4(),
        approval_policy=None,
        approved_actions=[],
        guardrails=None,
        worktree_inputs=None,
        review_worktree=None,
        task_acceptance_criteria=[],
        plan_has_prior_work=False,
        resolved_model={"kind": "scripted"},
        resolution_error=None,
    )


class _SlowRunner:
    """El contenedor tarda lo bastante para que el vigía llegue a sondear."""

    def __init__(self, run_for_s: float) -> None:
        self._run_for_s = run_for_s
        self.spec: ContainerSpec | None = None

    def run_streamed(self, spec: ContainerSpec, on_line: Any, timeout: float) -> ContainerResult:
        self.spec = spec
        import time

        time.sleep(self._run_for_s)
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
        raise AssertionError("nadie canceló este run")


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


class _Staged:
    """Doble del staging de la credencial: sólo importa si se limpió."""

    mounts: tuple[Any, ...] = ()

    def __init__(self) -> None:
        self.cleaned = False
        # `task_cv_20`: el spec y el token se escriben en el MISMO staging
        self.staging_dir = Path(tempfile.mkdtemp(prefix="staged-test-"))

    def cleanup(self) -> None:
        self.cleaned = True
        shutil.rmtree(self.staging_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_un_blip_de_bd_en_el_vigia_no_pierde_el_run_ni_la_limpieza(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llamadas = {"n": 0}

    async def _get_execution_con_blip(*_args: Any, **_kwargs: Any) -> None:
        # La primera llamada es la del lanzamiento (`container_launched_at`); a
        # partir de ahí el vigía sondea, y la BD se cae justo entonces.
        llamadas["n"] += 1
        if llamadas["n"] >= 2:
            raise ConnectionError("la BD se fue a dar un paseo")

    async def _no_publish(*_args: Any, **_kwargs: Any) -> None:
        return None

    staged = _Staged()
    monkeypatch.setattr(execution, "get_execution", _get_execution_con_blip)
    monkeypatch.setattr(execution, "publish_execution_event", _no_publish)
    monkeypatch.setattr(execution, "_stage_model_credentials", lambda *_a, **_k: (None, staged))
    runner = _SlowRunner(run_for_s=0.3)

    result, _approval, _decls = await execution._launch_and_stream(
        _request(),
        settings=Settings(),
        sessionmaker=_FakeSession,
        redis=None,
        prepared=_prepared(),
        workspace=_Workspace(host_path=None),
        exec_id="exec-1",
        runner=runner,
        cancel_poll_interval_s=0.05,
    )

    assert result.status == "completed", (
        "el fallo del vigía se llevó por delante un run que el contenedor terminó bien"
    )
    assert llamadas["n"] >= 2, "el vigía no llegó a sondear: el test no ejercita el fallo"
    assert staged.cleaned, "la credencial staged quedó en disco"


@pytest.mark.asyncio
async def test_la_credencial_se_limpia_aunque_el_contenedor_reviente(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El `cleanup()` no depende de que el vigía termine limpio ni de que el
    contenedor haya ido bien: va en su propio `finally`."""

    async def _get_execution_ok(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def _no_publish(*_args: Any, **_kwargs: Any) -> None:
        return None

    class _Explota(_SlowRunner):
        def run_streamed(
            self, spec: ContainerSpec, on_line: Any, timeout: float
        ) -> ContainerResult:
            raise RuntimeError("el daemon no respondió")

    staged = _Staged()
    monkeypatch.setattr(execution, "get_execution", _get_execution_ok)
    monkeypatch.setattr(execution, "publish_execution_event", _no_publish)
    monkeypatch.setattr(execution, "_stage_model_credentials", lambda *_a, **_k: (None, staged))

    # `task_cv_15` (A-07): el fallo del daemon ya no sale sin capturar; el run
    # vuelve como `failed(container_launch_failed)` y se finaliza como cualquiera.
    result, _approval, _decls = await execution._launch_and_stream(
        _request(),
        settings=Settings(),
        sessionmaker=_FakeSession,
        redis=None,
        prepared=_prepared(),
        workspace=_Workspace(host_path=None),
        exec_id="exec-2",
        runner=_Explota(run_for_s=0.0),
        cancel_poll_interval_s=3600.0,
    )

    assert (result.status, result.abort_code) == ("failed", "container_launch_failed")
    assert staged.cleaned
    await asyncio.sleep(0)  # deja morir al vigía cancelado sin avisos de tarea pendiente
