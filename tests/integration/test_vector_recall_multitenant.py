"""prod-13 · task_prod13_12 — el ajuste HNSW llega a la búsqueda vectorial (db-6).

El índice HNSW de `chunks` es GLOBAL. La búsqueda la resuelve el índice primero
—devuelve sus `ef_search` vecinos más cercanos mirando TODO el índice— y los
filtros (RLS y KBs visibles) se aplican DESPUÉS. Con un corpus desbalanceado, los
candidatos son casi todos del tenant grande, el filtro los tira, y el tenant
pequeño se queda con **cero resultados** para una consulta que sí tiene respuesta
en su corpus. No es una fuga: es una pérdida de recall silenciosa.

La mitigación (`api_server.rag.hnsw`) fija `hnsw.iterative_scan` y
`hnsw.ef_search` en la transacción de la búsqueda.

## Lo que este fichero SÍ verifica, y lo que NO

Verifica que `vector_chunks` **llama** al ajuste y que el GUC queda puesto en la
transacción en la que corre la consulta. Suena poco y no lo es: el modo de fallo
número uno de esta base de código es el mecanismo entregado que no llama nadie
(apartado 5 de `verificar-antes-de-implementar.md`), y este test lo cierra —
quitando el `await tune_hnsw_session(session)` de `vector_chunks`, se pone rojo.

NO verifica el recall con un corpus 95/5. Se intentó y se retiró a conciencia:
con las mil filas que un test puede sembrar, PostgreSQL **no elige el índice
HNSW** (resuelve por el filtro de tenant y ordena), así que el caso malo no se
reproduce — el «control» del test, que exigía cero resultados sin la mitigación,
devolvía los diez. Un test así queda verde sin medir nada, que es exactamente la
guarda-que-no-puede-fallar del apartado 4 de esa misma guía. Reproducirlo de
verdad pide cientos de miles de vectores: eso es un banco de pruebas, no un test
de integración. Queda reportado como hueco ABIERTO de la tarea en vez de
disfrazado de verde.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from alembic import command

pytestmark = pytest.mark.integration


@pytest.fixture()
def schema_at_head(alembic_config: Any) -> None:
    command.upgrade(alembic_config, "head")


@pytest.mark.asyncio
async def test_tune_hnsw_session_applies_the_guc_to_the_transaction(
    schema_at_head: None, app_database_url: str
) -> None:
    from api_server.rag.hnsw import ef_search, reset_hnsw_support_probe, tune_hnsw_session
    from sqlalchemy import text as sa_text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    reset_hnsw_support_probe()
    engine = create_async_engine(app_database_url)
    try:
        session = async_sessionmaker(engine, expire_on_commit=False)()
        try:
            await tune_hnsw_session(session)
            applied = (
                await session.execute(sa_text("SELECT current_setting('hnsw.ef_search', true)"))
            ).scalar_one()
        finally:
            await session.close()
    finally:
        await engine.dispose()

    assert applied == str(ef_search()), (
        "`tune_hnsw_session` no dejó `hnsw.ef_search` puesto en la transacción"
        f" (leído: {applied!r}, esperado: {ef_search()!r})"
    )


@pytest.mark.asyncio
async def test_the_vector_search_path_calls_the_tuning(
    schema_at_head: None, app_database_url: str
) -> None:
    """`vector_chunks` tiene que ajustar la sesión ANTES de lanzar la consulta."""
    from api_server.rag.hnsw import ef_search, reset_hnsw_support_probe
    from api_server.rag.search import vector_chunks
    from sqlalchemy import text as sa_text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    reset_hnsw_support_probe()
    assert ef_search() != 40, (
        "el `ef_search` configurado coincide con el default de pgvector, así que"
        " este test no podría distinguir «lo puso el código» de «ya estaba»"
    )

    engine = create_async_engine(app_database_url)
    try:
        session = async_sessionmaker(engine, expire_on_commit=False)()
        try:
            tenant_id = uuid4()
            await session.execute(
                sa_text("SELECT set_config('app.tenant_id', :tid, false)"),
                {"tid": str(tenant_id)},
            )
            # Sin datos: devuelve lista vacía, y da igual — lo que se mide es el
            # efecto lateral sobre la sesión, no el resultado.
            hits = await vector_chunks(
                session,
                query_embedding=[0.0] * 768,
                tenant_id=tenant_id,
                project_id=uuid4(),
            )
            applied = (
                await session.execute(sa_text("SELECT current_setting('hnsw.ef_search', true)"))
            ).scalar_one()
        finally:
            await session.close()
    finally:
        await engine.dispose()

    assert hits == []
    assert applied == str(ef_search()), (
        "`vector_chunks` corrió SIN ajustar la sesión HNSW (`hnsw.ef_search` ="
        f" {applied!r}): la mitigación de db-6 está entregada pero no la llama"
        " nadie en el camino de búsqueda"
    )
