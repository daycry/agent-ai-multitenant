"""Córtex F4 — curiosidad autónoma: selección de tema + persistencia (ADR 0078).

Lógica de la curiosidad **separada del LLM y testeable**:

  * :func:`gather_owner_entities` — agrega las ``entities`` que el OWNER ha mencionado
    (vocabulario de sus memorias del córtex + voto de sus turnos recientes),
    ordenadas por frecuencia. SQL BYPASSRLS con filtro ``owner_user_id`` explícito
    (tablas tenant-less, ADR 0074; RLS de eje owner desde la migración 0140).
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

from api_server.db.cortex import CortexTurn
from api_server.db.memory import MemoryEntry
from api_server.memorizer.recall import query_entity_terms

#: Cuántas memorias del córtex se escanean para extraer entities (acota el coste).
_ENTITY_SCAN_LIMIT = 200
#: Cuántos turnos recientes del owner votan sobre ese vocabulario. Ventana por
#: NÚMERO de turnos y no por fecha, igual que la de arriba: acota el coste de la
#: pasada de forma predecible aunque el owner se pase un mes sin hablar.
_TURN_SCAN_LIMIT = 100


async def gather_owner_entities(
    session: AsyncSession,
    *,
    owner_user_id: UUID,
    limit: int = 50,
    turn_scan_limit: int = _TURN_SCAN_LIMIT,
) -> list[tuple[str, int]]:
    """Entities que el owner ha mencionado, por frecuencia desc (filtro owner explícito).

    Dos fuentes, y el orden entre ellas es el diseño (F4 4.1 pedía las dos):

    1. **Memoria destilada** — el array JSONB ``entities`` de las memorias del córtex
       del owner (``scope='private'``, ``user_id=owner``, ``metadata_.cortex='true'``,
       ``deleted_at IS NULL``), sobre las :data:`_ENTITY_SCAN_LIMIT` más recientes.
       Esta fuente FIJA EL VOCABULARIO: solo puede ser tema lo que alguna vez se
       destiló como entity.
    2. **Turnos recientes** — los :data:`_TURN_SCAN_LIMIT` últimos ``cortex_turns``
       del owner con ``role='user'``, que **votan dentro de ese vocabulario**: cada
       turno suma 1 a cada entity conocida que menciona (una vez por turno, por
       mucho que la repita). Es lo que hace que un tema del que se está hablando
       AHORA adelante a otro destilado hace meses.

    **Por qué los turnos votan en vez de proponer.** El plan decía «reusa
    ``memorizer/recall.py::query_entity_terms``» y ``cortex_turns`` no tiene columna
    ``entities``, así que la lectura literal era extraer candidatos del texto crudo
    con ese helper. No vale: es un **matcher de recall**, no un ranker — devuelve
    todo token de ≥3 caracteres fuera de una lista de 26 stopwords, lo cual es
    inofensivo para BUSCAR (un término basura no hace match con ninguna entity
    guardada) y desastroso para ORDENAR. Medido poniendo la versión literal a
    propósito y corriendo
    ``test_una_palabra_cualquiera_del_turno_no_se_convierte_en_tema``, el ranking
    de tres turnos en castellano salía ``despliegue 3, manana 3, necesito 3``: el
    bucle autónomo sacaría a Internet, con dinero real, la palabra "necesito".
    Acotando el voto al vocabulario destilado, el helper hace justo aquello para lo
    que se escribió (emparejar texto con entities guardadas) y la basura no puede
    convertirse en tema.

    Limitación conocida, y consciente: un tema que aparece en la conversación pero
    que nunca se destiló a memoria NO es candidato, y una entity de menos de 3
    caracteres o que sea stopword no recibe votos de los turnos (el tokenizador las
    descarta) aunque sí cuente por la vía de la memoria. Levantar eso pide un
    extractor de entidades para turnos, que es trabajo aparte y con su propio coste.

    Cross-owner safe: ambas consultas llevan el predicado ``owner_user_id``/
    ``user_id == owner`` explícito — la capa que muerde hoy, porque el bucle corre
    con un rol BYPASSRLS (la RLS de eje owner de la migración ``0140`` es la segunda
    capa, ADR 0156). Devuelve ``[(entity, freq), ...]`` ordenado por frecuencia desc
    y luego alfabético (determinismo: :func:`pick_topic` se queda con el primero).
    """
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
    if not counter:
        # Sin vocabulario destilado no hay nada que los turnos puedan votar: nos
        # ahorramos la segunda consulta (y el bucle sale no-op, que es lo correcto
        # para un owner recién nacido).
        return []

    # Voto de los turnos recientes del OWNER (role='user': contar los turnos que
    # generó el córtex cerraría un bucle de autorrefuerzo — saca un tema, sus
    # propios turnos lo mencionan, sube en el ranking y lo vuelve a investigar).
    turn_contents = (
        (
            await session.execute(
                select(CortexTurn.content)
                .where(
                    CortexTurn.owner_user_id == owner_user_id,
                    CortexTurn.role == "user",
                )
                .order_by(CortexTurn.created_at.desc())
                .limit(max(0, turn_scan_limit))
            )
        )
        .scalars()
        .all()
    )
    vocabulary = set(counter)
    for content in turn_contents:
        # Un turno vale UN voto por entity, por insistente que sea. Hoy lo
        # garantiza ya `query_entity_terms` (deduplica manteniendo el orden); el
        # `set(...)` lo deja explícito y no depende de ese detalle del helper, que
        # se escribió para otra cosa. El contrato lo fija un test propio.
        for term in set(query_entity_terms(str(content or ""))) & vocabulary:
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
