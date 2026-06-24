"""Unit: WebSearch/WebFetch nativas del Claude Agent SDK (F1, Tarea 6 — ADR 0076).

La web del córtex se delega EXCLUSIVAMENTE al Claude Agent SDK (Anthropic gestiona
el fetch → anti-SSRF gratis, sin egress propio). Por tanto:

  * El builder del modelo del córtex añade ``allowed_tools=["WebSearch","WebFetch"]``
    SOLO cuando ``provider_kind == "claude_sdk"`` y el SDK está disponible; con
    cualquier otro kind NO se pasa ninguna web tool.
  * Esos ``allowed_tools`` se propagan por la vía agéntica del provider
    (``ClaudeAgentProvider`` → ``_build_options`` / ``_build_tool_options``).
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from api_server.assistant.model_config import ResolvedAssistantModel
from api_server.cortex.model_config import (
    CORTEX_WEB_TOOLS,
    build_cortex_model,
    cortex_call_kwargs,
)

pytestmark = pytest.mark.unit


def _resolved(kind: str) -> ResolvedAssistantModel:
    return ResolvedAssistantModel(
        provider_id=uuid4(),
        model_id="claude-sonnet-4-5" if kind == "claude_sdk" else "gpt-oss:20b",
        source="platform_default",
        provider_kind=kind,
        provider_display_name="x",
        reasoning_effort="high",
    )


class _StubProvider:
    name = "stub"

    async def complete(self, *_a: object, **_k: object) -> object:  # pragma: no cover
        raise AssertionError("no se debe llamar en el unit test")


# ---------------------------------------------------------------------------
# cortex_call_kwargs(web_enabled=...) — la web SOLO en claude_sdk.
# ---------------------------------------------------------------------------
def test_web_tools_only_for_claude_sdk() -> None:
    kwargs = cortex_call_kwargs("claude_sdk", "high", web_enabled=True)
    assert kwargs.get("allowed_tools") == list(CORTEX_WEB_TOOLS)
    assert "WebSearch" in kwargs["allowed_tools"]
    assert "WebFetch" in kwargs["allowed_tools"]


def test_no_web_tools_for_non_claude_kind() -> None:
    # Pedir web en un kind no-claude NO añade ninguna tool web (no hay web propia).
    kwargs = cortex_call_kwargs("ollama", "high", web_enabled=True)
    assert "allowed_tools" not in kwargs


def test_web_disabled_adds_no_tools_even_for_claude() -> None:
    kwargs = cortex_call_kwargs("claude_sdk", "high", web_enabled=False)
    assert "allowed_tools" not in kwargs


# ---------------------------------------------------------------------------
# build_cortex_model: las web tools llegan al extra_call_kwargs SOLO en claude_sdk.
# ---------------------------------------------------------------------------
def test_build_cortex_model_claude_sdk_has_web_tools() -> None:
    model = build_cortex_model(
        _resolved("claude_sdk"),
        provider=_StubProvider(),  # type: ignore[arg-type]
        claude_sdk_available=True,
        web_enabled=True,
    )
    assert model.extra_call_kwargs.get("allowed_tools") == list(CORTEX_WEB_TOOLS)


def test_build_cortex_model_non_claude_has_no_web_tools() -> None:
    model = build_cortex_model(
        _resolved("ollama"),
        provider=_StubProvider(),  # type: ignore[arg-type]
        claude_sdk_available=False,
        web_enabled=True,
    )
    assert "allowed_tools" not in model.extra_call_kwargs


# ---------------------------------------------------------------------------
# Propagación por la vía agéntica del provider: ``run_agent`` pasa los
# ``allowed_tools`` recibidos a ``_build_options`` (de donde el SDK los consume).
# Sin depender de tener el SDK instalado: capturamos el argumento con un
# ``_build_options`` monkeypatcheado y un ``query_fn`` inyectado.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_run_agent_propagates_allowed_tools_to_options() -> None:
    from shared_llm.providers.claude_agent import ClaudeAgentProvider

    captured: dict[str, Any] = {}

    async def _fake_query(*, prompt: str, options: Any) -> Any:
        if False:  # pragma: no cover - generador vacío con el tipo correcto
            yield None

    provider = ClaudeAgentProvider(query_fn=_fake_query)

    def _spy_build_options(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return None

    provider._build_options = _spy_build_options  # type: ignore[method-assign]

    async for _ in provider.run_agent(
        "hola",
        model="claude-sonnet-4-5",
        allowed_tools=list(CORTEX_WEB_TOOLS),
        effort="high",
    ):
        pass

    assert captured.get("allowed_tools") == list(CORTEX_WEB_TOOLS)
    assert captured.get("effort") == "high"
