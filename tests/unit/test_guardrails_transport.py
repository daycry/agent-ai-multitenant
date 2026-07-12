"""ADR 0102 D3 — transporte de la config de guardrails resuelta al runtime.

El worker fusiona la capa PLATAFORMA (platform_settings.guardrails_config) con
la capa PROYECTO (projects.guardrails_config) vía resolve_config (los checks
locked de plataforma ganan) y pone el resultado serializado en
``spec["guardrails"]`` — que build_pipeline del runtime ya consume. Sin capas
configuradas no se emite la clave (el runtime cae a su baseline LOG).
"""

from __future__ import annotations

from typing import Any
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
async def test_resolver_merges_platform_and_project(monkeypatch: pytest.MonkeyPatch) -> None:
    platform_cfg = {
        "guardrails": {
            "post_tool": [{"type": "prompt_injection", "action": "block", "locked": True}]
        }
    }

    async def _platform(session: Any) -> dict[str, Any]:
        return platform_cfg

    monkeypatch.setattr("api_server.db.platform_settings.get_guardrails_config", _platform)

    class _Project:
        def __init__(self) -> None:
            self.guardrails_config = {
                "guardrails": {"pre_tool": [{"type": "keyword", "action": "warn"}]}
            }

    resolved = await _resolve_effective_guardrails(object(), _Project())
    assert resolved is not None
    hooks = resolved["guardrails"]
    # Ambas capas presentes en el resultado fusionado.
    assert hooks["post_tool"][0]["type"] == "prompt_injection"
    assert hooks["post_tool"][0]["locked"] is True
    assert hooks["pre_tool"][0]["type"] == "keyword"


@pytest.mark.asyncio
async def test_resolver_locked_platform_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _platform(session: Any) -> dict[str, Any]:
        return {
            "guardrails": {
                "post_tool": [{"type": "prompt_injection", "action": "block", "locked": True}]
            }
        }

    monkeypatch.setattr("api_server.db.platform_settings.get_guardrails_config", _platform)

    class _Project:
        # El proyecto intenta RELAJAR el check locked de plataforma → ignorado.
        def __init__(self) -> None:
            self.guardrails_config = {
                "guardrails": {"post_tool": [{"type": "prompt_injection", "action": "warn"}]}
            }

    resolved = await _resolve_effective_guardrails(object(), _Project())
    assert resolved is not None
    assert resolved["guardrails"]["post_tool"][0]["action"] == "block"


@pytest.mark.asyncio
async def test_resolver_none_when_no_layers(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _platform(session: Any) -> dict[str, Any]:
        return {}

    monkeypatch.setattr("api_server.db.platform_settings.get_guardrails_config", _platform)
    assert await _resolve_effective_guardrails(object(), None) is None
