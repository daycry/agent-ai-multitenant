"""prod-13 · task_prod13_12 — el RECALL del tenant pequeño, medido de verdad.

El hallazgo db-6 no es una fuga: es una pérdida de recall silenciosa. El índice
HNSW de `chunks` es GLOBAL, la búsqueda la resuelve el índice primero y los
filtros (RLS + KBs visibles) se aplican DESPUÉS. Con un corpus desbalanceado, los
`ef_search` candidatos que devuelve el índice son casi todos del tenant grande,
el filtro los tira, y el tenant pequeño recibe **cero resultados** para una
consulta que sí tiene respuesta en su corpus. El RAG contesta «no encuentro
nada» y nadie puede distinguirlo de que realmente no haya nada.

## Por qué este test existía como «imposible», y por qué no lo era

`test_vector_recall_multitenant.py` fija el CABLEADO de la mitigación y anota que
el test de recall se retiró porque «con las mil filas que un test puede sembrar,
PostgreSQL no elige el índice HNSW» — el control devolvía resultados sin la
mitigación, o sea el test habría quedado verde sin medir nada. El diagnóstico era
correcto; la conclusión, no. Lo que faltaba era **una línea**:

    SET LOCAL enable_seqscan = off

Con el escaneo secuencial descartado, el planificador usa el índice y el modo de
fallo se reproduce con 2.030 filas. Y forzarlo es legítimo, no hacer trampa: en
producción, con un corpus real, el planificador elige el índice por su cuenta —
justamente el escenario que db-6 describe. Aquí solo se le quita la alternativa
que el tamaño de juguete le regalaba.

Medido antes de escribir el test, sobre pgvector 0.8.2 y 2.000 vectores del
tenant grande contra 30 del pequeño:

    ef_search=40  · sin iterative_scan  →  0 resultados   ← el defecto
    ef_search=100 · sin iterative_scan  →  0 resultados   ← subir ef NO basta
    ef_search=100 · con iterative_scan  → 10 resultados   ← la mitigación

La fila del medio es la que hace que este test signifique algo: descarta la
explicación alternativa de que lo que arregla el recall sea el `ef_search` más
alto. Lo que lo arregla es el escaneo iterativo.
"""

from __future__ import annotations

import random
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

# 768 = la dimensión real de `chunks.embedding`; usar otra no probaría nada
# sobre el índice que existe en producción.
_DIM = 768

# Con estos dos números el modo de fallo se reproduce y el test tarda ~30 s
# (inserción + construcción del índice HNSW). Subirlos no aporta señal.
_BIG_TENANT_CHUNKS = 2000
_SMALL_TENANT_CHUNKS = 30


def _vector(first: float, jitter: float, rng: random.Random) -> str:
    """Un vector pegado al eje 1 a distancia ``1 - first`` del query."""
    values = [first] + [rng.uniform(-jitter, jitter) for _ in range(_DIM - 1)]
    return "[" + ",".join(f"{value:.4f}" for value in values) + "]"


@pytest.fixture()
def schema_at_head(alembic_config: Any) -> None:
    command.upgrade(alembic_config, "head")


async def _seed_tenant(
    session: AsyncSession, *, chunks: int, first: float, jitter: float, rng: random.Random
) -> tuple[UUID, UUID]:
    """Un tenant con proyecto + KB concedida + documento + ``chunks`` chunks.

    Devuelve ``(tenant_id, project_id)``. Se siembra con la sesión ADMIN
    (BYPASSRLS): montar dos tenants es, por definición, cross-tenant.
    """
    tenant = uuid4()
    project = uuid4()
    kb = uuid4()
    document = uuid4()
    short = str(tenant)[:8]
    await session.execute(
        sa_text("INSERT INTO organizations (id, name, slug) VALUES (:i, :n, :s)"),
        {"i": tenant, "n": f"T{short}", "s": f"t-{short}"},
    )
    await session.execute(
        sa_text("INSERT INTO projects (id, tenant_id, name, slug) VALUES (:i, :t, :n, :s)"),
        {"i": project, "t": tenant, "n": f"P{short}", "s": f"p-{short}"},
    )
    await session.execute(
        sa_text("INSERT INTO knowledge_bases (id, tenant_id, name) VALUES (:i, :t, :n)"),
        {"i": kb, "t": tenant, "n": f"KB{short}"},
    )
    await session.execute(
        sa_text("INSERT INTO kb_projects (kb_id, project_id, tenant_id) VALUES (:k, :p, :t)"),
        {"k": kb, "p": project, "t": tenant},
    )
    await session.execute(
        sa_text(
            "INSERT INTO documents (id, tenant_id, kb_id, title, source_filename,"
            " source_mime_type, source_storage_key)"
            " VALUES (:i, :t, :k, 'D', 'd.md', 'text/markdown', :key)"
        ),
        {"i": document, "t": tenant, "k": kb, "key": f"kb/{tenant}/{kb}/{document}/d.md"},
    )
    rows = [
        {
            "i": uuid4(),
            "t": tenant,
            "d": document,
            "o": ordinal,
            "c": f"contenido {ordinal}",
            "e": _vector(first, jitter, rng),
        }
        for ordinal in range(chunks)
    ]
    await session.execute(
        sa_text(
            "INSERT INTO chunks (id, tenant_id, document_id, ordinal, content, embedding)"
            " VALUES (:i, :t, :d, :o, :c, CAST(:e AS vector))"
        ),
        rows,
    )
    return tenant, project


