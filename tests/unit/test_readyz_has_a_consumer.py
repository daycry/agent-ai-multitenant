"""`/readyz` tiene que tener un LLAMANTE (`task_audit14_08`, hallazgo AUD14-06).

El endpoint estaba entero desde el primer tramo de la casilla —PostgreSQL +
Redis, deadline por check, 503 estructurado, saneado de credenciales,
degradación parcial y recuperación sin reinicio, todo probado en
`tests/integration/test_health_readiness.py` y `tests/unit/test_readiness_scrub.py`—
y **no lo consultaba nadie**. Es el patrón dominante de esta base
(`docs/03-guides/verificar-antes-de-implementar.md` §5): mecanismo entregado,
cero llamantes. Una readiness que nadie consulta no evita ni un solo segundo de
tráfico servido a un proceso que no puede atenderlo.

El consumidor correcto es el **proxy**, y la elección importa:

- **El proxy, sí.** Caddy es el único que puede dejar de mandar tráfico a un
  backend que no está listo (`health_uri /readyz`) sin tocar el ciclo de vida
  del contenedor. Cuando `/readyz` vuelve a 200, el siguiente check lo repone
  solo.
- **El `healthcheck` del contenedor, NO.** Docker sólo tiene un healthcheck por
  contenedor, y el `watchdog` de esta plataforma **reinicia lo que sale
  `unhealthy`** (`apps/watchdog/src/watchdog/service_monitor.py`). Apuntarlo a
  `/readyz` significaría que un PostgreSQL caído reinicia la api-server en
  bucle: no arregla la BD, tira las conexiones sanas que quedaban y borra los
  logs del arranque anterior. Es exactamente el «restart loop por dependencias»
  que la descripción de la casilla prohíbe, y por eso este fichero lo afirma en
  negativo: `/healthz` (liveness) se queda donde está.

Las dos mitades se afirman juntas a propósito. Una sola —«el proxy mira
/readyz»— la cumpliría también quien, de paso, cambiara el healthcheck del
contenedor, que es el error que más caro sale.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from installer_backend.compose_generator import generate_compose
from installer_backend.config import (
    InstallerConfig,
    OllamaProvider,
    PortsConfig,
    ProvidersConfig,
    ResourceConfig,
    StorageConfig,
    SystemConfig,
    TenantConfig,
)
from installer_backend.proxy_generator import generate_caddyfile

pytestmark = pytest.mark.unit

_REPO = Path(__file__).resolve().parents[2]
_MANUALS_CADDYFILE = _REPO / "docker" / "caddy-manuals" / "Caddyfile"

_UPSTREAM = "reverse_proxy api-server:8000"


def _config() -> InstallerConfig:
    return InstallerConfig(
        system=SystemConfig(domain="agentic.example.com"),
        resources=ResourceConfig(worker_replicas=2, worker_memory_gib=4),
        storage=StorageConfig(
            data_root="/data/agent-platform",
            minio_bucket="agentic-platform",
            minio_access_key="throwaway-access",
            minio_secret_key="throwaway-secret-value-123",
        ),
        providers=ProvidersConfig(ollama=OllamaProvider(enabled=True, endpoint="http://o:11434")),
        tenant=TenantConfig(tenant_name="Acme", admin_email="admin@example.com"),
        ports=PortsConfig(),
    )


def _api_upstream_blocks(caddyfile: str) -> list[str]:
    """Cada directiva que enruta a la api-server, con su bloque de opciones.

    Un upstream SIN bloque (``reverse_proxy api-server:8000`` a secas) devuelve
    su propia línea, así que aparece en la lista y falla la comprobación en vez
    de desaparecer de ella — que es como una guarda estática se queda en verde
    sin vigilar nada.
    """
    blocks: list[str] = []
    lines = caddyfile.splitlines()
    for index, line in enumerate(lines):
        if _UPSTREAM not in line:
            continue
        if "{" not in line:
            blocks.append(line)
            continue
        depth = 0
        body: list[str] = []
        for current in lines[index:]:
            depth += current.count("{") - current.count("}")
            body.append(current)
            if depth == 0:
                break
        blocks.append("\n".join(body))
    return blocks


def _assert_every_upstream_checks_readyz(caddyfile: str, *, expected: int, origin: str) -> None:
    blocks = _api_upstream_blocks(caddyfile)
    assert len(blocks) >= expected, (
        f"la guarda dejó de encontrar upstreams de la api-server en {origin} "
        f"(vio {len(blocks)}, esperaba >= {expected}); si el enrutado cambió, "
        "actualiza la guarda, no la borres"
    )
    blind = [block for block in blocks if "health_uri /readyz" not in block]
    assert not blind, (
        f"estos upstreams de {origin} mandan tráfico a la api-server sin "
        f"comprobar su readiness: {blind}"
    )


def test_the_generated_caddyfile_gates_traffic_on_readyz() -> None:
    _assert_every_upstream_checks_readyz(
        generate_caddyfile(_config()), expected=2, origin="el Caddyfile generado"
    )


def test_the_manuals_caddyfile_gates_traffic_on_readyz() -> None:
    """El overlay de manuales es el stack que se levanta a diario aquí; si sólo
    lo hiciera el generado, el cableado no se probaría nunca de verdad."""
    assert _MANUALS_CADDYFILE.exists(), f"{_MANUALS_CADDYFILE} se movió"
    _assert_every_upstream_checks_readyz(
        _MANUALS_CADDYFILE.read_text(encoding="utf-8"),
        expected=1,
        origin="docker/caddy-manuals/Caddyfile",
    )


def test_the_container_healthcheck_stays_on_healthz() -> None:
    """La otra mitad: liveness NO se muda a `/readyz`.

    El watchdog reinicia lo `unhealthy`, así que un healthcheck de contenedor
    apuntado a readiness convierte «PostgreSQL caído» en «api-server
    reiniciándose en bucle».
    """
    compose = generate_compose(_config())
    services = compose["services"]
    assert isinstance(services, dict)
    probe = str(services["api-server"]["healthcheck"]["test"])

    assert "/healthz" in probe, f"el healthcheck de la api-server perdió su liveness: {probe}"
    assert "/readyz" not in probe, (
        "el healthcheck del CONTENEDOR no puede consultar readiness: el watchdog "
        "reinicia lo unhealthy, así que una dependencia caída pondría la "
        "api-server en bucle de reinicios en vez de dejarla servir lo que pueda"
    )
