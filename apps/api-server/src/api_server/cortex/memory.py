"""Memoria cognitiva del córtex — recall asociativo híbrido + escritura (F1).

Sustituye el MVP "N recientes" de :mod:`api_server.assistant.memory` por el
**recall híbrido real** (BM25 + vector + entidad, fusión RRF) de
:func:`api_server.memorizer.recall.recall` (ADR 0059), restringido a la memoria
PRIVADA del owner del córtex y al discriminante ``metadata_.cortex=true``.

Aislamiento (excepción consciente al Principio 1 — no hay RLS aquí, las tablas
del córtex son tenant-less): el recall pasa ``scopes=('private',)`` +
``user_id=owner`` al ``_scope_filter_sql`` de ``recall``, que impone
``scope='private' AND user_id=:user_id`` en TODO el SQL. El ``tenant_id`` es el
discriminante físico que ``memory_entries`` exige (Decisión D1), NO un eje de
autorización. El predicado ``metadata_.cortex=true`` se aplica POST-fetch (no
ensuciamos ``recall``) para no mezclar la memoria del córtex con la del
asistente (que comparte ``scope='private'`` del mismo usuario).

  * :func:`cortex_recall` — recall híbrido del owner (path vectorial best-effort
    con :class:`OllamaEmbedder`; si el embed falla → ``query_embedding=None`` y
    cae a BM25+entidad, nunca bloquea).
  * :func:`cortex_remember` — persiste un recuerdo del córtex
    (``persist_memory_candidates`` directo, ``scope='private'``,
    ``metadata={'source':'cortex','cortex':True}``); dedup como
    ``remember_user_fact``.
  * :func:`augment_cortex_prompt` — reutiliza el blindaje anti-inyección de
    ``assistant.memory.augment_system_prompt`` (marcadores ``<<<DATOS>>>``).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

import structlog
from sqlalchemy import Integer, func, select, update
from sqlalchemy import text as sqla_text
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.assistant.memory import MAX_MEMORY_CONTENT, augment_system_prompt
from api_server.db.memory import MemoryEntry
from api_server.ingestion.embeddings import Embedder, EmbeddingError, OllamaEmbedder
from api_server.memorizer.distillation import MemoryCandidate
from api_server.memorizer.persistence import persist_memory_candidates
from api_server.memorizer.recall import query_entity_terms, recall

logger = structlog.get_logger(__name__)

# Stamped on córtex-written memories — the discriminator that keeps the córtex's
# memory apart from the (also scope='private') assistant memory of the same user.
CORTEX_MEMORY_SOURCE = "cortex"
# How many of the owner's recalled córtex memories to surface per turn.
CORTEX_RECALL_LIMIT = 8
_VALID_TYPES = ("semantic", "episodic")


async def _embed_query(embedder: Embedder | None, query: str) -> list[float] | None:
    """Embebe la query (best-effort) para el path vectorial del recall.

    Devuelve el vector o ``None`` cuando no hay embedder, la query está vacía o
    el embed falla — en cuyo caso el recall cae a BM25+entidad. Nunca lanza: un
    fallo del embedder NUNCA bloquea el recall (mismo principio que
    ``persistence._embed_contents``)."""
    if embedder is None or not query.strip():
        return None
    try:
        vectors = await embedder.embed([query])
    except EmbeddingError as exc:
        logger.warning("cortex.recall_embed_failed", error=str(exc))
        return None
    if not vectors:
        return None
    return list(vectors[0])


async def cortex_recall(
    session: AsyncSession,
    *,
    owner_user_id: UUID,
    tenant_id: UUID,
    query: str,
    query_embedding: Sequence[float] | None = None,
    limit: int = CORTEX_RECALL_LIMIT,
    embedder: Embedder | None = None,
) -> list[str]:
    """Recall híbrido (BM25 + vector + entidad, RRF) de la memoria del córtex del owner.

    Llama a :func:`memorizer.recall.recall` con ``scopes=('private',)`` y
    ``user_id=owner_user_id`` — el ``_scope_filter_sql`` impone
    ``scope='private' AND user_id=:user_id``, así que NUNCA cruza de usuario. Sobre
    el resultado se filtra ``metadata_.cortex=true`` (post-fetch) para excluir la
    memoria del asistente (mismo scope/usuario) y se preserva el orden RRF.

    El path vectorial es best-effort: si no se pasa ``query_embedding`` se intenta
    embeber la query con un :class:`OllamaEmbedder`; si el embed falla → ``None`` y
    el recall cae a BM25+entidad (nunca bloquea).
    """
    if query_embedding is None:
        # Best-effort: embed the query so the vector path contributes; fall back
        # to BM25+entity if the embedder is unreachable (never blocks the recall).
        used_embedder = embedder if embedder is not None else OllamaEmbedder()
        query_embedding = await _embed_query(used_embedder, query)
        if embedder is None:
            # We created the embedder ourselves → close its HTTP client.
            await used_embedder.aclose()

    # Over-fetch from recall so that after the cortex=true filter we still have
    # `limit` hits (the owner may have non-córtex private memories interleaved).
    hits = await recall(
        session,
        query=query,
        tenant_id=tenant_id,
        scopes=("private",),
        user_id=owner_user_id,
        query_embedding=query_embedding,
        limit=max(limit * 2, limit),
    )
    if not hits:
        return []

    # Keep only the córtex memories, preserving the RRF order recall returned.
    # Defence-in-depth: re-assert tenant_id + scope='private' + user_id=owner in
    # this filter query too (never rely on recall alone for the isolation axis).
    ranked_ids = [h.memory_id for h in hits]
    rows = await session.execute(
        select(MemoryEntry.id).where(
            MemoryEntry.id.in_(ranked_ids),
            MemoryEntry.tenant_id == tenant_id,
            MemoryEntry.scope == "private",
            MemoryEntry.user_id == owner_user_id,
            MemoryEntry.deleted_at.is_(None),
            MemoryEntry.metadata_["cortex"].astext == "true",
        )
    )
    cortex_ids = {row[0] for row in rows.all()}
    selected = [h for h in hits if h.memory_id in cortex_ids][:limit]

    # recall_frequency real (ADR 0077): incrementa el contador de uso SOLO de las
    # memorias DEVUELTAS (las que se inyectan al prompt), en la misma sesión.
    # ≤ limit filas por PK ⇒ coste despreciable; un fallo del contador JAMÁS
    # rompe el recall (best-effort).
    if selected:
        await _bump_recall_counters(
            session,
            owner_user_id=owner_user_id,
            memory_ids=[h.memory_id for h in selected],
        )
    return [h.content for h in selected]


async def _bump_recall_counters(
    session: AsyncSession,
    *,
    owner_user_id: UUID,
    memory_ids: list[UUID],
) -> None:
    """``metadata_.recall_count += 1`` y ``last_recalled_at`` de las memorias usadas.

    UPDATE por PK re-filtrado por ``user_id=owner`` + ``scope='private'``
    (cross-owner safe: un id ajeno no toca nada). Best-effort: cualquier fallo se
    loguea y se traga — el contador alimenta la retención (``forgetting``), no el
    turno."""
    now_iso = datetime.now(UTC).isoformat()
    try:
        await session.execute(
            update(MemoryEntry)
            .where(
                MemoryEntry.id.in_(memory_ids),
                MemoryEntry.user_id == owner_user_id,
                MemoryEntry.scope == "private",
            )
            .values(
                metadata_=func.jsonb_set(
                    func.jsonb_set(
                        func.coalesce(MemoryEntry.metadata_, sqla_text("'{}'::jsonb")),
                        sqla_text("'{recall_count}'"),
                        func.to_jsonb(
                            func.coalesce(
                                MemoryEntry.metadata_["recall_count"].astext.cast(Integer), 0
                            )
                            + 1
                        ),
                        sqla_text("true"),
                    ),
                    sqla_text("'{last_recalled_at}'"),
                    func.to_jsonb(now_iso),
                    sqla_text("true"),
                )
            )
        )
    except Exception as exc:  # best-effort: el contador nunca rompe el recall
        logger.warning("cortex.recall_counter_failed", error=str(exc))


async def cortex_remember(
    session: AsyncSession,
    *,
    owner_user_id: UUID,
    tenant_id: UUID,
    content: str,
    type: str = "semantic",
    tags: tuple[str, ...] = (),
) -> dict[str, object]:
    """Persiste un recuerdo DURADERO del córtex como memoria privada del owner.

    Escribe una fila ``memory_entries`` con ``scope='private'``,
    ``user_id=owner_user_id`` y ``metadata={'source':'cortex','cortex':True}``
    (el discriminante que :func:`cortex_recall` exige). Deduplica contra una
    memoria privada existente del mismo owner con contenido normalizado idéntico
    (igual que :func:`assistant.memory.remember_user_fact`), así que re-guardar el
    mismo recuerdo es un no-op (flush, sin commit).
    """
    normalised = " ".join(content.split())[:MAX_MEMORY_CONTENT]
    if not normalised:
        return {"stored": False, "reason": "empty content"}
    memory_type = type if type in _VALID_TYPES else "semantic"

    # Dedup: identical normalised content already stored as a córtex memory for
    # this owner. Scoped to the córtex discriminator so it never collides with
    # the assistant's (also scope='private') memory of the same user.
    existing = await session.execute(
        select(MemoryEntry.id)
        .where(
            MemoryEntry.tenant_id == tenant_id,
            MemoryEntry.scope == "private",
            MemoryEntry.user_id == owner_user_id,
            MemoryEntry.deleted_at.is_(None),
            MemoryEntry.metadata_["cortex"].astext == "true",
            func.lower(func.btrim(MemoryEntry.content)) == normalised.lower(),
        )
        .limit(1)
    )
    if existing.scalar_one_or_none() is not None:
        return {"stored": False, "deduped": True}

    # Entity terms power the third recall signal (ADR 0059) — derive them the
    # same way the recall query does, so write + read align.
    entities = tuple(query_entity_terms(normalised))
    rows = await persist_memory_candidates(
        session,
        [
            MemoryCandidate(
                content=normalised, type=memory_type, tags=tuple(tags), entities=entities
            )
        ],
        tenant_id=tenant_id,
        scope="private",
        user_id=owner_user_id,
        agent_id=None,
        extra_metadata={"source": CORTEX_MEMORY_SOURCE, "cortex": True},
    )
    await session.flush()
    return {"stored": True, "id": str(rows[0].id), "type": memory_type}


def augment_cortex_prompt(
    base_prompt: str, *, known_facts: list[str], remember_enabled: bool
) -> str:
    """Inyecta los recuerdos recallados (+ pista de escritura) en el system prompt.

    Reutiliza :func:`assistant.memory.augment_system_prompt` tal cual — mismo
    blindaje anti-inyección con los marcadores ``<<<DATOS>>>`` / ``<<<FIN DATOS>>>``
    (el texto recallado es DATO, NUNCA instrucción). El córtex es el mismo patrón
    de "lo que sé de ti" del asistente.
    """
    return augment_system_prompt(
        base_prompt, known_facts=known_facts, remember_enabled=remember_enabled
    )


__all__ = [
    "CORTEX_MEMORY_SOURCE",
    "CORTEX_RECALL_LIMIT",
    "augment_cortex_prompt",
    "cortex_recall",
    "cortex_remember",
]
