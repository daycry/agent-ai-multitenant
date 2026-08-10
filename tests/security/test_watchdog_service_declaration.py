"""El watchdog está declarado y NO recibe el socket Docker (prod-08 task_prod08_watchdog_14).

El plan pedía literalmente declarar el servicio «con el socket Docker montado».
Eso choca de frente con una invariante que este mismo directorio ya vigila
(``test_pentest_findings.test_no_prod_service_mounts_docker_socket``): un
contenedor con ``/var/run/docker.sock`` escapa al host de forma trivial, y el
principio rector 2 lo prohíbe. La salida no es relajar la guarda sino el patrón
que el **ADR 0060** ya fijó para los workers: hablar con el daemon a través del
``docker-socket-proxy`` por TCP, con la ACL mínima que necesita cada cliente.

Estos tests fijan las cuatro cosas que hacen que la declaración sea honesta:

  1. el servicio existe (antes no aparecía en NINGÚN compose: el paquete estaba
     escrito, probado… y sin desplegar, que es la definición de código muerto);
  2. no monta el socket, y toma el daemon por ``DOCKER_HOST``;
  3. lleva el envelope de endurecimiento de los servicios de confianza;
  4. sabe adónde mandar la alerta — sin ``WATCHDOG_ALERTS_INGEST_URL`` la alerta
     terminal vuelve a ser una línea de log local, que es el defecto que esta
     tarea vino a cerrar.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "docker" / "docker-compose.yml"

SERVICE = "watchdog"


def _services() -> dict[str, dict[str, Any]]:
    raw = yaml.safe_load(COMPOSE.read_text(encoding="utf-8")) or {}
    return dict(raw.get("services") or {})


@pytest.fixture(scope="module")
def watchdog() -> dict[str, Any]:
    services = _services()
    assert SERVICE in services, (
        "docker/docker-compose.yml no declara el servicio `watchdog`: el paquete "
        "apps/watchdog existe y está probado, pero sin declaración no corre en "
        "ningún sitio (prod-08 observability-6 / deploy-10)."
    )
    spec = dict(services[SERVICE])
    # Resolver el merge de anclas YAML (`<<: [*a, *b]`), que safe_load deja como
    # clave literal cuando la lista de anclas no la fusiona el parser.
    merged: dict[str, Any] = {}
    for fragment in spec.pop("<<", []) or []:
        merged.update(fragment)
    merged.update(spec)
    return merged


def _volume_strings(spec: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for item in spec.get("volumes") or []:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            out.append(str(item.get("source", "")))
    return out


def _env(spec: dict[str, Any]) -> dict[str, str]:
    raw = spec.get("environment") or {}
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    out: dict[str, str] = {}
    for item in raw:
        key, _, value = str(item).partition("=")
        out[key] = value
    return out


# ---------------------------------------------------------------------------
# 2 — el socket no entra
# ---------------------------------------------------------------------------
def test_watchdog_does_not_mount_the_docker_socket(watchdog: dict[str, Any]) -> None:
    """La instrucción del plan («con el socket Docker montado») está SUPERADA por
    el ADR 0060. Montarlo aquí pondría en rojo test_no_prod_service_mounts_docker_socket."""
    offenders = [
        path for path in _volume_strings(watchdog) if "docker.sock" in path.replace("\\", "/")
    ]
    assert not offenders, f"el watchdog monta el socket Docker: {offenders}"


def test_watchdog_talks_to_the_daemon_through_docker_host(watchdog: dict[str, Any]) -> None:
    """Sin socket hace falta un destino TCP explícito, o el SDK cae al socket
    local y el servicio arranca sin poder inspeccionar nada."""
    env = _env(watchdog)
    assert "DOCKER_HOST" in env, (
        "el watchdog no declara DOCKER_HOST: sin socket montado y sin destino "
        "explícito, docker.from_env() no alcanza al daemon (ADR 0060)"
    )
    assert "tcp://" in env["DOCKER_HOST"], (
        f"DOCKER_HOST debería apuntar al docker-socket-proxy por TCP, no a "
        f"{env['DOCKER_HOST']!r}"
    )


# ---------------------------------------------------------------------------
# 3 — envelope de endurecimiento
# ---------------------------------------------------------------------------
def test_watchdog_carries_the_trusted_hardening_baseline(watchdog: dict[str, Any]) -> None:
    opts = [str(o) for o in (watchdog.get("security_opt") or [])]
    assert "no-new-privileges:true" in opts
    assert any(o.startswith("apparmor=") for o in opts)
    assert watchdog.get("cap_drop") == ["ALL"]


def test_watchdog_publishes_no_ports(watchdog: dict[str, Any]) -> None:
    """No sirve nada: cualquier puerto publicado sería superficie regalada."""
    assert not watchdog.get("ports")


# ---------------------------------------------------------------------------
# 4 — la alerta tiene adónde ir
# ---------------------------------------------------------------------------
def test_watchdog_knows_where_to_send_its_alert(watchdog: dict[str, Any]) -> None:
    env = _env(watchdog)
    assert "WATCHDOG_ALERTS_INGEST_URL" in env, (
        "sin URL de ingesta, agotar el backoff vuelve a ser una línea de log "
        "local dentro de un contenedor — el defecto de observability-6"
    )
    assert "/internal/alerts/ingest" in env["WATCHDOG_ALERTS_INGEST_URL"]
    assert "WATCHDOG_ALERTS_INGEST_TOKEN" in env, (
        "el endpoint exige Bearer token (API_SERVER_ALERTS_INGEST_TOKEN); sin él "
        "cada alerta se pierde con un 401"
    )


def test_the_ingest_token_is_not_inlined(watchdog: dict[str, Any]) -> None:
    """Debe venir del `.env`, nunca escrito en el fichero versionado."""
    value = _env(watchdog)["WATCHDOG_ALERTS_INGEST_TOKEN"]
    assert value.startswith("${"), f"token en claro en el compose: {value!r}"
