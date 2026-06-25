"""Unit tests para la validación de `reasoning_effort` por proveedor (ADR 0070).

Cada proveedor tiene su set de opciones (no hay uno común): claude_sdk admite
hasta `xhigh`/`max`, azure/copilot `low`/`medium`/`high`, ollama solo `think`.
`off` y la ausencia son siempre válidos (sin razonamiento)."""

from __future__ import annotations

import pytest
from api_server.db.platform_settings import (
    InvalidModelConfigError,
    resolve_model_config_chain,
    validate_model_config,
)

pytestmark = pytest.mark.unit


def _claude(**extra: object) -> dict[str, object]:
    return {"provider": "claude_sdk", "model": "claude-opus-4-8", **extra}


@pytest.mark.parametrize(
    ("cfg"),
    [
        _claude(),  # ausente
        _claude(reasoning_effort="off"),
        _claude(reasoning_effort="xhigh"),
        _claude(reasoning_effort="max"),
        {"provider": "azure_foundry", "model": "o3", "reasoning_effort": "high"},
        {"provider": "copilot", "model": "o4-mini", "reasoning_effort": "low"},
        {"provider": "ollama", "model": "qwen3", "reasoning_effort": "high"},
        {"provider": "ollama", "model": "llama3.2", "reasoning_effort": "off"},
    ],
)
def test_valid_reasoning_passes(cfg: dict[str, object]) -> None:
    assert validate_model_config(cfg) is cfg


# ---------------------------------------------------------------------------
# ADR 0082 — model_config pinned by concrete provider_id (unificación)
# ---------------------------------------------------------------------------
_PID = "019e83cd-bb5c-7f43-9031-b3a75f3bdd29"


def test_provider_id_pinned_with_kind_alongside_is_valid() -> None:
    cfg = {"provider_id": _PID, "provider": "ollama", "model": "gpt-oss:120b", "temperature": 0.1}
    assert validate_model_config(cfg) is cfg


def test_provider_id_pinned_without_kind_is_valid() -> None:
    # provider(kind) is optional alongside provider_id; the row governs the kind.
    cfg = {"provider_id": _PID, "model": "gpt-oss:120b"}
    assert validate_model_config(cfg) is cfg


def test_provider_id_malformed_uuid_is_rejected_even_with_valid_kind() -> None:
    with pytest.raises(InvalidModelConfigError):
        validate_model_config({"provider_id": "not-a-uuid", "provider": "ollama", "model": "x"})


def test_provider_id_pinned_still_requires_model() -> None:
    with pytest.raises(InvalidModelConfigError):
        validate_model_config({"provider_id": _PID, "model": ""})


def test_provider_id_with_invalid_kind_alongside_is_rejected() -> None:
    with pytest.raises(InvalidModelConfigError):
        validate_model_config({"provider_id": _PID, "provider": "openai", "model": "x"})


def test_legacy_kind_only_config_still_validates() -> None:
    assert validate_model_config({"provider": "ollama", "model": "llama3.2"})["model"] == "llama3.2"


def test_kind_not_in_catalog_still_rejected_without_provider_id() -> None:
    with pytest.raises(InvalidModelConfigError):
        validate_model_config({"provider": "openai", "model": "gpt-4o"})


def test_provider_id_reasoning_validated_against_kind_alongside() -> None:
    # xhigh is invalid for ollama → rejected even when pinned by provider_id.
    with pytest.raises(InvalidModelConfigError):
        validate_model_config(
            {"provider_id": _PID, "provider": "ollama", "model": "x", "reasoning_effort": "xhigh"}
        )


@pytest.mark.parametrize(
    ("cfg"),
    [
        # xhigh/max NO son válidos para azure (solo low/medium/high)
        {"provider": "azure_foundry", "model": "o3", "reasoning_effort": "xhigh"},
        # "think" no es una opción de ningún proveedor (Ollama /v1 usa niveles)
        _claude(reasoning_effort="think"),
        {"provider": "ollama", "model": "qwen3", "reasoning_effort": "think"},
        # xhigh/max no aplican a ollama (solo low/medium/high)
        {"provider": "ollama", "model": "qwen3", "reasoning_effort": "max"},
        # valor desconocido
        _claude(reasoning_effort="turbo"),
    ],
)
def test_invalid_reasoning_raises(cfg: dict[str, object]) -> None:
    with pytest.raises(InvalidModelConfigError):
        validate_model_config(cfg)


# ---------------------------------------------------------------------------
# Herencia (ADR 0070): reasoning_effort viaja con el provider, sin cruzarse.
# ---------------------------------------------------------------------------
def test_inherited_reasoning_does_not_leak_across_providers() -> None:
    """Un agente sin pinear provider+model que trae reasoning_effort NO debe
    arrastrarlo al provider que aporta un nivel superior (sería de otro provider
    y podría ser inválido). Se descarta si el nivel que pinea no fija ninguno."""
    out = resolve_model_config_chain(
        {"reasoning_effort": "max"},  # 'max' no es válido para azure
        {"provider": "azure_foundry", "model": "o3"},
        {},
        {"provider": "claude_sdk", "model": "claude-sonnet-4", "temperature": 0.1},
    )
    assert out["provider"] == "azure_foundry"
    assert "reasoning_effort" not in out
    validate_model_config(out)  # el spec resuelto es válido


def test_inherited_reasoning_from_pinning_level_wins() -> None:
    """Si el nivel que pinea provider+model trae su propio reasoning_effort, ese
    manda (es consistente con su provider), no el del agente unpinned."""
    out = resolve_model_config_chain(
        {"reasoning_effort": "xhigh"},
        {"provider": "azure_foundry", "model": "o3", "reasoning_effort": "high"},
        {},
        {"provider": "claude_sdk", "model": "m", "temperature": 0.1},
    )
    assert out["reasoning_effort"] == "high"


def test_agent_pinned_reasoning_is_kept_verbatim() -> None:
    """Si el agente pinea provider+model, su config (con reasoning) se devuelve tal cual."""
    cfg = {"provider": "claude_sdk", "model": "claude-opus-4-8", "reasoning_effort": "xhigh"}
    assert resolve_model_config_chain(cfg, {}, {}, {"provider": "ollama", "model": "x"}) == cfg
