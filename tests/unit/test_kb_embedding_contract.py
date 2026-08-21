"""El contrato de modelo de embeddings de una KB (ADR 0155, `task_audit14_05`).

Lógica pura: sin BD, sin Ollama, sin red. Fija las piezas de las que dependen
las cinco reglas del ADR, y que antes NO existían — cada sitio comparaba (o no
comparaba) los nombres de modelo por su cuenta:

  * canonizar una referencia de modelo (`:latest` fuera, alias heredado dentro),
  * decidir si dos referencias son EL MISMO modelo,
  * enumerar las grafías equivalentes (lo que el SQL del retrieval necesita para
    filtrar sin traducir alias en la base de datos),
  * resolver el modelo activo de la plataforma,
  * negarse —con los dos nombres en el mensaje— cuando el sello de la KB y el
    modelo activo no son el mismo.

El caso que motiva el alias: la instalación medida el 2026-08-19 tenía 14 KBs
selladas `nomic-embed-text-v1.5` mientras los dos procesos mandaban
`nomic-embed-text` a `/api/embed`. Son el mismo modelo; tratarlos como distintos
rompería la ingesta de todas las KBs existentes el día que se active la guarda.
"""

from __future__ import annotations

import pytest
from api_server.db.knowledge import CHUNK_EMBEDDING_DIM
from api_server.ingestion.embedding_contract import (
    LEGACY_MODEL_ALIASES,
    EmbeddingModelMismatchError,
    accepted_model_refs,
    active_embedding_model,
    canonical_model_ref,
    models_match,
    require_matching_model,
)
from api_server.ingestion.embedding_models import known_dim

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Canonización
# ---------------------------------------------------------------------------
def test_legacy_alias_canonises_to_the_real_registry_name() -> None:
    # `nomic-embed-text-v1.5` NO es un tag del registro de Ollama: pedirlo da
    # `model not found`. Es la etiqueta que quedó sellada en la columna.
    assert canonical_model_ref("nomic-embed-text-v1.5") == "nomic-embed-text"
    assert "nomic-embed-text-v1.5" in LEGACY_MODEL_ALIASES


def test_latest_tag_and_whitespace_are_stripped() -> None:
    assert canonical_model_ref("nomic-embed-text:latest") == "nomic-embed-text"
    assert canonical_model_ref("  nomic-embed-text  ") == "nomic-embed-text"
    # Un tag de tamaño SÍ distingue modelo (dims distintas) y se conserva.
    assert canonical_model_ref("snowflake-arctic-embed:110m") == "snowflake-arctic-embed:110m"


def test_empty_ref_canonises_to_empty() -> None:
    # Una fila sin sello no se convierte en "el modelo activo" por arte de magia:
    # queda vacía y el llamante decide (la regla 4 la trata como mismatch).
    assert canonical_model_ref("") == ""
    assert canonical_model_ref("   ") == ""


# ---------------------------------------------------------------------------
# Igualdad de modelos
# ---------------------------------------------------------------------------
def test_alias_and_canonical_are_the_same_model() -> None:
    assert models_match("nomic-embed-text-v1.5", "nomic-embed-text") is True
    assert models_match("nomic-embed-text:latest", "nomic-embed-text-v1.5") is True


def test_two_768_dim_models_are_NOT_interchangeable() -> None:
    # El fallo peligroso del ADR 0155: misma dimensión, espacios semánticos
    # distintos. Si esto devolviera True, la guarda no guardaría nada.
    assert known_dim("granite-embedding:278m") == CHUNK_EMBEDDING_DIM
    assert known_dim("nomic-embed-text") == CHUNK_EMBEDDING_DIM
    assert models_match("granite-embedding:278m", "nomic-embed-text") is False


def test_catalog_understands_the_legacy_alias() -> None:
    # Antes `known_dim("nomic-embed-text-v1.5")` era None → "no es un embedder",
    # que es falso: es el mismo nomic de 768 dims.
    assert known_dim("nomic-embed-text-v1.5") == CHUNK_EMBEDDING_DIM


# ---------------------------------------------------------------------------
# Grafías aceptadas (lo que consume el filtro SQL del retrieval)
# ---------------------------------------------------------------------------
def test_accepted_refs_cover_every_spelling_stored_in_the_column() -> None:
    accepted = accepted_model_refs("nomic-embed-text")
    assert "nomic-embed-text" in accepted
    assert "nomic-embed-text:latest" in accepted
    assert "nomic-embed-text-v1.5" in accepted  # el sello heredado
    # Y NO arrastra otro modelo, que sería mezclar espacios semánticos.
    assert "granite-embedding:278m" not in accepted


def test_accepted_refs_from_the_alias_are_the_same_set() -> None:
    assert accepted_model_refs("nomic-embed-text-v1.5") == accepted_model_refs("nomic-embed-text")


def test_accepted_refs_of_a_model_without_alias() -> None:
    accepted = accepted_model_refs("granite-embedding:278m")
    assert accepted == frozenset({"granite-embedding:278m", "granite-embedding:278m:latest"})


# ---------------------------------------------------------------------------
# Modelo activo de la plataforma
# ---------------------------------------------------------------------------
class _Cfg:
    """Settings de mentira — el contrato solo lee `embedding_model`."""

    def __init__(self, model: str) -> None:
        self.embedding_model = model


def test_active_model_comes_from_settings_and_is_canonical() -> None:
    assert active_embedding_model(_Cfg("granite-embedding:278m")) == "granite-embedding:278m"
    # Aunque el operador escriba la etiqueta heredada en el env var.
    assert active_embedding_model(_Cfg("nomic-embed-text-v1.5")) == "nomic-embed-text"


# ---------------------------------------------------------------------------
# La guarda
# ---------------------------------------------------------------------------
def test_require_matching_model_returns_the_canonical_model_when_they_agree() -> None:
    assert (
        require_matching_model(kb_model="nomic-embed-text-v1.5", active_model="nomic-embed-text")
        == "nomic-embed-text"
    )


def test_require_matching_model_names_both_models_and_the_fix() -> None:
    with pytest.raises(EmbeddingModelMismatchError) as exc:
        require_matching_model(kb_model="granite-embedding:278m", active_model="nomic-embed-text")
    message = str(exc.value)
    # Un mensaje que no nombra los dos modelos deja al operador sin saber qué
    # reindexar: es la mitad de la utilidad de fallar.
    assert "granite-embedding:278m" in message
    assert "nomic-embed-text" in message
    assert "reindex" in message.lower()
    assert exc.value.kb_model == "granite-embedding:278m"
    assert exc.value.active_model == "nomic-embed-text"


def test_require_matching_model_rejects_an_empty_stamp() -> None:
    with pytest.raises(EmbeddingModelMismatchError):
        require_matching_model(kb_model="", active_model="nomic-embed-text")
