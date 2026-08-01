"""Queue-depth + task-state metrics for Prometheus (prod-06 task_prod06_dag_03, parte B).

The autonomous execution loop can stall in ways that are invisible without
observability: messages piling up in a Celery queue (no worker draining it), or
tasks accumulating in a lifecycle state (e.g. a growing ``in_review`` backlog —
exactly the gap the reviewer-bridge wiring of dag_03's part A closes). prod-08
owns the scrape job, the alert rules (``CeleryQueueGrowing``) and the dashboard;
this module only EMITS the samples it consumes.

On a single-machine Docker host the dependency-free way to surface app metrics to
Prometheus is the **node-exporter textfile collector** — the same pattern as
:mod:`workers.backup_metrics`. A beat task samples the metrics and drops a
``*.prom`` file into the directory node-exporter watches via the shared
:func:`workers.textfile_collector.write_textfile_metric` helper (atomic write +
"sink-absent is expected, not a fault" semantics — see that module).

Exposed samples
---------------
``agentic_celery_queue_depth{queue}`` (gauge)
    Messages waiting in each Celery queue (Redis ``LLEN`` of the queue's list).

``agentic_tasks_by_status{status}`` (gauge)
    Count of non-deleted ``tasks`` rows in each lifecycle status, across tenants.

Best-effort: emitting metrics must never break the worker.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Any

from workers.textfile_collector import write_textfile_metric

# Metric names — namespace ``agentic_`` (mirrors agentic_backup_*). prod-08's
# scrape rules / dashboards reference these; keep in sync.
METRIC_QUEUE_DEPTH = "agentic_celery_queue_depth"
METRIC_TASKS_BY_STATUS = "agentic_tasks_by_status"
# prod-08 núcleo (2026-07-12): profundidad de los streams DLQ (XLEN) — un
# mensaje dead-lettered es trabajo PERDIDO hasta que un humano lo mira.
METRIC_DLQ_DEPTH = "agentic_dlq_depth"
# FASE 2b monitorización (2026-07-12): actividad real de runs — ejecuciones por
# estado en las últimas 24h (el pulso del sistema agéntico para el dashboard).
METRIC_EXECUTIONS_24H = "agentic_executions_24h"
# AUD16-09 (auditoría 2026-07-16): «No data» era indistinguible de «sampler
# muerto». El heartbeat alimenta la regla de staleness (MetricsSamplerStale) y
# el gauge up 1/0 por colector hace visible un colector que falló ESTA pasada
# — la ausencia de una familia deja de ser muda.
METRIC_SAMPLER_LAST_RUN = "agentic_sampler_last_run_timestamp_seconds"
METRIC_COLLECTOR_UP = "agentic_sampler_collector_up"
# prod-08 task_prod08_metrics_workers_05: RESULTADO de las tareas Celery, no
# solo cuántas esperan. La profundidad de cola no distingue un worker sano de
# uno que consume la cola y falla todo — en los dos casos vale cero. Los
# acumulan las señales de Celery en Redis (`workers/task_metrics.py`), porque el
# pool prefork descarta un exporter HTTP por proceso (ADR 0141).
METRIC_TASKS_TOTAL = "agentic_celery_tasks_total"
METRIC_TASK_DURATION_TOTAL = "agentic_celery_task_duration_seconds_total"
# prod-08 Fase B: aprobaciones humanas pendientes. Cuando un agente pide
# aprobación, su ejecución se PARA hasta que alguien responde; una petición
# olvidada no produce error, ni log de fallo, ni cola creciendo. El contador
# solo, además, no distingue «tres recién pedidas» de «tres olvidadas hace una
# semana» — de ahí el segundo gauge, que es sobre el que alerta
# `HumanApprovalsStale`.
METRIC_APPROVALS_PENDING = "agentic_human_approvals_pending"
METRIC_APPROVALS_OLDEST_AGE = "agentic_human_approvals_oldest_age_seconds"
# prod-08 Fase B: gasto LLM. Son DOS familias y no una porque las fuentes no son
# intercambiables: `llm_usage_events` tiene `provider_kind` pero solo cubre
# asistente, córtex y planning; el gasto del pipeline de runs vive en
# `executions.total_cost_usd`, que NO tiene columna de proveedor. Fundirlas bajo
# un único `{provider}` repartiría el gasto de los runs entre proveedores
# inventados; publicar solo la primera presentaría como «coste LLM» una fracción
# del real. Dos métricas honestas antes que una cómoda y falsa.
METRIC_LLM_TOKENS_24H = "agentic_llm_tokens_24h"
METRIC_LLM_COST_24H = "agentic_llm_cost_usd_24h"
METRIC_RUN_TOKENS_24H = "agentic_run_tokens_24h"
METRIC_RUN_COST_24H = "agentic_run_cost_usd_24h"
# prod-07 task_prod07_15 (llm-10): racha de destilaciones fallidas del
# Memorizer. La memorización es best-effort por diseño —se traga sus
# excepciones para no tumbar el pipeline—, así que un destilador caído se
# manifiesta como «nadie aprende nada» y ninguna otra señal. Consecutivos, no
# acumulados: lo que importa es si está roto AHORA.
METRIC_MEMORIZER_FAILURES = "agentic_memorizer_consecutive_distill_failures"
KNOWN_COLLECTORS: tuple[str, ...] = (
    "queue_depths",
    "tasks_by_status",
    "dlq_depth",
    "executions_24h",
    "celery_tasks",
    "approvals",
    "llm_spend",
    "memorizer",
)


def _escape_label(value: str) -> str:
    """Escape a Prometheus label value (backslash, double-quote, newline)."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _format_float(value: float) -> str:
    """Un float sin notación científica ni ceros de cola.

    ``str(1e6)`` da ``1000000.0`` pero ``f"{1e6:g}"`` da ``1e+06``; ambos son
    legales en el formato de exposición, pero el segundo hace ilegible el diff
    del fichero. Se fija a 3 decimales (milisegundos, de sobra para una suma de
    duraciones) y se recorta.
    """
    text = f"{value:.3f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _labels(**pairs: str) -> str:
    """El bloque ``{k="v",…}`` de una muestra, con los valores escapados."""
    if not pairs:
        return ""
    return "{" + ",".join(f'{k}="{_escape_label(v)}"' for k, v in pairs.items()) + "}"


