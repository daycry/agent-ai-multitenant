"""Córtex F4 — métricas del bucle de curiosidad (ADR 0078, Sub-fase 4.6).

El bucle de curiosidad gasta dinero y egress cada 30 minutos sin que nadie lo mire,
y hasta ahora **no dejaba ni una serie temporal**: para saber si funcionaba había
que abrir la BD y contar filas de ``cortex_curiosity_pursuits`` a mano. Peor: un
bucle silencioso porque nadie encendió el kill-switch era indistinguible de un bucle
silencioso porque el circuit-breaker está abierto.

Las cuatro métricas del plan, publicadas por el **textfile-collector de
node-exporter** (mismo patrón dependency-free de :mod:`workers.backup_metrics`, ver
:mod:`workers.textfile_collector` para las dos fases y sus niveles de log):

``agentic_cortex_curiosity_runs_total{outcome}`` (counter)
    Pasadas del bucle por resultado. La etiqueta viene de :func:`outcome_of` y su
    conjunto es CERRADO (:data:`KNOWN_OUTCOMES`) — cardinalidad abierta en Prometheus
    es una fuga de memoria. Es la métrica que distingue "apagado" de "sin budget" de
    "el breaker está abierto".

``agentic_cortex_curiosity_cost_usd_total`` (counter)
    Gasto acumulado en dólares. Con decimales: una pasada cuesta céntimos, y
    redondear a entero perdería el gasto entero.

``agentic_cortex_curiosity_searches_total`` (counter)
    Salidas a Internet acumuladas (la dimensión de egress del budget).

``agentic_cortex_curiosity_circuit_open`` (gauge)
    1 cuando el circuit-breaker está abierto al terminar la pasada. NO acumula:
    refleja el estado actual.

## Por qué el fichero se lee antes de escribirse

Un ``*_total`` que se reinicia en cada pasada no es un contador: ``rate()`` daría
cero y ninguna alerta armaría. Pero el textfile-collector reescribe el fichero
completo en cada publicación y el proceso del worker no sobrevive entre pasadas
(cada tarea Celery es un proceso corto), así que **el fichero es el único estado que
hay**: se lee lo publicado, se suma lo de esta pasada y se reescribe. Un fichero
corrupto o pisado se ignora y la cuenta empieza de nuevo — mejor perder el histórico
que dejar de emitir para siempre.

Best-effort: un fallo al emitir NUNCA rompe el bucle.

> Nota de carril: la ruta del ``.prom`` vive aquí (constante + override por env con
> el nombre que tendría el setting: ``WORKERS_CORTEX_CURIOSITY_METRICS_TEXTFILE_PATH``)
> en vez de en ``workers/config.py`` como sus hermanas ``backup_metrics_textfile_path``
> / ``queue_metrics_textfile_path``. Cuando ese campo se añada a ``Settings``, el env
> var ya coincide y basta con pasar ``path=`` desde el caller.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import structlog

from workers.textfile_collector import write_textfile_metric

_log = structlog.get_logger("workers.cortex_curiosity_metrics")

METRIC_RUNS = "agentic_cortex_curiosity_runs_total"
METRIC_COST_USD = "agentic_cortex_curiosity_cost_usd_total"
METRIC_SEARCHES = "agentic_cortex_curiosity_searches_total"
METRIC_CIRCUIT_OPEN = "agentic_cortex_curiosity_circuit_open"

#: Ruta por defecto del `.prom` (mismo dir que las métricas de backup/colas).
DEFAULT_METRICS_PATH = "/host/textfile/agentic_cortex_curiosity.prom"
#: Env var de override. El nombre es el que tendría el campo en ``Settings``
#: (prefijo ``WORKERS_``), para que añadirlo allí no cambie la configuración viva.
METRICS_PATH_ENV = "WORKERS_CORTEX_CURIOSITY_METRICS_TEXTFILE_PATH"

#: Conjunto CERRADO de valores de la etiqueta ``outcome``. Uno por rama observable
#: del bucle (la aceptación del plan pide «cada rama del gate observable»), más
#: ``unknown`` como red de seguridad para que un dict de retorno nuevo no levante.
KNOWN_OUTCOMES: tuple[str, ...] = (
    "digested",
    "pending_approval",
    "awaiting_approval",
    "empty_digest",
    "budget",
    "no_topic",
    "no_owner",
    "drive_satisfied",
    "circuit_open",
    "disabled",
    "curiosity_disabled",
    "web_disabled",
    "failed",
    "error",
    "unknown",
)


#: Ramas del bucle que se señalizan con una CLAVE propia en el dict de retorno (las
#: demás llegan como ``{"skipped": "<motivo>"}``). El orden es el de precedencia,
#: aunque en la práctica solo una está presente por pasada.
_FLAG_OUTCOMES: tuple[str, ...] = (
    "digested",
    "pending_approval",
    "awaiting_approval",
    "failed",
    "error",
)


def metrics_path() -> Path:
    """La ruta del ``.prom`` de la curiosidad (env override o el default)."""
    return Path(os.environ.get(METRICS_PATH_ENV) or DEFAULT_METRICS_PATH)


def _escape_label(value: str) -> str:
    """Escapa un valor de etiqueta Prometheus (backslash, comilla, salto)."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def outcome_of(result: Mapping[str, Any]) -> str:
    """Traduce el dict de retorno de una pasada a la etiqueta ``outcome`` (PURA).

    El bucle devuelve una forma distinta por rama (``{"digested": True}``,
    ``{"skipped": "budget", ...}``, ``{"failed": ...}``, ``{"error": ...}``); aquí se
    aplana a una etiqueta del conjunto cerrado. Un dict que no se reconoce da
    ``unknown`` en vez de levantar: la contabilidad de un bucle de fondo no puede
    convertir un cambio inocente del retorno en un fallo de beat."""
    skipped = result.get("skipped")
    if isinstance(skipped, str) and skipped in KNOWN_OUTCOMES:
        return skipped
    for flag in _FLAG_OUTCOMES:
        if result.get(flag):
            return flag
    return "unknown"


