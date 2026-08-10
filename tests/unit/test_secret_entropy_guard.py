"""Sin `environment` explícito no hay secretos default.

Plan prod-10 `task_prod10_04` (hallazgo secrets-3).

## El fail-open que estaba en el DEFAULT del propio interruptor

`prod-09 task_prod09_02` ya cerró `environment` a `{dev, staging, prod}` y
reescribió el guard anti-defaults como «salta sólo en dev» en vez de «aplica en
staging o prod». Lo que quedó abierto es más sutil: **el campo tiene
`default="dev"`**. Un despliegue que simplemente OLVIDE
`API_SERVER_ENVIRONMENT` arranca creyéndose dev y corre con la JWT secret que
está publicada en GitHub.

No es hipotético. Verificado el 2026-07-31 sobre este repo: el servicio
`api-server` de `docker/docker-compose.manuals.yml` **no declaraba** la variable
y sí declaraba `API_SERVER_JWT_SECRET: dev-only-jwt-secret-change-me` con un DSN
apuntando a `postgres:5432`. Los otros tres servicios que ejecutan código de
`api_server.*` sí la declaran desde el 2026-07-30 (ADR 0136); el propio
api-server era el que faltaba.

Es el modo de fallo nº3 de `verificar-antes-de-implementar.md`: la premisa «si no
hay X entonces Y» falla justo donde X falta **por diseño** — aquí, el default del
campo.

## El criterio, y por qué no es «exigir la variable siempre»

Exigirla rompería el arranque local de cualquiera (y media suite). Un `dev` NO
declarado se sigue aceptando si el despliegue **parece local** (el DSN de la BD
apunta a localhost/127.0.0.1). Si apunta a un host de verdad, se cae por el guard
normal — que sólo protesta si además hay un secreto de dev. O sea: el stack que
el instalador genera con secretos reales y sin la variable NO se rompe; el que
olvidó la variable Y lleva `changeme`, sí.

Se pesan a propósito los dos lados: los tres primeros tests son RECHAZOS (un
guard vale lo que se niega a aceptar) y los tres siguientes son CONTRAPESOS (un
falso positivo aquí no es un aviso, es un servicio que no arranca).

## La segunda mitad: el suelo de longitud y entropía (2026-08-10)

El marcador-substring sólo sabe reconocer los defaults que este repo publica.
`"a" * 48` no lleva ninguno, así que pasaba — y firma sesiones igual de bien que
la cadena que genera el instalador. El plan pedía complementarlo con «un mínimo
de 24 caracteres y rechazo de valores de entropía trivial».

Se aplica **sólo con `environment` declarado explícitamente a `staging`/`prod`**,
que es el ámbito que pide el plan y el que acota el riesgo 2: un falso positivo
aquí no es un aviso, es un servicio que no arranca. El camino de «dev implícito +
BD remota» sigue rechazando únicamente lo inequívoco (marcador de dev).

Dos criterios, porque uno solo se esquiva sin querer:

* **longitud ≥ 24** — un secreto corto es adivinable aunque sea aleatorio;
* **variedad**: al menos 8 caracteres DISTINTOS y una entropía de Shannon ≥ 2
  bits por carácter. Lo primero tumba `"x" * 48`; lo segundo tumba
  `"a"*40 + "bcdefghi"`, que tiene 9 distintos y sigue siendo trivial.

Los umbrales están deliberadamente bajos: `secrets.token_urlsafe(36)` (lo que
genera el instalador) da ~30 caracteres distintos y ~5,3 bits/carácter, o sea
pasa con seis veces de margen. Lo que se persigue es el relleno de plantilla, no
la contraseña mediocre de un operador.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

import pytest
import yaml
from api_server.config import Settings
from pydantic import ValidationError

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]

_DEV_JWT = "dev-only-jwt-secret-change-me"
_REMOTE_DSN = "postgresql+asyncpg://app_user:pw@postgres:5432/agentic"


def _strong() -> str:
    """Un secreto como los que genera el instalador: largo y de alta entropía."""
    return secrets.token_urlsafe(36)


def _real(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "jwt_secret": _strong(),
        "internal_token_secret": _strong(),
        "review_url_signing_secret": _strong(),
        "sso_encryption_key": _strong(),
        "notification_encryption_key": _strong(),
        "incoming_webhook_encryption_key": _strong(),
        "minio_secret_key": _strong(),
        "minio_access_key": "prod-access-key",
        "database_url": "postgresql+asyncpg://app_user:S3cr3tA@db.internal/agentic",
        "admin_database_url": "postgresql+asyncpg://service_user:S3cr3tM@db.internal/agentic",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Rechazos
# ---------------------------------------------------------------------------
def test_implicit_dev_with_a_remote_database_and_a_dev_secret_refuses_to_start() -> None:
    """El hallazgo secrets-3, literal: nadie puso `API_SERVER_ENVIRONMENT`, la BD
    es un host de verdad y la JWT secret es la pública de GitHub."""
    with pytest.raises(ValidationError) as excinfo:
        Settings(**_real(jwt_secret=_DEV_JWT, database_url=_REMOTE_DSN))

    rendered = str(excinfo.value)
    # El mensaje tiene que decir las DOS cosas: qué secreto y qué variable falta.
    # Sin la segunda, el operador lee «environment='dev' pero usas defaults de
    # dev» y concluye que el guard está roto.
    assert "API_SERVER_JWT_SECRET" in rendered
    assert "API_SERVER_ENVIRONMENT" in rendered


def test_implicit_dev_with_a_remote_minio_credential_also_refuses() -> None:
    """No es sólo la JWT secret: cualquiera de las familias con default de dev.
    `minio_secret_key` es la que usa la ingesta de KB desde los workers."""
    with pytest.raises(ValidationError):
        Settings(**_real(minio_secret_key="changeme-dev-only", database_url=_REMOTE_DSN))


def test_an_unparseable_dsn_counts_as_remote() -> None:
    """Fail-CLOSED ante la duda: si el DSN no se puede leer, no se concede el
    beneficio de «parece local». Equivocarse hacia este lado cuesta un mensaje de
    error; hacia el otro, una JWT secret pública en producción."""
    with pytest.raises(ValidationError):
        Settings(**_real(jwt_secret=_DEV_JWT, database_url="not-a-dsn-at-all"))


# ---------------------------------------------------------------------------
# Contrapesos — que el guard siga siendo usable
# ---------------------------------------------------------------------------
def test_declaring_dev_explicitly_still_allows_the_dev_defaults() -> None:
    """Declarar `dev` a mano sigue siendo un permiso válido aunque la BD sea un
    contenedor. Es lo que hacen los composes del repo, y romperlo dejaría el
    stack de desarrollo sin arrancar."""
    settings = Settings(environment="dev", jwt_secret=_DEV_JWT, database_url=_REMOTE_DSN)
    assert settings.environment == "dev"


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql+asyncpg://app_user:changeme-app-dev-only@localhost:15432/agentic_platform",
        "postgresql+asyncpg://app_user:pw@127.0.0.1:15432/agentic_platform",
    ],
)
def test_implicit_dev_on_a_localhost_deployment_is_still_allowed(dsn: str) -> None:
    """El desarrollador que arranca `uvicorn` a pelo contra el postgres del
    compose no tiene que exportar nada. Es exactamente lo que hace
    `scripts/dev/up.ps1` y lo que hace el conftest de integración."""
    settings = Settings(database_url=dsn, jwt_secret=_DEV_JWT)
    assert settings.environment == "dev"


def test_bare_settings_still_construct() -> None:
    """El caso más importante de todos: `Settings()` sin nada. Todos los defaults
    son de dev y el DSN por defecto es localhost, así que tiene que seguir
    funcionando — si no, el arranque local y media suite se caen."""
    assert Settings().environment == "dev"


def test_implicit_dev_with_a_remote_database_but_real_secrets_is_accepted() -> None:
    """Radio de explosión acotado a propósito: el stack que el instalador genera
    con secretos de verdad NO se rompe por no declarar la variable. Sólo se
    rechaza la combinación inequívocamente errónea: entorno sin declarar +
    credencial de dev."""
    assert Settings(**_real()).environment == "dev"


def test_the_manuals_stack_declares_the_variable_for_the_api_server_itself() -> None:
    """El cableado, no sólo el mecanismo (verificar-antes-de-implementar §5).

    El guard de arriba convierte «api-server sin `API_SERVER_ENVIRONMENT` + DSN
    remoto + secreto de dev» en un arranque fallido. El stack de manuales era
    EXACTAMENTE esa combinación, así que el cambio sin esta declaración habría
    dejado al operador sin api-server. Se afirma sobre el compose para que un
    borrado accidental de la línea salga en rojo aquí y no en el arranque.
    """
    compose = yaml.safe_load(
        (_REPO_ROOT / "docker" / "docker-compose.manuals.yml").read_text(encoding="utf-8")
    )
    services = compose.get("services") or {}
    assert "api-server" in services, "el compose de manuales perdió el servicio api-server"
    env = services["api-server"].get("environment") or {}
    assert env.get("API_SERVER_ENVIRONMENT") == "dev", (
        "el api-server del stack de manuales no declara API_SERVER_ENVIRONMENT: "
        "con el DSN apuntando a `postgres:5432` y la JWT secret de dev, el guard "
        "anti-defaults lo tumbará al arrancar"
    )


def test_explicit_staging_and_prod_are_unaffected() -> None:
    """La rama que ya existía sigue igual: declarar staging/prod aplica el guard
    completo, con o sin este cambio."""
    for env in ("staging", "prod"):
        assert Settings(environment=env, **_real()).environment == env
        with pytest.raises(ValidationError):
            Settings(environment=env, **_real(jwt_secret=_DEV_JWT))


# ---------------------------------------------------------------------------
# Suelo de longitud y entropía (segunda mitad de task_prod10_04)
# ---------------------------------------------------------------------------
#: Las familias que el suelo cubre, con un valor trivial que HOY pasaba el
#: marcador-substring. Se parametriza para que añadir una familia al config sin
#: añadirla aquí se note (el test de descubrimiento de abajo lo comprueba).
_TRIVIAL_BY_FAMILY = {
    "jwt_secret": "x" * 48,
    "internal_token_secret": "q" * 48,
    "review_url_signing_secret": "y" * 48,
    "sso_encryption_key": "w" * 48,
    "notification_encryption_key": "n" * 48,
    "incoming_webhook_encryption_key": "i" * 48,
    "minio_secret_key": "z" * 48,
    # Sólo cuenta cuando es DEDICADA: si hereda el anillo de SSO, ese anillo ya
    # lo cubre la entrada de arriba.
    "mfa_encryption_key": "m" * 48,
}


@pytest.mark.parametrize("field", sorted(_TRIVIAL_BY_FAMILY))
@pytest.mark.parametrize("env", ["staging", "prod"])
def test_a_single_repeated_character_is_rejected(env: str, field: str) -> None:
    """`"x" * 48` no lleva marcador de dev, mide 48 caracteres y hoy arrancaba
    producción. Firma sesiones y cifra secretos exactamente igual de bien que la
    cadena del instalador — y se adivina en un intento."""
    with pytest.raises(ValidationError) as excinfo:
        Settings(environment=env, **_real(**{field: _TRIVIAL_BY_FAMILY[field]}))

    rendered = str(excinfo.value)
    assert field.upper() in rendered.upper(), (
        f"el error no nombra la variable ofensora; el operador no sabrá cuál "
        f"cambiar:\n{rendered}"
    )


@pytest.mark.parametrize("env", ["staging", "prod"])
def test_a_short_secret_is_rejected_even_if_random(env: str) -> None:
    """Aleatorio pero corto sigue siendo adivinable. 24 es el suelo que pide el
    plan; el instalador genera 48."""
    with pytest.raises(ValidationError) as excinfo:
        Settings(environment=env, **_real(sso_encryption_key=secrets.token_urlsafe(8)[:12]))

    assert "SSO_ENCRYPTION_KEY" in str(excinfo.value).upper()


@pytest.mark.parametrize("env", ["staging", "prod"])
def test_a_long_value_with_almost_no_variety_is_rejected(env: str) -> None:
    """El contraejemplo del criterio de «caracteres distintos» a secas:
    `"a"*40 + "bcdefghi"` tiene 9 distintos y sigue siendo relleno. Lo caza la
    entropía de Shannon."""
    with pytest.raises(ValidationError):
        Settings(environment=env, **_real(notification_encryption_key="a" * 40 + "bcdefghi"))


# --- contrapesos: lo que NO puede romper -----------------------------------
@pytest.mark.parametrize("env", ["staging", "prod"])
def test_installer_grade_secrets_pass_with_margin(env: str) -> None:
    """El caso que importa no romper: lo que genera el instalador
    (`secrets.token_urlsafe(36)`) pasa con seis veces de margen."""
    assert Settings(environment=env, **_real()).environment == env


@pytest.mark.parametrize("env", ["staging", "prod"])
def test_a_human_chosen_passphrase_still_passes(env: str) -> None:
    """Umbral deliberadamente bajo: se persigue el relleno de plantilla, no la
    contraseña mediocre de un operador con prisa. Romper el arranque de un stack
    real por severidad de más es el riesgo 2 del plan."""
    passphrase = "correct-horse-battery-staple-2026"
    assert Settings(environment=env, **_real(sso_encryption_key=passphrase)).environment == env


def test_dev_is_untouched_by_the_entropy_floor() -> None:
    """En dev NADA de esto aplica: los defaults del repo son literalmente
    `dev-only-…` y media suite construye `Settings()` a pelo."""
    assert Settings().environment == "dev"
    assert Settings(environment="dev", jwt_secret="x" * 48).environment == "dev"


def test_implicit_dev_with_a_remote_dsn_does_not_apply_the_entropy_floor() -> None:
    """Acotación deliberada del radio: sin `environment` declarado se sigue
    rechazando SÓLO lo inequívoco (un marcador de dev). Un stack del instalador
    que olvidó la variable y lleva un secreto flojo arranca — y se queja el
    catálogo de variables, no el arranque."""
    assert Settings(**_real(jwt_secret="x" * 48, database_url=_REMOTE_DSN)).environment == "dev"


def test_the_guard_covers_every_family_the_config_declares() -> None:
    """Guarda de descubrimiento (§4): si mañana se añade una familia de secretos
    al config y no entra en el suelo, este test lo dice — en vez de dejar la
    parametrización de arriba pasando en vacío sobre las de siempre."""
    from api_server.config import entropy_checked_secret_fields

    declared = set(entropy_checked_secret_fields())
    assert declared, "la lista de familias con suelo de entropía está vacía"
    missing = declared - set(_TRIVIAL_BY_FAMILY)
    assert not missing, (
        f"estas familias tienen suelo de entropía en el config pero no se prueban "
        f"aquí: {sorted(missing)}"
    )
