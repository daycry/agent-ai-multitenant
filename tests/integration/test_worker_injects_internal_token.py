"""El worker mintea + inyecta el token interno del agente en el env del
contenedor (Plan 06.17 / followup-worker-internal-token).

Estos tests ejercitan la función PURA :func:`workers.execution._build_runtime_env`
(sin docker, sin red): dada una :class:`ExecutionRequest` produce el dict de env
del `agent-runtime`. La costura 04.5 que quedaba incompleta era cablear ahí el
``AGENTIC_INTERNAL_TOKEN`` + ``AGENTIC_API_URL`` para que el runtime active las
familias de conocimiento/memoria (rag-search, memory-recall/store,
document-convert, promote-to-kb — el corazón SABER/RECORDAR de 06.17).

Contrato verificado:
  - con agente asignado → el env lleva un ``AGENTIC_INTERNAL_TOKEN`` que
    :func:`decode_agent_token` resuelve al agent_id / tenant_id correctos y con
    el claim ``task`` = ``request.task_id``; ``AGENTIC_API_URL`` presente y
    apuntando a la URL interna operator-configurable.
  - sin agente asignado → NO se mintea token (backward-compat: el runtime salta
    esas familias con gracia, el comportamiento actual).
  - ``AGENT_TASK_SPEC`` siempre presente (lo que el runtime parsea para correr).

No tocan DB ni Redis: ``mint_agent_token`` / ``decode_agent_token`` firman y
validan con el ``jwt_secret`` de la config del api-server, que fijamos por env
y reseteamos la caché para que ambos lados acuerden la misma clave.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest
from api_server.auth.internal_agent import decode_agent_token
from api_server.config import get_settings as get_api_settings
from workers.execution import ExecutionRequest, _build_runtime_env

pytestmark = pytest.mark.integration

_API_URL = "http://api-server:8000"


@pytest.fixture(autouse=True)
def _api_jwt_secret(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Fija un ``jwt_secret`` conocido para el api-server y resetea su caché.

    ``mint_agent_token`` (lado worker) y ``decode_agent_token`` (lado api-server)
    leen el mismo ``api_server.config.get_settings().jwt_secret``; el worker en
    producción recibe ``API_SERVER_JWT_SECRET`` (mismo secreto que api-server)
    para que el token minteado valide.
    """
    monkeypatch.setenv("API_SERVER_JWT_SECRET", "followup-internal-token-secret")
    get_api_settings.cache_clear()
    try:
        yield
    finally:
        get_api_settings.cache_clear()


def _request(*, agent_id: str | None) -> ExecutionRequest:
    return ExecutionRequest(
        tenant_id=str(uuid4()),
        task_id=str(uuid4()),
        agent_id=agent_id,
        task={"id": "t", "title": "Saber y recordar", "description": "exercise the seam"},
        model={"kind": "scripted", "decisions": [{"kind": "finish", "output": "ok"}]},
    )


def test_build_runtime_env_mints_token_for_assigned_agent() -> None:
    agent_id = str(uuid4())
    request = _request(agent_id=agent_id)

    env = _build_runtime_env(request, None, agent_internal_api_url=_API_URL)

    # AGENT_TASK_SPEC siempre presente: es lo que el runtime parsea.
    assert "AGENT_TASK_SPEC" in env
    spec: dict[str, Any] = json.loads(env["AGENT_TASK_SPEC"])
    assert spec["task"]["title"] == "Saber y recordar"

    # El token interno está presente y apunta al api-server interno.
    assert env["AGENTIC_API_URL"] == _API_URL
    token = env["AGENTIC_INTERNAL_TOKEN"]
    assert token

    # decode_agent_token lo resuelve al agente / tenant / tarea correctos.
    principal = decode_agent_token(token)
    assert str(principal.agent_id) == agent_id
    assert str(principal.tenant_id) == request.tenant_id
    assert principal.task_id is not None
    assert str(principal.task_id) == request.task_id


def test_build_runtime_env_without_agent_omits_token() -> None:
    request = _request(agent_id=None)

    env = _build_runtime_env(request, None, agent_internal_api_url=_API_URL)

    # Backward-compat: sin agente no hay token ni URL — el runtime salta las
    # familias de conocimiento/memoria con gracia (comportamiento actual).
    assert "AGENT_TASK_SPEC" in env
    assert "AGENTIC_INTERNAL_TOKEN" not in env
    assert "AGENTIC_API_URL" not in env


def test_build_runtime_env_honours_operator_configured_api_url() -> None:
    request = _request(agent_id=str(uuid4()))
    custom_url = "http://internal-api.svc.cluster.local:9100"

    env = _build_runtime_env(request, None, agent_internal_api_url=custom_url)

    assert env["AGENTIC_API_URL"] == custom_url
