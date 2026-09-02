"""Un fallo del daemon al lanzar sella el run; un review nunca aparca.

Auditoría 2026-09-01 (A-07, A-08), `task_cv_15`.

A-07: un `APIError` de `docker.from_env()` / `containers.run` salía de
`_launch_and_stream` sin capturar: la fila quedaba `running`, el mensaje iba a la
DLQ y cinco minutos después el sweeper la sellaba como «worker loss», que no es
lo que pasó. Ahora el lanzamiento que falla es un resultado `failed` con nombre
(`container_launch_failed`) que se finaliza y transiciona como cualquier otro.
El `SoftTimeLimitExceeded` de Celery NO se captura: tiene su propio camino.

A-08: un run de REVIEW que aparcaba en `awaiting_human_approval` quedaba no
terminal para siempre (nadie lo reanuda: su workspace es de sólo lectura y su
único producto es el veredicto). Dos capas: la política que recibe un review no
gatea ninguna categoría, y si aun así aparca, se sella como `failed` con nombre.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

import docker.errors
import pytest
from api_server.db import approval_repo
from celery.exceptions import SoftTimeLimitExceeded
from shared_domain.approval_categories import APPROVAL_CATEGORIES
from workers import execution
from workers.config import Settings
from workers.container import ContainerResult, ContainerSpec
from workers.execution import _PreparedRun, _RuntimeResult, _Workspace
from workers.run_contract import ExecutionRequest

pytestmark = pytest.mark.unit


def _request(*, review: bool = False) -> ExecutionRequest:
    return ExecutionRequest(
        tenant_id=str(uuid4()),
        task_id=str(uuid4()),
        agent_id=None,
        task={"title": "t", "description": ""},
        model={"kind": "scripted"},
        review=review,
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
    mounts: tuple[Any, ...] = ()

    def __init__(self) -> None:
        self.cleaned = False
        # `task_cv_20`: el spec y el token se escriben en el MISMO staging
        self.staging_dir = Path(tempfile.mkdtemp(prefix="staged-test-"))

    def cleanup(self) -> None:
        self.cleaned = True
        shutil.rmtree(self.staging_dir, ignore_errors=True)


class _DaemonRechaza:
    def run_streamed(self, spec: ContainerSpec, on_line: Any, timeout: float) -> ContainerResult:
        raise docker.errors.APIError("500 Server Error: cannot start container: no space left")

    def kill_by_label(self, *_a: Any) -> None:  # pragma: no cover - nadie cancela
        raise AssertionError


class _SeAgotaElTiempo(_DaemonRechaza):
    def run_streamed(self, spec: ContainerSpec, on_line: Any, timeout: float) -> ContainerResult:
        raise SoftTimeLimitExceeded()


@pytest.fixture()
def _sin_bd(monkeypatch: pytest.MonkeyPatch) -> _Staged:
    async def _get_execution(*_a: Any, **_k: Any) -> None:
        return None

    async def _no_publish(*_a: Any, **_k: Any) -> None:
        return None

    staged = _Staged()
    monkeypatch.setattr(execution, "get_execution", _get_execution)
    monkeypatch.setattr(execution, "publish_execution_event", _no_publish)
    monkeypatch.setattr(execution, "_stage_model_credentials", lambda *_a, **_k: (None, staged))
    return staged


async def _launch(runner: Any) -> tuple[_RuntimeResult, Any, Any]:
    return await execution._launch_and_stream(
        _request(),
        settings=Settings(),
        sessionmaker=_FakeSession,
        redis=None,
        prepared=_prepared(),
        workspace=_Workspace(host_path=None),
        exec_id="exec-launch",
        runner=runner,
        cancel_poll_interval_s=3600.0,
    )


@pytest.mark.asyncio
async def test_un_apierror_del_daemon_es_un_run_fallido_con_nombre(_sin_bd: _Staged) -> None:
    result, approval, _decls = await _launch(_DaemonRechaza())

    assert result.status == "failed"
    assert result.abort_code == "container_launch_failed"
    assert "no space left" in (result.output or "")
    assert approval is None
    assert _sin_bd.cleaned, "la credencial staged quedó en disco"


@pytest.mark.asyncio
async def test_el_soft_time_limit_de_celery_sigue_su_propio_camino(_sin_bd: _Staged) -> None:
    with pytest.raises(SoftTimeLimitExceeded):
        await _launch(_SeAgotaElTiempo())
    assert _sin_bd.cleaned


# ------------------------------------------------------------------ A-08: review


def _awaiting(**kw: Any) -> _RuntimeResult:
    base: dict[str, Any] = {
        "status": "awaiting_human_approval",
        "abort_code": None,
        "output": "quiero hacer un POST",
        "iterations": 3,
        "steps": [],
        "usage": {"input_tokens": 10},
    }
    base.update(kw)
    return _RuntimeResult(**base)


_ACTION = {"category": "http_post", "action": {"tool": "http_post", "args": {}}}


def test_un_review_que_aparca_se_sella_como_fallido_con_nombre() -> None:
    sealed = execution._seal_invalid_park(is_review=True, result=_awaiting(), approval=_ACTION)
    assert (sealed.status, sealed.abort_code) == ("failed", "review_parked")
    assert sealed.iterations == 3 and sealed.usage == {"input_tokens": 10}


def test_un_implementador_que_aparca_sin_payload_sigue_siendo_f12() -> None:
    sealed = execution._seal_invalid_park(is_review=False, result=_awaiting(), approval=None)
    assert (sealed.status, sealed.abort_code) == ("failed", "approval_payload_missing")


def test_un_implementador_que_aparca_con_payload_no_se_toca() -> None:
    result = _awaiting()
    assert execution._seal_invalid_park(is_review=False, result=result, approval=_ACTION) is result


def test_un_resultado_terminal_no_se_toca_en_ningun_camino() -> None:
    done = _awaiting(status="done")
    assert execution._seal_invalid_park(is_review=True, result=done, approval=None) is done
    assert execution._seal_invalid_park(is_review=False, result=done, approval=None) is done


def test_la_politica_de_un_review_no_gatea_ninguna_categoria() -> None:
    policy = execution._review_run_policy()
    for category in APPROVAL_CATEGORIES:
        if category == approval_repo.HUMAN_QUESTION_CATEGORY:
            continue  # siempre humana por diseño (ADR 0114), la política no manda ahí
        assert approval_repo.requires_human_approval(policy, category) is False, category
    assert approval_repo.requires_human_approval(policy, "categoria_inventada") is False
    assert approval_repo.unlisted_category_reason(policy, "categoria_inventada") is None
    json.dumps(policy)  # viaja al runtime como JSON