def _family(
    name: str,
    help_text: str,
    kind: str,
    samples: Iterable[tuple[str, Any]],
) -> list[str]:
    """Las líneas de una familia: cabeceras ``HELP``/``TYPE`` + sus muestras.

    Se extrajo cuando el render pasó de tres familias a nueve: repetir el par de
    cabeceras a mano en cada bloque es justo donde se cuela un ``# TYPE`` con el
    nombre de la métrica de al lado, y Prometheus se traga esa incoherencia sin
    quejarse.
    """
    return [
        f"# HELP {name} {help_text}",
        f"# TYPE {name} {kind}",
        *(f"{name}{labels} {value}" for labels, value in samples),
    ]


def render_queue_metrics(
    *,
    queue_depths: dict[str, int],
    status_counts: dict[str, int],
    dlq_depths: dict[str, int] | None = None,
    execution_counts: dict[str, int] | None = None,
    sampled_at: float | None = None,
    collector_failures: frozenset[str] | set[str] | None = None,
    task_counts: dict[tuple[str, str], int] | None = None,
    task_durations: dict[str, float] | None = None,
    approvals_pending: int | None = None,
    approvals_oldest_age_s: float | None = None,
    llm_usage: dict[str, tuple[int, float]] | None = None,
    run_tokens: int | None = None,
    run_cost_usd: float | None = None,
    memorizer_failures: int | None = None,
) -> str:
    """Render the Prometheus text-exposition body. Pure (no I/O) so it is
    unit-testable. Keys are emitted in sorted order for a noise-free file diff.

    AUD16-09: con ``sampled_at`` (unix seconds, lo pasa el sampler) se emiten
    además el heartbeat ``agentic_sampler_last_run_timestamp_seconds`` y un
    ``agentic_sampler_collector_up{collector}`` 1/0 por colector conocido
    (0 = ese colector falló en esta pasada). Sin ``sampled_at`` la salida es
    byte a byte la histórica (callers/tests legacy intactos)."""
    lines = _family(
        METRIC_QUEUE_DEPTH,
        "Messages waiting in each Celery queue (Redis LLEN).",
        "gauge",
        ((_labels(queue=q), queue_depths[q]) for q in sorted(queue_depths)),
    )
    lines += _family(
        METRIC_TASKS_BY_STATUS,
        "Non-deleted tasks in each lifecycle status (all tenants).",
        "gauge",
        ((_labels(status=s), status_counts[s]) for s in sorted(status_counts)),
    )
    if dlq_depths:
        lines += _family(
            METRIC_DLQ_DEPTH,
            "Entries in each dead-letter stream (Redis XLEN).",
            "gauge",
            ((_labels(stream=s), dlq_depths[s]) for s in sorted(dlq_depths)),
        )
    if execution_counts:
        lines += _family(
            METRIC_EXECUTIONS_24H,
            "Executions per terminal status, last 24 hours.",
            "gauge",
            ((_labels(status=s), execution_counts[s]) for s in sorted(execution_counts)),
        )
    if task_counts:
        lines += _family(
            METRIC_TASKS_TOTAL,
            "Celery tasks finished, by queue and outcome.",
            "counter",
            ((_labels(queue=q, status=s), task_counts[(q, s)]) for q, s in sorted(task_counts)),
        )
    if task_durations:
        lines += _family(
            METRIC_TASK_DURATION_TOTAL,
            "Accumulated Celery task runtime, by queue.",
            "counter",
            ((_labels(queue=q), _format_float(task_durations[q])) for q in sorted(task_durations)),
        )
    # `is not None` y no truthiness: cero pendientes es el estado SANO y es un
    # dato. Omitirlo dejaría a Prometheus sin poder distinguir «nadie espera»
    # de «el colector se cayó», y `HumanApprovalsStale` dejaría de evaluarse en
    # silencio — exactamente el defecto que este plan corrige.
    if approvals_pending is not None:
        lines += _family(
            METRIC_APPROVALS_PENDING,
            "Human approval requests still pending.",
            "gauge",
            [("", approvals_pending)],
        )
    if approvals_oldest_age_s is not None:
        lines += _family(
            METRIC_APPROVALS_OLDEST_AGE,
            "Age of the oldest pending approval, seconds.",
            "gauge",
            [("", _format_float(approvals_oldest_age_s))],
        )
    if llm_usage:
        lines += _family(
            METRIC_LLM_TOKENS_24H,
            "Non-run LLM tokens (assistant/cortex/planning), last 24 hours.",
            "gauge",
            ((_labels(provider=p), llm_usage[p][0]) for p in sorted(llm_usage)),
        )
        lines += _family(
            METRIC_LLM_COST_24H,
            "Non-run LLM spend in USD, last 24 hours.",
            "gauge",
            ((_labels(provider=p), _format_float(llm_usage[p][1])) for p in sorted(llm_usage)),
        )
    if run_tokens is not None:
        lines += _family(
            METRIC_RUN_TOKENS_24H,
            "Tokens consumed by agent runs, last 24 hours (no provider dimension).",
            "gauge",
            [("", run_tokens)],
        )
    if run_cost_usd is not None:
        lines += _family(
            METRIC_RUN_COST_24H,
            "Agent-run spend in USD, last 24 hours (no provider dimension).",
            "gauge",
            [("", _format_float(run_cost_usd))],
        )
    # `is not None`: cero es el estado SANO y es un dato. Omitirlo dejaría a
    # Prometheus sin distinguir «el destilador va bien» de «el colector cayó».
    if memorizer_failures is not None:
        lines += _family(
            METRIC_MEMORIZER_FAILURES,
            "Consecutive failed Memorizer distillations (0 = healthy).",
            "gauge",
            [("", memorizer_failures)],
        )
    if sampled_at is not None:
        failures = collector_failures or frozenset()
        lines += _family(
            METRIC_SAMPLER_LAST_RUN,
            "Unix timestamp of the last sampler run.",
            "gauge",
            [("", int(sampled_at))],
        )
        lines += _family(
            METRIC_COLLECTOR_UP,
            "1 if the collector succeeded on the last run.",
            "gauge",
            ((_labels(collector=c), 0 if c in failures else 1) for c in KNOWN_COLLECTORS),
        )
    return "\n".join(lines) + "\n"


