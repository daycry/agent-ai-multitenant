"""Loki + Promtail en el overlay de monitorización (prod-08 `task_prod08_loki_deploy_12`).

El ADR 0139 dejó constancia de que la premisa del plan («Loki está declarado en
CLAUDE.md pero no existe en ningún compose») era falsa: Loki y Promtail llevan
desplegados desde antes. Lo que NO había era ninguna guarda, y este subsistema
tiene una propiedad desagradable: **casi todas sus formas de romperse son
silenciosas**.

Los cuatro fallos mudos que este fichero cierra:

1. **Promtail sin el bind de `/var/lib/docker/containers`** arranca perfecto,
   se conecta a Loki, y no envía ni una línea. Grafana enseña «no logs» — que es
   exactamente lo que enseña un sistema sano y callado.
2. **`retention_period` sin `retention_enabled` en el compactor** es el clásico
   de Loki: la config parece decir «guardo 7 días» y en realidad no borra nunca.
   Se descubre cuando el disco se llena.
3. **`loki_data` sin volumen nombrado**: la retención pasa a ser «hasta el
   próximo `docker compose down`», y el buscador de logs miente sobre su ventana.
4. **La URL del datasource y el nombre del servicio se separan**: Grafana
   muestra un datasource que da error solo al consultarlo, no al provisionarlo.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _ROOT / "docker" / "docker-compose.monitoring.yml"
_LOKI_CONFIG = _ROOT / "docker" / "monitoring" / "loki" / "loki-config.yml"
_DATASOURCES = _ROOT / "docker" / "monitoring" / "grafana" / "provisioning" / "datasources"


def _compose() -> dict[str, Any]:
    return yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))


def _services() -> dict[str, Any]:
    return _compose().get("services") or {}


def test_loki_and_promtail_are_declared_services() -> None:
    services = _services()

    assert len(services) >= 4, f"la guarda dejó de encontrar servicios (vio {len(services)})"
    for name in ("loki", "promtail"):
        assert name in services, f"{name} no está en el overlay de monitorización"
        assert "agentic-net" in (services[name].get("networks") or []), (
            f"{name} fuera de agentic-net: Grafana y los contenedores de la "
            "plataforma no lo alcanzan por nombre DNS"
        )


def test_promtail_can_actually_read_the_container_logs() -> None:
    """El fallo mudo nº1: sin este bind, Promtail arranca sano y no envía nada.

    Y `:ro` no es cosmético — es un servicio con `cap_drop: ALL` leyendo el
    directorio de logs del daemon Docker; montarlo escribible le daría capacidad
    de manipular la evidencia que precisamente sirve para auditar incidentes.
    """
    volumes = _services()["promtail"].get("volumes") or []
    mounts = [str(v) for v in volumes]

    docker_logs = [m for m in mounts if "/var/lib/docker/containers" in m]
    assert docker_logs, f"Promtail no monta los logs del daemon: {mounts}"
    assert docker_logs[0].endswith(":ro"), f"el bind debe ser read-only: {docker_logs[0]}"


def test_promtail_waits_for_loki_to_be_healthy() -> None:
    """Sin la espera, Promtail arranca contra un Loki que aún no responde,
    reintenta y pierde la ventana inicial de logs — justo la del arranque, que
    es la que se mira cuando algo no levanta."""
    depends = _services()["promtail"].get("depends_on") or {}

    assert "loki" in depends, f"promtail no depende de loki: {depends}"
    assert depends["loki"].get("condition") == "service_healthy", depends["loki"]


def test_loki_storage_survives_a_compose_recreate() -> None:
    """El fallo mudo nº3: sin volumen nombrado la retención real es «hasta el
    próximo `docker compose down`», y el buscador miente sobre su ventana."""
    compose = _compose()
    loki_volumes = [str(v) for v in (compose["services"]["loki"].get("volumes") or [])]

    persisted = [v for v in loki_volumes if v.startswith("loki_data:")]
    assert persisted, f"loki no persiste en un volumen nombrado: {loki_volumes}"
    assert "loki_data" in (compose.get("volumes") or {}), "loki_data no está declarado en `volumes`"


def test_retention_is_actually_enabled_not_just_configured() -> None:
    """El fallo mudo nº2, el clásico de Loki.

    `limits_config.retention_period` SOLO tiene efecto si el compactor tiene
    `retention_enabled: true`. Con uno sin el otro, la config parece decir
    «guardo N días» y en realidad no borra nunca — y eso se descubre el día que
    el disco del host se llena.
    """
    config = yaml.safe_load(_LOKI_CONFIG.read_text(encoding="utf-8"))

    period = (config.get("limits_config") or {}).get("retention_period")
    assert period, "sin retention_period: los logs crecen sin límite"

    compactor = config.get("compactor") or {}
    assert compactor.get("retention_enabled") is True, (
        f"retention_period={period} declarado pero el compactor no lo aplica: "
        "la retención es decorativa y el disco crecerá hasta llenarse"
    )


def test_the_grafana_datasource_points_at_the_service_compose_declares() -> None:
    """Si el nombre del servicio y la URL del datasource se separan, Grafana
    provisiona el datasource sin quejarse y falla solo al consultarlo."""
    datasources: list[dict[str, Any]] = []
    for path in _DATASOURCES.glob("*.yml"):
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        datasources.extend(document.get("datasources") or [])

    loki_ds = [d for d in datasources if d.get("type") == "loki"]
    assert loki_ds, "no hay datasource Loki provisionado: los logs no se ven en Grafana"

    url = str(loki_ds[0].get("url") or "")
    assert "loki" in _services(), "el datasource apunta a un servicio que no existe"
    assert url.startswith("http://loki:"), (
        f"la URL del datasource ({url}) no usa el nombre DNS del servicio del "
        "compose; el datasource se provisiona igual y falla al consultarlo"
    )
