"""Enrutado de Alertmanager: el respaldo para `severity=critical` (prod-08 A-2).

El diseño original entrega TODAS las alertas al api-server
(``/internal/alerts/ingest``), que las convierte en notificación del Plan 10.
Tiene un punto ciego evidente en cuanto se enuncia: **si el que está caído es
el api-server, no puede entregarse la alerta a sí mismo**. La alerta
`ServiceDown` más importante del stack es justo la que no llegaría.

De ahí el receiver de respaldo: para `severity=critical` —y solo para esa
clase, para no duplicar el ruido— la alerta sale ADEMÁS por un canal que no
depende de la plataforma. Redundancia deliberada, documentada como tal en el
riesgo #5 del plan.

La pieza que lo hace funcionar es `continue: true`: el árbol de rutas de
Alertmanager **se detiene en la primera coincidencia** salvo que se le diga lo
contrario. Sin ese flag, añadir la segunda ruta no duplica el envío: lo
SUSTITUYE, y la notificación por la plataforma dejaría de llegar. Es un fallo
silencioso y por eso tiene test propio.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_ALERTMANAGER_YML = (
    Path(__file__).resolve().parents[2]
    / "docker"
    / "monitoring"
    / "alertmanager"
    / "alertmanager.yml"
)


@pytest.fixture(scope="module")
def config() -> dict:
    return yaml.safe_load(_ALERTMANAGER_YML.read_text(encoding="utf-8"))


def _critical_routes(config: dict) -> list[dict]:
    routes = (config.get("route") or {}).get("routes") or []
    return [
        route
        for route in routes
        if any('severity = "critical"' in str(m) for m in (route.get("matchers") or []))
    ]


def test_critical_alerts_reach_both_the_platform_and_a_fallback(config) -> None:
    receivers = {route["receiver"] for route in _critical_routes(config)}

    assert "platform-notifier" in receivers, "las críticas dejaron de ir a la plataforma"
    assert len(receivers) >= 2, (
        "`severity=critical` solo tiene un receiver: si el caído es el api-server, "
        f"esa alerta no llega a nadie (receivers: {receivers})"
    )


def test_the_first_critical_route_continues_so_the_second_also_fires(config) -> None:
    """Sin `continue: true` la segunda ruta no añade: reemplaza."""
    routes = _critical_routes(config)
    assert len(routes) >= 2, "se esperaban dos rutas para severity=critical"

    # Todas menos la última deben continuar la evaluación.
    for route in routes[:-1]:
        assert route.get("continue") is True, (
            f"la ruta a `{route['receiver']}` no lleva `continue: true`: Alertmanager "
            "se detiene en la primera coincidencia y las rutas siguientes NUNCA "
            "se evalúan"
        )


def test_every_routed_receiver_is_actually_defined(config) -> None:
    """Un receiver enrutado pero no declarado impide ARRANCAR a Alertmanager.

    Y un Alertmanager que no arranca es el stack de alertas entero caído — el
    modo de fallo más caro de este fichero.
    """
    defined = {receiver["name"] for receiver in config.get("receivers") or []}
    assert defined, "la guarda dejó de encontrar receivers declarados"

    routed = {(config.get("route") or {}).get("receiver")}
    for route in (config.get("route") or {}).get("routes") or []:
        routed.add(route.get("receiver"))
    routed.discard(None)

    assert routed <= defined, f"receivers enrutados y no declarados: {sorted(routed - defined)}"


def test_the_fallback_does_not_depend_on_the_platform(config) -> None:
    """El respaldo no puede volver a apuntar al api-server: sería el mismo
    punto único de fallo con otro nombre."""
    fallback_names = {
        route["receiver"]
        for route in _critical_routes(config)
        if route["receiver"] != "platform-notifier"
    }
    receivers = {r["name"]: r for r in config.get("receivers") or []}

    for name in fallback_names:
        rendered = yaml.safe_dump(receivers[name])
        assert "api-server" not in rendered, (
            f"el receiver de respaldo `{name}` entrega al api-server: no es un "
            "respaldo, es el mismo punto único de fallo"
        )


def test_the_platform_ingest_still_carries_its_bearer_token(config) -> None:
    """El ingest es fail-closed: sin token responde 503 y la alerta se pierde."""
    receivers = {r["name"]: r for r in config.get("receivers") or []}
    webhook = receivers["platform-notifier"]["webhook_configs"][0]

    assert webhook["url"].endswith("/internal/alerts/ingest")
    assert webhook["http_config"]["authorization"]["type"] == "Bearer"
    assert webhook["http_config"]["authorization"]["credentials"]
    assert (
        webhook.get("send_resolved") is True
    ), "sin send_resolved el operador nunca se entera de que el incidente se cerró"
