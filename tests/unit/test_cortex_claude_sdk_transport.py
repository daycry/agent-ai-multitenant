"""I-6 parcial (auditoría 2026-07-10): el transporte del córtex sobre claude_sdk.

Con el fix del schema-gap (#10e) el córtex pasó del ``complete()`` PLANO a la vía
de tools del ``ClaudeAgentProvider`` (``_complete_with_tools``: las host tools se
advierten como MCP in-process y la tool-call se captura vía ``can_use_tool``
deny+interrupt) — un cambio real de transporte en el «camino primario» de ADR
0074 que quedó sin test. Este pin lo fija de punta a punta SIN el SDK real
(``query_fn`` inyectado): ``decide()`` lleva el catálogo COMPLETO del córtex al
provider por la vía de tools, y un ``tool_use`` del modelo vuelve como
``ToolInvocation`` con el prefijo MCP limpio y sin preámbulo como respuesta.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from api_server.assistant.graph import AssistantState
from api_server.assistant.model_config import ResolvedAssistantModel
from api_server.cortex.model_config import build_cortex_model
from api_server.cortex.tools import cortex_enabled_tool_names, cortex_tool_schemas
from shared_llm.providers.claude_agent import ClaudeAgentProvider

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_cortex_decide_on_claude_sdk_takes_the_tools_transport() -> None:
    # Un turno del "modelo": pide la host tool web_search (bloque tool_use con el
    # namespacing MCP del SDK) — la forma que _harvest_tool_calls duck-typea.
    tool_use = SimpleNamespace(
        id="tu_1", name="mcp__host__web_search", input={"query": "agentic platform", "limit": 3}
    )
    assistant_msg = SimpleNamespace(content=[tool_use], stop_reason="tool_use")

    async def _fake_query(*, prompt: Any, options: Any) -> AsyncIterator[Any]:
        yield assistant_msg

    provider = ClaudeAgentProvider(query_fn=_fake_query)

    captured: dict[str, Any] = {}
    original = provider._complete_with_tools

    async def _spy(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return await original(**kwargs)

    provider._complete_with_tools = _spy  # type: ignore[method-assign]

    model = build_cortex_model(
        ResolvedAssistantModel(
            provider_id=uuid4(),
            model_id="claude-sonnet-4-5",
            source="platform_default",
            provider_kind="claude_sdk",
            provider_display_name="Claude",
            reasoning_effort=None,
        ),
        provider=provider,
        claude_sdk_available=True,
    )
    enabled = cortex_enabled_tool_names(web_enabled=True)
    turn = await model.decide(
        AssistantState(system_prompt="Eres el córtex.", enabled_tools=enabled)
    )

    # 1) El transporte fue la vía de tools (no el complete() plano) y llevó el
    #    catálogo COMPLETO del córtex — el schema-gap #10e no puede reabrirse en
    #    silencio por este camino.
    sent = [t["function"]["name"] for t in captured["tools"]]
    assert sent == [s["name"] for s in cortex_tool_schemas(enabled)]
    assert "web_search" in sent and "cortex_remember" in sent

    # 2) El tool_use vuelve como ToolInvocation con el prefijo MCP limpio y los
    #    args del modelo intactos (idéntico contrato al de los providers HTTP).
    assert turn.tool_calls
    assert turn.tool_calls[0].name == "web_search"
    assert turn.tool_calls[0].arguments == {"query": "agentic platform", "limit": 3}

    # 3) En un turno de tool-call el preámbulo NO es la respuesta.
    assert turn.content is None
