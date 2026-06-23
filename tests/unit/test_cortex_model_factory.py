"""Unit: el builder del modelo del córtex (F1, Tarea 5) debe cablear el
``reasoning_effort`` resuelto (ADR 0070) al ``extra_call_kwargs`` del
``LLMAssistantModel`` igual que el asistente personal, y degradar limpio
(sin 500) cuando un modelo claude_sdk corre sin el Claude Agent SDK.

Espejo de ``tests/unit/test_assistant_llm_reasoning.py`` (efecto observable)
pero a nivel del builder del córtex (``cortex/model_config.py``)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from api_server.assistant.llm import LLMAssistantModel
from api_server.assistant.model_config import ResolvedAssistantModel
from api_server.cortex.model_config import (
    CortexModelUnavailableError,
    build_cortex_model,
    cortex_call_kwargs,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# cortex_call_kwargs: traducción del reasoning_effort al kwarg nativo del kind
# (espejo de reasoning_call_kwargs, sin web — la web se prueba aparte).
# ---------------------------------------------------------------------------
def test_claude_sdk_high_effort_maps_to_effort() -> None:
    # claude_sdk consume ``effort`` (no ``reasoning_effort``).
    assert cortex_call_kwargs("claude_sdk", "high") == {"effort": "high"}


def test_non_claude_kind_maps_to_reasoning_effort() -> None:
    # Un kind no-claude (azure/copilot/ollama) usa ``reasoning_effort``.
    kwargs = cortex_call_kwargs("ollama", "high")
    assert kwargs == {"reasoning_effort": "high"}


def test_no_effort_sends_nothing_extra() -> None:
    assert cortex_call_kwargs("claude_sdk", None) == {}


def _resolved(kind: str, effort: str | None = "high") -> ResolvedAssistantModel:
    return ResolvedAssistantModel(
        provider_id=uuid4(),
        model_id="claude-sonnet-4-5" if kind == "claude_sdk" else "gpt-oss:20b",
        source="platform_default",
        provider_kind=kind,
        provider_display_name="x",
        reasoning_effort=effort,
    )


# ---------------------------------------------------------------------------
# build_cortex_model: el effort resuelto llega al extra_call_kwargs del modelo.
# El provider se inyecta (sin red) — sólo verificamos el cableado del builder.
# ---------------------------------------------------------------------------
class _StubProvider:
    name = "stub"

    async def complete(self, *_a: object, **_k: object) -> object:  # pragma: no cover - no se llama
        raise AssertionError("no se debe llamar en el unit test del builder")


def test_build_cortex_model_wires_effort_for_claude_sdk() -> None:
    resolved = _resolved("claude_sdk", "high")
    model = build_cortex_model(
        resolved,
        provider=_StubProvider(),
        claude_sdk_available=True,  # type: ignore[arg-type]
    )
    assert isinstance(model, LLMAssistantModel)
    assert model.extra_call_kwargs.get("effort") == "high"


def test_build_cortex_model_wires_reasoning_effort_for_non_claude() -> None:
    resolved = _resolved("ollama", "high")
    model = build_cortex_model(
        resolved,
        provider=_StubProvider(),
        claude_sdk_available=False,  # type: ignore[arg-type]
    )
    assert isinstance(model, LLMAssistantModel)
    assert model.extra_call_kwargs.get("reasoning_effort") == "high"
    # Un kind no-claude NUNCA debe recibir el kwarg ``effort`` (es de claude_sdk).
    assert "effort" not in model.extra_call_kwargs


# ---------------------------------------------------------------------------
# Degradación honesta: claude_sdk sin el SDK y sin provider construible → no 500.
# El builder señaliza la indisponibilidad con una excepción específica que el
# router (Bloque E) traduce a 503; jamás un 500 por ImportError aguas abajo.
# ---------------------------------------------------------------------------
def test_build_cortex_model_signals_503_when_claude_sdk_missing_and_no_provider() -> None:
    resolved = _resolved("claude_sdk", "high")
    with pytest.raises(CortexModelUnavailableError):
        build_cortex_model(resolved, provider=None, claude_sdk_available=False)


def test_build_cortex_model_signals_503_when_provider_is_none() -> None:
    # Cualquier kind: si el provider no se pudo construir (None) → indisponible.
    resolved = _resolved("ollama", "high")
    with pytest.raises(CortexModelUnavailableError):
        build_cortex_model(resolved, provider=None, claude_sdk_available=True)


# ---------------------------------------------------------------------------
# resolve_cortex_model → None cuando NADA está configurado (clave inexistente):
# el router lo expone como 503 (Bloque E). Unit con un fake session cuyo
# ``get(PlatformSetting, key)`` devuelve None (no hay fila).
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_resolve_cortex_model_none_when_unconfigured() -> None:
    from api_server.cortex.model_config import resolve_cortex_model

    class _FakeSession:
        async def get(self, _model: object, _key: object) -> None:
            return None  # no hay fila cortex.default_model

    resolved = await resolve_cortex_model(_FakeSession())  # type: ignore[arg-type]
    assert resolved is None
