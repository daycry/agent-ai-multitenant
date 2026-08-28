"""Init de Vault + desellado + KV v2 + políticas por servicio — idempotente.

Es la mitad de Vault del one-shot del paso 8 del ADR 0161, y corre **dentro de
la red del stack**: `http://vault:8200`. Desde el host no era alcanzable —el
servicio `vault` no publica ningún puerto, el único que publica es Caddy (ADR
0061)— y por ahí murió el camino anterior, con una traza cruda.

## Por qué esto vive aquí y no se importa del instalador

El catálogo de políticas nació en `installer_backend.vault_bootstrap`, y sigue
ahí: `tests/unit/test_vault_service_tokens.py` lo importa para cruzar los cuatro
nombres con `scripts/vault-mint-service-tokens.sh`. Pero el api-server **no
depende del instalador** —es una app de arranque que no viaja en esta imagen—,
así que aquí se reimplementa. El precedente está escrito y es el mismo caso:
`workers/credential_rotation.py` reproduce el layout del KV a mano «without
importing it (workers must stay import-clean of the installer app)».

Reimplementar sin guarda devolvería la deriva por donde aquel test la cerró, así
que hay una: `test_el_catalogo_de_politicas_no_puede_derivar_del_que_vigila_el_instalador`
cruza las dos listas regla por regla.

## Qué hace, en qué orden, y qué protege el orden

    0. validar el umbral      ← ANTES de tocar Vault: un init con umbral
                                imposible es irreversible
    1. ¿ya inicializado?
    2a. no  → operator init + desellado con las claves recién acuñadas
    2b. sí y SELLADO → desellado con las que aporta el operador (o error)
    2c. sí y abierto → no se toca el sello
    3. KV v2 en `secret/`     ← idempotente
    4. las cuatro políticas   ← idempotente por definición

Los pasos 3 y 4 corren en los TRES caminos: un re-bootstrap converge la
configuración. El init sólo se intenta en 2a: nunca hay dos `operator init`.

## Los dos desellados, que no son el mismo

1. **El que sigue al init, en esta misma pasada: es del bootstrap.** Las claves
   están vivas en memoria en ese instante y en ninguno más, y un Vault sellado
   no contesta nada — sin desellar no existirían los pasos 3 y 4. El ADR 0145 no
   dice nada en contra: su decisión 2 (desellado manual) trata el **reinicio del
   host**, no el arranque en frío.
2. **El de cada reinicio del host: acto humano, y este módulo no participa.** Es
   el PASO 0 de `docs/06-runbooks/restart-services.md`: 3 de 5 shares, de
   custodias distintas. Aquí no se puede hacer (no tenemos las claves: se
   entregaron una vez y no hay recuperación) y no se debe intentar.

## Seguridad

Las unseal keys y el root token viven **sólo** dentro de :class:`VaultInitResult`
y :class:`VaultBootstrapResult`, cuyos ``__repr__``/``__str__`` van redactados
para que un frame de traceback o una línea de log suelta no los deje escritos
donde nadie los borra. Nada de este módulo escribe a disco ni loguea. Los
documentos HCL NO llevan secreto: sólo rutas y capacidades.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from api_server.bootstrap.errors import BootstrapError, ExitCode
from api_server.bootstrap.options import VAULT_TOKEN_ENV

# ---------------------------------------------------------------------------
# Layout del KV de plataforma. Un único motor KV v2 montado en `secret/`, con
# una ruta estable por dominio. Estas rutas YA las consume código vivo:
# `workers/credential_rotation.py` y `scripts/rotate-platform-secret.sh` las
# reproducen por literal. Cambiar el mount o una ruta rompe la rotación de
# prod-10 EN SILENCIO — hay un test que lo vigila.
# ---------------------------------------------------------------------------
#: El mount del KV v2 de plataforma. KV v2 anida los datos bajo
#: ``<mount>/data/<path>`` y los listados bajo ``<mount>/metadata/<path>``.
PLATFORM_KV_MOUNT = "secret"

#: ``options.version`` con el que se habilita el motor. Versionado a propósito:
#: una rotación conserva la versión anterior y un servicio puede volver a ella.
KV_VERSION = "2"

#: Dominios lógicos bajo el mount. Cada uno agrupa los secretos que gobierna una
#: preocupación; las políticas de abajo dan `read` sobre exactamente el
#: subconjunto que cada servicio resuelve en tiempo de ejecución.
SECRET_PATH_DATABASE = "platform/database"  # DSNs / contraseñas de rol
SECRET_PATH_MINIO = "platform/minio"  # access key / secret key del object store
SECRET_PATH_JWT = "platform/jwt"  # firma JWT + firma de review-url
SECRET_PATH_ENCRYPTION = "platform/encryption"  # claves SSO / notif / webhook
SECRET_PATH_LLM = "platform/llm-providers"  # credenciales de proveedor (ADR 0021)

#: El reparto de Shamir por defecto: cinco shares, tres para abrir. Es el que
#: documenta el runbook de reinicio («3 de 5, de custodias distintas»).
DEFAULT_KEY_SHARES = 5
DEFAULT_KEY_THRESHOLD = 3


class VaultBootstrapError(BootstrapError):
    """El bootstrap de Vault no puede continuar.

    El mensaje se le enseña al operador, así que **jamás** lleva un secreto: ni
    una unseal key, ni el root token.
    """

    exit_code = ExitCode.VAULT


@dataclass(frozen=True)
class PolicyRule:
    """Un grant ``path "<…>" { capabilities = [...] }`` dentro de una política.

    ``path`` es la ruta LÓGICA del secreto (``platform/database``); el renderer
    la expande a la del motor (``<mount>/data/<path>``, y además
    ``<mount>/metadata/<path>`` cuando hay `list`).
    """

    path: str
    capabilities: tuple[str, ...]


@dataclass(frozen=True)
class VaultPolicy:
    """Una política ACL con nombre: la identidad de un servicio y sus grants.

    Un documento de política NO lleva secreto: sólo rutas y capacidades.
    """

    name: str
    rules: tuple[PolicyRule, ...]

    def render_hcl(self, *, mount: str = PLATFORM_KV_MOUNT) -> str:
        """Serializa la política al HCL que guarda Vault.

        La salida es DETERMINISTA a propósito: así el documento generado se
        puede aseverar en un test en vez de confiar en que «se ve bien».
        """

        blocks: list[str] = []
        for rule in self.rules:
            caps = ", ".join(f'"{c}"' for c in rule.capabilities)
            blocks.append(f'path "{mount}/data/{rule.path}" {{\n  capabilities = [{caps}]\n}}')
            if "list" in rule.capabilities:
                # KV v2 guarda los listados bajo `metadata`, no bajo `data`.
                blocks.append(
                    f'path "{mount}/metadata/{rule.path}" {{\n  capabilities = ["list", "read"]\n}}'
                )
        return "\n\n".join(blocks) + "\n"


@dataclass(frozen=True)
class MountInfo:
    """Un motor de secretos ya montado: su tipo y —si es KV— su VERSIÓN.

    La versión no es decorado. ``list_mounts`` devuelve el tipo ``kv`` para KV v1
    y para KV v2 por igual (la versión vive en ``options``), así que un `secret/`
    montado como v1 pasaba desapercibido y las políticas se escribían sobre
    rutas ``secret/data/...`` que en v1 no existen. El fallo aparecía después,
    en la primera lectura de un servicio, como un `permission denied` sin
    relación aparente con la instalación.
    """

    engine_type: str
    kv_version: str | None


@dataclass(frozen=True)
class VaultInitResult:
    """La salida de `vault operator init` — se enseña UNA vez y no hay recuperación.

    ``__repr__``/``__str__`` van redactados: basta un frame de traceback o un
    `log.info(result)` para dejar cinco unseal keys escritas para siempre.
    """

    unseal_keys: tuple[str, ...]
    root_token: str
    key_threshold: int

    def __repr__(self) -> str:
        return "VaultInitResult(<redacted: se muestra una vez, sin recuperación, nunca en un log>)"

    __str__ = __repr__


@dataclass(frozen=True)
class VaultBootstrapResult:
    """Lo que hizo :func:`bootstrap_vault`.

    ``init`` lleva el material sólo cuando **esta** pasada inicializó Vault; es
    ``None`` en un re-bootstrap, porque el material se entregó en el primero y no
    hay recuperación. ``kv_enabled``/``policies_written`` registran la
    reconciliación, que corre en los tres caminos.
    """

    init: VaultInitResult | None
    already_initialized: bool
    kv_mount: str
    kv_enabled: bool
    policies_written: tuple[str, ...]

    def __repr__(self) -> str:
        return (
            "VaultBootstrapResult(already_initialized="
            f"{self.already_initialized}, kv_mount={self.kv_mount!r}, "
            f"kv_enabled={self.kv_enabled}, policies_written={self.policies_written!r}, "
            "init=<redacted>)"
        )

    __str__ = __repr__


#: Las cuatro políticas iniciales, todas de SÓLO LECTURA. Cada servicio recibe
#: exactamente los dominios que resuelve en ejecución — mínimo privilegio:
#:
#:   * api-server              — BD, MinIO, firma JWT/review-url, las claves de
#:                               cifrado (SSO / notificaciones / webhooks) y las
#:                               credenciales de proveedor LLM.
#:   * workers                 — BD, MinIO (bundles de backup) y proveedor LLM.
#:   * orchestrator            — sólo BD: planifica, no hace I/O con secreto.
#:   * notification-dispatcher — BD y la clave de cifrado de notificaciones.
#:
#: Ninguna tiene `create`/`update`/`delete`: los servicios CONSUMEN secretos. Las
#: escrituras ocurren aquí (políticas) y en la rotación (prod-10).
_INITIAL_POLICIES: tuple[VaultPolicy, ...] = (
    VaultPolicy(
        name="api-server",
        rules=(
            PolicyRule(path=SECRET_PATH_DATABASE, capabilities=("read",)),
            PolicyRule(path=SECRET_PATH_MINIO, capabilities=("read",)),
            PolicyRule(path=SECRET_PATH_JWT, capabilities=("read",)),
            PolicyRule(path=SECRET_PATH_ENCRYPTION, capabilities=("read",)),
            PolicyRule(path=SECRET_PATH_LLM, capabilities=("read",)),
        ),
    ),
    VaultPolicy(
        name="workers",
        rules=(
            PolicyRule(path=SECRET_PATH_DATABASE, capabilities=("read",)),
            PolicyRule(path=SECRET_PATH_MINIO, capabilities=("read",)),
            PolicyRule(path=SECRET_PATH_LLM, capabilities=("read",)),
        ),
    ),
    VaultPolicy(
        name="orchestrator",
        rules=(PolicyRule(path=SECRET_PATH_DATABASE, capabilities=("read",)),),
    ),
    VaultPolicy(
        name="notification-dispatcher",
        rules=(
            PolicyRule(path=SECRET_PATH_DATABASE, capabilities=("read",)),
            PolicyRule(path=SECRET_PATH_ENCRYPTION, capabilities=("read",)),
        ),
    ),
)


def initial_policies() -> tuple[VaultPolicy, ...]:
    """Las políticas de lectura por servicio que escribe el bootstrap."""

    return _INITIAL_POLICIES


@runtime_checkable
class VaultClient(Protocol):
    """La superficie de Vault que necesita el bootstrap — la costura inyectable.

    Modela sólo lo que hace falta: estado de init/sello, `operator init`,
    desellado, montar un motor, escribir políticas… y **si el cliente lleva
    token**, que es la comprobación que faltaba (ver :func:`bootstrap_vault`).
    La implementación real es :class:`~api_server.bootstrap.hvac_client.HvacVaultClient`;
    los tests inyectan un doble en memoria.
    """

    def is_initialized(self) -> bool:
        """¿Se le ha hecho ya `operator init` a este Vault?"""
        ...

    def is_sealed(self) -> bool:
        """¿Está sellado ahora mismo? Un Vault sellado no contesta nada útil."""
        ...

    def has_token(self) -> bool:
        """¿Lleva el cliente un token con el que escribir?

        Es una comprobación LOCAL (mira el token del cliente, no pregunta a
        Vault): sirve para fallar con un mensaje antes de cosechar un 403.
        """
        ...

    def initialize(self, *, secret_shares: int, secret_threshold: int) -> VaultInitResult:
        """`vault operator init`. Devuelve el material UNA vez y autentica al cliente."""
        ...

    def submit_unseal_key(self, key: str) -> bool:
        """Somete un share. Devuelve True cuando Vault ya está desellado."""
        ...

    def list_mounts(self) -> dict[str, MountInfo]:
        """``<mount>/`` → motor montado ahí (tipo y, si es KV, versión)."""
        ...

    def enable_kv_v2(self, *, mount_point: str) -> None:
        """Habilita un motor KV **v2** en *mount_point*."""
        ...

    def write_policy(self, *, name: str, policy_hcl: str) -> None:
        """Crea o actualiza la política ACL con nombre *name*."""
        ...


def _ensure_kv_v2(client: VaultClient, *, mount: str) -> bool:
    """Monta el KV v2 en *mount* si no está. Devuelve True si lo montó AHORA.

    Idempotente: con el motor ya presente y en v2 no hace nada y devuelve False,
    así que un re-bootstrap no falla por un mount existente — y el revelado no
    puede decir que lo montó él cuando no fue así.

    Rechaza dos casos, y el segundo es el que la especificación dejó abierto:

    * el mount existe y **no es KV** (alguien montó ahí un `transit`);
    * el mount existe, es KV, pero es **v1**. `list_mounts` devuelve el tipo
      `kv` para las dos versiones, así que sin mirar `options.version` esto
      pasaba: las políticas se escribían sobre rutas `secret/data/...` que en v1
      no existen, y el fallo salía luego, en la primera lectura de un servicio.
    """

    mounts = client.list_mounts()
    existing = mounts.get(f"{mount}/") or mounts.get(mount)
    if existing is None:
        client.enable_kv_v2(mount_point=mount)
        return True
    if existing.engine_type != "kv":
        raise VaultBootstrapError(
            f"El mount '{mount}/' ya existe pero no es un motor KV (es "
            f"'{existing.engine_type}'). La plataforma guarda todos sus secretos "
            f"en un KV v2 montado ahí; revisa la instalación de Vault."
        )
    if existing.kv_version != KV_VERSION:
        raise VaultBootstrapError(
            f"El mount '{mount}/' existe como KV v1 (options.version="
            f"{existing.kv_version!r}), y la plataforma necesita KV v2. Las "
            f"políticas conceden sobre rutas '{mount}/data/...', que en v1 NO "
            "existen: si esto siguiera adelante, el error no saldría aquí sino "
            "en la primera lectura de un servicio, como un `permission denied` "
            "sin relación aparente con la instalación. Desmonta ese `secret/` "
            "(o monta el KV v2 en otro punto) y vuelve a ejecutar."
        )
    return False


def _write_initial_policies(client: VaultClient, *, mount: str) -> tuple[str, ...]:
    """Escribe las políticas por servicio. Devuelve los nombres, en orden."""

    written: list[str] = []
    for policy in initial_policies():
        client.write_policy(name=policy.name, policy_hcl=policy.render_hcl(mount=mount))
        written.append(policy.name)
    return tuple(written)


def _require_token(client: VaultClient) -> None:
    """Sin token no se intenta escribir: se dice qué falta.

    El `operator init` es lo ÚNICO que autentica al cliente (deja puesto el root
    token recién acuñado). En un Vault ya inicializado ese paso no corre, así que
    el `enable_kv_v2` y los cuatro `write_policy` salían sin credencial y Vault
    contestaba un 403 crudo. Y el entorno no lo suple: el compose deja escrito
    que ``VAULT_TOKEN`` **no** viaja en el env del api-server («it is optional
    (default None) and injected by the Vault bootstrap»).

    Se comprueba justo antes de las escrituras y no sólo en la rama del
    re-bootstrap, para que también cace a un adaptador que se olvidara de
    autenticarse después del init.
    """

    if client.has_token():
        return
    raise VaultBootstrapError(
        "Vault ya está inicializado, así que este one-shot no ha acuñado ningún "
        "root token, y sin token no puede montar el KV ni escribir las políticas "
        "(Vault responde 403). Aporta uno con capacidad sobre `sys/mounts` y "
        f"`sys/policies/acl` en la variable de entorno {VAULT_TOKEN_ENV} — por "
        f"ENTORNO y no por argv, igual que las unseal keys: "
        f"`docker compose run --rm -e {VAULT_TOKEN_ENV} bootstrap`."
    )


def _unseal_with(client: VaultClient, keys: tuple[str, ...], *, threshold: int) -> None:
    """Aplica shares hasta que Vault se abre. Las claves nunca se loguean."""

    if len(keys) < threshold:
        raise VaultBootstrapError(
            f"No hay suficientes unseal keys para alcanzar el umbral "
            f"({len(keys)} < {threshold}); Vault no se puede abrir con lo aportado."
        )
    for key in keys[:threshold]:
        if client.submit_unseal_key(key):
            return
    if client.is_sealed():
        raise VaultBootstrapError(
            "Vault sigue sellado tras aplicar el umbral de unseal keys: las "
            "claves aportadas no son las de este Vault."
        )


def bootstrap_vault(
    client: VaultClient,
    *,
    key_shares: int = DEFAULT_KEY_SHARES,
    key_threshold: int = DEFAULT_KEY_THRESHOLD,
    mount: str = PLATFORM_KV_MOUNT,
    existing_unseal_keys: tuple[str, ...] | None = None,
) -> VaultBootstrapResult:
    """Init (una vez) + desellado + KV v2 + políticas. Seguro de re-ejecutar.

    * **Vault virgen** — `operator init` acuña las unseal keys y el root token
      (que salen en el :class:`VaultInitResult` para el revelado único) y el
      umbral de esas mismas claves lo desella acto seguido.
    * **Vault ya inicializado** — NO se re-inicializa. Un segundo `operator init`
      fallaría y, peor, se lee como «rotar el material raíz»: destructivo y sin
      recuperación. Si está sellado se abre con *existing_unseal_keys* (las que
      aporta el operador); sin ellas, error, nunca un intento a ciegas.

    En los dos casos se reconcilian el mount y las políticas, así que un
    re-bootstrap converge la configuración.
    """

    if key_threshold < 1 or key_threshold > key_shares:
        # Antes de tocar Vault: un init con un umbral imposible es irreversible.
        raise VaultBootstrapError(
            "El umbral de unseal debe estar entre 1 y el número de shares "
            f"({key_threshold} no es válido para {key_shares} shares)."
        )

    already = client.is_initialized()

    init: VaultInitResult | None = None
    if not already:
        init = client.initialize(secret_shares=key_shares, secret_threshold=key_threshold)
        # Aquí y sólo aquí: las claves están vivas en memoria en este instante.
        _unseal_with(client, init.unseal_keys, threshold=init.key_threshold)
    elif client.is_sealed():
        if not existing_unseal_keys:
            raise VaultBootstrapError(
                "Vault ya está inicializado y sellado, pero no se han aportado "
                "las unseal keys existentes para desellarlo (no hay recuperación "
                "de las claves originales: se entregaron una vez). Pásalas por "
                "entorno con --vault-unseal-keys-from, o desella a mano con "
                "`docker compose exec vault vault operator unseal` (PASO 0 de "
                "docs/06-runbooks/restart-services.md) y vuelve a ejecutar."
            )
        _unseal_with(client, existing_unseal_keys, threshold=key_threshold)

    _require_token(client)
    kv_enabled = _ensure_kv_v2(client, mount=mount)
    policies = _write_initial_policies(client, mount=mount)

    return VaultBootstrapResult(
        init=init,
        already_initialized=already,
        kv_mount=mount,
        kv_enabled=kv_enabled,
        policies_written=policies,
    )
