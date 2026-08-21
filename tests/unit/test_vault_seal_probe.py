"""Un Vault SELLADO deja de parecer sano.

Plan prod-10 `task_prod10_09` (hallazgos secrets-5, deploy-8).

## Qué estaba pasando

Tras un reinicio del host, un Vault con backend de fichero arranca **sellado**:
está vivo, contesta HTTP, y no puede descifrar un solo secreto. Tres capas lo
daban por bueno:

1. el healthcheck del compose canónico pide
   `/v1/sys/health?...&sealedcode=200&uninitcode=200`, o sea traduce
   explícitamente «sellado» (503) y «sin inicializar» (501) a **200**;
2. el compose que genera el instalador hace `depends_on: vault:
   service_healthy`, así que las apps arrancan detrás de ese 200;
3. el watchdog considera sano cualquier `{"healthy","running","starting"}`.

El mapeo (1) tiene una razón legítima —si `sealed` fuese unhealthy, Vault se
reiniciaría en bucle antes de que nadie pueda desellarlo— pero deja al operador
sin ninguna señal. El probe de este módulo es esa señal: pregunta por
`/v1/sys/seal-status`, que responde el estado REAL, y publica
`agentic_vault_sealed` para que la alerta de prod-08 tenga sobre qué colgarse.

## Semántica del gauge, a propósito

`1` = sellado o sin inicializar (Vault existe y NO sirve secretos). `0` = abierto
y operativo. Si Vault **no contesta**, el gauge se deja como estaba: «no
responde» es trabajo de la regla `ServiceDown` (`up == 0`, ya existe en
`app_alerts.yml`), y escribir un 1 ahí haría que una alerta llamada «Vault
sealed» se disparase por un contenedor caído — el operador iría a desellar algo
que no está sellado.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from api_server.vault_client import (
    SEALED_GAUGE_NAME,
    probe_vault_seal,
    vault_sealed_gauge,
)
from prometheus_client import CollectorRegistry

pytestmark = pytest.mark.unit


def _transport(handler: Any) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def _json_response(status: int, payload: dict[str, Any]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/sys/seal-status", (
            f"el probe consulta {request.url.path}; tiene que ser "
            "/v1/sys/seal-status, que es el único endpoint que dice la verdad "
            "sobre el sellado (health la esconde tras sealedcode)"
        )
        return httpx.Response(status, json=payload)

    return handler


async def _probe(handler: Any, registry: CollectorRegistry) -> Any:
    return await probe_vault_seal(
        "http://vault:8200", registry=registry, transport=_transport(handler)
    )


# ---------------------------------------------------------------------------
# El caso del hallazgo
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_sealed_vault_is_reported_degraded() -> None:
    registry = CollectorRegistry()

    result = await _probe(
        _json_response(503, {"sealed": True, "initialized": True, "t": 3, "n": 5}), registry
    )

    assert result.status == "degraded", "un Vault sellado no puede salir como ok"
    assert result.sealed is True
    assert "sealed" in (result.detail or "").lower()
    # El detalle tiene que llevar al operador al procedimiento, no sólo decirle
    # que algo va mal: desellar es la primera acción post-reinicio.
    assert "restart-services" in (result.detail or "")
    assert registry.get_sample_value(SEALED_GAUGE_NAME) == 1.0


@pytest.mark.asyncio
async def test_an_uninitialized_vault_is_also_degraded() -> None:
    """`uninitcode=200` esconde este caso igual que el anterior. Un Vault sin
    inicializar no sirve un solo secreto."""
    registry = CollectorRegistry()

    result = await _probe(_json_response(501, {"sealed": True, "initialized": False}), registry)

    assert result.status == "degraded"
    assert "initial" in (result.detail or "").lower()
    assert registry.get_sample_value(SEALED_GAUGE_NAME) == 1.0


# ---------------------------------------------------------------------------
# Contrapesos
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_an_open_vault_is_ok_and_clears_the_gauge() -> None:
    registry = CollectorRegistry()

    result = await _probe(_json_response(200, {"sealed": False, "initialized": True}), registry)

    assert result.status == "ok"
    assert result.sealed is False
    assert registry.get_sample_value(SEALED_GAUGE_NAME) == 0.0


@pytest.mark.asyncio
async def test_an_unreachable_vault_is_down_and_leaves_the_gauge_alone() -> None:
    """«No responde» no es «sellado». Escribir 1 aquí haría que una alerta
    llamada `VaultSealed` se disparase por un contenedor caído, y el operador
    iría a desellar algo que no está sellado. Eso lo cubre `ServiceDown`."""
    registry = CollectorRegistry()
    vault_sealed_gauge(registry).set(0)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    result = await _probe(handler, registry)

    assert result.status == "down"
    assert result.sealed is None
    assert registry.get_sample_value(SEALED_GAUGE_NAME) == 0.0


@pytest.mark.asyncio
async def test_the_detail_does_not_leak_internal_topology() -> None:
    """Mismo criterio que `_safe_detail` en admin.py (error-obs-logging-6): la
    respuesta de `/admin/system-health` la consumen dashboards, y el texto crudo
    de una excepción filtra la URL interna de Vault."""
    registry = CollectorRegistry()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("failed to connect to http://vault.internal:8200")

    result = await _probe(handler, registry)

    assert "vault.internal" not in (result.detail or "")


@pytest.mark.asyncio
async def test_a_garbage_response_does_not_claim_health() -> None:
    """Un proxy que devuelve 200 con HTML no es un Vault abierto."""
    registry = CollectorRegistry()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>gateway</html>")

    result = await _probe(handler, registry)

    assert result.status != "ok"


# ---------------------------------------------------------------------------
# El cableado: /admin/system-health y la regla de alerta
# ---------------------------------------------------------------------------
def test_system_health_uses_the_seal_probe() -> None:
    """«Mecanismo entregado, cero llamantes» (§5): el probe no vale nada si el
    endpoint que el operador mira sigue preguntando a `/v1/sys/health`."""
    from pathlib import Path

    import api_server

    source = (Path(next(iter(api_server.__path__))) / "routers" / "admin.py").read_text(
        encoding="utf-8"
    )
    assert "probe_vault_seal" in source, "/admin/system-health no usa el probe de sellado"

    # Sólo el CÓDIGO: los comentarios y docstrings mencionan el endpoint viejo a
    # propósito (explican por qué se dejó de usar), y ese texto es justo lo que
    # evita que alguien lo «arregle» de vuelta. Se recorre el AST en vez de
    # filtrar líneas: un docstring no empieza por `#` y una heurística textual
    # acabaría prohibiendo documentar el hallazgo.
    import ast

    holders = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, holders) and ast.get_docstring(node) is not None:
            node.body = node.body[1:]
    code = ast.unparse(tree)
    assert "/v1/sys/health" not in code, (
        "/admin/system-health sigue preguntando a /v1/sys/health, que es el "
        "endpoint que esconde el sellado tras `sealedcode=200`"
    )


def test_a_prometheus_rule_watches_the_gauge() -> None:
    """Y la métrica no vale nada si nadie la vigila."""
    from pathlib import Path

    import yaml

    rules_dir = (
        Path(__file__).resolve().parents[2] / "docker" / "monitoring" / "prometheus" / "rules"
    )
    exprs: list[str] = []
    for path in sorted(rules_dir.glob("*.yml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for group in document.get("groups") or []:
            for rule in group.get("rules") or []:
                exprs.append(str(rule.get("expr", "")))

    assert len(exprs) >= 5, f"la guarda dejó de encontrar reglas (vio {len(exprs)})"
    assert any(SEALED_GAUGE_NAME in expr for expr in exprs), (
        f"ninguna regla de Prometheus mira {SEALED_GAUGE_NAME}: la métrica se "
        "publica y no la vigila nadie"
    )
