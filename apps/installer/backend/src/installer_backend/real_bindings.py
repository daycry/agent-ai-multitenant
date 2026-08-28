"""Host-only seam bindings the real installer wires (Plan prod-01 task_19).

These touch the host (write files, ``mkdir``/``chmod``) so they are
``# pragma: no cover`` — never exercised by the unit suite (which drives the
orchestration via the in-memory fakes) and validated only by the e2e / Tests
Humanos on a real Linux host with Docker.

El cliente de Vault del host: para qué sigue existiendo
------------------------------------------------------
``build_hvac_vault_client`` **ya no lo usa ningún paso de instalación**, y la
pregunta que su versión anterior dejaba abierta —«la alcanzabilidad exacta de
Vault desde el host (un puerto publicado vs ``docker compose exec``) se cierra
con prod-10»— **está respondida, y la respuesta es ninguna de las dos**: desde el
ADR 0161 el bootstrap de Vault corre DENTRO de la red del stack, en el one-shot
``bootstrap`` del compose generado. El host no habla con Vault, y no puede: el
servicio ``vault`` no publica ningún puerto (el único que publica es Caddy, ADR
0061) y el proxy no lo enruta.

Sigue aquí por un único motivo, y conviene que conste para que no se lea como
una capacidad viva: ``reinstall.build_preserve_executor`` todavía lo importa y lo
pasa al ejecutor, donde su propio comentario ya decía que «nunca se llama»
—``PRESERVE_STEP_ORDER`` no incluye BOOTSTRAP_VAULT—. Retirarlo desde aquí
rompería ese módulo desde otro fichero, así que **la retirada va junto con ese
llamador**; no queda ningún otro, y con él se va también el extra ``host`` de
``pyproject.toml`` que declara ``hvac``.
"""

from __future__ import annotations

import os
from pathlib import Path

from .config import InstallerConfig
from .config_generators import DataDir
from .vault_bootstrap import VaultClient, VaultInitResult


class RealEnvFileWriter:
    """Escribe un fichero generado con su modo POSIX, sin ventanas ni restos.

    Tres propiedades, y ninguna es cosmética:

    1. **El fichero nunca existe con un modo más laxo del declarado.** La versión
       anterior hacía ``write_text`` y ``chmod`` DESPUÉS, en ese orden: entre las
       dos llamadas, el ``.env`` —que lleva la contraseña de Postgres, las claves
       de MinIO, el secreto JWT, el de tokens internos y las tres claves Fernet—
       existía con el modo por defecto del umask, típicamente 0644, legible por
       cualquiera. La ventana duraba milisegundos y el contenido era literalmente
       todo lo que protege el despliegue.
    2. **La escritura es atómica.** Se escribe en un temporal del MISMO
       directorio y se hace ``os.replace``, que en POSIX y en Windows es atómico:
       un disco lleno o un proceso muerto a mitad no puede dejar un ``.env``
       truncado, que es la forma de romper una instalación sin que nada falle.
    3. **El modo se fija sobre el temporal, antes del rename.** ``os.open`` aplica
       el umask al modo que se le pasa (con umask 077 un 0644 nace 0600), así que
       el ``chmod`` explícito hace falta para que el modo declarado sea el modo
       real — y hacerlo antes del rename mantiene cerrada la ventana de (1).
    """

    def write(self, path: str, content: str, *, mode: int) -> None:  # pragma: no cover
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.tmp-{os.getpid()}")
        try:
            fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
            os.chmod(str(tmp), mode)
            os.replace(str(tmp), str(target))
        except OSError:
            # Un temporal huérfano con secretos dentro sería el mismo defecto que
            # esta clase arregla, una capa más abajo. Se limpia y se deja subir la
            # excepción: quien la traduce es el ejecutor (`_describe_os_error`).
            tmp.unlink(missing_ok=True)
            raise


class RealDataTreeProvisioner:
    """Creates each data-tree directory with its declared POSIX mode."""

    def provision(self, plan: list[DataDir]) -> None:  # pragma: no cover
        for entry in plan:
            path = Path(entry.path)
            path.mkdir(parents=True, exist_ok=True)
            path.chmod(entry.mode)


