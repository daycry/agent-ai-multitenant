"""AUD16-23 (auditoría 2026-07-16): la credencial LLM caducada se detecta y
NOTIFICA en el primer abort, no tras quemar un lote de runs.

provider_error fue el abort dominante del último ciclo (17 filas, oauth de
claude_sdk caducado + cuota 429) y nadie se enteró hasta la forense. El probe
manual de /admin/llm-providers/{id}/test solo verifica PRESENCIA de la
credencial para claude_sdk — la detección eficaz es REACTIVA: un run abortado
por provider_error cuya salida lleva marcadores de auth/cuota emite el evento
platform-scoped ``provider_credential_invalid`` (prioridad, in_app+telegram).
"""

from __future__ import annotations

from typing import Any

import pytest
from workers.execution import _is_credential_failure_output, _notify_execution_outcome

pytestmark = pytest.mark.unit


def test_auth_markers_are_detected() -> None:
    assert _is_credential_failure_output("Error: Not logged in — run `claude login`") is True
    assert _is_credential_failure_output("claude_sdk: auth failed (401) invalid token") is True
    assert _is_credential_failure_output("session limit reached, HTTP 429") is True


def test_ordinary_failures_are_not_credential_noise() -> None:
    assert _is_credential_failure_output("KeyError: 'url_template'") is False
    assert _is_credential_failure_output("command exited with code 2") is False
    assert _is_credential_failure_output(None) is False
    assert _is_credential_failure_output("") is False


@pytest.fixture()
def _sent(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    async def fake_enqueue(payload: dict[str, Any]) -> None:
        sent.append(payload)

    from api_server import celery_client

    monkeypatch.setattr(celery_client, "enqueue_event_dispatch", fake_enqueue)
    return sent


@pytest.mark.asyncio
async def test_credential_failure_emits_platform_event(_sent: list[dict[str, Any]]) -> None:
    await _notify_execution_outcome(
        tenant_id="11111111-1111-1111-1111-111111111111",
        task_id="t-1",
        task_title="Tarea X",
        status="aborted",
        abort_code="provider_error",
        output="AuthError: Not logged in",
    )
    types = [p["event_type"] for p in _sent]
    assert "execution_failed" in types
    assert "provider_credential_invalid" in types
    cred = next(p for p in _sent if p["event_type"] == "provider_credential_invalid")
    # Platform-scoped: la credencial del provider es de plataforma, la ve el SA.
    assert cred["tenant_id"] is None
    assert cred["context"]["abort_code"] == "provider_error"


@pytest.mark.asyncio
async def test_non_credential_abort_emits_only_execution_failed(
    _sent: list[dict[str, Any]],
) -> None:
    await _notify_execution_outcome(
        tenant_id="11111111-1111-1111-1111-111111111111",
        task_id="t-1",
        task_title="Tarea X",
        status="aborted",
        abort_code="max_iterations_exceeded",
        output="ran out of budget",
    )
    assert [p["event_type"] for p in _sent] == ["execution_failed"]