def write_queue_metrics(
    path: str | os.PathLike[str],
    *,
    queue_depths: dict[str, int],
    status_counts: dict[str, int],
    dlq_depths: dict[str, int] | None = None,
    execution_counts: dict[str, int] | None = None,
    sampled_at: float | None = None,
    collector_failures: frozenset[str] | set[str] | None = None,
    task_counts: dict[tuple[str, str], int] | None = None,
    task_durations: dict[str, float] | None = None,
    approvals_pending: int | None = None,
    approvals_oldest_age_s: float | None = None,
    llm_usage: dict[str, tuple[int, float]] | None = None,
    run_tokens: int | None = None,
    run_cost_usd: float | None = None,
    memorizer_failures: int | None = None,
) -> bool:
    """Atomically write the queue-metrics file. Returns ``True`` on success,
    ``False`` otherwise — best-effort, never breaks the worker. Delegates the
    publish (and the sink-absent-vs-real-failure log semantics) to
    :func:`workers.textfile_collector.write_textfile_metric`."""
    return write_textfile_metric(
        path,
        lambda: render_queue_metrics(
            queue_depths=queue_depths,
            status_counts=status_counts,
            dlq_depths=dlq_depths,
            execution_counts=execution_counts,
            sampled_at=sampled_at,
            collector_failures=collector_failures,
            task_counts=task_counts,
            task_durations=task_durations,
            approvals_pending=approvals_pending,
            approvals_oldest_age_s=approvals_oldest_age_s,
            llm_usage=llm_usage,
            run_tokens=run_tokens,
            run_cost_usd=run_cost_usd,
            memorizer_failures=memorizer_failures,
        ),
        event_prefix="queue_metrics",
    )
