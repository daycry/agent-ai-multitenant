"""Resultado de las tareas Celery como métrica (prod-08 `task_prod08_metrics_workers_05`).

El agujero
----------
``agentic_celery_queue_depth`` dice cuántos mensajes **esperan** en cada cola.
No dice cuántas tareas se ejecutaron, cuántas fallaron ni cuánto tardaron. Un
worker sano y un worker que consume la cola y **falla el 100%** de lo que saca
presentan exactamente la misma profundidad de cola: cero. Sin contadores de
resultado, «el pipeline funciona» y «el pipeline se está comiendo el trabajo»
son indistinguibles desde Prometheus.

Por qué Redis y no un exporter HTTP
-----------------------------------
El plan proponía ``start_http_server(9540)`` en ``worker_process_init``. El
ADR 0141 lo descartó y tenía razón: con el pool **prefork** esa señal se dispara
en CADA proceso hijo, así que o se pelean por el puerto (``EADDRINUSE``) o
—peor— el bind prospera y Prometheus scrapea el estado de **un hijo al azar**
presentándolo como el del worker.

Lo que sí funciona con N procesos: acumular en un sitio que todos comparten. Las
señales incrementan contadores en **Redis** (el broker, que el worker ya tiene
abierto) y el sampler de beat los publica por el textfile-collector de
node-exporter en la misma pasada que el resto de métricas de workers. Un solo
camino de salida, ninguna competencia por puertos, y el agregado es del worker
entero en vez de un proceso.

Semántica de contador
---------------------
Los valores son **monotónicos y acumulados en Redis**, no un gauge del último
intervalo: así un scrape perdido no pierde información y ``rate()`` funciona.
Sobreviven al reinicio del worker (viven en Redis, no en el proceso) y se
reinician si alguien limpia el Redis del broker — Prometheus detecta el reset de
un counter y lo maneja.

Best-effort en todos los caminos: emitir una métrica NUNCA puede tumbar el
trabajo real. Un Redis caído se traga y se sigue.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Mapping
from typing import Any

import structlog

_log = structlog.get_logger("workers.task_metrics")

# Hashes en el Redis del BROKER (el que el worker ya tiene abierto). El prefijo
# `agentic:metrics:` no colisiona con las claves de Celery (nombres de cola
# desnudos, `_kombu.*`, `celery-task-meta-*`).
TASKS_HASH_KEY = "agentic:metrics:celery:tasks"
DURATION_HASH_KEY = "agentic:metrics:celery:duration"

# Separador de los dos componentes del campo (`{queue}|{status}`). Ni los
# nombres de cola ni los estados de Celery contienen `|`.
_FIELD_SEP = "|"

# Cola de una tarea sin `delivery_info` (llamada eager o directa, no entregada
# por el broker). Se etiqueta como desconocida A PROPÓSITO: colapsarla en
# `default` mezclaría dos poblaciones en la misma serie y haría mentir a
# `CeleryQueueGrowing`.
UNKNOWN_QUEUE = "unknown"

# Cota del dict de arranques pendientes. Si un worker muere entre `task_prerun`
# y `task_postrun` (SIGKILL, OOM, hard-limit) el arranque queda huérfano; sin
# cota, un proceso que vive semanas acumularía una entrada por cada muerte.
MAX_PENDING_STARTS = 512

# task_id → (monotonic del arranque, estado forzado por `task_failure` o None).
_pending_starts: dict[str, tuple[float, str | None]] = {}

# Cliente Redis perezoso por PROCESO. Se crea en el primer `task_prerun`, que
# ocurre siempre DESPUÉS del fork, así que ningún hijo hereda el socket del
# padre (heredarlo corrompe las respuestas al entrelazarse dos procesos en la
# misma conexión).
_redis_client: Any | None = None

# Los receptores conectados, para que `install_task_metrics` sea idempotente:
# el módulo se importa desde `celery_app`, que a su vez importa media base, y
# una doble instalación contaría cada tarea dos veces.
_installed = False


def _metrics_redis() -> Any:
    """El cliente Redis síncrono de este proceso (las señales son síncronas)."""
    global _redis_client  # noqa: PLW0603 - caché por proceso, reseteada tras el fork
    if _redis_client is None:
        from redis import Redis

        from workers.config import get_settings

        _redis_client = Redis.from_url(get_settings().broker_url)
    return _redis_client


def reset_metrics_redis() -> None:
    """Suelta el cliente cacheado. Lo llama ``worker_process_init``: si el padre
    llegó a crear uno, el hijo NO debe reutilizar el socket heredado — dos
    procesos entrelazados en la misma conexión corrompen las respuestas."""
    global _redis_client  # noqa: PLW0603 - misma caché por proceso
    client = _redis_client
    _redis_client = None
    if client is not None:
        with contextlib.suppress(Exception):  # cierre best-effort
            client.close()


def record_task_outcome(
    redis: Any,
    *,
    queue: str,
    status: str,
    duration_s: float | None = None,
) -> bool:
    """Suma una tarea terminada a los contadores. ``True`` si se registró.

    Nunca lanza: un Redis caído devuelve ``False`` y la tarea sigue su curso.
    """
    try:
        redis.hincrby(TASKS_HASH_KEY, f"{queue}{_FIELD_SEP}{status}", 1)
        if duration_s is not None:
            redis.hincrbyfloat(DURATION_HASH_KEY, queue, float(duration_s))
    except Exception as exc:
        _log.debug("task_metrics.record_failed", error=str(exc))
        return False
    return True


def _as_text(value: Any) -> str:
    """redis-py devuelve ``bytes`` salvo con ``decode_responses=True``."""
    return value.decode() if isinstance(value, bytes) else str(value)


def parse_task_counters(
    raw_counts: Mapping[Any, Any],
    raw_durations: Mapping[Any, Any],
) -> tuple[dict[tuple[str, str], int], dict[str, float]]:
    """Parsea los dos hashes. Pura (sin I/O) para poder testearla y para que el
    sampler async y el lector síncrono compartan exactamente el mismo parser.

    Un campo malformado (otro proceso escribiendo en la clave, un despliegue a
    medias) se **salta**: tirar el muestreo entero por una clave basura se
    llevaría por delante las métricas buenas de la misma pasada.
    """
    counts: dict[tuple[str, str], int] = {}
    for field, value in raw_counts.items():
        name = _as_text(field)
        if _FIELD_SEP not in name:
            continue
        queue, _, status = name.partition(_FIELD_SEP)
        if not queue or not status:
            continue
        try:
            counts[(queue, status)] = int(_as_text(value))
        except (TypeError, ValueError):
            continue

    durations: dict[str, float] = {}
    for field, value in raw_durations.items():
        try:
            durations[_as_text(field)] = float(_as_text(value))
        except (TypeError, ValueError):
            continue
    return counts, durations


def read_task_counters(redis: Any) -> tuple[dict[tuple[str, str], int], dict[str, float]]:
    """Lee y parsea los contadores con un cliente Redis **síncrono**.

    El sampler usa su propia lectura async (``redis.asyncio``) sobre el mismo
    parser; esta versión existe para los llamantes síncronos y los tests.
    """
    return parse_task_counters(redis.hgetall(TASKS_HASH_KEY), redis.hgetall(DURATION_HASH_KEY))


# ---------------------------------------------------------------------------
# Señales de Celery
# ---------------------------------------------------------------------------
def _queue_of(sender: Any) -> str:
    """La cola por la que llegó la tarea, de su ``delivery_info``."""
    request = getattr(sender, "request", None)
    delivery_info = getattr(request, "delivery_info", None)
    if isinstance(delivery_info, Mapping):
        routing_key = delivery_info.get("routing_key")
        if routing_key:
            return str(routing_key)
    return UNKNOWN_QUEUE


def _normalise_state(state: Any) -> str:
    """El estado de Celery (``SUCCESS``/``FAILURE``/``RETRY``…) como label."""
    text = str(state or "unknown").lower()
    return "success" if text == "success" else text


def _on_task_prerun(task_id: Any = None, **_kwargs: Any) -> None:
    if task_id is None:
        return
    key = str(task_id)
    # Cota ANTES de insertar, expulsando el más antiguo (los dicts preservan el
    # orden de inserción). Un arranque huérfano viejo ya no se va a cerrar.
    while len(_pending_starts) >= MAX_PENDING_STARTS:
        _pending_starts.pop(next(iter(_pending_starts)), None)
    _pending_starts[key] = (time.monotonic(), None)


def _on_task_failure(task_id: Any = None, **_kwargs: Any) -> None:
    """Marca el fallo; NO cuenta.

    Celery emite ``task_failure`` y **después** ``task_postrun`` para la misma
    tarea. Contar en las dos duplicaría cada fallo e inflaría el total, que es
    justo el denominador de la tasa de fallo. Además, en esa secuencia
    ``task_postrun`` recibe a menudo ``state=SUCCESS``, así que sin esta marca
    los fallos se contarían como éxitos.
    """
    if task_id is None:
        return
    key = str(task_id)
    started, _status = _pending_starts.get(key, (time.monotonic(), None))
    _pending_starts[key] = (started, "failure")


def _on_task_postrun(
    sender: Any = None, task_id: Any = None, state: Any = None, **_kwargs: Any
) -> None:
    """Cuenta la tarea. Es el ÚNICO punto que cuenta (ver `_on_task_failure`).

    Todo el cuerpo va bajo `try`, no solo la escritura. `record_task_outcome` ya
    se traga los fallos de Redis, pero sus ARGUMENTOS se evalúan antes de entrar
    en él: si `_metrics_redis()` reventara (settings incompletos, URL de broker
    mal formada) la excepción escaparía por la señal y se llevaría por delante la
    tarea real. Una métrica jamás puede tumbar el trabajo que mide.
    """
    if task_id is None:
        return
    try:
        started, forced_status = _pending_starts.pop(str(task_id), (None, None))
        duration = None if started is None else max(0.0, time.monotonic() - started)
        record_task_outcome(
            _metrics_redis(),
            queue=_queue_of(sender),
            status=forced_status or _normalise_state(state),
            duration_s=duration,
        )
    except Exception as exc:  # pragma: no cover - red de seguridad de la señal
        _log.debug("task_metrics.postrun_failed", error=str(exc))


def _on_worker_process_init(**_kwargs: Any) -> None:
    reset_metrics_redis()


def install_task_metrics() -> None:
    """Conecta los receptores. Idempotente."""
    global _installed  # noqa: PLW0603 - estado de instalación por proceso
    if _installed:
        return
    from celery.signals import task_failure, task_postrun, task_prerun, worker_process_init

    task_prerun.connect(_on_task_prerun, weak=False)
    task_postrun.connect(_on_task_postrun, weak=False)
    task_failure.connect(_on_task_failure, weak=False)
    worker_process_init.connect(_on_worker_process_init, weak=False)
    _installed = True


def uninstall_task_metrics() -> None:
    """Desconecta los receptores (tests: dejarlos colgados contamina la suite)."""
    global _installed  # noqa: PLW0603 - mismo estado
    from celery.signals import task_failure, task_postrun, task_prerun, worker_process_init

    task_prerun.disconnect(_on_task_prerun)
    task_postrun.disconnect(_on_task_postrun)
    task_failure.disconnect(_on_task_failure)
    worker_process_init.disconnect(_on_worker_process_init)
    _installed = False


__all__ = [
    "DURATION_HASH_KEY",
    "MAX_PENDING_STARTS",
    "TASKS_HASH_KEY",
    "UNKNOWN_QUEUE",
    "install_task_metrics",
    "parse_task_counters",
    "read_task_counters",
    "record_task_outcome",
    "reset_metrics_redis",
    "uninstall_task_metrics",
]
