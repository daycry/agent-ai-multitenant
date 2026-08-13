"""Métricas de las señales de Celery (prod-08 `task_prod08_metrics_workers_05`).

El plan pedía «señales `task_prerun/postrun/failure` → `celery_tasks_total{queue,
status}` y duración por cola». Lo que el plan proponía como transporte
(`start_http_server(9540)` en `worker_process_init`) el ADR 0141 lo descartó: con
el pool **prefork** esa llamada se ejecuta en CADA hijo, así que o se pelean por
el puerto o Prometheus acaba scrapeando el estado de un hijo al azar y
presentándolo como el del worker.

Pero el ADR resolvió el TRANSPORTE, no la métrica: hasta ahora nadie contaba los
resultados de las tareas Celery. `agentic_celery_queue_depth` dice cuántos
mensajes ESPERAN; no dice cuántos se ejecutaron ni cuántos fallaron. Un worker
que consume la cola y falla el 100% de las tareas presenta exactamente la misma
profundidad de cola que uno sano.

El transporte que sí funciona con prefork es acumular en **Redis** (compartido
por todos los hijos) y dejar que el sampler de beat lo publique por el
textfile-collector, igual que el resto de métricas de workers.
"""

from __future__ import annotations

from typing import Any
from weakref import ReferenceType

import pytest

pytestmark = pytest.mark.unit


class _FakeRedis:
    """Redis síncrono mínimo: hashes con HINCRBY / HINCRBYFLOAT / HGETALL."""

    def __init__(self, *, fail: bool = False) -> None:
        self.hashes: dict[str, dict[str, float]] = {}
        self.fail = fail
        self.closed = False

    def hincrby(self, key: str, field: str, amount: int = 1) -> int:
        if self.fail:
            raise RuntimeError("redis caído")
        bucket = self.hashes.setdefault(key, {})
        bucket[field] = bucket.get(field, 0) + amount
        return int(bucket[field])

    def hincrbyfloat(self, key: str, field: str, amount: float = 1.0) -> float:
        if self.fail:
            raise RuntimeError("redis caído")
        bucket = self.hashes.setdefault(key, {})
        bucket[field] = bucket.get(field, 0.0) + amount
        return float(bucket[field])

    def hgetall(self, key: str) -> dict[bytes, bytes]:
        if self.fail:
            raise RuntimeError("redis caído")
        return {k.encode(): str(v).encode() for k, v in self.hashes.get(key, {}).items()}

    def close(self) -> None:
        self.closed = True


class _Request:
    def __init__(self, routing_key: str | None) -> None:
        self.delivery_info = {"routing_key": routing_key} if routing_key else {}


class _Sender:
    def __init__(self, routing_key: str | None = "default", name: str = "workers.run") -> None:
        self.request = _Request(routing_key)
        self.name = name


# ---------------------------------------------------------------------------
# Acumulación en Redis
# ---------------------------------------------------------------------------
def test_a_finished_task_is_counted_by_queue_and_status() -> None:
    from workers.task_metrics import DURATION_HASH_KEY, TASKS_HASH_KEY, record_task_outcome

    redis = _FakeRedis()
    assert record_task_outcome(redis, queue="review", status="success", duration_s=2.5) is True

    assert redis.hashes[TASKS_HASH_KEY] == {"review|success": 1}
    assert redis.hashes[DURATION_HASH_KEY] == {"review": 2.5}


def test_failures_are_a_separate_series_from_successes() -> None:
    """El punto entero de la métrica: `queue_depth` no distingue un worker sano
    de uno que consume la cola y falla todo."""
    from workers.task_metrics import TASKS_HASH_KEY, record_task_outcome

    redis = _FakeRedis()
    record_task_outcome(redis, queue="default", status="success", duration_s=1.0)
    record_task_outcome(redis, queue="default", status="failure", duration_s=0.5)
    record_task_outcome(redis, queue="default", status="failure", duration_s=0.5)

    assert redis.hashes[TASKS_HASH_KEY] == {"default|success": 1, "default|failure": 2}


def test_a_redis_failure_never_breaks_the_task() -> None:
    """Best-effort: emitir una métrica no puede tumbar el trabajo real."""
    from workers.task_metrics import record_task_outcome

    assert record_task_outcome(_FakeRedis(fail=True), queue="q", status="success") is False


def test_counters_are_parsed_back_from_redis_bytes() -> None:
    from workers.task_metrics import read_task_counters

    redis = _FakeRedis()
    redis.hashes["agentic:metrics:celery:tasks"] = {"default|success": 3, "test|failure": 1}
    redis.hashes["agentic:metrics:celery:duration"] = {"default": 12.5}

    counts, durations = read_task_counters(redis)

    # redis-py devuelve bytes salvo que se configure decode_responses; el parser
    # debe tragarse ambos.
    assert counts == {("default", "success"): 3, ("test", "failure"): 1}
    assert durations == {"default": 12.5}


