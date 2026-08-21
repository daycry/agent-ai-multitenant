"""Fallos consecutivos de destilación del Memorizer (prod-07 `task_prod07_15`).

El agujero (llm-10)
-------------------
La memorización post-run es **best-effort a propósito**: corre al final del run
y `_memorize_execution_async` se traga cualquier excepción para no tumbar el
pipeline de Celery. Eso está bien y es justo lo que la vuelve invisible — si el
LLM del destilador deja de responder (el caso real: ollama-local apagado por el
ADR 0056 mientras el fallback seguía apuntando a `localhost:11434`), el run
termina `ok`, no se persiste ni una memoria, y la única huella es un
`memorize_skip_reason` en la fila de esa ejecución. Un dato que solo se
encuentra si ya sospechas.

Consecutivos, no acumulados
---------------------------
Lo que hay que alertar no es «han fallado 40 destilaciones desde el lunes»,
sino «las últimas N seguidas han fallado» — un proveedor caído AHORA. Por eso
un éxito **borra** la racha: si el contador solo creciera, la alerta se quedaría
encendida después de arreglar el proveedor y acabaría silenciada a mano.

Por qué Redis
-------------
El worker corre con pool **prefork**: N procesos hijo. Un contador en memoria
contaría lo de un hijo al azar (la misma razón por la que el ADR 0141 descartó
el exporter HTTP por proceso). Se acumula en el Redis del broker —que el worker
ya tiene abierto— y el sampler de beat lo publica por el textfile-collector de
node-exporter, en la misma pasada que el resto de métricas de workers.

Best-effort en TODOS los caminos: emitir una métrica no puede tumbar el trabajo
real. Un Redis caído se traga y se sigue — si este contador propagase, se
convertiría en la causa del fallo que venía a delatar.
"""

from __future__ import annotations

from typing import Any

import structlog

_log = structlog.get_logger("workers.memorizer_metrics")

# Clave en el Redis del BROKER. El prefijo `agentic:metrics:` es el mismo que
# usa `workers/task_metrics.py` y no colisiona con las claves de Celery.
DISTILL_FAILURES_KEY = "agentic:metrics:memorizer:consecutive_distill_failures"

# Causas de `DistillationResult` que cuentan como FALLO del proveedor. Fuera
# queda `llm_empty`: ahí el modelo contestó bien y esa ejecución no tenía nada
# que memorizar — contarlo dispararía la alerta en un sistema sano, que es la
# forma más rápida de enseñar a ignorarla.
_FAILURE_CAUSES = frozenset({"llm_error", "llm_unparseable"})


def is_distillation_failure(cause: str | None) -> bool:
    """True cuando ``cause`` significa «el LLM del destilador no respondió bien»."""
    return cause in _FAILURE_CAUSES


async def record_distillation_outcome(redis: Any, *, ok: bool) -> int | None:
    """Anota el resultado de una destilación y devuelve la racha actual.

    ``ok=True`` borra la racha y devuelve 0; ``ok=False`` la incrementa y
    devuelve el nuevo total. Devuelve ``None`` si Redis no responde — el
    contador es observabilidad, no el trabajo.
    """
    try:
        if ok:
            await redis.delete(DISTILL_FAILURES_KEY)
            return 0
        return int(await redis.incr(DISTILL_FAILURES_KEY))
    except Exception as exc:  # — best-effort por diseño
        _log.warning("memorizer.metrics_unavailable", error=str(exc))
        return None


async def read_consecutive_failures(redis: Any) -> int:
    """La racha actual (0 si la clave no existe). El sampler la publica."""
    raw = await redis.get(DISTILL_FAILURES_KEY)
    if raw is None:
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):  # pragma: no cover - defensivo
        return 0