def circuit_open_from_result(result: Mapping[str, Any]) -> bool:
    """¿Queda el circuit-breaker abierto al terminar la pasada? (PURA).

    Dos caminos: o ya estaba abierto (la pasada salió por ``circuit_open``), o este
    fallo lo acaba de abrir (``cb_opened``, que la rama de fallo del bucle reporta).
    Se deduce del resultado en vez de volver a preguntar a Redis: una segunda
    consulta podría contradecir lo que la pasada hizo (y costaría otra ida y vuelta
    por una métrica)."""
    if result.get("skipped") == "circuit_open":
        return True
    return bool(result.get("cb_opened"))


def render_curiosity_metrics(
    *,
    runs_by_outcome: Mapping[str, int],
    cost_usd_total: float,
    searches_total: int,
    circuit_open: bool,
) -> str:
    """Renderiza el cuerpo Prometheus. PURA (sin I/O) y con orden estable.

    Las etiquetas salen ordenadas alfabéticamente: el fichero se reescribe entero en
    cada pasada y sin orden estable el diff cambiaría sin que cambiase nada."""
    lines = [
        f"# HELP {METRIC_RUNS} Pasadas del bucle de curiosidad del córtex, por resultado.",
        f"# TYPE {METRIC_RUNS} counter",
    ]
    for outcome in sorted(runs_by_outcome):
        lines.append(
            f'{METRIC_RUNS}{{outcome="{_escape_label(outcome)}"}} {runs_by_outcome[outcome]}'
        )
    lines.extend(
        [
            f"# HELP {METRIC_COST_USD} Gasto acumulado (USD) de la curiosidad autónoma.",
            f"# TYPE {METRIC_COST_USD} counter",
            f"{METRIC_COST_USD} {max(0.0, cost_usd_total):.6f}",
            f"# HELP {METRIC_SEARCHES} Búsquedas web acumuladas de la curiosidad autónoma.",
            f"# TYPE {METRIC_SEARCHES} counter",
            f"{METRIC_SEARCHES} {max(0, searches_total)}",
            f"# HELP {METRIC_CIRCUIT_OPEN} 1 si el circuit-breaker de la curiosidad está abierto.",
            f"# TYPE {METRIC_CIRCUIT_OPEN} gauge",
            f"{METRIC_CIRCUIT_OPEN} {1 if circuit_open else 0}",
        ]
    )
    return "\n".join(lines) + "\n"


