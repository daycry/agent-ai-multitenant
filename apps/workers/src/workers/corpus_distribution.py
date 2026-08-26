"""Reparto del corpus vectorial por tenant — la métrica que el ADR 0152 pidió.

El [ADR 0152](../../../../docs/05-architecture-decisions/0152-recall-vectorial-multitenant-hnsw.md)
aceptó quedarse en la mitigación (opción A) y pasar a particionar `chunks`
(opción C) sólo cuando se cumpla un disparador escrito con número:

    el tenant más grande supera el 60 % de los chunks con embedding
    Y el corpus total pasa de ~200.000 chunks

Los dos a la vez, y no cualquiera de los dos: el desequilibrio sin volumen lo
absorbe `iterative_scan` sin que se note, y el volumen sin desequilibrio no
produce el fallo que motivó el ADR (todos los tenants son grandes, así que los
candidatos que devuelve el índice HNSW ya son suyos).

El propio ADR dijo qué faltaba para poder tomar esa decisión: *«una métrica del
reparto del corpus por tenant. Sin ella el disparador de arriba no es comprobable
y este ADR se queda en literatura.»* Esto es esa métrica, y nada más: **no
gobierna nada**. Publica tres cifras y el disparador lo evalúa un humano —o una
regla de Prometheus— mirando las dos condiciones juntas.

Por qué la cuota del MAYOR y no la media
----------------------------------------
Parece intercambiable y no lo es. La media baja al crecer el número de tenants,
así que enmascararía justo el caso que el ADR persigue: un tenant con el 95 % del
corpus entre otros veinte pequeños da una media del 4,75 %. Lo que hace daño al
recall es el máximo.

Cross-tenant a propósito
------------------------
Es una métrica de PLATAFORMA, no de tenant, así que la consulta agrega sobre
todos y corre bajo la sesión admin BYPASSRLS de mantenimiento — el mismo camino
que `maintenance/knowledge_gc.py`. No expone nada por tenant: publica el total,
cuántos tenants tienen corpus y la cuota del mayor, sin decir cuál es.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from workers.textfile_collector import write_textfile_metric

_log = structlog.get_logger("workers.corpus_distribution")

METRIC_TOTAL = "agentic_kb_chunks_total"
METRIC_TENANTS = "agentic_kb_tenants_with_chunks"
METRIC_LARGEST_SHARE = "agentic_kb_largest_tenant_share"


def render_corpus_distribution(*, por_tenant: Sequence[int]) -> str:
    """Cuerpo Prometheus a partir de los recuentos por tenant.

    Función pura y sin E/S: la aritmética de esta métrica falla en SILENCIO —una
    cuota calculada sobre el total equivocado publica un 0,4 donde había un 0,7 y
    nadie lo nota—, así que es la mitad que se testea. Ver
    `tests/unit/test_corpus_distribution_metrics.py`.

    `por_tenant` son los `COUNT(*)` de cada tenant con al menos un chunk con
    embedding. El orden no importa.
    """
    negativos = [n for n in por_tenant if n < 0]
    if negativos:
        raise ValueError(
            f"recuentos negativos en el reparto del corpus: {negativos}. Un COUNT "
            "no puede serlo, así que la consulta cambió de forma; publicar una "
            "cuota calculada sobre esto sería peor que no publicar nada."
        )

    total = sum(por_tenant)
    tenants = len(por_tenant)
    # Corpus vacío: la cuota es 0, no una división entre cero ni un NaN que
    # node-exporter serviría igual y Grafana pintaría como un hueco.
    cuota = (max(por_tenant) / total) if total else 0.0

    return "\n".join(
        [
            f"# HELP {METRIC_TOTAL} Chunks con embedding en toda la plataforma.",
            f"# TYPE {METRIC_TOTAL} gauge",
            f"{METRIC_TOTAL} {total}",
            f"# HELP {METRIC_TENANTS} Tenants con al menos un chunk con embedding.",
            f"# TYPE {METRIC_TENANTS} gauge",
            f"{METRIC_TENANTS} {tenants}",
            f"# HELP {METRIC_LARGEST_SHARE} Fraccion del corpus del tenant mayor (0-1). "
            f"ADR 0152: particionar si supera 0.6 Y {METRIC_TOTAL} pasa de 200000.",
            f"# TYPE {METRIC_LARGEST_SHARE} gauge",
            f"{METRIC_LARGEST_SHARE} {cuota:.6f}",
            "",
        ]
    )


async def leer_reparto(sessionmaker: async_sessionmaker[AsyncSession]) -> list[int]:
    """Un `COUNT(*)` por tenant sobre los chunks que TIENEN embedding.

    `embedding IS NOT NULL` no es un detalle: los chunks sin vector no entran en
    el índice HNSW, así que contarlos inflaría el total y bajaría artificialmente
    la cuota del mayor — falseando el disparador hacia «todo va bien».
    """
    async with sessionmaker() as session:
        filas = await session.execute(
            text("SELECT COUNT(*) AS n FROM chunks WHERE embedding IS NOT NULL GROUP BY tenant_id")
        )
        return [int(fila.n) for fila in filas]


async def publicar_reparto(
    sessionmaker: async_sessionmaker[AsyncSession], *, destino: str | os.PathLike[str]
) -> bool:
    """Mide y publica. Best-effort, como el resto de métricas de workers.

    Emitir una métrica no puede tumbar el trabajo real: si la consulta falla se
    traga y se sigue. Si ESTA métrica propagase, se convertiría en la causa del
    fallo que venía a delatar.
    """
    try:
        por_tenant = await leer_reparto(sessionmaker)
    except Exception as exc:  # pragma: no cover - best-effort por diseño
        _log.warning("corpus_distribution.query_failed", error=str(exc))
        return False

    def _render() -> str:
        return render_corpus_distribution(por_tenant=por_tenant)

    return write_textfile_metric(destino, _render, event_prefix="corpus_distribution")


__all__: list[str] = [
    "METRIC_LARGEST_SHARE",
    "METRIC_TENANTS",
    "METRIC_TOTAL",
    "leer_reparto",
    "publicar_reparto",
    "render_corpus_distribution",
]
