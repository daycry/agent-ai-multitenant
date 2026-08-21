"""El compose canónico no cae en contraseñas publicadas si falta una variable.

Plan prod-10 `task_prod10_05` (hallazgo secrets-6).

`docker/docker-compose.yml` es el fichero de PRODUCCIÓN: el overlay `dev.yml` es
lo que se apila encima para desarrollar. Y sin embargo el canónico llevaba

    POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-changeme-dev-only}

es decir: un despliegue al que se le olvida una variable **arranca igualmente**,
con una contraseña que está escrita en este repositorio público. Silencioso, sin
un aviso, y el operador no tiene forma de notarlo hasta que alguien entra.

El instalador ya hacía lo correcto (`compose_generator._env_ref` emite
`${VAR:?...}` en modo prod). Esta guarda lleva el mismo criterio al compose
canónico: **fallar al arrancar es mejor que arrancar con una credencial
conocida**, porque el fallo se ve y la credencial débil no.

Consecuencia buscada para desarrollo: hace falta `docker/.env`. El propio
`docker/.env.example` lleva desde el día uno la línea «Copy this file to .env»;
lo que cambia es que ahora es obligatorio en vez de opcional. `${VAR:?msg}` se
resuelve al CARGAR cada fichero, antes del merge, así que un overlay con
`${VAR:-default}` NO rescata al base — comprobado con `docker compose config`.
Por eso el default de dev vive en `.env.example` y no en `dev.yml`.

Las dos guardas de este fichero son estáticas ⇒ llevan su aserción de que
**encontraron algo** (`verificar-antes-de-implementar.md` §4).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DOCKER = _REPO_ROOT / "docker"
_CANONICAL = _DOCKER / "docker-compose.yml"
_MONITORING = _DOCKER / "docker-compose.monitoring.yml"
_ENV_EXAMPLE = _DOCKER / ".env.example"

#: Toda variable de este catálogo es una CREDENCIAL: si falta, el servicio no
#: puede arrancar de forma segura, así que tiene que abortar. Se enumeran a mano
#: (en vez de deducirlas por el nombre) para que añadir una credencial nueva sin
#: `:?` sea una decisión consciente y no un descuido.
_MANDATORY_CREDENTIALS: dict[str, Path] = {
    "POSTGRES_PASSWORD": _CANONICAL,
    "MIGRATIONS_USER_PASSWORD": _CANONICAL,
    "APP_USER_PASSWORD": _CANONICAL,
    "MINIO_ROOT_PASSWORD": _CANONICAL,
    "REDIS_PASSWORD": _CANONICAL,
    "SEARXNG_SECRET": _CANONICAL,
    "GRAFANA_ADMIN_PASSWORD": _MONITORING,
}

#: `${VAR:-fallback}` — la forma que hace fail-open.
_WITH_FALLBACK = re.compile(r"\$\{(?P<name>[A-Z][A-Z0-9_]*):-")
#: `${VAR:?mensaje}` — la forma que aborta.
_REQUIRED = re.compile(r"\$\{(?P<name>[A-Z][A-Z0-9_]*):\?(?P<msg>[^}]*)\}")


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_the_guard_finds_the_compose_files() -> None:
    for path in (_CANONICAL, _MONITORING, _ENV_EXAMPLE):
        assert path.is_file(), f"la guarda dejó de encontrar {path}"
    assert len(_text(_CANONICAL).splitlines()) > 100


@pytest.mark.parametrize(("name", "path"), sorted(_MANDATORY_CREDENTIALS.items()))
def test_credential_has_no_silent_fallback(name: str, path: Path) -> None:
    """La forma `${VAR:-…}` sobre una credencial es exactamente el hallazgo."""
    fallbacks = {m.group("name") for m in _WITH_FALLBACK.finditer(_text(path))}
    assert name not in fallbacks, (
        f"{path.name} sigue cayendo en un default para {name}: un despliegue que "
        "olvide la variable arranca con la credencial publicada en este repo"
    )


@pytest.mark.parametrize(("name", "path"), sorted(_MANDATORY_CREDENTIALS.items()))
def test_credential_is_declared_mandatory_with_a_message(name: str, path: Path) -> None:
    """Y no basta con quitar el default: `${VAR}` a secas interpola a cadena
    vacía y postgres arranca SIN contraseña. Tiene que ser `${VAR:?…}`, y el
    mensaje tiene que decir dónde ponerla."""
    required = {m.group("name"): m.group("msg") for m in _REQUIRED.finditer(_text(path))}
    assert name in required, f"{path.name} no declara {name} como obligatoria (`${{{name}:?…}}`)"
    message = required[name]
    assert message.strip(), f"{name} aborta sin explicar nada; el operador merece un mensaje"
    assert ".env" in message, (
        f"el mensaje de {name} no dice dónde ponerla: {message!r}. Un fallo de "
        "arranque sin instrucción es una sesión de depuración."
    )


def test_env_example_documents_every_mandatory_credential() -> None:
    """El contrapeso operativo: si el compose las exige, `.env.example` tiene que
    traerlas todas, o el `cp .env.example .env` deja el stack sin arrancar."""
    declared = {
        line.split("=", 1)[0].strip()
        for line in _text(_ENV_EXAMPLE).splitlines()
        if "=" in line and not line.strip().startswith("#")
    }
    assert len(declared) >= 10, f"la guarda dejó de parsear .env.example (vio {declared})"

    missing = sorted(set(_MANDATORY_CREDENTIALS) - declared)
    assert not missing, f"docker/.env.example no documenta: {missing}"


def test_no_other_credential_shaped_variable_kept_a_fallback() -> None:
    """Descubrimiento, no lista blanca: si mañana alguien añade
    `FOO_PASSWORD:-hunter2`, esto lo caza aunque nadie actualice el catálogo."""
    suspicious: list[str] = []
    for path in (_CANONICAL, _MONITORING):
        for match in _WITH_FALLBACK.finditer(_text(path)):
            name = match.group("name")
            if name.endswith(("PASSWORD", "SECRET", "TOKEN", "KEY")):
                suspicious.append(f"{path.name}:{name}")
    assert not suspicious, (
        "estas variables con pinta de credencial siguen teniendo un default "
        f"silencioso: {sorted(suspicious)}"
    )