def test_malformed_fields_are_skipped_not_crashed() -> None:
    """Una clave basura en Redis (otro proceso, una migración a medias) no puede
    tirar el muestreo entero y llevarse por delante las métricas buenas."""
    from workers.task_metrics import read_task_counters

    redis = _FakeRedis()
    redis.hashes["agentic:metrics:celery:tasks"] = {"sin-separador": 9, "default|success": 2}

    counts, _durations = read_task_counters(redis)

    assert counts == {("default", "success"): 2}


# ---------------------------------------------------------------------------
# Cableado de las señales
# ---------------------------------------------------------------------------
def _receiver_names(signal: Any) -> list[str]:
    """Nombres de los receptores conectados a una señal de Celery.

    ``Signal.receivers`` guarda ``(clave, receptor)``; con ``weak=True`` el
    receptor es un weakref (hay que dereferenciarlo) y con ``weak=False`` es la
    propia función. Se cubren los dos porque confundirlos hace que el test mire
    una lista vacía y apruebe cualquier cosa.
    """
    names: list[str] = []
    for _key, receiver in signal.receivers:
        target = receiver() if isinstance(receiver, ReferenceType) else receiver
        names.append(getattr(target, "__name__", ""))
    return names


def test_the_signal_handlers_are_actually_connected() -> None:
    """El modo de fallo nº1 de esta base: mecanismo entregado, cero llamantes.

    Que exista `record_task_outcome` no sirve de nada si nadie lo llama, así que
    se comprueba que `install_task_metrics()` deja los receptores enganchados en
    las señales REALES de Celery, no solo que la función exista.
    """
    from celery.signals import task_failure, task_postrun, task_prerun
    from workers.task_metrics import install_task_metrics, uninstall_task_metrics

    install_task_metrics()
    try:
        for signal in (task_prerun, task_postrun, task_failure):
            names = _receiver_names(signal)
            assert any(name.startswith("_on_task_") for name in names), (
                f"la señal {signal.name} quedó sin receptor: {names}"
            )
    finally:
        uninstall_task_metrics()
        install_task_metrics()  # restaurar: la app real las quiere puestas


def test_install_is_idempotent() -> None:
    """El módulo se importa desde `celery_app`, que a su vez importa media base;
    una doble instalación contaría cada tarea dos veces."""
    from celery.signals import task_postrun
    from workers.task_metrics import install_task_metrics, uninstall_task_metrics

    install_task_metrics()
    install_task_metrics()
    try:
        ours = [n for n in _receiver_names(task_postrun) if n.startswith("_on_task_")]
        assert len(ours) == 1, f"receptores duplicados: {len(ours)}"
    finally:
        uninstall_task_metrics()
        install_task_metrics()


