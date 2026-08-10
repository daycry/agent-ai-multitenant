"""Toda variable `${VAR:?…}` de un compose viaja en `docker/.env.example`.

Por qué esta guarda existe, y por qué es de ARRANQUE y no de higiene.

`${VAR:?mensaje}` es la forma fail-closed que prod-10 llevó al compose canónico
(`tests/unit/test_compose_no_default_credentials.py`): si falta la variable, el
despliegue **aborta** en vez de arrancar con la contraseña publicada en este
repo. Es la decisión correcta. Pero tiene un contrapeso que es fácil olvidar,
porque solo se manifiesta en la máquina de otro:

**la interpolación ocurre al CARGAR el fichero, antes de filtrar por perfiles y
antes del merge de los `-f`.** O sea que una sola variable obligatoria sin
documentar no rompe «su» servicio: rompe el proyecto entero. Y no solo
`up`: también `docker compose ps`, `logs`, `down` y `config` — justo los
comandos con los que alguien intentaría diagnosticar el problema.

Medido en este repo el 2026-08-12, y es lo que motivó el fichero. El servicio
`watchdog` vive bajo `profiles: [watchdog]`, así que un `docker compose up`
normal ni lo mira; sin embargo::

    $ grep -v '^API_SERVER_ALERTS_INGEST_TOKEN=' docker/.env > /tmp/env2
    $ docker compose -f docker/docker-compose.yml --env-file /tmp/env2 config -q
    error while interpolating services.watchdog.environment.WATCHDOG_ALERTS_INGEST_TOKEN:
    required variable API_SERVER_ALERTS_INGEST_TOKEN is missing a value
    exit 1

Un servicio que el operador no levanta tumba el stack completo. La única cosa
que hace segura esa forma fail-closed es que `cp docker/.env.example docker/.env`
deje TODAS las variables puestas.

**Por descubrimiento, no por catálogo.** `test_compose_no_default_credentials.py`
ya cruza `.env.example` contra una lista escrita a mano, y esa lista es
deliberadamente manual (añadir una credencial sin `:?` debe ser una decisión
consciente). Pero para ESTA invariante el catálogo manual es el modo de fallo:
hoy no incluye `API_SERVER_ALERTS_INGEST_TOKEN` ni `SERVICE_USER_PASSWORD`, las
dos últimas que se añadieron. La guarda que impide el fallo de arranque tiene
que enterarse sola de cada `:?` nuevo, sin que nadie se acuerde de registrarlo.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DOCKER = _REPO_ROOT / "docker"
_ENV_EXAMPLE = _DOCKER / ".env.example"

#: `${VAR:?mensaje}` — la forma que aborta el proyecto entero si falta.
_REQUIRED = re.compile(r"\$\{(?P<name>[A-Z][A-Z0-9_]*):\?(?P<msg>[^}]*)\}")

#: Suelo del descubrimiento: si alguien reescribe los compose y el recorrido deja
#: de ver variables, la guarda pasaría vacía diciendo que todo está documentado.
_MINIMUM_EXPECTED = 8


def _compose_files() -> list[Path]:
    return sorted(_DOCKER.glob("docker-compose*.yml"))


def _required_variables() -> dict[str, list[tuple[str, str]]]:
    """``{VAR: [(fichero, mensaje de aborto), …]}`` sobre todos los compose."""
    found: dict[str, list[tuple[str, str]]] = {}
    for path in _compose_files():
        for match in _REQUIRED.finditer(path.read_text(encoding="utf-8")):
            found.setdefault(match.group("name"), []).append((path.name, match.group("msg")))
    return found


def _env_example_keys() -> set[str]:
    """Claves ASIGNADAS (no comentadas) en `.env.example`.

    Una línea `# FOO=bar` documenta pero no define: `cp` la deja igual de
    ausente, así que para esta invariante no cuenta.
    """
    keys: set[str] = set()
    for line in _ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        keys.add(stripped.split("=", 1)[0].strip())
    return keys


_REQUIRED_VARIABLES = _required_variables()


def test_the_guard_actually_found_required_variables() -> None:
    """Sin esto, borrar los `:?` haría pasar el resto del fichero en vacío."""
    assert _ENV_EXAMPLE.is_file(), f"la guarda dejó de encontrar {_ENV_EXAMPLE}"
    assert len(_compose_files()) >= 5, "la guarda dejó de encontrar los ficheros compose"
    assert len(_REQUIRED_VARIABLES) >= _MINIMUM_EXPECTED, (
        f"solo se han descubierto {sorted(_REQUIRED_VARIABLES)}. O alguien ha "
        "cambiado los compose a la forma fail-open `${VAR:-default}` (que es el "
        "hallazgo secrets-6 volviendo), o el descubrimiento está roto."
    )
    assert len(_env_example_keys()) >= 20, "la guarda dejó de parsear docker/.env.example"


@pytest.mark.parametrize("name", sorted(_REQUIRED_VARIABLES))
def test_every_required_variable_is_shipped_in_env_example(name: str) -> None:
    """El contrapeso del fail-closed: `cp .env.example .env` tiene que bastar."""
    sources = ", ".join(f"{file}" for file, _ in _REQUIRED_VARIABLES[name])
    assert name in _env_example_keys(), (
        f"{sources} exige {name} con `${{{name}:?…}}`, pero docker/.env.example no "
        f"la asigna. La interpolación ocurre ANTES del filtrado por perfiles y del "
        f"merge de los `-f`, así que quien copie el ejemplo no se queda sin ese "
        f"servicio: se queda sin poder ejecutar NINGÚN comando de compose, "
        f"tampoco `ps` ni `logs` para averiguar por qué."
    )


@pytest.mark.parametrize("name", sorted(_REQUIRED_VARIABLES))
def test_the_abort_message_says_where_to_set_the_variable(name: str) -> None:
    """Un aborto sin instrucción es una sesión de depuración para el operador."""
    for file, message in _REQUIRED_VARIABLES[name]:
        assert message.strip(), f"{file}: {name} aborta sin explicar nada"
        assert ".env" in message, (
            f"{file}: el mensaje de {name} no dice dónde ponerla ({message!r}). "
            "El operador ve el aborto en un comando que no tiene nada que ver "
            "con el servicio culpable; el mensaje es su único hilo."
        )
