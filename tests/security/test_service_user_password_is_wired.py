"""La contraseña del rol BYPASSRLS llega al init de PostgreSQL (prod-14 task_prod14_04/05).

`service_user` es el rol con el que corren los workers, el orchestrator y el
notification-dispatcher desde que los cuatro `config.py` cambiaron su default:
**BYPASSRLS sin DDL**. Es decir, la llave que se salta el aislamiento por tenant
de TODA la plataforma — el principio rector 1.

El contrato estaba roto justo en la costura, y de la peor manera posible:

* `docker/postgres/init/05-service-role-password.sh` honra `SERVICE_USER_PASSWORD`
  y cae a un literal de desarrollo (`changeme-service-dev-only`) si no está;
* `docker/docker-compose.yml` **nunca le pasaba la variable**.

O sea que un arranque limpio creaba el rol BYPASSRLS con la contraseña escrita en
este repositorio público, y lo único que lo delataba era una línea en el `stderr`
del contenedor de postgres. Los tests del rol pasaban —el contrato roto no estaba
dentro de la base de datos, sino entre el init y el compose—, así que ninguna
suite lo veía. De ahí esta guarda, que mira exactamente esa costura y las dos
puntas a la vez.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "docker" / "docker-compose.yml"
ENV_EXAMPLE = REPO_ROOT / "docker" / ".env.example"
INIT_DIR = REPO_ROOT / "docker" / "postgres" / "init"

VAR = "SERVICE_USER_PASSWORD"


@pytest.fixture(scope="module")
def postgres_env() -> dict[str, str]:
    raw = yaml.safe_load(COMPOSE.read_text(encoding="utf-8")) or {}
    services = dict(raw.get("services") or {})
    assert "postgres" in services, "el compose canónico dejó de declarar postgres"
    env = services["postgres"].get("environment") or {}
    assert isinstance(env, dict) and env, "postgres sin `environment:`"
    return {str(k): str(v) for k, v in env.items()}


def test_the_init_script_that_consumes_the_variable_still_exists() -> None:
    """No-vacuo: si el init dejara de leerla, esta guarda estaría defendiendo
    un contrato que ya no existe y pasaría por las razones equivocadas."""
    consumers = [p for p in INIT_DIR.glob("*.sh") if VAR in p.read_text(encoding="utf-8")]
    assert consumers, f"ningún script de {INIT_DIR} consume {VAR}"


def test_compose_passes_the_service_user_password_to_postgres(postgres_env: dict[str, str]) -> None:
    assert VAR in postgres_env, (
        f"el servicio postgres no recibe {VAR}: el init cae al literal de "
        f"desarrollo y crea el rol BYPASSRLS con la contraseña publicada en "
        f"este repositorio"
    )


def test_it_is_mandatory_and_says_where_to_put_it(postgres_env: dict[str, str]) -> None:
    """Mismo criterio que las otras dos contraseñas de rol (prod-10 secrets-6):
    `${VAR:?mensaje}`, nunca `${VAR:-default}` ni `${VAR}` a secas — este último
    interpola a cadena vacía y el rol nace sin contraseña."""
    value = postgres_env[VAR]
    match = re.fullmatch(r"\$\{" + VAR + r":\?(?P<msg>[^}]+)\}", value)
    assert match, f"{VAR} debería declararse `${{{VAR}:?…}}`, y vale {value!r}"
    assert ".env" in match.group("msg"), (
        "el mensaje de fallo no dice dónde poner la variable; un arranque que "
        "falla sin instrucción es una sesión de depuración"
    )


def test_env_example_ships_a_dev_value(postgres_env: dict[str, str]) -> None:
    """El contrapeso: si el compose la exige y `.env.example` no la trae, el
    `cp .env.example .env` documentado deja el stack sin arrancar."""
    declared = {
        line.split("=", 1)[0].strip()
        for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.strip().startswith("#")
    }
    assert VAR in declared, f"docker/.env.example no documenta {VAR}"


def test_the_three_role_passwords_are_declared_the_same_way(
    postgres_env: dict[str, str],
) -> None:
    """Los tres roles del init (migrations/app/service) van juntos o no van: una
    asimetría aquí es exactamente cómo se coló el hueco original."""
    for var in ("MIGRATIONS_USER_PASSWORD", "APP_USER_PASSWORD", VAR):
        assert var in postgres_env, f"falta {var}"
        assert postgres_env[var].startswith(f"${{{var}:?"), (
            f"{var} no es obligatoria; los tres roles deben declararse igual"
        )


def test_no_role_password_literal_leaks_into_the_compose(postgres_env: dict[str, str]) -> None:
    """Ninguna de las tres puede llevar el valor en claro."""
    offenders: list[str] = []
    for var in ("MIGRATIONS_USER_PASSWORD", "APP_USER_PASSWORD", VAR):
        value: Any = postgres_env[var]
        if not str(value).startswith("${"):
            offenders.append(f"{var}={value}")
    assert not offenders, f"contraseñas en claro en el compose: {offenders}"
