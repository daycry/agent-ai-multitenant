"""Córtex F4 — curiosidad autónoma: selección de tema + persistencia (ADR 0078).

Lógica de la curiosidad **separada del LLM y testeable**:

  * :func:`gather_owner_entities` — agrega las ``entities`` que el OWNER ha mencionado
    (de sus memorias del córtex), ordenadas por frecuencia. SQL BYPASSRLS con filtro
    ``owner_user_id`` explícito (tablas tenant-less, ADR 0074).
  * :func:`pick_topic` — **pura**: elige la entity más frecuente NO investigada
    recientemente; sesga hacia los ``learning_goals`` de la identidad (F3) si solapan.
  * :func:`persist_learning_memory` — escribe la memoria ``semantic/learning`` con el
    ``cortex_pursuit_id`` en ``metadata_`` (idempotencia + protección del olvido).

El egress de la investigación NO vive aquí: lo hace el worker con las **web tools
existentes** (``api_server.cortex.web.web_search`` por el egress-proxy + anti-SSRF).
"""

from __future__ import annotations

from collections import Counter
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.memory import MemoryEntry

#: Cuántas memorias del córtex se escanean para extraer entities (acota el coste).
_ENTITY_SCAN_LIMIT = 200


async def gather_owner_entities(
    session: AsyncSession,
    *,
    owner_user_id: UUID,
    limit: int = 50,
) -> list[tuple[str, int]]:
    """Entities que el owner ha mencionado, por frecuencia desc (filtro owner explícito).

    Agrega el array JSONB ``entities`` de las memorias del córtex del owner
    (``scope='private'``, ``user_id=owner``, ``metadata_.cortex='true'``,
    ``deleted_at IS NULL``). Cross-owner safe: NUNCA mira entities de otro user (el
    ``user_id == owner`` lo impone). La agregación se hace en Python sobre las
    ``_ENTITY_SCAN_LIMIT`` memorias más recientes (simple y robusto; el volumen de
    la memoria privada del owner es modesto). Devuelve ``[(entity, freq), ...]``
    ordenado por frecuencia desc, luego alfabético (determinismo)."""
    rows = (
        (
            await session.execute(
                select(MemoryEntry.entities)
                .where(
                    MemoryEntry.user_id == owner_user_id,
                    MemoryEntry.scope == "private",
                    MemoryEntry.deleted_at.is_(None),
                    MemoryEntry.metadata_["cortex"].astext == "true",
                )
                .order_by(MemoryEntry.created_at.desc())
                .limit(_ENTITY_SCAN_LIMIT)
            )
        )
        .scalars()
        .all()
    )

    counter: Counter[str] = Counter()
    for entities in rows:
        for raw in entities or []:
            term = str(raw).strip().lower()
            if term:
                counter[term] += 1
    # Orden estable: frecuencia desc, luego alfabético.
    ranked = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    return ranked[:limit]


def pick_topic(
    entity_freqs: list[tuple[str, int]],
    *,
    recently_pursued: set[str],
    learning_goals: list[str] | None = None,
) -> str | None:
    """Elige el tema a investigar (PURO, determinista).

    Reglas (en orden):
      1. Descarta las entities ya investigadas recientemente (``recently_pursued``,
         comparación case-insensitive).
      2. Si alguna entity candidata solapa con un ``learning_goal`` de la identidad
         (F3), gana la de MAYOR frecuencia entre las que solapan (sesgo hacia lo que
         el córtex se propuso aprender).
      3. Si no hay solape, gana la candidata de mayor frecuencia.
      4. ``None`` si no queda ninguna candidata.

    El input ``entity_freqs`` viene ya ordenado por frecuencia desc; respetamos ese
    orden como desempate (estable)."""
    recently = {t.strip().lower() for t in recently_pursued}
    candidates = [(e, f) for e, f in entity_freqs if e.strip().lower() not in recently]
    if not candidates:
        return None

    goals = {g.strip().lower() for g in (learning_goals or []) if g.strip()}
    if goals:
        overlapping = [(e, f) for e, f in candidates if e.strip().lower() in goals]
        if overlapping:
            # Mayor frecuencia entre las que solapan (orden ya estable).
            return overlapping[0][0]
    return candidates[0][0]


async def persist_learning_memory(
    session: AsyncSession,
    *,
    owner_user_id: UUID,
    tenant_id: UUID,
    topic: str,
    digest: str,
    pursuit_id: UUID,
    entities: tuple[str, ...] = (),
) -> UUID | None:
    """Escribe la memoria ``semantic/learning`` del digest (idempotente por pursuit_id).

    DIRECTO vía :func:`persist_memory_candidates` (NO ``workers.memorizer``, que
    enruta episodic→project_shared y rompería el scope private): ``scope='private'``,
    ``user_id=owner``, ``type='semantic'``, ``metadata_={cortex, kind:'learning',
    cortex_pursuit_id, source:'cortex_curiosity'}``, ``tags=('cortex','learning')``.

    **Idempotencia (ADR 0078)**: antes de escribir comprueba que no exista ya una
    memoria con ``metadata_->>'cortex_pursuit_id' == pursuit_id`` → si existe, no-op
    (devuelve su id). Protegida del olvido (kind='learning', ADR 0077). Flush, sin
    commit. Devuelve el id de la memoria (existente o nueva), o ``None`` si el digest
    estaba vacío."""
    from api_server.assistant.memory import MAX_MEMORY_CONTENT
    from api_server.memorizer.distillation import MemoryCandidate
    from api_server.memorizer.persistence import persist_memory_candidates

    normalised = " ".join((digest or "").split())[:MAX_MEMORY_CONTENT]
    if not normalised:
        return None

    # Idempotencia por pursuit_id (dedup por metadata_, ADR 0078).
    existing = (
        await session.execute(
            select(MemoryEntry.id)
            .where(
                MemoryEntry.user_id == owner_user_id,
                MemoryEntry.scope == "private",
                MemoryEntry.deleted_at.is_(None),
                MemoryEntry.metadata_["cortex_pursuit_id"].astext == str(pursuit_id),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    rows = await persist_memory_candidates(
        session,
        [
            MemoryCandidate(
                content=normalised,
                type="semantic",
                tags=("cortex", "learning"),
                entities=tuple(entities),
            )
        ],
        tenant_id=tenant_id,
        scope="private",
        user_id=owner_user_id,
        agent_id=None,
        extra_metadata={
            "cortex": True,
            "kind": "learning",
            "cortex_pursuit_id": str(pursuit_id),
            "source": "cortex_curiosity",
            "topic": topic,
        },
    )
    await session.flush()
    return rows[0].id


__all__ = ["gather_owner_entities", "persist_learning_memory", "pick_topic"]
