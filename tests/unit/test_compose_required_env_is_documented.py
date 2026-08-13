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

**Y el tercer consumidor del contrato es la CI**, que es el agujero por el que se
escapó el fallo de arriba (2026-08-13, PR #67). Este fichero comprobaba que
`.env.example` estuviese completo —lo estaba— pero no que alguien lo COPIE en el
runner. El job «Integration tests» no lo copiaba: enumeraba seis credenciales en
un bloque `env:` escrito a mano, o sea la misma lista manual que este fichero
declara modo de fallo, y se quedó atrás en cuanto prod-14 añadió
`SERVICE_USER_PASSWORD` y prod-08 `API_SERVER_ALERTS_INGEST_TOKEN`::

    error while interpolating services.postgres.environment.SERVICE_USER_PASSWORD:
    required variable SERVICE_USER_PASSWORD is missing a value

En local no se veía —ahí hay `docker/.env`—, así que la CI llevaba roja desde que
prod-10 aterrizó sin que el rojo señalara a nadie en concreto. Por eso
`test_ci_materialises_docker_env_before_the_first_compose_command` fija la otra
mitad: en la CI el `cp` tiene que existir y tiene que ocurrir ANTES del primer
`docker compose`, porque `config`, `up`, `logs` y hasta el `down` del teardown
abortan todos igual.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DOCKER = _REPO_ROOT / "docker"
_ENV_EXAMPLE = _DOCKER / ".env.example"
_CI_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "ci.yml"

#: El `cp` que materializa el `.env` en el runner. Se busca por la forma exacta
#: que documenta `.env.example` (y que aparece en los mensajes de aborto del
#: compose), no por «alguna línea con .env.example»: un `grep` laxo lo daría por
#: hecho con un comentario que sólo lo menciona.
_MATERIALISE_ENV = re.compile(r"cp\s+docker/\.env\.example\s+docker/\.env")

#: Cualquier invocación real de compose sobre los ficheros del repo.
_COMPOSE_INVOCATION = re.compile(r"docker\s+compose\s+(?:-f|--file|--env-file|--profile)")

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


def _ci_shell_lines() -> list[tuple[int, str]]:
    """Las líneas EJECUTABLES de `ci.yml`: sin comentarios YAML ni de shell.

    Un comentario que explica el `cp` no materializa nada, y un comentario que
    menciona `docker compose` no lo ejecuta. Contar cualquiera de los dos haría
    que la guarda pasara por la prosa en vez de por lo que corre el runner.
    """
    lineas: list[tuple[int, str]] = []
    for numero, linea in enumerate(_CI_WORKFLOW.read_text(encoding="utf-8").splitlines(), 1):
        limpia = linea.strip()
        if not limpia or limpia.startswith("#"):
            continue
        lineas.append((numero, limpia))
    return lineas


def test_ci_materialises_docker_env_before_the_first_compose_command() -> None:
    """En el runner no hay `docker/.env`; sin copiarlo, NINGÚN compose funciona.

    Se exige el orden y no sólo la presencia porque el aborto no distingue
    subcomandos: un `cp` colocado después del `docker compose config` dejaría el
    job rojo exactamente igual, y con la copia hecha a tiempo para el resto de
    pasos — que es la variante más confusa de todas.
    """
    assert _CI_WORKFLOW.is_file(), f"la guarda dejó de encontrar {_CI_WORKFLOW}"
    lineas = _ci_shell_lines()

    copias = [n for n, texto in lineas if _MATERIALISE_ENV.search(texto)]
    composes = [n for n, texto in lineas if _COMPOSE_INVOCATION.search(texto)]

    assert composes, (
        "la guarda no ha encontrado ninguna invocación de `docker compose` en "
        f"{_CI_WORKFLOW.name}. O se han movido los tests de integración a otro "
        "workflow (y esta guarda debe seguirlos), o el recorrido está roto y "
        "estaría pasando en vacío."
    )
    assert copias, (
        f"{_CI_WORKFLOW.name} ejecuta `docker compose` (línea {composes[0]}) pero "
        "nunca hace `cp docker/.env.example docker/.env`. El runner no tiene ese "
        "fichero y cada credencial del compose es `${VAR:?…}`: el job aborta en el "
        "primer comando de compose, incluidos `logs` y el `down` del teardown. "
        "Enumerar las variables en un bloque `env:` del job NO sustituye al `cp`: "
        "esa lista se escribe a mano y ya se quedó atrás una vez (PR #67)."
    )
    assert copias[0] < composes[0], (
        f"el `cp docker/.env.example docker/.env` está en la línea {copias[0]}, "
        f"después del primer `docker compose` (línea {composes[0]}). La "
        "interpolación ocurre al cargar el fichero: ese primer comando aborta."
    )
