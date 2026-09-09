"""El `base_url` de un proveedor Ollama lleva SIEMPRE `/v1`, lo escriba quien lo escriba.

Plan `remediacion-instalador-runs-de-serie-2026-09-09`, `task_inst_01` (G3).

## El contrato que nadie había escrito

`shared_llm.providers.ollama.OllamaProvider` hace `POST {base_url}/chat/completions`
y la sonda de vida (`llm_providers/liveness.py`) asume que `base_url` «ya acaba en
`/v1`». El factory (`llm_providers/factory.py::_build_ollama`) pasa la columna tal
cual. O sea: la fila de `llm_providers` tiene que llevar el `/v1`, y hasta hoy eso
lo tenía que saber quien rellenaba el formulario o el `install.yaml`.

Encontrado el 2026-09-09 a las malas: proveedor creado con
`http://host.docker.internal:11434`, el run llegó a `plan` y murió con
`ollama: HTTP 404 — 404 page not found`, tres pasos después de arrancar y sin
ninguna pista de que fuera la URL. El instalador emitía `endpoint:
http://ollama:11434` — el mismo error, de serie.

## Dónde se normaliza

En el esquema (Create) y en el router (Update, donde el `kind` se conoce por la
fila), a través de UNA función pura, `normalize_ollama_base_url`. Los demás kinds
no se tocan: `/v1` es la convención OpenAI-compatible de Ollama, no de Azure APIM.
"""

from __future__ import annotations

import pytest
from api_server.db.llm_providers import LLMProviderKind
from api_server.schemas.llm_providers import (
    LLMProviderCreateRequest,
    normalize_ollama_base_url,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://ollama:11434", "http://ollama:11434/v1"),
        ("http://ollama:11434/", "http://ollama:11434/v1"),
        ("http://ollama:11434/v1", "http://ollama:11434/v1"),
        ("http://ollama:11434/v1/", "http://ollama:11434/v1"),
        ("https://ollama.com/v1", "https://ollama.com/v1"),
        ("  http://host.docker.internal:11434  ", "http://host.docker.internal:11434/v1"),
    ],
)
def test_normalize_ollama_base_url_appends_v1_exactly_once(raw: str, expected: str) -> None:
    assert normalize_ollama_base_url(raw) == expected


def test_create_request_normalizes_an_ollama_base_url_without_v1() -> None:
    """El caso del instalador y del formulario: el operador escribe el host."""
    req = LLMProviderCreateRequest(
        kind=LLMProviderKind.OLLAMA,
        slug="ollama",
        display_name="Ollama",
        base_url="http://ollama:11434",
    )
    assert req.base_url == "http://ollama:11434/v1"


def test_create_request_leaves_a_correct_ollama_base_url_alone() -> None:
    req = LLMProviderCreateRequest(
        kind=LLMProviderKind.OLLAMA,
        slug="ollama-cloud",
        display_name="Ollama cloud",
        base_url="https://ollama.com/v1",
        bearer_token="tok",
    )
    assert req.base_url == "https://ollama.com/v1"


def test_other_kinds_keep_their_base_url_untouched() -> None:
    """`/v1` es la convención de Ollama, no un sufijo universal: un gateway APIM
    tiene su propia forma y añadirle `/v1` lo rompería."""
    req = LLMProviderCreateRequest(
        kind=LLMProviderKind.AZURE_FOUNDRY,
        slug="apim",
        display_name="APIM",
        base_url="https://apim.example.test/openai",
        api_key="k",
    )
    assert req.base_url == "https://apim.example.test/openai"
