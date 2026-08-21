"""La guarda HTTP del contrato de embeddings de KB (ADR 0155, regla 2).

Vive fuera de los dos routers que crean KBs (`/knowledge-bases` y `/api/v1/kbs`)
porque los dos escribían el MISMO literal por su cuenta
(`payload.embedding_model_id or "nomic-embed-text-v1.5"`) y esa duplicación es
media causa del hallazgo AUD14-03: dos sitios sellando una etiqueta que ningún
embedder envía.

La regla, en una frase: **la plataforma indexa con un único modelo, así que la
API no acepta uno que no vaya a usar**. Aceptarlo y guardarlo —lo que se hacía—
es peor que rechazarlo, porque produce una pantalla que enseña un modelo y una
ingesta que usa otro.
"""

from __future__ import annotations

from fastapi import HTTPException, status

from api_server.ingestion.embedding_contract import (
    active_embedding_model,
    canonical_model_ref,
    models_match,
)

__all__ = ["stamp_for_kb_update", "stamp_for_new_kb"]


def _reject_foreign_model(requested: str, active: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=(
            f"embedding_model_id '{requested}' no es el modelo de embeddings de esta "
            f"plataforma ('{active}'). La plataforma indexa con un único modelo "
            "(ADR 0155): omite el campo y se sella el activo. Cambiar el modelo es "
            "una operación de plataforma (API_SERVER_EMBEDDING_MODEL + reindexado), "
            "no un campo por KB."
        ),
    )


def stamp_for_new_kb(requested: str | None, *, active_model: str | None = None) -> str:
    """El sello que se guarda al crear una KB.

    Omitir el campo —el caso normal, y lo que hace la UI desde el ADR 0155— sella
    el modelo activo. Mandar otro da 422 en vez de un 201 con una etiqueta
    decorativa.
    """
    active = active_model if active_model is not None else active_embedding_model()
    if requested is not None and requested.strip() and not models_match(requested, active):
        raise _reject_foreign_model(requested, active)
    return active


def stamp_for_kb_update(
    *,
    requested: str,
    current: str,
    has_chunks: bool,
    active_model: str | None = None,
) -> str | None:
    """El sello nuevo tras un `PUT`, o ``None`` si no hay que tocar nada.

    Tres desenlaces, y los tres tienen motivo:

    * pedir el modelo que la KB ya tiene → no-op (un `PUT` idempotente con el
      cuerpo entero no puede fallar por reenviar un campo sin cambios);
    * pedir un modelo que no es el de la plataforma → **422**;
    * pedir el modelo activo sobre una KB CON chunks → **409**: re-sellar sin
      re-embeber convertiría el sello en mentira, que es justo lo que este ADR
      viene a quitar. Sin chunks no hay nada que invalidar y se re-sella.
    """
    active = active_model if active_model is not None else active_embedding_model()
    if models_match(requested, current):
        return None
    if not models_match(requested, active):
        raise _reject_foreign_model(requested, active)
    if has_chunks:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "no se puede re-sellar embedding_model_id: la KB ya tiene chunks "
                f"indexados con '{canonical_model_ref(current)}'. Reindexa sus "
                "documentos con el modelo activo y vuelve a intentarlo (ADR 0155)."
            ),
        )
    return canonical_model_ref(active)
