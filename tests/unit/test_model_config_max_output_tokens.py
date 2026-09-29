"""`model_config.max_output_tokens` se valida como las demás palancas (ADR 0169 D4).

`task_cm_01` del plan `compat-modelos-2026-09-29`. El spec del runtime copia el
``model_config`` entero, así que la clave llega al cliente del proveedor. Sin esta
validación, un ``"max_output_tokens": "mucho"`` o un ``0`` pasarían la API y el run
moriría dentro del contenedor, lejos de quien escribió el valor.
"""

from __future__ import annotations

import pytest
from api_server.db.platform_settings import InvalidModelConfigError, validate_model_config

_BASE = {"provider": "ollama", "model": "qwen3.5:9b"}


@pytest.mark.parametrize("value", [1024, 8192, 32000])
def test_a_sane_cap_is_accepted(value: int) -> None:
    assert (
        validate_model_config({**_BASE, "max_output_tokens": value})["max_output_tokens"] == value
    )


def test_absence_is_fine_and_means_the_runtime_default() -> None:
    assert "max_output_tokens" not in validate_model_config(dict(_BASE))


@pytest.mark.parametrize("value", [0, -5, 100, 10_000_000, "mucho", 12.5, True])
def test_a_nonsense_cap_is_rejected_at_the_api(value: object) -> None:
    with pytest.raises(InvalidModelConfigError, match="max_output_tokens"):
        validate_model_config({**_BASE, "max_output_tokens": value})