def test_the_handlers_measure_duration_between_prerun_and_postrun(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from workers import task_metrics

    redis = _FakeRedis()
    monkeypatch.setattr(task_metrics, "_metrics_redis", lambda: redis)
    clock = iter([100.0, 103.5])
    monkeypatch.setattr(task_metrics.time, "monotonic", lambda: next(clock))

    sender = _Sender(routing_key="review")
    task_metrics._on_task_prerun(sender=sender, task_id="t-1")
    task_metrics._on_task_postrun(sender=sender, task_id="t-1", state="SUCCESS")

    assert redis.hashes[task_metrics.TASKS_HASH_KEY] == {"review|success": 1}
    assert redis.hashes[task_metrics.DURATION_HASH_KEY]["review"] == pytest.approx(3.5)


def test_a_failed_task_is_recorded_once_not_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    """Celery emite `task_failure` Y DESPUÉS `task_postrun` para la misma tarea.

    Contar en ambas duplicaría cada fallo e inflaría el total, que es el
    denominador de la tasa de fallo. `task_failure` solo marca el estado; quien
    cuenta es siempre `task_postrun`.
    """
    from workers import task_metrics

    redis = _FakeRedis()
    monkeypatch.setattr(task_metrics, "_metrics_redis", lambda: redis)

    sender = _Sender(routing_key="default")
    task_metrics._on_task_prerun(sender=sender, task_id="t-2")
    task_metrics._on_task_failure(sender=sender, task_id="t-2")
    task_metrics._on_task_postrun(sender=sender, task_id="t-2", state="SUCCESS")

    # Una sola muestra, y con el estado que la señal de fallo impuso — no el
    # `SUCCESS` que Celery pasa a postrun cuando el retval quedó a medias.
    assert redis.hashes[task_metrics.TASKS_HASH_KEY] == {"default|failure": 1}


def test_an_unrouted_task_does_not_invent_a_queue_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sin `delivery_info` (llamada eager/directa) la cola es DESCONOCIDA.

    Etiquetarla como `default` mezclaría dos poblaciones distintas en la misma
    serie y haría mentir a `CeleryQueueGrowing`.
    """
    from workers import task_metrics

    redis = _FakeRedis()
    monkeypatch.setattr(task_metrics, "_metrics_redis", lambda: redis)

    sender = _Sender(routing_key=None)
    task_metrics._on_task_prerun(sender=sender, task_id="t-3")
    task_metrics._on_task_postrun(sender=sender, task_id="t-3", state="SUCCESS")

    assert task_metrics.UNKNOWN_QUEUE in next(iter(redis.hashes[task_metrics.TASKS_HASH_KEY]))


def test_pending_starts_do_not_grow_without_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    """Si `postrun` no llega (worker SIGKILLed a media tarea) el `prerun` queda
    huérfano. Un dict que solo crece es una fuga de memoria en un proceso que
    vive semanas."""
    from workers import task_metrics

    redis = _FakeRedis()
    monkeypatch.setattr(task_metrics, "_metrics_redis", lambda: redis)
    task_metrics._pending_starts.clear()

    sender = _Sender()
    for i in range(task_metrics.MAX_PENDING_STARTS + 50):
        task_metrics._on_task_prerun(sender=sender, task_id=f"orphan-{i}")

    assert len(task_metrics._pending_starts) <= task_metrics.MAX_PENDING_STARTS


# ---------------------------------------------------------------------------
# Render (formato de exposición) — lo consume el textfile-collector
# ---------------------------------------------------------------------------
def test_render_emits_the_celery_task_counters() -> None:
    from workers.queue_metrics import (
        METRIC_TASK_DURATION_TOTAL,
        METRIC_TASKS_TOTAL,
        render_queue_metrics,
    )

    body = render_queue_metrics(
        queue_depths={},
        status_counts={},
        task_counts={("default", "success"): 12, ("review", "failure"): 3},
        task_durations={"default": 61.25},
    )

    assert f"# TYPE {METRIC_TASKS_TOTAL} counter" in body
    assert f'{METRIC_TASKS_TOTAL}{{queue="default",status="success"}} 12' in body
    assert f'{METRIC_TASKS_TOTAL}{{queue="review",status="failure"}} 3' in body
    assert f"# TYPE {METRIC_TASK_DURATION_TOTAL} counter" in body
    assert f'{METRIC_TASK_DURATION_TOTAL}{{queue="default"}} 61.25' in body


def test_render_omits_the_celery_counters_when_not_sampled() -> None:
    """Ausencia ≠ cero: si el colector falló, la familia no aparece (y
    `agentic_sampler_collector_up` lo delata)."""
    from workers.queue_metrics import METRIC_TASKS_TOTAL, render_queue_metrics

    body = render_queue_metrics(queue_depths={}, status_counts={})

    assert METRIC_TASKS_TOTAL not in body


def test_celery_tasks_is_a_known_collector() -> None:
    """Sin esto el gauge `collector_up` no cubriría el colector nuevo y su fallo
    volvería a ser mudo (justo lo que AUD16-09 arregló para los otros cuatro)."""
    from workers.queue_metrics import KNOWN_COLLECTORS

    assert "celery_tasks" in KNOWN_COLLECTORS


# ---------------------------------------------------------------------------
# El sampler lo recoge de verdad
# ---------------------------------------------------------------------------
class _AsyncFakeRedis:
    """Vista async del mismo almacén (el sampler usa ``redis.asyncio``)."""

    def __init__(self, backing: _FakeRedis) -> None:
        self.backing = backing

    async def hgetall(self, key: str) -> dict[bytes, bytes]:
        return self.backing.hgetall(key)


@pytest.mark.asyncio
async def test_the_sampler_collects_the_celery_counters() -> None:
    """Punta a punta del lado emisor: señal → Redis → colector del sampler.

    Sin este test, `record_task_outcome` podría estar perfecto y el fichero
    `.prom` seguir sin la métrica — el patrón «mecanismo entregado, cero
    llamantes» que la guía de verificación señala como dominante en esta base.
    """
    from workers import task_metrics
    from workers.maintenance.queue_sampler import _collect_celery_task_counters
    from workers.queue_metrics import render_queue_metrics

    redis = _FakeRedis()
    task_metrics.record_task_outcome(redis, queue="default", status="success", duration_s=4.0)
    task_metrics.record_task_outcome(redis, queue="default", status="failure", duration_s=1.0)

    counts, durations = await _collect_celery_task_counters(_AsyncFakeRedis(redis))
    assert counts, "el colector no devolvió nada; el test pasaría en vacío"

    body = render_queue_metrics(
        queue_depths={},
        status_counts={},
        task_counts=counts,
        task_durations=durations,
    )
    assert 'agentic_celery_tasks_total{queue="default",status="failure"} 1' in body
    assert 'agentic_celery_task_duration_seconds_total{queue="default"} 5' in body


def test_a_broken_settings_lookup_never_escapes_the_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`record_task_outcome` se traga los fallos de Redis, pero sus ARGUMENTOS se
    evalúan antes de entrar en él.

    Si `_metrics_redis()` revienta (settings incompletos, URL de broker mal
    formada), la excepción escapa por la señal `task_postrun` y se lleva por
    delante la tarea real. Una métrica jamás puede tumbar el trabajo que mide.
    """
    from workers import task_metrics

    def _boom() -> Any:
        raise RuntimeError("settings rotos")

    monkeypatch.setattr(task_metrics, "_metrics_redis", _boom)

    sender = _Sender()
    task_metrics._on_task_prerun(sender=sender, task_id="t-boom")
    task_metrics._on_task_postrun(sender=sender, task_id="t-boom", state="SUCCESS")
