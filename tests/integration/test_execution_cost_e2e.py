"""prod-07 `task_prod07_14` (llm-1) — la cadena entera: de la respuesta del
proveedor a la cifra que consumen los budgets.

Las tres piezas de llm-1 ya tienen test propio, y cada una prueba su tramo:

  * `task_prod07_12` — el paso `model_call` lleva `provider` y su clave casa
    con el catálogo (`test_execution_capture.py -k snapshot_provider`);
  * `task_prod07_13` — un 0 del runtime con snapshots preciados se persiste
    como la suma del catálogo (`test_execution_cost_finalize.py`).

Lo que NINGUNO cubre, y es lo que este fichero cierra, son los dos extremos:

  1. **el origen del 0**: que un proveedor OpenAI-compat REAL, hablando HTTP de
     verdad contra un endpoint que no manda `cost` (la forma exacta de Ollama y
     Copilot), produce `cost_usd = 0`. Sin este extremo, la cadena entera se
     apoya en una suposición sobre el proveedor;
  2. **el destino**: que la cifra corregida llega a `budgets/consumption`, que
     es quien decide si un proyecto se pausa. El bug llm-1 no dolía en la
     columna: dolía porque el presupuesto de un proyecto que gastaba de verdad
     marcaba 0 % usado para siempre.

Por qué el tramo del medio usa `ScriptedModelClient` y no el cliente HTTP: el
lazo del agente necesita decisiones y reviews deterministas para terminar, y
scriptarlas no debilita nada aquí — el único dato que este test necesita del
proveedor es su `cost_usd`, y de ese se ocupa el extremo 1 con HTTP real.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from uuid import UUID, uuid4

import api_server.db.domain  # noqa: F401 — resuelve las FK que la consulta de gasto une
import pytest
from agent_runtime.graph import AgentDeps, run_agent
from agent_runtime.model import (
    DecisionKind,
    ModelDecision,
    ModelResponse,
    ReviewResponse,
    ScriptedModelClient,
)
from agent_runtime.providers import OllamaModelClient
from alembic import command
from api_server.budgets import compute_budget_consumption
from api_server.db.budget_alert_state import BudgetScope
from api_server.db.domain import Project, Task
from api_server.db.execution_repo import get_execution, record_execution
from api_server.db.models import Organization
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

_MODEL = "llama3.1"  # clave NATIVA del runtime
_CATALOG_MODEL = "ollama/llama3.1"  # clave del catálogo (tecleada a la LiteLLM)
_CATALOG_FAMILY = "ollama"
_KIND = "ollama"
_TASK = {"id": "t-cost", "title": "Coste", "description": "ejercita la cadena de coste"}

# 3 USD/1M in + 15 USD/1M out (el `unit` cae a su default `per_1m_tokens`).
_PRICE_IN = Decimal("3.0")
_PRICE_OUT = Decimal("15.0")


# ---------------------------------------------------------------------------
# Extremo 1 — el proveedor OpenAI-compat REAL no puede reportar coste
# ---------------------------------------------------------------------------
class _Handler(BaseHTTPRequestHandler):
    """`/v1/chat/completions` con la forma EXACTA de Ollama: `usage` sin `cost`."""

    protocol_version = "HTTP/1.1"
    disable_nagle_algorithm = True  # en Windows, Nagle + ACK diferido mete ~2 s

    def do_POST(self) -> None:  # — nombre impuesto por BaseHTTPRequestHandler
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        payload = json.dumps(
            {
                "model": _MODEL,
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "he terminado"},
                        "finish_reason": "stop",
                    }
                ],
                # Ni `cost` ni nada que se le parezca: es lo que devuelve Ollama.
                "usage": {"prompt_tokens": 1000, "completion_tokens": 500},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args: Any) -> None:
        return


@pytest.fixture()
def openai_compat_server() -> Any:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_an_openai_compat_provider_reports_zero_cost(openai_compat_server: str) -> None:
    """El origen de llm-1, con HTTP de verdad.

    `_openai_compat` sólo puede pasar por lo que el endpoint envía, y ningún
    endpoint de Ollama/Copilot envía `cost`. Si este test se pusiera verde con
    un coste > 0, la cadena de estimación de catálogo sobraría — así que aquí se
    acredita la PREMISA, no un comportamiento deseado.
    """
    client = OllamaModelClient(model=_MODEL, base_url=openai_compat_server)
    try:
        response = client.decide({"task": {"title": "algo"}, "context": []})
    finally:
        client.close()

    assert response.tokens_in == 1000 and response.tokens_out == 500
    assert response.cost_usd == 0.0, (
        "el proveedor reportó coste: la premisa de llm-1 cambió y la estimación "
        "por catálogo habría que revisarla"
    )


# ---------------------------------------------------------------------------
# Extremo 2 — la cifra corregida llega hasta budgets
# ---------------------------------------------------------------------------
@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


_INSERT_PRICE = text(
    "INSERT INTO model_prices"
    " (id, provider, model_id, modality, input_price, output_price, source, effective_from)"
    " VALUES (:id, :provider, :model_id, 'text', :pin, :pout, 'manual', now())"
)


def _act(tool: str, tokens_in: int, tokens_out: int, **args: object) -> ModelResponse:
    """Una llamada de modelo SIN coste reportado — como las de Ollama."""
    return ModelResponse(
        decision=ModelDecision(kind=DecisionKind.ACT, tool=tool, tool_args=dict(args)),
        model=_MODEL,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=0.0,
    )


def _finish(tokens_in: int, tokens_out: int) -> ModelResponse:
    return ModelResponse(
        decision=ModelDecision(kind=DecisionKind.FINISH, output="la respuesta final"),
        model=_MODEL,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=0.0,
    )


def _run() -> AgentDeps:
    """echo → noop → finish, más una review: 62 000 tokens, 0,306 USD.

    Dos calibraciones deliberadas, y las dos tienen trampa detrás:

    * los tokens son GRANDES (un run de juguete gasta 0,0015 USD, el porcentaje
      de presupuesto redondea a 0,0 % y el test no distinguiría «budgets
      consume la cifra corregida» de «budgets sigue en cero»);
    * pero **por debajo de los 100 000** de `safeguards.RunBudgets.max_tokens`.
      Con 120 000 en la primera llamada el run se aborta ahí mismo y sólo queda
      UN `model_call` — el test seguiría verde midiendo un sexto del gasto.
    """
    return AgentDeps(
        model=ScriptedModelClient(
            decisions=[
                _act("echo", 20_000, 4_000, text="alpha"),
                _act("noop", 16_000, 3_000),
                _finish(10_000, 2_000),
            ],
            reviews=[
                ReviewResponse(
                    passed=True, model=_MODEL, tokens_in=6_000, tokens_out=1_000, cost_usd=0.0
                )
            ],
        ),
        provider_kind=_KIND,
    )


def _expected_cost(steps: list[dict[str, Any]]) -> Decimal:
    """Lo que el catálogo debe cobrar por las llamadas que el run hizo."""
    total = Decimal(0)
    for step in steps:
        if step.get("kind") != "model_call":
            continue
        total += Decimal(step["tokens_in"]) * _PRICE_IN / Decimal(1_000_000)
        total += Decimal(step["tokens_out"]) * _PRICE_OUT / Decimal(1_000_000)
    return total


async def _seed(session: async_sessionmaker) -> dict[str, UUID]:
    ids = {"tenant": uuid4(), "project": uuid4(), "task": uuid4()}
    async with session() as s, s.begin():
        await s.execute(
            text(
                "TRUNCATE budget_alert_states, executions, task_dependencies, tasks,"
                " projects, organizations, model_prices RESTART IDENTITY CASCADE"
            )
        )
        await s.execute(
            _INSERT_PRICE,
            {
                "id": uuid4(),
                "provider": _CATALOG_FAMILY,
                "model_id": _CATALOG_MODEL,
                "pin": _PRICE_IN,
                "pout": _PRICE_OUT,
            },
        )
        s.add(Organization(id=ids["tenant"], name="E2E tenant", slug="e2e-cost-tenant"))
        await s.flush()
        s.add(
            Project(
                id=ids["project"],
                tenant_id=ids["tenant"],
                name="E2E project",
                status="active",
                is_template=False,
                # El run cuesta 0,306 USD al precio sembrado: con este cap el
                # gasto queda en el 87,4 % y CRUZA el umbral de 80.
                budget_amount=Decimal("0.35"),
                budget_currency="USD",
                budget_period="monthly",
            )
        )
        await s.flush()
        s.add(
            Task(
                id=ids["task"],
                tenant_id=ids["tenant"],
                project_id=ids["project"],
                title="E2E task",
                status="backlog",
                priority="medium",
            )
        )
    return ids


@pytest.mark.asyncio
async def test_a_run_without_provider_cost_still_reaches_the_budget(
    _migrated: None, admin_database_url: str
) -> None:
    """La cadena completa: run → steps → snapshot → total_cost_usd → budgets."""
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm)

        result = run_agent(_run(), _TASK)
        assert result.usage["cost_usd"] == 0.0, "el arnés dejó de simular un proveedor sin coste"
        assert result.usage["total_tokens"] < 100_000, (
            "el run tropieza con el cap de `safeguards.RunBudgets.max_tokens`: se "
            "aborta en la primera llamada y el gasto medido sería una fracción"
        )
        expected = _expected_cost(result.steps)
        assert expected > 0

        async with sm() as s, s.begin():
            execution = await record_execution(
                s, tenant_id=ids["tenant"], task_id=ids["task"], result=result
            )
            execution_id = execution.id

        async with sm() as s:
            loaded = await get_execution(s, execution_id)
        assert loaded is not None

        # (a) cada llamada lleva el kind del proveedor y casó con el catálogo
        calls = [step for step in loaded.steps_log if step.get("kind") == "model_call"]
        assert calls, "el run no registró ninguna llamada de modelo"
        for call in calls:
            assert call["provider"] == _KIND
            assert call["model"] == _MODEL
            assert call["price_snapshot"]["available"] is True, (
                "la clave nativa del runtime no casó con la del catálogo: el "
                "coste facturable vuelve a ser 0 (llm-1)"
            )

        # (b) el coste facturable es la suma del catálogo, no el 0 del runtime
        assert loaded.total_cost_usd == expected

        # (c) y los budgets lo consumen: esto es lo que decide una pausa
        async with sm() as s:
            consumptions = await compute_budget_consumption(
                s, tenant_id=ids["tenant"], thresholds=[80, 90, 100]
            )
        project_scope = [c for c in consumptions if c.scope == BudgetScope.PROJECT]
        assert len(project_scope) == 1, "el proyecto con presupuesto no apareció en el cálculo"
        snapshot = project_scope[0]
        assert snapshot.ai_spend_usd == expected, (
            "budgets siguió sumando el 0 del runtime: el proyecto gastaba de "
            "verdad y su presupuesto marcaba 0 % usado"
        )
        # …y lo consumen HASTA EL PUNTO DE DECIDIR: el gasto corregido cruza el
        # umbral del 80 %. Con el 0 del runtime, este proyecto habría marcado
        # 0 % usado para siempre por mucho que gastara.
        assert snapshot.percent_used is not None and snapshot.percent_used > Decimal(80)
        assert 80 in snapshot.crossed_thresholds
        assert snapshot.window.contains(datetime.now(tz=UTC).date())
    finally:
        await engine.dispose()
