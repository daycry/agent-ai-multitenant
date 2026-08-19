"""El contrato de modelo de embeddings de la plataforma (ADR 0155).

La plataforma indexa con **un solo modelo** de 768 dimensiones —el activo,
`settings.embedding_model` / `API_SERVER_EMBEDDING_MODEL`— y
`knowledge_bases.embedding_model_id` es el **sello** de con cuál se produjeron
los vectores de esa KB, no una elección del usuario.

Este módulo es el ÚNICO sitio donde se decide si dos referencias de modelo son
el mismo modelo. Antes no había ninguno, y ahí estaba el hallazgo AUD14-03: la
API guardaba una etiqueta, el worker mandaba otra a `/api/embed` y nadie las
comparaba nunca.

Tres cosas que conviene tener claras al leer esto:

* **`nomic-embed-text-v1.5` y `nomic-embed-text` son el MISMO modelo.** El
  primero es la etiqueta que quedó sellada en la columna (y en cuatro literales
  del código); el segundo es el nombre real del registro de Ollama, el único que
  `/api/embed` reconoce. Tratarlos como distintos dejaría sin ingesta a todas las
  KBs existentes en cuanto se active la guarda, así que la equivalencia vive
  aquí, escrita y con test, en vez de repartida en comparaciones ad-hoc.
* **Un tag de tamaño SÍ distingue.** `snowflake-arctic-embed` (1024 dims) y
  `snowflake-arctic-embed:110m` (768) no son intercambiables; sólo `:latest`,
  que es el tag implícito, se descarta.
* **Misma dimensión no es el mismo espacio.** `nomic-embed-text` y
  `granite-embedding:278m` emiten los dos 768 floats y un `<=>` entre ellos
  devuelve un número válido y sin sentido. Por eso la comparación es por
  NOMBRE y no por dimensión: la dimensión no detecta el fallo peligroso.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - sólo para el type checker
    from api_server.config import Settings

#: Sufijo de tag implícito que Ollama añade al listar modelos instalados.
_IMPLICIT_TAG = ":latest"

#: Etiquetas heredadas → nombre real del registro. Una entrada aquí afirma «son
#: el mismo modelo, distinta grafía»; NO es un mapa de sustitución de modelos.
LEGACY_MODEL_ALIASES: dict[str, str] = {
    # Escrita en la migración 0022 (server_default), en los dos routers de
    # creación de KB, en el seed de built-ins y en la constante del panel.
    # El modelo ES la v1.5; el tag `-v1.5` nunca existió en el registro.
    "nomic-embed-text-v1.5": "nomic-embed-text",
}


class EmbeddingModelMismatchError(RuntimeError):
    """El sello de la KB y el modelo activo de la plataforma no coinciden.

    Se lanza ANTES de embeber: seguir adelante metería en la KB vectores de otro
    espacio semántico, que no fallan —tienen la dimensión correcta— y degradan el
    recall sin dejar rastro. Lleva los dos nombres para que el mensaje diga qué
    reindexar."""

    def __init__(self, *, kb_model: str, active_model: str) -> None:
        self.kb_model = kb_model
        self.active_model = active_model
        super().__init__(
            f"modelo de embeddings incoherente: la KB se indexó con "
            f"'{kb_model or '(sin sello)'}' y la plataforma usa '{active_model}'. "
            "Los vectores de los dos modelos no son comparables aunque tengan la "
            "misma dimensión. Reindexa los documentos de la KB con el modelo "
            "activo (ADR 0155) o repón API_SERVER_EMBEDDING_MODEL."
        )


def canonical_model_ref(name: str) -> str:
    """La forma canónica de una referencia de modelo.

    Quita espacios, descarta el tag implícito ``:latest`` y traduce las
    etiquetas heredadas. Una cadena vacía se queda vacía: una fila sin sello NO
    se convierte en «el modelo activo» por defecto — eso volvería a tapar el
    fallo que este contrato destapa."""
    ref = name.strip()
    if not ref:
        return ""
    if ref.endswith(_IMPLICIT_TAG):
        ref = ref[: -len(_IMPLICIT_TAG)]
    return LEGACY_MODEL_ALIASES.get(ref, ref)


def models_match(left: str, right: str) -> bool:
    """Si las dos referencias nombran el mismo modelo. Dos vacías NO casan:
    «no sé» no es «sí»."""
    canonical = canonical_model_ref(left)
    return bool(canonical) and canonical == canonical_model_ref(right)


def accepted_model_refs(name: str) -> frozenset[str]:
    """Todas las grafías con las que ese modelo puede estar escrito en la
    columna `knowledge_bases.embedding_model_id`.

    Lo consume el filtro SQL del retrieval: comparar en la base de datos exige
    o traducir alias en SQL (frágil, duplicado) o pasar el conjunto de grafías
    equivalentes (`= ANY(:refs)`), que es lo que se hace."""
    canonical = canonical_model_ref(name)
    if not canonical:
        return frozenset()
    refs = {canonical, canonical + _IMPLICIT_TAG}
    refs.update(alias for alias, target in LEGACY_MODEL_ALIASES.items() if target == canonical)
    return frozenset(refs)


def active_embedding_model(settings: Settings | Any | None = None) -> str:
    """El modelo de embeddings ACTIVO de la plataforma, canonizado.

    Es el que usan el embedder de ingesta, el de memoria y el de consulta —los
    tres construyen `OllamaEmbedder` sin `model_id`, que cae a este mismo
    setting. Que sean el mismo es lo que hace que el sello de la KB pueda
    gobernar la ingesta."""
    if settings is None:
        from api_server.config import get_settings

        settings = get_settings()
    return canonical_model_ref(settings.embedding_model)


def require_matching_model(*, kb_model: str, active_model: str) -> str:
    """Devuelve el modelo canónico con el que embeber esa KB, o se niega.

    La regla 4 del ADR 0155: la ingesta se niega antes que mezclar espacios
    semánticos dentro de una misma KB."""
    if not models_match(kb_model, active_model):
        raise EmbeddingModelMismatchError(
            kb_model=canonical_model_ref(kb_model) or kb_model,
            active_model=canonical_model_ref(active_model) or active_model,
        )
    return canonical_model_ref(active_model)


__all__ = [
    "LEGACY_MODEL_ALIASES",
    "EmbeddingModelMismatchError",
    "accepted_model_refs",
    "active_embedding_model",
    "canonical_model_ref",
    "models_match",
    "require_matching_model",
]
