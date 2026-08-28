"""Los argumentos del one-shot: por ENTORNO, nunca por `argv`.

## Por qué el entorno y no la línea de comandos

Un share de Shamir o una contraseña en `argv` queda a la vista de cualquier
usuario del host en `ps` y en el historial del shell. Es la misma razón por la
que `api_server.seeds.init_tenant` lee su contraseña de `INIT_ADMIN_PASSWORD` y
la deja escrita en su docstring, y la que hace que el instalador pase las unseal
keys como PASO A TRAVÉS (`-e NOMBRE`, sin el valor). Aquí la superficie se cierra
entera: el one-shot **no tiene parser de argumentos**.

## Qué NO llega por aquí

`INIT_ADMIN_PASSWORD` no está en esta lista y no es un olvido. El compose no la
pasa a propósito: ponerla ahí la escribiría en el `.env` del host, legible por
cualquiera que ya lo tenga y superviviente a la sesión. Eso no es un revelado
único, es un secreto en un fichero. La mintea el propio one-shot
(:func:`~api_server.bootstrap.runner.mint_admin_password`) y la enseña una vez.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from api_server.bootstrap.errors import OptionsError
from api_server.seeds import PLATFORM_TENANT_SLUG
from api_server.slug import slugify

#: El nombre visible del tenant del operador. Lo pone `_bootstrap_service` del
#: compose generado, SIN el prefijo `API_SERVER_` a propósito: no es un campo de
#: `api_server.config.Settings`, y emitirlo con el prefijo haría creer al
#: contrato de prefijos que existe un campo que no existe.
TENANT_NAME_ENV = "AGENTIC_BOOTSTRAP_TENANT_NAME"

#: El email del primer System Owner. Mismo criterio de prefijo que el anterior.
ADMIN_EMAIL_ENV = "AGENTIC_BOOTSTRAP_ADMIN_EMAIL"

#: Las unseal keys que aporta el operador para reintentar sobre un Vault YA
#: inicializado y SELLADO. El nombre y el separador los fija la otra mitad
#: (`installer_backend.real_step_executor.BOOTSTRAP_UNSEAL_KEYS_ENV`) y aquí se
#: reproducen: son el contrato entre las dos, y no se tocan.
UNSEAL_KEYS_ENV = "AGENTIC_BOOTSTRAP_UNSEAL_KEYS"
UNSEAL_KEYS_SEPARATOR = ","

#: El token con el que ESCRIBIR en un Vault que ya estaba inicializado.
#:
#: El `operator init` es lo único que autentica al cliente; en un re-bootstrap no
#: corre, así que sin esto el montaje del KV y las cuatro políticas salen sin
#: credencial y Vault devuelve 403. Es la variable hermana de
#: :data:`UNSEAL_KEYS_ENV`, con la misma disciplina de no-`argv`, y el mensaje de
#: `api_server.bootstrap.vault` la nombra cuando hace falta.
VAULT_TOKEN_ENV = "AGENTIC_BOOTSTRAP_VAULT_TOKEN"

#: El ancho de `organizations.slug`: `String(64)`, `nullable=False`, `unique`
#: (`db/models.py`), y así lo crea `0001_initial` en columna.
#:
#: **De qué protege este número, que es la parte que no se puede borrar.**
#: `TenantConfig.tenant_name` del instalador admite 120 caracteres y PostgreSQL
#: **no trunca**: un slug de más de 64 levanta `value too long for type character
#: varying(64)` (asyncpg `StringDataRightTruncationError` → SQLAlchemy
#: `DataError`). Y lo hacía en el ÚLTIMO paso de la instalación, que en este
#: diseño es *después* de que Vault haya emitido unas unseal keys que se muestran
#: exactamente una vez. Un guardarraíl que se borra sin decir de qué protegía
#: vuelve como bug — que es literalmente lo que pasó aquí.
#:
#: Va explícito y no se deja el `max_length=60` por defecto de `slugify` para que
#: el número quede atado al ancho de la columna; hay un test que cruza los dos.
ORG_SLUG_MAX_LENGTH = 64

#: Slugs que el one-shot no puede usar para el tenant del operador.
#:
#: `init_tenant` resuelve la organización POR SLUG, y `PLATFORM_TENANT_SLUG` es
#: literalmente `platform`. Un nombre que slugifique a `platform` haría que el
#: primer System Owner colgara del **tenant de plataforma** en vez del suyo, y
#: sin ningún error: para `init_tenant` ése es el camino idempotente normal.
RESERVED_SLUGS = frozenset({PLATFORM_TENANT_SLUG})


@dataclass(frozen=True)
class BootstrapOptions:
    """Lo que el one-shot recibió, ya validado.

    ``__repr__`` va redactado: dentro hay unseal keys y, en un re-bootstrap, un
    token de Vault. Lo que NO es secreto sí se ve — un repr que tapa el nombre
    del tenant no protege nada y deja el diagnóstico a ciegas.
    """

    tenant_name: str
    slug: str
    admin_email: str
    existing_unseal_keys: tuple[str, ...]
    vault_token: str | None

    def __repr__(self) -> str:
        return (
            f"BootstrapOptions(tenant_name={self.tenant_name!r}, slug={self.slug!r}, "
            f"admin_email={self.admin_email!r}, "
            f"existing_unseal_keys=<{len(self.existing_unseal_keys)} shares redactados>, "
            f"vault_token={'<redactado>' if self.vault_token else None})"
        )

    __str__ = __repr__


def tenant_slug(tenant_name: str) -> str:
    """El slug de la organización del operador, con su guardarraíl.

    Se usa `api_server.slug.slugify` y no una copia propia: vive en esta misma
    imagen y hace todo lo que hacía el `_slugify` retirado del instalador **y una
    cosa más** — translitera acentos por NFKD (`Dirección` → `direccion`, no
    `direcci-n`) y corta en frontera de palabra, no a media palabra.

    Tres cosas van con el cap y ninguna es opcional:

    1. el ``max_length`` explícito, atado al ancho de la columna;
    2. el respaldo: un nombre sin caracteres ascii-safe (cirílico, CJK, `"!!!"`)
       da ``untitled``. Feo, pero instala; lo que no puede es reventar;
    3. la reserva de ``platform`` — ver :data:`RESERVED_SLUGS`.
    """

    slug = slugify(tenant_name, max_length=ORG_SLUG_MAX_LENGTH)
    if slug in RESERVED_SLUGS:
        raise OptionsError(
            f"El nombre de tenant {tenant_name!r} se convierte en el slug "
            f"{slug!r}, que está reservado para el tenant de PLATAFORMA "
            f"(PLATFORM_TENANT_SLUG={PLATFORM_TENANT_SLUG!r}). Si se dejara "
            "pasar, el primer System Owner quedaría colgado del tenant de "
            "plataforma en vez del tuyo, y sin ningún error visible. Cambia "
            f"{TENANT_NAME_ENV} en el install.yaml y vuelve a ejecutar."
        )
    return slug


def _required(env: Mapping[str, str], name: str, *, what: str) -> str:
    value = (env.get(name) or "").strip()
    if not value:
        raise OptionsError(
            f"Falta la variable de entorno {name} ({what}). La pone el servicio "
            "`bootstrap` del compose generado a partir del install.yaml: si no "
            "está, o estás ejecutando el módulo a mano fuera del stack, o el "
            "compose se generó con una versión anterior del instalador."
        )
    return value


def parse_options(env: Mapping[str, str]) -> BootstrapOptions:
    """Lee y valida los argumentos del one-shot del entorno. Nada de `argv`."""

    tenant_name = _required(env, TENANT_NAME_ENV, what="el nombre del tenant inicial")
    admin_email = _required(env, ADMIN_EMAIL_ENV, what="el email del primer System Owner")

    raw_keys = env.get(UNSEAL_KEYS_ENV) or ""
    keys = tuple(part.strip() for part in raw_keys.split(UNSEAL_KEYS_SEPARATOR) if part.strip())

    token = (env.get(VAULT_TOKEN_ENV) or "").strip() or None

    return BootstrapOptions(
        tenant_name=tenant_name,
        slug=tenant_slug(tenant_name),
        admin_email=admin_email.lower(),
        existing_unseal_keys=keys,
        vault_token=token,
    )
