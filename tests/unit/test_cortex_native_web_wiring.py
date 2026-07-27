"""El camino web NATIVO del ADR 0076 no puede ser código muerto.

Auditoría del córtex 2026-07-27, F1.6. El ADR 0076 está `accepted` y su decisión
**3** dice que el egress recomendado del córtex son las **WebSearch/WebFetch
nativas del Claude Agent SDK** (`ClaudeAgentOptions.allowed_tools`): la salida es
la del api-server y Anthropic gestiona el fetch, así que sale **anti-SSRF gratis**
sin abrir egress en los runtimes. Su decisión **4** —una tool web propia desde el
api-server— es el camino *degradado*, el que exige anti-SSRF obligatorio porque un
fetch desde el proceso confiable alcanza Vault y la red interna.

`build_cortex_model(web_enabled=...)` implementa la decisión 3 entera, incluida la
exclusión mutua nativa/host de I-6 (con las nativas activas el catálogo NO ofrece
además `web_search`/`web_fetch` host, para que el modelo no tenga dos herramientas
para el mismo trabajo). Pero **nadie le pasaba el flag**: el único call site de
producción construía el modelo sin él, así que `native_web` era siempre `False`.
Efecto: con el owner en `claude_sdk` y la web encendida, el córtex usaba SIEMPRE el
camino degradado y el recomendado no se ejercía nunca.

Estos tests fijan las dos direcciones — que el nativo se active y que NO se active
donde no debe — sobre el builder puro, sin DB ni red.
"""

from __future__ import annotations

from typing import Any

import pytest
from api_server.cortex.model_config import (
    CORTEX_WEB_TOOLS,
    build_cortex_model,
    cortex_call_kwargs,
)
from api_server.cortex.tools import (
    cortex_tool_schemas,
    cortex_tool_schemas_without_host_web,
)

pytestmark = pytest.mark.unit


class _FakeProvider:
    """Marcador: `build_cortex_model` sólo comprueba que no sea None."""


def _resolved(kind: str = "claude_sdk") -> Any:
    from uuid import uuid4

    from api_server.assistant.model_config import ResolvedAssistantModel

    return ResolvedAssistantModel(
        provider_id=uuid4(),
        model_id="claude-opus-5",
        source="platform_default",
        provider_kind=kind,
        provider_display_name="Claude",
        reasoning_effort="high",
    )


def _build(*, kind: str = "claude_sdk", web_enabled: bool) -> Any:
    return build_cortex_model(
        _resolved(kind),
        provider=_FakeProvider(),  # type: ignore[arg-type]
        claude_sdk_available=True,
        web_enabled=web_enabled,
    )


# ---------------------------------------------------------------------------
# El builder: nativo ON ⇒ allowed_tools + catálogo sin las host web.
# ---------------------------------------------------------------------------
def test_native_web_tools_reach_the_sdk_options() -> None:
    kwargs = cortex_call_kwargs("claude_sdk", "high", web_enabled=True)
    assert kwargs["allowed_tools"] == list(CORTEX_WEB_TOOLS)


def test_native_web_excludes_the_host_web_tools_from_the_catalog() -> None:
    """I-6: dos herramientas para el mismo trabajo confunden al modelo y duplican
    el gasto. Con las nativas activas, el catálogo no ofrece las host."""
    assert _build(web_enabled=True).schema_fn is cortex_tool_schemas_without_host_web


def test_host_web_stays_when_the_native_path_is_off() -> None:
    assert _build(web_enabled=False).schema_fn is cortex_tool_schemas


def test_a_non_claude_provider_never_gets_native_web() -> None:
    """Sólo el Claude Agent SDK tiene WebSearch/WebFetch nativas. Con otro kind el
    córtex debe conservar las host o se quedaría sin web ninguna."""
    kwargs = cortex_call_kwargs("ollama", "high", web_enabled=True)
    assert "allowed_tools" not in kwargs
    assert _build(kind="ollama", web_enabled=True).schema_fn is cortex_tool_schemas


# ---------------------------------------------------------------------------
# El cableado: el call site de producción PASA el flag.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize("web_enabled", [True, False])
async def test_build_cortex_default_model_forwards_the_web_setting(
    monkeypatch: pytest.MonkeyPatch, web_enabled: bool
) -> None:
    """Sin esto, todo lo de arriba sigue siendo un motor sin conductor.

    Es el mismo modo de fallo que g1 (un `GuardrailPipeline` correcto que nadie
    instanciaba) y por el que la auditoría clasifica «existe pero nadie lo llama»
    como parcial, no como hecho.
    """
    from api_server.routers import cortex as cortex_router

    class _Session:
        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr(cortex_router, "get_admin_sessionmaker", lambda: _Session)
    monkeypatch.setattr(cortex_router, "resolve_cortex_model", _async(_resolved()))
    monkeypatch.setattr(cortex_router, "_claude_sdk_available", lambda: True)
    monkeypatch.setattr(cortex_router, "build_llm_provider", _async(_FakeProvider()))
    monkeypatch.setattr(cortex_router, "get_cortex_web_enabled", _async(web_enabled))

    seen: dict[str, object] = {}

    def _spy(resolved: object, **kwargs: object) -> object:
        seen.update(kwargs)
        return object()

    monkeypatch.setattr(cortex_router, "build_cortex_model", _spy)
    await cortex_router.build_cortex_default_model(None)

    assert seen["web_enabled"] is web_enabled


def _async(value: object):  # - helper local
    async def _call(*_a: object, **_k: object) -> object:
        return value

    return _call
