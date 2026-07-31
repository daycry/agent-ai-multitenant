"""Reglas de alerta y jobs de scrape de Prometheus (prod-08 Fase B).

Dos cosas distintas se comprueban aquí, y la segunda es la que importa.

**Estructura**: que los ficheros de reglas sean YAML válido con la forma que
Prometheus exige (``groups[].rules[]`` con ``alert``/``expr``/``labels.severity``).
Un fichero de reglas malformado no rompe nada al arrancar: Prometheus lo
rechaza y sigue funcionando **sin esas alertas**, en silencio. Es el modo de
fallo más traicionero de todo el stack de monitorización.

**Que las reglas hablen de métricas que existen**: criterio de cierre 4 del
plan — «Ningún /metrics, regla o dashboard referencia métricas que no se
emiten». Una regla sobre una métrica inexistente NUNCA dispara. Es
indistinguible de «todo va bien» y es exactamente el defecto que prod-08
corrige, así que no puede reintroducirse por la puerta de atrás. `promtool`
valida la sintaxis pero NO esto: para promtool `agentic_typo_total > 0` es una
expresión perfectamente correcta.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
_RULES_DIR = _ROOT / "docker" / "monitoring" / "prometheus" / "rules"
_PROMETHEUS_YML = _ROOT / "docker" / "monitoring" / "prometheus" / "prometheus.yml"

# Prefijos de métricas que publica infraestructura de terceros ya desplegada.
_INFRA_PREFIXES = ("node_", "container_", "process_", "python_", "prometheus_", "scrape_")

# Series sintetizadas por el propio Prometheus en cada scrape.
_SYNTHETIC = {"up"}

# Funciones y palabras clave de PromQL — no son métricas.
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
}


def _emitted_metric_names() -> set[str]:
    """Inventario de métricas que la plataforma emite DE VERDAD.

    Se lee del código fuente, no de una lista escrita a mano: una lista a mano
    envejece en cuanto alguien renombra una métrica, y entonces el test empieza
    a mentir en la dirección cómoda (pasar).
    """
    names: set[str] = set()

    # 1) Las que publica el sampler por textfile-collector (node-exporter las
    #    re-exporta) y las del backup.
    for module in ("queue_metrics.py", "backup_metrics.py"):
        source = (_ROOT / "apps" / "workers" / "src" / "workers" / module).read_text(
            encoding="utf-8"
        )
        names.update(re.findall(r'"(agentic_[a-z0-9_]+)"', source))

    # 2) Las que expone el exporter HTTP del api-server, con los sufijos que
    #    Prometheus deriva de un histograma.
    api_metrics = (_ROOT / "apps" / "api-server" / "src" / "api_server" / "metrics.py").read_text(
        encoding="utf-8"
    )
    for base in re.findall(r'"(agentic_[a-z0-9_]+)"', api_metrics):
        names.add(base)
        names.update({f"{base}_bucket", f"{base}_count", f"{base}_sum"})

    return names


def _iter_rules():
    for path in sorted(_RULES_DIR.glob("*.yml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        for group in document.get("groups") or []:
            for rule in group.get("rules") or []:
                yield path.name, group.get("name"), rule


def _metric_tokens(expr: str) -> set[str]:
    """Identificadores de la expresión que parecen nombres de métrica.

    Se limpia por eliminación, en este orden:

    1. Los bloques de matchers ``{fstype!~"tmpfs|overlay"}`` — ni los nombres
       de label ni sus valores son métricas. Se eliminan ENTEROS: quitar solo
       las cadenas entrecomilladas dejaba escapar los valores alternados por
       ``|`` dentro de una misma cadena, que es como este test se equivocó la
       primera vez y acusó a `HostDiskUsageHigh` de referenciar una métrica
       llamada ``tmpfs``.
    2. Las cláusulas de agrupación ``by (instance)`` / ``without (...)``.
    3. Las cadenas sueltas y las palabras clave de PromQL.
    """
    cleaned = re.sub(r"\{[^}]*\}", " ", expr)
    cleaned = re.sub(
        r"\b(?:by|without|on|ignoring|group_left|group_right)\s*\([^)]*\)", " ", cleaned
    )
    cleaned = re.sub(r'"[^"]*"', " ", cleaned)
    tokens = set(re.findall(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\b", cleaned))
    return tokens - _PROMQL_KEYWORDS


# ---------------------------------------------------------------------------
# Estructura
# ---------------------------------------------------------------------------
def test_every_rule_file_is_well_formed() -> None:
    rules = list(_iter_rules())

    # Guarda contra el paso en vacío: si el descubrimiento deja de encontrar
    # reglas, este test debe fallar en vez de aprobar por silencio.
    assert len(rules) >= 10, f"la guarda dejó de encontrar reglas (vio {len(rules)})"

    for filename, group, rule in rules:
        where = f"{filename}:{group}:{rule.get('alert')}"
        assert rule.get("alert"), f"{where} — regla sin nombre de alerta"
        assert rule.get("expr"), f"{where} — regla sin expr"
        severity = (rule.get("labels") or {}).get("severity")
        assert severity in {"critical", "warning", "info"}, (
            f"{where} — severity '{severity}' fuera del catálogo; el enrutado de "
            "alertmanager.yml discrimina por `severity=critical`"
        )
        annotations = rule.get("annotations") or {}
        assert annotations.get("summary"), (
            f"{where} — sin `summary`: la notificación al System Admin llegaría "
            "sin decir qué pasa"
        )


def test_no_rule_references_a_metric_nobody_emits() -> None:
    """Criterio de cierre 4 del plan: cero configuración muerta."""
    emitted = _emitted_metric_names()
    assert emitted, "no se pudo inventariar ninguna métrica emitida"

    unknown: dict[str, set[str]] = {}
    for filename, _group, rule in _iter_rules():
        for token in _metric_tokens(str(rule["expr"])):
            if token in emitted or token in _SYNTHETIC:
                continue
            if token.startswith(_INFRA_PREFIXES):
                continue
            unknown.setdefault(f"{filename}:{rule.get('alert')}", set()).add(token)

    assert not unknown, (
        "estas reglas referencian métricas que nadie emite, así que NUNCA "
        f"dispararán (indistinguible de «todo va bien»): {unknown}"
    )


# ---------------------------------------------------------------------------
# Alertas que el plan exige que existan
# ---------------------------------------------------------------------------
def _alert_names() -> set[str]:
    return {rule["alert"] for _f, _g, rule in _iter_rules()}


@pytest.mark.parametrize(
    "alert_name",
    ["ServiceDown", "CeleryQueueGrowing", "NotificationsDLQNotEmpty"],
)
def test_the_alerts_that_actually_page_someone_exist(alert_name: str) -> None:
    """Las tres del encargo: servicio caído, cola creciendo y DLQ no vacía."""
    assert alert_name in _alert_names()


def test_service_down_is_critical_and_uses_up() -> None:
    """``up == 0`` es la única alerta que detecta «no hay nadie al otro lado».

    Debe ser `critical` porque es la que enruta al receiver de respaldo
    email/Slack: si el que está caído es el api-server, la notificación por la
    plataforma no puede entregarse a sí misma.
    """
    rule = next(r for _f, _g, r in _iter_rules() if r["alert"] == "ServiceDown")

    assert "up" in _metric_tokens(str(rule["expr"]))
    assert "== 0" in str(rule["expr"]).replace(" ==0", " == 0")
    assert rule["labels"]["severity"] == "critical"


# ---------------------------------------------------------------------------
# Scrape
# ---------------------------------------------------------------------------
def _scrape_jobs() -> dict[str, list[str]]:
    document = yaml.safe_load(_PROMETHEUS_YML.read_text(encoding="utf-8"))
    jobs: dict[str, list[str]] = {}
    for job in document.get("scrape_configs") or []:
        targets: list[str] = []
        for static in job.get("static_configs") or []:
            targets.extend(static.get("targets") or [])
        jobs[job["job_name"]] = targets
    return jobs


def test_the_api_server_is_a_scrape_target() -> None:
    """Sin target no hay serie ``up``, y sin serie ``up`` ServiceDown no puede
    disparar para el api-server — el agujero central del hallazgo."""
    jobs = _scrape_jobs()

    assert "api-server" in jobs, (
        "prometheus.yml no scrapea el api-server: la regla ServiceDown existiría "
        "pero no tendría datos para el servicio más importante del stack"
    )
    assert any("api-server:8000" in target for target in jobs["api-server"]), jobs["api-server"]


def test_infrastructure_targets_are_still_scraped() -> None:
    """Las métricas de aplicación viajan por el textfile-collector de
    node-exporter: si node-exporter deja de scrapearse, se van TODAS con él."""
    jobs = _scrape_jobs()

    for job in ("node-exporter", "cadvisor", "prometheus"):
        assert job in jobs, f"desapareció el job de scrape {job}"
