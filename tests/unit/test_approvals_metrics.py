"""Aprobaciones humanas pendientes como métrica (prod-08 Fase B).

El plan pide un gauge `human_approvals_pending` (task_prod08_metrics_api_04), un
panel de «aprobaciones humanas pendientes» en el dashboard (task_..._07) y una
alerta `HumanApprovalsStale` (task_..._06). Los tres necesitan lo mismo y no
existía: **alguien que emita el número**.

Por qué importa: cuando un agente pide aprobación humana, su ejecución se
DETIENE hasta que alguien responde. Una petición olvidada no produce ningún
error, ningún log de fallo y ninguna cola creciendo — el plan simplemente no
avanza y nadie se entera. Es el modo de fallo más silencioso del pipeline
autónomo, y el único síntoma medible es la EDAD de la más vieja: contar cuántas
hay pendientes no distingue «tres recién pedidas» de «tres olvidadas hace una
semana».

Por qué en el sampler de workers y no en `/metrics` del api-server: un gauge
in-process del api-server solo vería lo que pasó por SU proceso; el sampler
consulta la BD, así que ve el sistema entero (misma razón que el ADR 0141 da
para el resto de métricas de plataforma).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


class _Rows:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[object, ...]]:
        return self._rows

    def first(self) -> tuple[object, ...] | None:
        return self._rows[0] if self._rows else None


class _Session:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows
        self.queries: list[str] = []

    async def execute(self, stmt: object) -> _Rows:
        self.queries.append(str(stmt))
        return _Rows(self.rows)


# ---------------------------------------------------------------------------
# Colector
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_collect_counts_pending_and_measures_the_oldest() -> None:
    from workers.maintenance.queue_sampler import _collect_approval_metrics

    session = _Session([(4, 93_600.0)])

    pending, oldest = await _collect_approval_metrics(session)

    assert pending == 4
    assert oldest == pytest.approx(93_600.0)
    query = session.queries[0]
    # Solo las PENDIENTES: contar las resueltas haría subir el gauge para
    # siempre y la alerta no se apagaría nunca.
    assert "pending" in query
    assert "approval_requests" in query


@pytest.mark.asyncio
async def test_no_pending_approvals_is_zero_not_absent() -> None:
    """Aquí cero SÍ es un dato: significa «nadie espera», y es el estado sano.

    (Distinto de `dlq_depths`, donde la ausencia señala un colector caído.)
    """
    from workers.maintenance.queue_sampler import _collect_approval_metrics

    pending, oldest = await _collect_approval_metrics(_Session([(0, None)]))

    assert pending == 0
    assert oldest == 0.0


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------
def test_render_emits_the_approval_gauges() -> None:
    from workers.queue_metrics import (
        METRIC_APPROVALS_OLDEST_AGE,
        METRIC_APPROVALS_PENDING,
        render_queue_metrics,
    )

    body = render_queue_metrics(
        queue_depths={},
        status_counts={},
        approvals_pending=3,
        approvals_oldest_age_s=93_600.0,
    )

    assert f"# TYPE {METRIC_APPROVALS_PENDING} gauge" in body
    assert f"{METRIC_APPROVALS_PENDING} 3" in body
    assert f"{METRIC_APPROVALS_OLDEST_AGE} 93600" in body


def test_render_emits_zero_pending_explicitly() -> None:
    """El caso que un `if approvals_pending:` se comería.

    Sin muestra, Prometheus no distingue «cero pendientes» de «el colector se
    cayó», y `HumanApprovalsStale` dejaría de evaluarse en silencio.
    """
    from workers.queue_metrics import METRIC_APPROVALS_PENDING, render_queue_metrics

    body = render_queue_metrics(
        queue_depths={},
        status_counts={},
        approvals_pending=0,
        approvals_oldest_age_s=0.0,
    )

    assert f"{METRIC_APPROVALS_PENDING} 0" in body


def test_render_omits_the_gauges_when_not_sampled() -> None:
    from workers.queue_metrics import METRIC_APPROVALS_PENDING, render_queue_metrics

    body = render_queue_metrics(queue_depths={}, status_counts={})

    assert METRIC_APPROVALS_PENDING not in body


def test_approvals_is_a_known_collector() -> None:
    from workers.queue_metrics import KNOWN_COLLECTORS

    assert "approvals" in KNOWN_COLLECTORS


# ---------------------------------------------------------------------------
# Coste LLM — la otra métrica de negocio que el plan nombra
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_llm_cost_is_collected_per_provider() -> None:
    from workers.maintenance.queue_sampler import _collect_llm_cost

    session = _Session([("claude_sdk", 12000, 1.25), ("ollama", 500, 0.0)])

    costs = await _collect_llm_cost(session)

    assert costs == {"claude_sdk": (12000, 1.25), "ollama": (500, 0.0)}
    assert "24 hours" in session.queries[0]
    assert "llm_usage_events" in session.queries[0]


@pytest.mark.asyncio
async def test_run_spend_is_collected_separately_from_provider_spend() -> None:
    """Dos métricas y no una, porque las fuentes NO son intercambiables.

    `llm_usage_events` tiene `provider_kind` pero solo cubre asistente, córtex y
    planning. El gasto del pipeline de runs vive en `executions.total_cost_usd`,
    que **no tiene columna de proveedor**. Sumarlas bajo una sola métrica
    `{provider}` repartiría el gasto de los runs entre proveedores inventados;
    publicar solo la primera presentaría como «coste LLM» una fracción del real.
    """
    from workers.maintenance.queue_sampler import _collect_run_spend

    tokens, cost = await _collect_run_spend(_Session([(48_000, 7.5)]))

    assert tokens == 48_000
    assert cost == pytest.approx(7.5)


def test_render_emits_both_spend_families() -> None:
    from workers.queue_metrics import (
        METRIC_LLM_COST_24H,
        METRIC_LLM_TOKENS_24H,
        METRIC_RUN_COST_24H,
        METRIC_RUN_TOKENS_24H,
        render_queue_metrics,
    )

    body = render_queue_metrics(
        queue_depths={},
        status_counts={},
        llm_usage={"claude_sdk": (12000, 1.25)},
        run_tokens=48_000,
        run_cost_usd=7.5,
    )

    assert f'{METRIC_LLM_TOKENS_24H}{{provider="claude_sdk"}} 12000' in body
    assert f'{METRIC_LLM_COST_24H}{{provider="claude_sdk"}} 1.25' in body
    assert f"{METRIC_RUN_TOKENS_24H} 48000" in body
    assert f"{METRIC_RUN_COST_24H} 7.5" in body


def test_llm_spend_is_a_known_collector() -> None:
    from workers.queue_metrics import KNOWN_COLLECTORS

    assert "llm_spend" in KNOWN_COLLECTORS


# ---------------------------------------------------------------------------
# Las alertas que estas métricas hacen posibles
# ---------------------------------------------------------------------------
def _rules() -> dict[str, dict[str, object]]:
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parents[2]
    rules: dict[str, dict[str, object]] = {}
    for path in (root / "docker" / "monitoring" / "prometheus" / "rules").glob("*.yml"):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        for group in document.get("groups") or []:
            for rule in group.get("rules") or []:
                rules[rule["alert"]] = rule
    return rules


def test_human_approvals_stale_alerts_on_age_not_on_count() -> None:
    """Tres aprobaciones recién pedidas son sanas; una de hace tres días no.

    Una alerta sobre el CONTADOR dispararía con el sistema funcionando bien y
    acabaría silenciada, que es como muere una alerta.
    """
    rule = _rules().get("HumanApprovalsStale")
    assert rule is not None, "la alerta que el plan exige no está en las reglas"

    assert "agentic_human_approvals_oldest_age_seconds" in str(rule["expr"])
    assert rule["labels"]["severity"] in {"warning", "critical"}


def test_execution_failure_rate_high_exists_and_has_a_volume_floor() -> None:
    """Sin suelo de volumen, 1 fallo de 2 runs = 50% y la alerta grita de noche
    con el sistema parado."""
    rule = _rules().get("ExecutionFailureRateHigh")
    assert rule is not None, "la alerta que el plan exige no está en las reglas"

    expr = str(rule["expr"])
    assert "agentic_executions_24h" in expr
    assert ">= 10" in expr or "> 9" in expr, f"sin suelo de volumen: {expr}"
