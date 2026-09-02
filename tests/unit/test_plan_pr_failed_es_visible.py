"""Un auto-PR que falla se ve: evento `plan_pr_failed` y motivo persistido SIEMPRE.

Auditoría 2026-09-01 (D-01/D-02), `task_cv_14`. Cuando el auto-PR fallaba, el
motivo iba a `plan.pr_error` (P6)… sólo si el fallo ocurría DENTRO del `try` del
opener. Un fallo en el preámbulo —docs de cierre, contexto del PR, motor de BD—
lo capturaba la task Celery, lo logueaba y devolvía `error:`: el plan quedaba
`completed` sin `pr_url` ni `pr_error`, indistinguible de «aún en cola». Y aun
cuando el motivo se persistía, nadie avisaba: la ficha del plan lo mostraba a
quien la abriera, ninguna notificación llegaba a nadie.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from workers import plan_pr

pytestmark = pytest.mark.unit


class _Txn:
    async def __aenter__(self) -> _Txn:
        return self

    async def __aexit__(self, *_exc: Any) -> bool:
        return False


class _Session:
    def __init__(self, plan: Any) -> None:
        self._plan = plan

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_exc: Any) -> bool:
        return False

    def begin(self) -> _Txn:
        return _Txn()

    async def get(self, _model: Any, _id: Any) -> Any:
        return self._plan


def _plan(**kw: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "title": "Migrar a CI4",
        "pr_url": None,
        "pr_branch": None,
        "pr_error": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture()
def emitted(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    async def _record(payload: dict[str, Any]) -> bool:
        events.append(payload)
        return True

    from api_server import celery_client

    monkeypatch.setattr(celery_client, "enqueue_event_dispatch", _record)
    return events


@pytest.mark.asyncio
async def test_a_persisted_pr_failure_emits_plan_pr_failed(
    emitted: list[dict[str, Any]],
) -> None:
    plan = _plan()
    await plan_pr._persist_pr_result(
        lambda: _Session(plan),
        str(plan.id),
        pr_url=None,
        pr_branch="plan/abc-migrar",
        pr_error="GitHub PR falló (401): bad credentials",
    )

    assert plan.pr_error == "GitHub PR falló (401): bad credentials"
    # `task_cv_45` (G-10): un 401 es ADEMÁS una credencial rechazada.
    assert [e["event_type"] for e in emitted] == ["plan_pr_failed", "git_credential_failed"]
    event = emitted[0]
    assert event["tenant_id"] == str(plan.tenant_id)
    assert event["context"]["plan_id"] == str(plan.id)
    assert event["context"]["plan_name"] == "Migrar a CI4"
    assert "401" in event["context"]["reason"]


@pytest.mark.asyncio
async def test_a_successful_pr_emits_nothing(emitted: list[dict[str, Any]]) -> None:
    plan = _plan()
    await plan_pr._persist_pr_result(
        lambda: _Session(plan),
        str(plan.id),
        pr_url="https://github.com/o/r/pull/7",
        pr_branch="plan/abc-migrar",
        pr_error=None,
    )
    assert plan.pr_url == "https://github.com/o/r/pull/7"
    assert emitted == []


@pytest.mark.asyncio
async def test_a_failure_that_keeps_an_existing_pr_emits_nothing(
    emitted: list[dict[str, Any]],
) -> None:
    """El PR sigue abierto en el proveedor: no hay nada que avisar ni que pisar."""
    plan = _plan(pr_url="https://github.com/o/r/pull/7")
    await plan_pr._persist_pr_result(
        lambda: _Session(plan),
        str(plan.id),
        pr_url=None,
        pr_branch="plan/abc-migrar",
        pr_error="GitHub PR falló (422): already exists",
        keep_existing_url=True,
    )
    assert plan.pr_url == "https://github.com/o/r/pull/7"
    assert plan.pr_error is None
    assert emitted == []


def test_a_failure_before_the_opener_still_lands_on_the_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La task Celery captura TODO; lo que capture tiene que llegar a `pr_error`."""
    persisted: list[dict[str, Any]] = []

    async def _explota(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise RuntimeError("closure docs: el motor de BD no arrancó")

    async def _persist(_sm: Any, plan_id: str, **kw: Any) -> None:
        persisted.append({"plan_id": plan_id, **kw})

    monkeypatch.setattr(plan_pr, "_open_plan_pr_async", _explota)
    monkeypatch.setattr(plan_pr, "_persist_pr_result", _persist)
    monkeypatch.setattr(plan_pr, "get_settings", SimpleNamespace)

    async def _dispose() -> None:
        return None

    monkeypatch.setattr(plan_pr, "worker_engine", lambda _s: SimpleNamespace(dispose=_dispose))

    plan_id = str(uuid4())
    result = plan_pr.open_plan_pr(str(uuid4()), plan_id, "Plan: x", "body")

    assert result["status"].startswith("error:")
    assert len(persisted) == 1
    assert persisted[0]["plan_id"] == plan_id
    assert persisted[0]["pr_url"] is None
    assert persisted[0]["keep_existing_url"] is True
    assert "motor de BD" in persisted[0]["pr_error"]
