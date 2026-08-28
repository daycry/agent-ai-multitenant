"""El orden de los cuatro pasos del one-shot, y los porqués de ese orden.

    0. pre-flight de ESQUEMA        ← antes de tocar Vault. Barato y ruidoso.
    1. Vault: init → unseal → KV v2 → políticas
    2. init_tenant(...)             ← 3 filas, rápido. Devuelve `created_user`.
    3. EMITIR LA LÍNEA DE REVELADO  ← con admin_password + admin_user_created
    4. run_seeds(...)               ← los 21 pasos del catálogo. Minutos.

**Por qué el pre-flight va el primero.** Un fallo de esquema descubierto DESPUÉS
del `operator init` cuesta unas unseal keys irrecuperables —un Vault ya
inicializado no se re-inicializa—; descubierto antes cuesta un mensaje.

**Por qué `init_tenant` va ANTES del catálogo**, invirtiendo el orden del
instalador viejo: el revelado tiene que llevar `admin_user_created` dentro, y ese
dato sólo existe después de `init_tenant`. Con el catálogo delante, el revelado
quedaría al otro lado de los minutos de ingesta contra Ollama — exactamente el
tramo que la otra mitad teme por escrito: «el one-shot que inicializa Vault,
emite el revelado, se pone a sembrar el catálogo built-in —minutos— y muere». El
precio de invertirlo es nulo: no hay FK entre las dos siembras.

**Por qué el revelado sale entre el paso 2 y el 4** y no al final: es el único
material sin recuperación de toda la instalación, y el paso 4 es el más largo y
el más frágil. Sale antes de entrar ahí.

**Y qué NO hace este módulo**, cada cosa con su motivo:

* No re-inicializa un Vault ya inicializado (destructivo, sin recuperación).
* No desella tras un reinicio del host por su cuenta: eso es el PASO 0 del
  runbook, con 3 de 5 shares de custodias distintas (ADR 0145, decisión C).
* No implementa auto-unseal, ni transit seal, ni KMS (descartados por el ADR
  0145; adoptarlos reabriría además el ADR 0146).
* No toca el healthcheck del compose ni sondea `/v1/sys/health`, que miente por
  diseño (traduce sellado y sin-inicializar a 200).
* No escribe secretos de plataforma en el KV: escribe POLÍTICAS. Los secretos
  siguen en el `.env` a 0600 y la rotación es de prod-10.
* No migra a Vault los secretos Fernet de SSO / notificaciones / webhooks
  entrantes (ADR 0146, opción B).
* No mintea los tokens de servicio: eso es `scripts/vault-mint-service-tokens.sh`,
  un paso posterior y humano que necesita el root token recién revelado.
* No escribe NADA en disco. Es efímero: quien guarda las unseal keys es el
  instalador, que tiene su escrow. Escribirlas aquí las mandaría a una capa que
  se va con el `--rm` y dejaría una segunda copia donde nadie la borra.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, TextIO

from api_server.bootstrap.database import BootstrapDatabase, assert_schema_ready
from api_server.bootstrap.errors import BootstrapError, DatabaseError, first_line, redact
from api_server.bootstrap.options import BootstrapOptions
from api_server.bootstrap.reveal import Reveal, emit_reveal
from api_server.bootstrap.vault import (
    VaultBootstrapError,
    VaultBootstrapResult,
    VaultClient,
    bootstrap_vault,
)

#: A dónde se habla con Vault desde dentro de la red del stack. El compose ya lo
#: fija en `API_SERVER_VAULT_URL`; esto es sólo el respaldo para un uso directo.
DEFAULT_VAULT_URL = "http://vault:8200"

#: Bytes de entropía de la contraseña del primer System Owner. `token_urlsafe(18)`
#: da 24 caracteres del alfabeto urlsafe (~144 bits): sobra para una credencial
#: que el operador va a cambiar, y cabe en una línea de terminal sin partirse.
_PASSWORD_ENTROPY_BYTES = 18


def mint_admin_password() -> str:
    """La contraseña del primer System Owner, acuñada AQUÍ y CSPRNG.

    No llega por el entorno a propósito: el compose no pasa `INIT_ADMIN_PASSWORD`
    porque ponerla ahí la escribiría en el `.env` del host —legible por
    cualquiera que ya lo tenga y superviviente a la sesión—, y eso no es un
    revelado único sino un secreto en un fichero.
    """

    return secrets.token_urlsafe(_PASSWORD_ENTROPY_BYTES)


class EventLogger(Protocol):
    """La superficie de logging que usa el one-shot.

    Se declara como Protocol para poder inyectar un doble: el repo ya aprendió
    que afirmar sobre logs con `caplog` es frágil porque la app hace
    `logging.disable`. Y para dejar escrito, en un sitio que se comprueba, que de
    todo structlog aquí sólo se usan tres métodos.
    """

    def info(self, event: str, **kwargs: Any) -> Any: ...

    def warning(self, event: str, **kwargs: Any) -> Any: ...

    def error(self, event: str, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class BootstrapDeps:
    """Las costuras del one-shot. Todo lo que habla con el mundo entra por aquí."""

    database: BootstrapDatabase
    vault: VaultClient
    #: Dónde va la línea de revelado. Es un stream y NO el logger: ver
    #: `api_server.bootstrap.reveal` — el masker de PII destruiría el root token.
    stdout: TextIO
    log: EventLogger
    mint_password: Callable[[], str] = field(default=mint_admin_password)
    #: El head de las migraciones que viaja con esta imagen. Vacío = «no consta»,
    #: que se avisa pero no bloquea.
    expected_revisions: tuple[str, ...] = ()
    #: Sólo para el mensaje de error: si Vault no contesta, hay que decir a qué
    #: dirección no contestó.
    vault_url: str = DEFAULT_VAULT_URL


def _run_vault(options: BootstrapOptions, deps: BootstrapDeps) -> VaultBootstrapResult:
    """El paso 1, con los fallos de red traducidos a mensaje.

    `bootstrap_vault` es SÍNCRONO y `hvac` va sobre `requests`, así que esta
    llamada bloquea el bucle de eventos. Aquí es lo correcto: el one-shot es un
    proceso de propósito único y no hay nada más en ese bucle esperando turno.
    Meterlo en un executor sólo añadiría un hilo y un modo de fallo.
    """

    try:
        return bootstrap_vault(
            deps.vault,
            existing_unseal_keys=options.existing_unseal_keys or None,
        )
    except VaultBootstrapError:
        # Ya está explicado y ya lleva su código de salida.
        raise
    except Exception as exc:
        raise VaultBootstrapError(
            f"Vault no ha contestado como se esperaba en {deps.vault_url}: "
            f"{first_line(exc)}. El servicio `vault` tiene que estar arriba (su "
            "healthcheck acepta a propósito un Vault SELLADO y sin inicializar: "
            "desellarlo es justo el trabajo de este one-shot), y sólo es "
            "alcanzable desde dentro de la red del stack — no publica puertos."
        ) from exc


async def _seed_tenant(
    options: BootstrapOptions,
    deps: BootstrapDeps,
    *,
    password: str,
    secret_values: tuple[str, ...],
) -> tuple[bool, str]:
    """El paso 2. Devuelve ``(created_user, contraseña a revelar)``.

    La contraseña a revelar es la minteada **sólo si el usuario nació aquí**. Si
    ya existía, `init_tenant` no ha tocado su hash —su docstring lo dice: «the
    password of an existing user is left untouched»— y revelar la minteada sería
    enseñar una contraseña que la base de datos no ha visto nunca.

    Se mintea igualmente antes de saberlo porque quien resuelve «¿existe?» es el
    propio `init_tenant`, dentro de su transacción, y adelantarse con una
    consulta previa sería una segunda verdad que puede envejecer entre las dos.
    """

    try:
        result = await deps.database.init_tenant(
            tenant_name=options.tenant_name,
            slug=options.slug,
            admin_email=options.admin_email,
            admin_password=password,
        )
    except BootstrapError:
        raise
    except Exception as exc:
        raise DatabaseError(
            redact(
                "La siembra del tenant inicial ha fallado: "
                f"{first_line(exc)}. Vault SÍ ha quedado configurado, así que el "
                "reintento tendrá que aportar sus unseal keys "
                "(--vault-unseal-keys-from).",
                secret_values,
            )
        ) from exc

    # El marcador de respaldo. El instalador lee `admin_user_created` del
    # revelado y, si no está, cae a ESTA línea: son dos vías al mismo dato a
    # propósito, y la segunda es la que sobrevive a que el revelado cambie. Por
    # eso se emite aunque el campo ya viaje en el revelado.
    deps.log.info(
        "init_tenant.completed",
        tenant_id=str(result.tenant_id),
        user_id=str(result.user_id),
        created_org=result.created_org,
        created_user=result.created_user,
        created_membership=result.created_membership,
        is_system_admin=result.is_system_admin,
        is_system_owner=result.is_system_owner,
    )
    return result.created_user, (password if result.created_user else "")


async def run_bootstrap(options: BootstrapOptions, deps: BootstrapDeps) -> Reveal:
    """Ejecuta el one-shot entero y devuelve lo que reveló.

    Levanta un :class:`~api_server.bootstrap.errors.BootstrapError` —nunca una
    traza cruda— en cuanto algo no cuadra. Si el fallo llega DESPUÉS del paso 3,
    el revelado ya ha salido por stdout y el instalador ya lo ha depositado: lo
    que se pierde es el catálogo, que se re-siembra.
    """

    # --- paso 0: el esquema -------------------------------------------------
    state = await deps.database.schema_state()
    if not deps.expected_revisions:
        deps.log.warning(
            "bootstrap.schema.head_unknown",
            applied=list(state.applied_revisions),
            reason=(
                "no se ha podido resolver el head de las migraciones desde este "
                "proceso; se comprueba que las tablas existen, pero no que el "
                "esquema sea el de esta imagen"
            ),
        )
    assert_schema_ready(state, expected_revisions=deps.expected_revisions)
    deps.log.info("bootstrap.schema.ready", applied=list(state.applied_revisions))

    # --- paso 1: Vault ------------------------------------------------------
    vault = _run_vault(options, deps)
    deps.log.info(
        "bootstrap.vault.completed",
        already_initialized=vault.already_initialized,
        kv_mount=vault.kv_mount,
        kv_enabled=vault.kv_enabled,
        policies_written=list(vault.policies_written),
    )

    # --- paso 2: el tenant y el primer System Owner -------------------------
    minted = deps.mint_password()
    init = vault.init
    secret_values = tuple(
        v
        for v in (minted, init.root_token if init else "", *(init.unseal_keys if init else ()))
        if v
    )
    created_user, admin_password = await _seed_tenant(
        options, deps, password=minted, secret_values=secret_values
    )

    # --- paso 3: el revelado, ANTES del paso largo --------------------------
    reveal = Reveal(
        already_initialized=vault.already_initialized,
        unseal_keys=init.unseal_keys if init else (),
        root_token=init.root_token if init else "",
        key_threshold=init.key_threshold if init else 0,
        kv_mount=vault.kv_mount,
        kv_enabled=vault.kv_enabled,
        policies_written=vault.policies_written,
        admin_password=admin_password,
        admin_user_created=created_user,
    )
    emit_reveal(reveal, stream=deps.stdout)

    # --- paso 4: el catálogo built-in (minutos) -----------------------------
    try:
        await deps.database.seed_catalog()
    except BootstrapError:
        raise
    except Exception as exc:
        raise DatabaseError(
            redact(
                f"La siembra del catálogo built-in ha fallado: {first_line(exc)}. "
                "El revelado de arriba YA es válido —Vault quedó configurado y el "
                "tenant creado—: guárdalo. El catálogo se vuelve a sembrar con "
                "`docker compose run --rm bootstrap`, que es idempotente.",
                # `secret_values` y NO `reveal.secret_values`: son casi lo mismo,
                # salvo en el caso en que el admin YA EXISTÍA. Ahí el revelado no
                # lleva contraseña —a propósito— pero la minteada sí llegó a
                # existir en este proceso, así que es la lista larga la que hay
                # que tapar.
                secret_values,
            )
        ) from exc

    deps.log.info(
        "bootstrap.completed",
        already_initialized=reveal.already_initialized,
        admin_user_created=reveal.admin_user_created,
        policies_written=list(reveal.policies_written),
    )
    return reveal