class RealFileReader:
    """Lee ficheros del host: el seam de :class:`installer_backend.install_state.FileReader`.

    Lo usan dos cosas: la inspección de la raíz de datos (¿hay un ``.env`` de una
    instalación anterior?) y ``tls_mode: provided`` (el par certificado+clave que
    el operador declaró). Ambas leen texto: el ``.env`` lo escribió este mismo
    instalador en UTF-8, y un par TLS que Caddy pueda usar es PEM, que es ASCII.
    """

    def exists(self, path: str) -> bool:  # pragma: no cover - host-only
        return Path(path).is_file()

    def read_text(self, path: str) -> str:  # pragma: no cover - host-only
        return Path(path).read_text(encoding="utf-8")


class RealEscrowFile:
    """El seam del depósito de unseal keys (:mod:`installer_backend.key_escrow`).

    Es propio y no el escritor de configuración porque necesita ``remove``: lo
    que mantiene el depósito siendo un apaño de noventa segundos, y no una
    segunda copia permanente de las cinco claves en la misma máquina que Vault,
    es que se borre en el revelado.
    """

    def write(self, path: str, content: str, *, mode: int) -> None:  # pragma: no cover
        RealEnvFileWriter().write(path, content, mode=mode)

    def exists(self, path: str) -> bool:  # pragma: no cover - host-only
        return Path(path).is_file()

    def remove(self, path: str) -> None:  # pragma: no cover - host-only
        Path(path).unlink(missing_ok=True)


class _HvacVaultClient:  # pragma: no cover - host-only adapter over hvac
    """Adapter mapping the :class:`VaultClient` Protocol onto an ``hvac.Client``."""

    def __init__(self, addr: str) -> None:
        import hvac

        self._hvac = hvac
        self._client = hvac.Client(url=addr)

    def is_initialized(self) -> bool:
        return bool(self._client.sys.is_initialized())

    def is_sealed(self) -> bool:
        return bool(self._client.sys.is_sealed())

    def initialize(self, *, secret_shares: int, secret_threshold: int) -> VaultInitResult:
        result = self._client.sys.initialize(
            secret_shares=secret_shares, secret_threshold=secret_threshold
        )
        keys = tuple(result.get("keys_base64") or result.get("keys") or ())
        root_token = str(result["root_token"])
        # Authenticate the client as root for the subsequent KV/policy writes.
        self._client.token = root_token
        return VaultInitResult(
            unseal_keys=keys, root_token=root_token, key_threshold=secret_threshold
        )

    def submit_unseal_key(self, key: str) -> bool:
        status = self._client.sys.submit_unseal_key(key)
        return not bool(status["sealed"])

    def list_mounts(self) -> dict[str, str]:
        mounts = self._client.sys.list_mounted_secrets_engines()
        data = mounts.get("data", mounts)
        return {name: str(info.get("type", "")) for name, info in data.items()}

    def enable_kv_v2(self, *, mount_point: str) -> None:
        self._client.sys.enable_secrets_engine(
            backend_type="kv", path=mount_point, options={"version": "2"}
        )

    def write_policy(self, *, name: str, policy_hcl: str) -> None:
        self._client.sys.create_or_update_policy(name=name, policy=policy_hcl)


def build_hvac_vault_client(cfg: InstallerConfig) -> VaultClient:  # pragma: no cover
    """VESTIGIO — ningún paso de instalación construye ya un cliente de Vault.

    Ver el docstring del módulo: el bootstrap corre dentro de la red del stack, y
    el ``http://127.0.0.1:8200`` de aquí no era alcanzable desde el host porque
    el servicio ``vault`` no publica puertos. Se conserva sólo mientras
    ``reinstall.build_preserve_executor`` lo pase (donde nunca se invoca), y se
    retira con él.

    ``VAULT_ADDR`` sigue mandando sobre la dirección, e ``hvac`` se importa de
    forma diferida, así que construir esto no cuesta nada y llamarlo desde el
    host tampoco funcionaría mejor que antes.
    """

    _ = cfg  # la dirección por despliegue murió con el rediseño del ADR 0161
    addr = os.environ.get("VAULT_ADDR", "http://127.0.0.1:8200")
    return _HvacVaultClient(addr)
