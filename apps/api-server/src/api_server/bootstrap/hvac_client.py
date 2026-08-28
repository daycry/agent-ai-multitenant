"""El adaptador real: :class:`~api_server.bootstrap.vault.VaultClient` sobre ``hvac``.

`hvac` viaja en ESTA imagen (`apps/api-server/pyproject.toml`, en
`[project].dependencies`, no en un extra), y es literalmente una de las dos
razones por las que el one-shot reutiliza la imagen del api-server en vez de
publicar una séptima: «es la que trae los seeds… y `hvac`».

## Cómo se habla con Vault desde dentro de la red

`http://vault:8200`, que ya viaja en el entorno del servicio
(`API_SERVER_VAULT_URL` → :attr:`api_server.config.Settings.vault_url`). **Sin
TLS y a propósito**: `docker/vault/config.hcl` declara `tls_disable = "true"`
porque el stack vive detrás del único terminador TLS, que es Caddy (ADR 0061).
Cliente HTTP plano, sin CA, sin `verify`.

## Por qué no hay espera ni sondeo previo

El `depends_on` del servicio pide `vault: service_healthy`, y `docker compose
run` honra esa condición — pero el healthcheck del compose traduce «sellado»
(503) y «sin inicializar» (501) a **200 a propósito**, para que Vault no entre en
bucle de reinicio antes de que nadie pueda desellarlo. O sea que `service_healthy`
garantiza que el listener contesta, y nada más. Eso es exactamente lo que este
one-shot necesita —desellar es su trabajo—, así que **no se añade polling sobre
`/v1/sys/health`**: con ese mapeo nunca diría lo que se busca, y el ADR 0145
prohíbe tocar el healthcheck para arreglarlo por ahí. El endpoint que dice la
verdad es `/v1/sys/seal-status` (el que usa `api_server.vault_client.probe_vault_seal`),
y aquí lo que se usa es directamente `sys.is_initialized()`/`sys.is_sealed()`,
que preguntan lo mismo sin necesitar token.

## La línea que no es cosmética

:meth:`initialize` deja el cliente **autenticado con el root token recién
acuñado**. Es lo único que autoriza el `enable_kv_v2` y los cuatro
`write_policy` que vienen detrás. En el camino del re-bootstrap ese paso no
corre, y por eso el token entra por :data:`~api_server.bootstrap.options.VAULT_TOKEN_ENV`
y :func:`~api_server.bootstrap.vault.bootstrap_vault` lo exige antes de escribir.
"""

from __future__ import annotations

from typing import Any

from api_server.bootstrap.vault import KV_VERSION, MountInfo, VaultInitResult


class HvacVaultClient:
    """Traducción fina del Protocol al `hvac.Client`.

    El constructor recibe el cliente ya construido (y :meth:`connect` lo
    construye) para que la traducción —que es donde vivían los dos defectos que
    la especificación dejó nombrados— se pueda probar con un doble sin levantar
    un Vault.
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    @classmethod
    def connect(cls, url: str, *, token: str | None = None) -> HvacVaultClient:
        """Construye el cliente contra *url*, opcionalmente ya autenticado.

        El import de `hvac` es diferido para que importar este módulo no cueste
        la dependencia en un proceso que no la usa.
        """

        import hvac

        client = hvac.Client(url=url)
        if token:
            client.token = token
        return cls(client)

    # -- superficie VaultClient --------------------------------------------
    def is_initialized(self) -> bool:
        return bool(self._client.sys.is_initialized())

    def is_sealed(self) -> bool:
        return bool(self._client.sys.is_sealed())

    def has_token(self) -> bool:
        return bool(getattr(self._client, "token", None))

    def initialize(self, *, secret_shares: int, secret_threshold: int) -> VaultInitResult:
        result = self._client.sys.initialize(
            secret_shares=secret_shares, secret_threshold=secret_threshold
        )
        keys = tuple(str(k) for k in (result.get("keys_base64") or result.get("keys") or ()))
        root_token = str(result["root_token"])
        # Deja el cliente autenticado como root para las escrituras que siguen.
        # Sin esta línea, el camino limpio también acabaría en 403.
        self._client.token = root_token
        return VaultInitResult(
            unseal_keys=keys, root_token=root_token, key_threshold=secret_threshold
        )

    def submit_unseal_key(self, key: str) -> bool:
        status = self._client.sys.submit_unseal_key(key)
        return not bool(status["sealed"])

    def list_mounts(self) -> dict[str, MountInfo]:
        """Los motores montados, CON su versión de KV.

        La versión vive en ``options.version`` y no en ``type`` —que dice `kv`
        para v1 y para v2 por igual—, así que sin leerla un `secret/` montado
        como v1 pasaría por bueno. Las claves que no describen un mount (la
        metadata que hvac mete en la misma respuesta, como ``request_id``) se
        descartan por no ser un diccionario.
        """

        mounts = self._client.sys.list_mounted_secrets_engines()
        data = mounts.get("data", mounts)
        result: dict[str, MountInfo] = {}
        for name, info in data.items():
            if not isinstance(info, dict):
                continue
            options = info.get("options") or {}
            version = options.get("version") if isinstance(options, dict) else None
            result[str(name)] = MountInfo(
                engine_type=str(info.get("type", "")),
                kv_version=str(version) if version is not None else None,
            )
        return result

    def enable_kv_v2(self, *, mount_point: str) -> None:
        self._client.sys.enable_secrets_engine(
            backend_type="kv", path=mount_point, options={"version": KV_VERSION}
        )

    def write_policy(self, *, name: str, policy_hcl: str) -> None:
        self._client.sys.create_or_update_policy(name=name, policy=policy_hcl)
