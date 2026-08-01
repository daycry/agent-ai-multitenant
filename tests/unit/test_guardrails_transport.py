"""ADR 0102 D3 — transporte de la config de guardrails resuelta al runtime.

El worker fusiona la capa PLATAFORMA (platform_settings.guardrails_config) con
la capa PROYECTO (projects.guardrails_config) vía resolve_config (los checks
locked de plataforma ganan) y pone el resultado serializado en
``spec["guardrails"]`` — que build_pipeline del runtime ya consume. Sin capas
configuradas no se emite la clave (el runtime cae a su baseline LOG).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from workers.execution import ExecutionRequest, _agent_spec, _resolve_effective_guardrails

pytestmark = pytest.mark.unit


def _request() -> ExecutionRequest:
    return ExecutionRequest(
        tenant_id=str(uuid4()),
        task_id=str(uuid4()),
        agent_id=str(uuid4()),
        task={"id": "t-1", "title": "x", "description": ""},
        model={"kind": "ollama"},
    )


def test_agent_spec_threads_guardrails() -> None:
    # Como approval_policy: la config resuelta viaja como kwarg del worker.
    config = {"guardrails": {"pre_tool": [{"type": "keyword", "action": "block"}]}}
    spec = _agent_spec(_request(), None, guardrails=config)
    assert spec["guardrails"] == config


def test_agent_spec_omits_empty_guardrails() -> None:
    assert "guardrails" not in _agent_spec(_request(), None)
    assert "guardrails" not in _agent_spec(_request(), None, guardrails={})


@pytest.mark.asyncio
async def test_resolver_none_when_there_is_no_project() -> None:
    """Sin proyecto no hay capas que resolver: el runtime cae a su baseline."""
    assert await _resolve_effective_guardrails(object(), None) is None


@pytest.mark.asyncio
async def test_resolver_degrades_to_the_baseline_instead_of_breaking_the_dispatch() -> None:
    """Contrato best-effort: un fallo resolviendo NUNCA tumba un run.

    Se le pasa un proyecto de mentira sobre una sesión que no lo es; el
    resolvedor tiene que devolver ``None`` (baseline del runtime) en vez de
    propagar. Es la mitad del contrato que se puede fijar sin base de datos: la
    fusión de las TRES capas se prueba contra PostgreSQL en
    `tests/integration/test_dispatch_guardrail_config.py`, que es donde de
    verdad se lee la tabla `guardrail_configs` (la capa TENANT no es
    observable con un monkeypatch de la capa de plataforma).
    """

    class _NotAProject:
        pass

    assert await _resolve_effective_guardrails(object(), _NotAProject()) is None
