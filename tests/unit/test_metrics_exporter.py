"""Exporter Prometheus del api-server (prod-08 Fase B, observability-2).

Hallazgo: Prometheus solo scrapeaba INFRAESTRUCTURA (node-exporter, cadvisor).
Ni api-server ni ningún servicio de aplicación exponía ``/metrics``, así que:

  * no existía la serie ``up`` para las apps y la regla ``ServiceDown`` era
    imposible de escribir — un api-server caído no disparaba NADA;
  * la latencia y los 5xx del API eran invisibles.

Este exporter cubre lo que de verdad vive DENTRO del proceso api-server
(peticiones HTTP) y nada más. Deliberadamente **no** declara contadores de
ejecuciones/tokens/coste: esos suceden en los workers, así que un contador
in-process del api-server valdría siempre 0 — «configuración muerta», que es
justo el defecto que este plan corrige (criterio de cierre 4). Esa familia la
publica ya el sampler por textfile-collector (``workers/queue_metrics.py``).

El riesgo #2 del plan es la EXPLOSIÓN DE CARDINALIDAD, y aquí es donde muerde:
etiquetar por path crudo permite a cualquier escáner de vulnerabilidades crear
una serie temporal nueva por URL inventada y tumbar la TSDB de una máquina
única. Por eso se etiqueta por PLANTILLA de ruta y todo lo no enrutado colapsa
en un único valor centinela. Hay un test dedicado a ello.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from prometheus_client import CollectorRegistry

pytestmark = pytest.mark.unit


@pytest.fixture()
def registry() -> CollectorRegistry:
    """Un registro AISLADO por test: el global filtraría contadores entre tests."""
    return CollectorRegistry()


@pytest.fixture()
def app(registry: CollectorRegistry) -> FastAPI:
    from api_server.metrics import PrometheusMiddleware, build_metrics_endpoint

    application = FastAPI()

    @application.get("/items/{item_id}")
    async def _item(item_id: str) -> dict[str, str]:
        return {"id": item_id}

    @application.get("/boom")
    async def _boom() -> None:
        raise HTTPException(status_code=500, detail="nope")

    application.add_middleware(PrometheusMiddleware, registry=registry)
    # Como RUTA, no como mount: `mount` haría que /metrics respondiera 307.
    application.add_route("/metrics", build_metrics_endpoint(registry), methods=["GET"])
    return application


async def _get(app: FastAPI, path: str):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


@pytest.mark.asyncio
async def test_metrics_endpoint_serves_prometheus_text_format(app) -> None:
    resp = await _get(app, "/metrics")

    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    # El formato de exposición se identifica por las cabeceras HELP/TYPE.
    assert "# HELP" in resp.text
    assert "# TYPE" in resp.text


@pytest.mark.asyncio
async def test_requests_are_counted_by_route_template_and_status(app) -> None:
    await _get(app, "/items/abc")
    await _get(app, "/items/def")

    body = (await _get(app, "/metrics")).text

    assert 'agentic_http_requests_total{method="GET",route="/items/{item_id}",status="200"}' in body
    # Dos peticiones a la MISMA plantilla → una sola serie con valor 2.
    line = next(
        ln
        for ln in body.splitlines()
        if ln.startswith(
            'agentic_http_requests_total{method="GET",route="/items/{item_id}",status="200"}'
        )
    )
    assert line.endswith(" 2.0"), line


@pytest.mark.asyncio
async def test_unmatched_paths_collapse_into_one_series(app) -> None:
    """Riesgo #2 del plan: un escáner no puede crear una serie por URL.

    Sin esto, `GET /wp-admin`, `/.env`, `/phpmyadmin`… cada 404 de un bot sería
    una serie temporal nueva y permanente en la TSDB.
    """
    from api_server.metrics import UNMATCHED_ROUTE

    for path in ("/wp-admin", "/.env", "/phpmyadmin", "/api/v9/nope"):
        await _get(app, path)

    body = (await _get(app, "/metrics")).text

    for path in ("/wp-admin", "/.env", "/phpmyadmin"):
        assert (
            f'route="{path}"' not in body
        ), f"el path no enrutado {path} creó su propia serie: cardinalidad sin cota"
    line = next(
        ln
        for ln in body.splitlines()
        if f'route="{UNMATCHED_ROUTE}"' in ln and ln.startswith("agentic_http_requests_total")
    )
    assert line.endswith(" 4.0"), line


@pytest.mark.asyncio
async def test_server_errors_are_counted_with_their_status(app) -> None:
    """Sin esto no hay tasa de 5xx, que es la mitad del dashboard de aplicación."""
    await _get(app, "/boom")

    body = (await _get(app, "/metrics")).text
    assert 'agentic_http_requests_total{method="GET",route="/boom",status="500"}' in body


@pytest.mark.asyncio
async def test_latency_histogram_is_observed(app) -> None:
    await _get(app, "/items/abc")

    body = (await _get(app, "/metrics")).text
    assert "agentic_http_request_duration_seconds_bucket" in body
    assert (
        'agentic_http_request_duration_seconds_count{method="GET",route="/items/{item_id}"} 1.0'
        in body
    )


@pytest.mark.asyncio
async def test_the_metrics_scrape_does_not_count_itself(app) -> None:
    """Scrapear cada 15s inflaría el ratio de peticiones con ruido propio."""
    await _get(app, "/metrics")
    body = (await _get(app, "/metrics")).text

    assert 'route="/metrics"' not in body


def test_collectors_can_be_built_twice_on_the_same_registry() -> None:
    """REGRESIÓN (2026-07-31): `create_app()` dos veces reventaba el proceso.

    Las métricas se declaran contra el registro GLOBAL del proceso, y
    `prometheus_client` prohíbe registrar dos veces el mismo nombre:

        prometheus_client.registry.DuplicateTimeseries: Duplicated timeseries
        in CollectorRegistry: {'agentic_http_requests_total', ...}

    Construir la app más de una vez en el mismo proceso es corriente (cada
    módulo de tests de integración levanta la suya), así que la primera versión
    de este exporter tumbó media suite de integración. Los tests unitarios no
    lo vieron porque cada uno usaba su propio `CollectorRegistry`.

    La declaración debe ser IDEMPOTENTE: la segunda vez reutiliza los
    colectores ya registrados en vez de intentar registrarlos de nuevo.
    """
    from api_server.metrics import _build_collectors

    shared = CollectorRegistry()
    first_counter, first_histogram = _build_collectors(shared)
    second_counter, second_histogram = _build_collectors(shared)

    # Y son LOS MISMOS: si se devolvieran colectores nuevos no registrados, las
    # cuentas del segundo middleware no aparecerían nunca en /metrics.
    assert second_counter is first_counter
    assert second_histogram is first_histogram


def test_no_forbidden_high_cardinality_labels_are_declared() -> None:
    """Catálogo de labels CERRADO (decisión clave del plan).

    `execution_id` / `task_id` / `user_id` como label tumbarían la TSDB en una
    máquina única. Eso es trabajo de logs, no de métricas.
    """
    from api_server.metrics import ALLOWED_LABELS, FORBIDDEN_LABELS, iter_declared_labels

    declared = set(iter_declared_labels())
    assert declared, "la guarda dejó de encontrar métricas declaradas"

    offenders = declared & set(FORBIDDEN_LABELS)
    assert not offenders, f"labels de cardinalidad ilimitada declarados: {sorted(offenders)}"
    assert declared <= set(
        ALLOWED_LABELS
    ), f"labels fuera del catálogo cerrado: {sorted(declared - set(ALLOWED_LABELS))}"
