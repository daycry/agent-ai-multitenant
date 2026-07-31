"""La app REAL expone ``/metrics`` (prod-08 Fase B, cableado del último tramo).

`api_server.metrics` podría estar impecablemente probado y no servir de nada si
`create_app()` no lo enchufa — el patrón «mecanismo entregado, cero llamantes»
(``docs/03-guides/verificar-antes-de-implementar.md`` §5).

Y hay un detalle que solo se ve sobre la app real: ``/metrics`` debe responder
**200 directo**, no un 307. Si se montara con ``app.mount`` en vez de
registrarse como ruta, Starlette redirigiría ``/metrics`` → ``/metrics/`` y el
scrape de Prometheus seguiría un redirect en cada pasada.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def real_app():
    from api_server.main import create_app

    return create_app()


def test_metrics_route_is_registered_on_the_real_app(real_app) -> None:
    paths = {getattr(route, "path", None) for route in real_app.routes}

    assert "/metrics" in paths, (
        "create_app() no registró /metrics: Prometheus no tendrá target para el "
        "api-server y la regla ServiceDown seguirá siendo inescribible"
    )


def test_metrics_does_not_shadow_the_authenticated_inbox_metrics(real_app) -> None:
    """El ``/inbox/metrics`` JSON autenticado es OTRO endpoint y debe seguir vivo."""
    paths = {getattr(route, "path", None) for route in real_app.routes}

    assert "/inbox/metrics" in paths


def test_metrics_is_excluded_from_the_openapi_schema(real_app) -> None:
    """No es parte del contrato de la API pública: no debe ensuciar el OpenAPI
    ni acabar en los SDK generados."""
    schema = real_app.openapi()

    assert "/metrics" not in schema.get("paths", {})


@pytest.mark.asyncio
async def test_metrics_answers_200_not_a_redirect(real_app) -> None:
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=real_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/metrics")

    assert resp.status_code == 200, (
        f"esperaba 200 y llegó {resp.status_code}: si es 307, /metrics se montó "
        "con app.mount en vez de registrarse como ruta"
    )
    assert "# HELP" in resp.text
    # El middleware ya está contando: la familia HTTP debe estar declarada.
    assert "agentic_http_requests_total" in resp.text
