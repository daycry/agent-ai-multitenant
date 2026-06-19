"""Unit tests para la validación de `reasoning_effort` por proveedor (ADR 0070).

Cada proveedor tiene su set de opciones (no hay uno común): claude_sdk admite
hasta `xhigh`/`max`, azure/copilot `low`/`medium`/`high`, ollama solo `think`.
`off` y la ausencia son siempre válidos (sin razonamiento)."""

from __future__ import annotations

import pytest
from api_server.db.platform_settings import InvalidModelConfigError, validate_model_config

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
        {"provider": "ollama", "model": "qwen3", "reasoning_effort": "think"},
        {"provider": "ollama", "model": "llama3.2", "reasoning_effort": "off"},
    ],
)
def test_valid_reasoning_passes(cfg: dict[str, object]) -> None:
    assert validate_model_config(cfg) is cfg


@pytest.mark.parametrize(
    ("cfg"),
    [
        # xhigh/max NO son válidos para azure (solo low/medium/high)
        {"provider": "azure_foundry", "model": "o3", "reasoning_effort": "xhigh"},
        # think es de ollama, no de claude
        _claude(reasoning_effort="think"),
        # niveles de OpenAI no aplican a ollama (solo off/think)
        {"provider": "ollama", "model": "qwen3", "reasoning_effort": "high"},
        # valor desconocido
        _claude(reasoning_effort="turbo"),
    ],
)
def test_invalid_reasoning_raises(cfg: dict[str, object]) -> None:
    with pytest.raises(InvalidModelConfigError):
        validate_model_config(cfg)
