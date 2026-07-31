"""Redis con contraseña, y los puertos de dev sin salir del portátil.

Plan prod-10 `task_prod10_06` (hallazgo secrets-7).

Dos cosas que iban juntas en el hallazgo y que juntas son mucho peores que por
separado:

1. **Redis no pedía autenticación.** No es una caché de resultados: aloja las
   SESIONES de servidor (una sesión revocable vive ahí), el broker de Celery
   —o sea, la capacidad de encolar trabajo para los workers— y los contadores de
   rate limit. Cualquiera con acceso al puerto podía leer sesiones y encolar
   tareas.
2. **El overlay de dev publicaba esos puertos en `0.0.0.0`.** En una máquina
   corporativa eso es toda la LAN: postgres con datos reales, MinIO con los
   adjuntos, Vault, y el Redis de arriba. Un `docker compose -f … -f dev.yml up`
   en una oficina expone el stack entero a la red.

Nada de esto exige un atacante sofisticado: exige un `redis-cli -h <ip-del-
portátil>`.

## Sobre el bind

Se afirma que TODO puerto publicado por el overlay de dev empieza por
`127.0.0.1:`. Enumerar servicios uno a uno dejaría pasar el siguiente que se
añada, que es justo cómo se llegó aquí. La aserción de descubrimiento (§4 de
`verificar-antes-de-implementar.md`) evita que un renombrado del fichero deje la
guarda pasando en vacío.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DOCKER = _REPO_ROOT / "docker"
_CANONICAL = _DOCKER / "docker-compose.yml"
_DEV = _DOCKER / "docker-compose.dev.yml"
_MANUALS = _DOCKER / "docker-compose.manuals.yml"

#: Los overlays que publican puertos en el host para desarrollo. `manuals` no
#: entra: publica el 8080 de Caddy a propósito para ver los manuales.
_DEV_OVERLAYS = (_DEV, _DOCKER / "docker-compose.monitoring.dev.yml")


class _ComposeLoader(yaml.SafeLoader):
    """SafeLoader que tolera las etiquetas propias de Compose (`!reset`,
    `!override`). `docker-compose.dev.yml` usa `volumes: !reset` para descartar
    la lista del base, y `yaml.safe_load` a secas revienta con ella — el mismo
    motivo por el que el hook `check-yaml` corre con `--unsafe`."""


_ComposeLoader.add_multi_constructor(
    "!", lambda loader, suffix, node: loader.construct_sequence(node, deep=True)
)


def _load(path: Path) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_ComposeLoader) or {}


def _redis_command() -> list[str]:
    services = _load(_CANONICAL)["services"]
    command = services["redis"]["command"]
    assert isinstance(command, list), "el command de redis dejó de ser una lista"
    return [str(part) for part in command]


# ---------------------------------------------------------------------------
# (1) Redis pide contraseña
# ---------------------------------------------------------------------------
def test_redis_requires_a_password() -> None:
    command = _redis_command()
    assert "--requirepass" in command, (
        "el servicio redis del compose canónico arranca sin autenticación, y ahí "
        "viven las sesiones y el broker de Celery"
    )
    value = command[command.index("--requirepass") + 1]
    assert value.startswith("${REDIS_PASSWORD:?"), (
        f"la contraseña de redis no es obligatoria ({value!r}): con un default, "
        "un despliegue que olvide la variable vuelve a quedarse sin auth"
    )


def test_redis_healthcheck_authenticates() -> None:
    """Un healthcheck que no autentica devuelve NOAUTH, que no es 0: el
    contenedor se quedaría `unhealthy` para siempre y `depends_on:
    service_healthy` bloquearía el arranque del stack entero."""
    healthcheck = _load(_CANONICAL)["services"]["redis"]["healthcheck"]["test"]
    rendered = " ".join(str(p) for p in healthcheck)
    assert (
        "-a" in rendered.split() or "--pass" in rendered
    ), f"el healthcheck de redis no pasa credencial: {rendered!r}"
    assert "REDIS_PASSWORD" in rendered


def test_every_service_that_talks_to_redis_carries_the_credential() -> None:
    """«Mecanismo entregado, cero llamantes» al revés: poner `requirepass` sin
    actualizar las URLs deja el stack sin arrancar. Se comprueba sobre el compose
    que SÍ define servicios de aplicación."""
    text = _MANUALS.read_text(encoding="utf-8")
    naked = [
        line.strip()
        for line in text.splitlines()
        if "redis://redis:" in line and "redis://:" not in line
    ]
    assert not naked, (
        "estas URLs de Redis no llevan credencial y fallarán con NOAUTH en cuanto "
        f"el stack se levante con requirepass: {naked}"
    )

    # Descubrimiento: si el parser deja de ver URLs, la guarda no vale nada.
    with_credential = [line for line in text.splitlines() if "redis://:" in line]
    assert (
        len(with_credential) >= 10
    ), f"la guarda dejó de encontrar las URLs de Redis (vio {len(with_credential)})"


# ---------------------------------------------------------------------------
# (2) Los puertos de dev no salen del host
# ---------------------------------------------------------------------------
def _published_ports(path: Path) -> list[tuple[str, str]]:
    services = _load(path).get("services") or {}
    found: list[tuple[str, str]] = []
    for name, service in services.items():
        if not isinstance(service, dict):
            continue
        for entry in service.get("ports") or []:
            found.append((name, str(entry)))
    return found


def test_the_guard_finds_the_published_ports() -> None:
    total = [entry for path in _DEV_OVERLAYS if path.is_file() for entry in _published_ports(path)]
    assert len(total) >= 8, f"la guarda dejó de encontrar puertos publicados (vio {total})"


@pytest.mark.parametrize("path", [p for p in _DEV_OVERLAYS if p.is_file()], ids=lambda p: p.name)
def test_dev_overlay_publishes_only_on_loopback(path: Path) -> None:
    offenders = [
        f"{service}: {entry}"
        for service, entry in _published_ports(path)
        if not entry.startswith("127.0.0.1:")
    ]
    assert not offenders, (
        f"{path.name} publica estos puertos en TODAS las interfaces, o sea en la "
        f"LAN corporativa: {offenders}. Prefija con `127.0.0.1:`."
    )
