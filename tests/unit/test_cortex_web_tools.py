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
# I-6 (auditoría 2026-07-10): exclusión mutua nativa/host. Con las web tools
# NATIVAS del SDK activas (allowed_tools WebSearch/WebFetch, ADR 0076), el modelo
# no debe ver ADEMÁS los schemas de las host tools web_search/web_fetch (ADR 0067)
# — dos herramientas para lo mismo confunden al modelo y duplican el gasto. El
# resto del catálogo (cortex_remember, cortex_recall_more) sigue disponible.
# ---------------------------------------------------------------------------
def test_native_web_excludes_host_web_schemas() -> None:
    from api_server.cortex.tools import cortex_enabled_tool_names

    model = build_cortex_model(
        _resolved("claude_sdk"),
        provider=_StubProvider(),  # type: ignore[arg-type]
        claude_sdk_available=True,
        web_enabled=True,
    )
    enabled = cortex_enabled_tool_names(web_enabled=True)
    names = [s["name"] for s in model.schema_fn(enabled)]
    assert "web_search" not in names and "web_fetch" not in names
    assert "cortex_remember" in names and "cortex_recall_more" in names


def test_without_native_web_host_schemas_are_intact() -> None:
    from api_server.cortex.tools import cortex_enabled_tool_names, cortex_tool_schemas

    # Sin web nativa (kind no-claude, aunque web_enabled=True) el catálogo host
    # completo sigue siendo la fuente de schemas — incluidas las web tools, cuyo
    # gate real es cortex_enabled_tool_names/run_cortex_tool (ADR 0067).
    model = build_cortex_model(
        _resolved("ollama"),
        provider=_StubProvider(),  # type: ignore[arg-type]
        claude_sdk_available=False,
        web_enabled=True,
    )
    assert model.schema_fn is cortex_tool_schemas
    names = [s["name"] for s in model.schema_fn(cortex_enabled_tool_names(web_enabled=True))]
    assert "web_search" in names and "web_fetch" in names


# ---------------------------------------------------------------------------
# Caracterización del modo de fallo PREVIO al fix del schema-gap (#10e): sin los
# schemas, gpt-oss infería los args de su web_search NATIVA ({topn, source}) y
# el dispatch host los rechazaba. El fallo era (y debe seguir siendo) CONTROLADO:
# TypeError en el despacho — antes de tocar la red — que _node_run_tools captura
# y devuelve al modelo como resultado de error, sin tumbar el turno.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_native_style_args_fail_at_dispatch_before_any_network() -> None:
    from types import SimpleNamespace

    from api_server.cortex.tools import run_cortex_tool

    ctx = SimpleNamespace(web_enabled=True)  # sin proveedor de búsqueda: si se
    # llegara a la red, el impl reventaría por otro camino — el TypeError del
    # despacho debe saltar ANTES.
    with pytest.raises(TypeError):
        await run_cortex_tool("web_search", ctx, {"topn": 3, "source": "news"})  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_tool_dispatch_error_returns_to_the_model_not_the_turn() -> None:
    from types import SimpleNamespace

    from api_server.assistant.graph import (
        AssistantState,
        ModelTurn,
        ToolInvocation,
        _node_run_tools,
    )
    from api_server.cortex.tools import run_cortex_tool

    state = AssistantState(system_prompt="x", enabled_tools=("web_search",))
    state.pending = ModelTurn(
        content=None,
        tool_calls=(ToolInvocation(name="web_search", arguments={"topn": 3, "source": "news"}),),
    )
    state.tool_ctx = SimpleNamespace(web_enabled=True)  # type: ignore[assignment]

    out = await _node_run_tools(run_cortex_tool)(state)

    # El turno sobrevive: la excepción se convierte en un resultado de error que
    # el modelo ve en la ronda siguiente (y corrige los args — hoy, con los
    # schemas enviados, ya no necesita adivinarlos).
    assert out.tool_results and out.tool_results[0]["tool"] == "web_search"
    assert "falló" in str(out.tool_results[0]["result"].get("error", ""))


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
