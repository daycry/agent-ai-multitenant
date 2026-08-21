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

## Segunda vez que el planificador se escapa por la puerta de al lado (2026-08-19)

El control dejó de reproducir el defecto: devolvía 10 chunks donde debía devolver
cero, y el test se puso rojo por su propia guarda —correctamente— en vez de pasar
en vacío. La causa no fue la mitigación ni el corpus: fue que `vector_chunks`
ganó un filtro nuevo, el `EXISTS` de espacio de embeddings de la **regla 5 del
ADR 0155** (`kbm.embedding_model_id = ANY(:embedding_model_refs)`).

Ese `EXISTS` es un semi-join que PostgreSQL puede subir al plan, y con 2.030
filas le sale barato: en vez de preguntarle al índice HNSW, recorre los 30 chunks
alcanzables desde `kb_projects → documents → chunks` y los **ordena a mano**.
Recall exacto, cero relación con db-6. Medido con `EXPLAIN`: el plan del control
no mencionaba `ix_chunks_embedding_hnsw` por ninguna parte.

Es la MISMA trampa que el `enable_seqscan = off` de más arriba, un piso más
abajo: a escala de juguete el planificador tiene un atajo exacto que en
producción —millones de chunks— no puede pagar. La cura es la misma, quitarle el
atajo:

    SET LOCAL enable_sort = off

Con eso vuelve el `Nested Loop Semi Join` sobre `ix_chunks_embedding_hnsw`, y con
él las tres filas medidas de la tabla de arriba. Comprobado en esta base:

    enable_sort=on   · control → 10 resultados   ← el atajo: ordenar 30 filas
    enable_sort=off  · control →  0 resultados   ← db-6, otra vez reproducido
    enable_sort=off  · mitigado → 10 resultados

Y para que la próxima no cueste una tarde: el test **comprueba el plan**, no solo
el número de filas. Si el HNSW deja de aparecer en el `EXPLAIN`, lo dice con esas
palabras en vez de dejar que lo adivine quien vea un cero raro.
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


#: El índice que tiene que aparecer en el plan. Si no está, la consulta se
#: resolvió por otro camino y lo que mida el test no es db-6.
_HNSW_INDEX = "ix_chunks_embedding_hnsw"


async def _vector_query_plan(session: AsyncSession, *, tenant: UUID, project: UUID) -> str:
    """El `EXPLAIN` de la MISMA consulta que acaba de correr `vector_chunks`.

    Sobre `search.vector_sql`, no sobre una copia: una copia se desincroniza y
    el test seguiría afirmando cosas sobre un SQL que ya no se ejecuta.
    Se llama dentro de la misma transacción, así que hereda sus `SET LOCAL`
    —incluidos o no los GUC de HNSW según el caso— y describe el plan real.
    """
    from api_server.rag import search as search_mod

    query = [1.0] + [0.0] * (_DIM - 1)
    rows = await session.execute(
        sa_text("EXPLAIN (COSTS OFF) " + search_mod.vector_sql(with_agent=False)),
        {
            "qvec": "[" + ",".join(f"{x:.6f}" for x in query) + "]",
            "tenant_id": tenant,
            "project_id": project,
            "limit": 10,
            "embedding_model_refs": search_mod._accepted_refs_param(None),
        },
    )
    return "\n".join(str(row[0]) for row in rows.all())


async def _small_tenant_hits(
    session: AsyncSession, *, tenant: UUID, project: UUID, mitigated: bool
) -> tuple[list[UUID], str]:
    """Los ids que la búsqueda vectorial devuelve al tenant PEQUEÑO, y su plan.

    ``mitigated=False`` desactiva el ajuste HNSW dejando el resto idéntico: es el
    control, y sin él la aserción del caso bueno no mediría nada.
    """
    from api_server.rag import search as search_mod

    query = [1.0] + [0.0] * (_DIM - 1)
    await session.execute(
        sa_text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant)}
    )
    # Las dos líneas que le quitan al planificador los atajos que sólo existen a
    # escala de juguete; en producción no puede pagar ninguno de los dos y acaba
    # en el índice, que es el escenario que db-6 describe. Ver la cabecera.
    #  · sin `enable_seqscan = off` resuelve por el filtro de tenant;
    #  · sin `enable_sort = off` recorre los 30 chunks alcanzables por el
    #    semi-join del `EXISTS` del ADR 0155 y los ordena a mano.
    await session.execute(sa_text("SET LOCAL enable_seqscan = off"))
    await session.execute(sa_text("SET LOCAL enable_sort = off"))

    original = search_mod.tune_hnsw_session
    if not mitigated:

        async def _no_tuning(_session: AsyncSession) -> None:
            return None

        search_mod.tune_hnsw_session = _no_tuning  # type: ignore[assignment]
    try:
        hits = await search_mod.vector_chunks(
            session,
            query_embedding=query,
            tenant_id=tenant,
            project_id=project,
            limit=10,
        )
    finally:
        search_mod.tune_hnsw_session = original  # type: ignore[assignment]
    return hits, await _vector_query_plan(session, tenant=tenant, project=project)


@pytest.mark.asyncio
async def test_the_small_tenant_keeps_its_recall_in_a_lopsided_corpus(
    schema_at_head: None, admin_database_url: str
) -> None:
    """Corpus 98/2 con el tenant grande MÁS cerca del query que el pequeño.

    Se comprueban tres cosas, y cada una sostiene a la siguiente:

    1. que las dos consultas las resuelve el índice HNSW (si no, no estamos
       mirando db-6 y el recuento de filas no dice nada);
    2. que sin la mitigación el tenant pequeño recibe CERO resultados aunque su
       corpus tenga respuesta — el control;
    3. que con ella recibe resultados.

    Si algún día el control dejara de dar cero, el test habría dejado de
    reproducir el defecto y su parte verde no valdría nada. Ya pasó dos veces, y
    las dos por lo mismo: el planificador encontró un atajo que sólo existe con
    dos mil filas. La cabecera del módulo cuenta ambas.
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
            unmitigated, control_plan = await _small_tenant_hits(
                session, tenant=small_tenant, project=small_project, mitigated=False
            )
        async with sessionmaker() as session, session.begin():
            mitigated, mitigated_plan = await _small_tenant_hits(
                session, tenant=small_tenant, project=small_project, mitigated=True
            )
    finally:
        await engine.dispose()

    # Antes que nada: las dos mitades tienen que estar midiendo db-6, o sea que
    # la consulta la resuelve el ÍNDICE y los filtros van después. Si el
    # planificador se escapa por otro camino, el número de filas no significa
    # nada y hay que decirlo con esas palabras, no dejar un cero sospechoso.
    for label, plan in (("control", control_plan), ("mitigado", mitigated_plan)):
        assert _HNSW_INDEX in plan, (
            f"el plan del caso «{label}» no usa {_HNSW_INDEX}, así que esta consulta"
            " ya no reproduce db-6 (el índice global resolviendo primero y los"
            " filtros después). Un filtro nuevo en `vector_sql` le habrá dado al"
            f" planificador un atajo exacto a escala de test. Plan:\n{plan}"
        )

    assert unmitigated == [], (
        "el CONTROL no reprodujo db-6: sin la mitigación el tenant pequeño debería"
        f" quedarse a cero y recibió {len(unmitigated)} chunks. Mientras el control"
        " no falle, la aserción de abajo no demuestra nada."
    )
    assert mitigated, (
        "con `hnsw.iterative_scan` activo el tenant pequeño SIGUE recibiendo cero"
        " resultados: la mitigación de db-6 no está surtiendo efecto"
    )