def _read_published(path: Path) -> tuple[dict[str, int], float, int]:
    """Lee los contadores ya publicados: ``(runs_por_outcome, coste, búsquedas)``.

    El fichero es el único estado entre pasadas (cada tarea Celery es un proceso
    corto). Cualquier línea que no parsee se ignora — el ``.prom`` es un fichero
    suelto en un volumen compartido y un parseo estricto haría que una corrupción
    dejase la métrica muerta para siempre."""
    runs: dict[str, int] = {}
    cost = 0.0
    searches = 0
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return runs, cost, searches
    for line in text.splitlines():
        if line.startswith("#") or " " not in line:
            continue
        name, _, raw = line.partition(" ")
        try:
            if name.startswith(f'{METRIC_RUNS}{{outcome="'):
                label = name[len(METRIC_RUNS) + len('{outcome="') : -2]
                runs[label] = runs.get(label, 0) + int(raw)
            elif name == METRIC_COST_USD:
                cost += float(raw)
            elif name == METRIC_SEARCHES:
                searches += int(raw)
        except ValueError:  # línea pisada/truncada: se ignora, no rompe
            continue
    return runs, cost, searches


def write_curiosity_metrics(
    path: str | os.PathLike[str],
    *,
    outcome: str,
    cost_usd: float,
    searches: int,
    circuit_open: bool,
) -> bool:
    """Publica las métricas de UNA pasada, acumulando sobre lo ya publicado.

    Los ``*_total`` se suman a lo que hubiera en el fichero; el gauge del breaker se
    reemplaza (refleja el estado actual). Escritura atómica y no-op silencioso si el
    dir del collector no está aprovisionado, vía
    :func:`workers.textfile_collector.write_textfile_metric`.

    Devuelve ``True`` si se publicó. Best-effort: nunca levanta."""
    target = Path(path)

    def _render() -> str:
        # Se llama solo cuando el dir del collector existe (fase 1 del helper).
        runs, prev_cost, prev_searches = _read_published(target)
        runs[outcome] = runs.get(outcome, 0) + 1
        return render_curiosity_metrics(
            runs_by_outcome=runs,
            cost_usd_total=prev_cost + max(0.0, cost_usd),
            searches_total=prev_searches + max(0, searches),
            circuit_open=circuit_open,
        )

    ok = write_textfile_metric(target, _render, event_prefix="cortex_curiosity.metrics")
    if ok:
        _log.debug(
            "cortex_curiosity.metrics.written",
            path=str(target),
            outcome=outcome,
            cost_usd=cost_usd,
            searches=searches,
            circuit_open=circuit_open,
        )
    return ok


def publish_pass_metrics(result: Mapping[str, Any]) -> bool:
    """Emite las métricas de una pasada a partir de su dict de retorno.

    El punto de entrada que usa la tarea Celery: traduce el resultado a etiqueta,
    saca el coste/búsquedas de la pasada y el estado del breaker, y publica.
    Best-effort de punta a punta — una métrica no puede tumbar el bucle."""
    try:
        return write_curiosity_metrics(
            metrics_path(),
            outcome=outcome_of(result),
            cost_usd=float(result.get("cost_usd") or 0.0),
            searches=int(result.get("search_count") or 0),
            circuit_open=circuit_open_from_result(result),
        )
    except Exception as exc:  # pragma: no cover - defensa de última línea
        _log.warning("cortex_curiosity.metrics.failed", error=str(exc))
        return False


__all__ = [
    "DEFAULT_METRICS_PATH",
    "KNOWN_OUTCOMES",
    "METRICS_PATH_ENV",
    "METRIC_CIRCUIT_OPEN",
    "METRIC_COST_USD",
    "METRIC_RUNS",
    "METRIC_SEARCHES",
    "circuit_open_from_result",
    "metrics_path",
    "outcome_of",
    "publish_pass_metrics",
    "render_curiosity_metrics",
    "write_curiosity_metrics",
]
