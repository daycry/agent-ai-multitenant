"""Ajuste de la sesión para la búsqueda vectorial HNSW (prod-13, task_prod13_12).

## El problema (hallazgo db-6)

El índice HNSW de `chunks` es **global**, no por tenant. Una búsqueda vectorial
la resuelve el índice primero y los filtros después: HNSW devuelve sus `ef_search`
candidatos más cercanos MIRANDO TODO EL ÍNDICE, y solo entonces se aplican la RLS
y el filtro de KBs visibles. Con un corpus desbalanceado —un tenant con el 95 %
de los chunks y otro con el 5 %— los candidatos que devuelve el índice son casi
todos del tenant grande, se descartan por el filtro, y el tenant pequeño recibe
**cero resultados** para una consulta que sí tiene respuesta en su corpus.

No es una fuga: la RLS hace su trabajo y nadie ve lo que no debe. Es lo contrario,
una pérdida de recall silenciosa — el RAG contesta «no encuentro nada» y el
usuario no tiene forma de distinguirlo de que realmente no haya nada.

## La mitigación

pgvector ≥ 0.8 trae `hnsw.iterative_scan`: cuando el filtro descarta demasiados
candidatos, el índice SIGUE recorriendo en vez de rendirse con lo que ya tenía.
`relaxed_order` (y no `strict_order`) porque el orden exacto lo vuelve a imponer
después la fusión RRF de `recall_chunks`, y `strict_order` cuesta bastante más.
`hnsw.ef_search` sube el número de candidatos de partida.

Esto es una MITIGACIÓN, no la solución: la solución estructural —índices
parciales por tenant o particionado de `chunks`— es una decisión de arquitectura
que el plan deja explícitamente a un ADR.

## Dos decisiones de implementación que conviene tener escritas

1. **`SET LOCAL` y no `SET`**. Las conexiones vienen de un pool y se reutilizan
   entre requests de tenants distintos: un `SET` a secas dejaría el parámetro
   pegado a la conexión para quien la coja después. `SET LOCAL` muere con la
   transacción.

2. **Un `SAVEPOINT` alrededor**. Con pgvector < 0.8 el parámetro
   `hnsw.iterative_scan` NO EXISTE, y en PostgreSQL un GUC desconocido bajo un
   prefijo con extensión cargada es un ERROR que aborta la transacción entera.
   Sin el savepoint, arrancar contra una pgvector antigua no degradaría el
   recall: tumbaría la búsqueda. Se intenta una vez por proceso y, si no está,
   se recuerda y no se vuelve a intentar.
"""

from __future__ import annotations

import os

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_log = structlog.get_logger("api_server.rag.hnsw")

__all__ = ["ef_search", "iterative_scan_mode", "reset_hnsw_support_probe", "tune_hnsw_session"]

#: Modo de escaneo iterativo. `relaxed_order` porque el orden final lo pone la
#: fusión RRF, no el índice.
_DEFAULT_ITERATIVE_SCAN = "relaxed_order"

#: Candidatos de partida. El default de pgvector es 40, que con el corpus
#: desbalanceado es justo lo que se queda corto. 100 es un compromiso: mejora el
#: recall del tenant pequeño sin convertir cada búsqueda en un recorrido.
_DEFAULT_EF_SEARCH = 100

#: Se leen del entorno con el mismo prefijo `API_SERVER_` que usa `Settings`.
#: Viven aquí y no en `Settings` porque son perillas del PLANIFICADOR consumidas
#: en un único punto; moverlas a `Settings` es un follow-up sin efecto funcional.
_ITERATIVE_SCAN_ENV = "API_SERVER_HNSW_ITERATIVE_SCAN"
_EF_SEARCH_ENV = "API_SERVER_HNSW_EF_SEARCH"

#: Sonda de soporte, en un contenedor mutable para no necesitar `global`:
#: `None` = todavía no se ha probado; `True`/`False` = lo que dijo el servidor.
_PROBE: dict[str, bool | None] = {"iterative_scan": None}


def iterative_scan_mode() -> str:
    """El modo configurado. Cadena vacía = desactivado a propósito."""
    return os.environ.get(_ITERATIVE_SCAN_ENV, _DEFAULT_ITERATIVE_SCAN).strip()


def ef_search() -> int:
    """`hnsw.ef_search` configurado. Un valor no numérico cae al default."""
    raw = os.environ.get(_EF_SEARCH_ENV)
    if raw is None:
        return _DEFAULT_EF_SEARCH
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_EF_SEARCH
    return value if value > 0 else _DEFAULT_EF_SEARCH


def reset_hnsw_support_probe() -> None:
    """Olvida si el servidor soporta `hnsw.iterative_scan` (para los tests)."""
    _PROBE["iterative_scan"] = None


async def tune_hnsw_session(session: AsyncSession) -> None:
    """Aplica los GUC de HNSW a la transacción en curso. Nunca levanta.

    Best-effort por diseño: si el ajuste falla, la búsqueda debe seguir
    funcionando con el comportamiento de antes (peor recall en el tenant
    pequeño, que es exactamente el estado previo a esta tarea), no romperse.
    """
    # `ef_search` existe en todas las versiones de pgvector con HNSW, así que no
    # necesita sonda; si aun así fallara, el savepoint lo absorbe igual.
    await _set_local(session, "hnsw.ef_search", str(ef_search()))

    mode = iterative_scan_mode()
    if not mode or _PROBE["iterative_scan"] is False:
        return
    ok = await _set_local(session, "hnsw.iterative_scan", mode)
    if _PROBE["iterative_scan"] is None:
        _PROBE["iterative_scan"] = ok
        if not ok:
            _log.warning(
                "rag.hnsw.iterative_scan_unsupported",
                hint="pgvector < 0.8: el recall multi-tenant se queda sin mitigar",
            )


async def _set_local(session: AsyncSession, parameter: str, value: str) -> bool:
    """`SET LOCAL <parameter> = <value>` dentro de un SAVEPOINT. True si coló.

    El nombre del parámetro NO puede ir como bind (`SET` no acepta parámetros),
    así que se interpola — por eso ambos argumentos salen de constantes de este
    módulo y nunca de datos de una request.
    """
    try:
        async with session.begin_nested():
            await session.execute(text(f"SET LOCAL {parameter} = '{value}'"))
        return True
    except Exception as exc:  # - degradar, jamás romper la búsqueda
        _log.debug("rag.hnsw.set_local_failed", parameter=parameter, error=str(exc))
        return False