async def _small_tenant_hits(
    session: AsyncSession, *, tenant: UUID, project: UUID, mitigated: bool
) -> list[UUID]:
    """Los ids que la búsqueda vectorial devuelve al tenant PEQUEÑO.

    ``mitigated=False`` desactiva el ajuste HNSW dejando el resto idéntico: es el
    control, y sin él la aserción del caso bueno no mediría nada.
    """
    from api_server.rag import search as search_mod

    query = [1.0] + [0.0] * (_DIM - 1)
    await session.execute(
        sa_text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant)}
    )
    # La línea que hacía «imposible» este test: sin ella el planificador resuelve
    # por el filtro de tenant y el defecto no aparece a escala de test.
    await session.execute(sa_text("SET LOCAL enable_seqscan = off"))

    original = search_mod.tune_hnsw_session
    if not mitigated:

        async def _no_tuning(_session: AsyncSession) -> None:
            return None

        search_mod.tune_hnsw_session = _no_tuning  # type: ignore[assignment]
    try:
        return await search_mod.vector_chunks(
            session,
            query_embedding=query,
            tenant_id=tenant,
            project_id=project,
            limit=10,
        )
    finally:
        search_mod.tune_hnsw_session = original  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_the_small_tenant_keeps_its_recall_in_a_lopsided_corpus(
    schema_at_head: None, admin_database_url: str
) -> None:
    """Corpus 98/2 con el tenant grande MÁS cerca del query que el pequeño.

    Se comprueban las dos mitades, y la primera es la que da sentido a la
    segunda: sin la mitigación el tenant pequeño recibe CERO resultados aunque su
    corpus tenga respuesta. Si algún día ese control dejara de dar cero, el test
    habría dejado de reproducir el defecto y su parte verde no valdría nada.
    """
    from api_server.rag.hnsw import reset_hnsw_support_probe

    reset_hnsw_support_probe()
    rng = random.Random(11)
    engine = create_async_engine(admin_database_url)
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        async with sessionmaker() as session, session.begin():
            await session.execute(
                sa_text(
                    "TRUNCATE chunks, documents, kb_projects, knowledge_bases, projects,"
                    " organizations CASCADE"
                )
            )
            await _seed_tenant(session, chunks=_BIG_TENANT_CHUNKS, first=1.0, jitter=0.005, rng=rng)
            small_tenant, small_project = await _seed_tenant(
                session, chunks=_SMALL_TENANT_CHUNKS, first=0.5, jitter=0.02, rng=rng
            )
            await session.execute(sa_text("ANALYZE chunks"))

        async with sessionmaker() as session, session.begin():
            unmitigated = await _small_tenant_hits(
                session, tenant=small_tenant, project=small_project, mitigated=False
            )
        async with sessionmaker() as session, session.begin():
            mitigated = await _small_tenant_hits(
                session, tenant=small_tenant, project=small_project, mitigated=True
            )
    finally:
        await engine.dispose()

    assert unmitigated == [], (
        "el CONTROL no reprodujo db-6: sin la mitigación el tenant pequeño debería"
        f" quedarse a cero y recibió {len(unmitigated)} chunks. Mientras el control"
        " no falle, la aserción de abajo no demuestra nada."
    )
    assert mitigated, (
        "con `hnsw.iterative_scan` activo el tenant pequeño SIGUE recibiendo cero"
        " resultados: la mitigación de db-6 no está surtiendo efecto"
    )
