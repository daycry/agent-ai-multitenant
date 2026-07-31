"""Dashboards de Grafana versionados en el repo (prod-08 Fase B, task 07).

Los dashboards se provisionan desde ficheros (``provisioning/dashboards.yml``),
nunca editando en la UI: una edición manual se pierde al recrear el contenedor
y no deja rastro en el repo.

Dos comprobaciones, y otra vez la segunda es la que tiene dientes:

* **JSON válido** con la forma que el provisioner de Grafana espera. Un JSON
  malformado no rompe Grafana: simplemente **no carga ese dashboard** y lo
  anota en un log que nadie mira. El panel desaparece en silencio.
* **Que los paneles consulten métricas que existen** — criterio de cierre 4 del
  plan. Un panel apuntando a una métrica inexistente pinta «No data», que es
  indistinguible de «no está pasando nada». Es precisamente el defecto que
  prod-08 corrige, así que se cierra con test.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
_DASHBOARD_DIR = _ROOT / "docker" / "monitoring" / "grafana" / "dashboards"

_INFRA_PREFIXES = ("node_", "container_", "process_", "python_", "prometheus_", "scrape_")
_SYNTHETIC = {"up"}
_PROMQL_KEYWORDS = {
    "time",
    "rate",
    "irate",
    "sum",
    "avg",
    "min",
    "max",
    "count",
    "by",
    "without",
    "increase",
    "delta",
    "deriv",
    "predict_linear",
    "histogram_quantile",
    "absent",
    "changes",
    "clamp_min",
    "clamp_max",
    "humanizeDuration",
    "on",
    "ignoring",
    "group_left",
    "group_right",
    "offset",
    "unless",
    "and",
    "or",
    "bool",
    "le",
    "topk",
    "bottomk",
    "quantile",
    "stddev",
    "round",
    "abs",
}


def _dashboards() -> list[tuple[str, dict]]:
    found = []
    for path in sorted(_DASHBOARD_DIR.glob("*.json")):
        found.append((path.name, json.loads(path.read_text(encoding="utf-8"))))
    return found


def _emitted_metric_names() -> set[str]:
    """Mismo inventario leído del código que usa la guarda de reglas."""
    names: set[str] = set()
    for module in ("queue_metrics.py", "backup_metrics.py"):
        source = (_ROOT / "apps" / "workers" / "src" / "workers" / module).read_text(
            encoding="utf-8"
        )
        names.update(re.findall(r'"(agentic_[a-z0-9_]+)"', source))
    api_metrics = (_ROOT / "apps" / "api-server" / "src" / "api_server" / "metrics.py").read_text(
        encoding="utf-8"
    )
    for base in re.findall(r'"(agentic_[a-z0-9_]+)"', api_metrics):
        names.add(base)
        names.update({f"{base}_bucket", f"{base}_count", f"{base}_sum"})
    return names


def _exprs(dashboard: dict):
    for panel in dashboard.get("panels") or []:
        for target in panel.get("targets") or []:
            if isinstance(target, dict) and target.get("expr"):
                yield panel.get("title"), target["expr"]


def _metric_tokens(expr: str) -> set[str]:
    cleaned = re.sub(r"\{[^}]*\}", " ", expr)
    cleaned = re.sub(
        r"\b(?:by|without|on|ignoring|group_left|group_right)\s*\([^)]*\)", " ", cleaned
    )
    cleaned = re.sub(r'"[^"]*"', " ", cleaned)
    tokens = set(re.findall(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\b", cleaned))
    return tokens - _PROMQL_KEYWORDS


def test_every_dashboard_is_valid_json_with_a_title_and_panels() -> None:
    dashboards = _dashboards()

    assert len(dashboards) >= 2, f"la guarda dejó de encontrar dashboards (vio {len(dashboards)})"
    for name, dashboard in dashboards:
        assert dashboard.get("title"), f"{name} — dashboard sin título"
        assert dashboard.get("panels"), f"{name} — dashboard sin paneles"
        for panel in dashboard["panels"]:
            assert panel.get("title"), f"{name} — panel sin título: {panel.get('id')}"


def test_dashboard_uids_are_unique() -> None:
    """Dos dashboards con el mismo uid: el provisioner sobrescribe uno con otro
    y el operador ve desaparecer un panel sin explicación."""
    uids = [(name, d.get("uid")) for name, d in _dashboards() if d.get("uid")]
    seen: dict[str, str] = {}

    for name, uid in uids:
        assert uid not in seen, f"{name} y {seen[uid]} comparten uid '{uid}'"
        seen[uid] = name


def test_no_panel_queries_a_metric_nobody_emits() -> None:
    """Criterio de cierre 4: un panel «No data» miente por omisión."""
    emitted = _emitted_metric_names()
    assert emitted, "no se pudo inventariar ninguna métrica emitida"

    unknown: dict[str, set[str]] = {}
    for name, dashboard in _dashboards():
        for panel_title, expr in _exprs(dashboard):
            for token in _metric_tokens(str(expr)):
                if token in emitted or token in _SYNTHETIC:
                    continue
                if token.startswith(_INFRA_PREFIXES):
                    continue
                unknown.setdefault(f"{name}:{panel_title}", set()).add(token)

    assert (
        not unknown
    ), f"paneles que consultan métricas que nadie emite (pintarán «No data»): {unknown}"


def test_the_platform_dashboard_shows_api_server_health() -> None:
    """El exporter de la Fase B existe para verse. Si nadie lo pinta, la mitad
    del trabajo no llega al operador (§5: «alguien lo llama y alguien lo ve»).
    """
    dashboard = next(d for name, d in _dashboards() if "agentic-platform" in name)
    all_exprs = " ".join(expr for _title, expr in _exprs(dashboard))

    assert "agentic_http_requests_total" in all_exprs, (
        "el dashboard «Plataforma» no pinta las peticiones HTTP del api-server: "
        "el exporter de la Fase B no se ve en ningún sitio"
    )
    assert (
        "agentic_http_request_duration_seconds_bucket" in all_exprs
    ), "falta la latencia (p95) del api-server"
