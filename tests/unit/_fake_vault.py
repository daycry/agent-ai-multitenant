"""Doble determinista de Vault para los tests del one-shot `api_server.bootstrap`.

Vive en `tests/` y no junto al módulo a propósito. El instalador guarda su fake
DENTRO de `installer_backend.vault_bootstrap` porque aquel paquete no enlaza
`hvac` y el fake es su única implementación; aquí la implementación real
(`api_server.bootstrap.hvac_client`) SÍ viaja en la imagen, así que meter además
un doble en el paquete de producción sería embarcar código de test en la imagen
del api-server para siempre.

Modela lo único que el bootstrap necesita: estado de init/sello, mounts con su
VERSIÓN de KV —que es lo que distingue un `secret/` v1 de uno v2, y el agujero
que el adaptador del instalador no cubría— y el token con el que se escribe.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from api_server.bootstrap.vault import MountInfo, VaultBootstrapError, VaultInitResult

#: Material de mentira, reconocible a simple vista en un diff. Nunca se parece a
#: un secreto real: si algún día aparece en una salida, se ve que es de test.
SCRIPTED_UNSEAL_KEYS = tuple(f"unseal-de-mentira-{i}" for i in range(1, 6))
SCRIPTED_ROOT_TOKEN = "hvs.token-de-mentira-para-tests-0123456789abcdef"


@dataclass
class FakeVaultClient:
    """Un :class:`~api_server.bootstrap.vault.VaultClient` en memoria.

    Arranca sin inicializar y sellado (un Vault recién levantado). Registra lo
    que hace falta para aseverar las tres propiedades que importan: que NO hay
    doble init, que el KV queda en v2 y que las políticas se reconcilian.
    """

    initialized: bool = False
    sealed: bool = True
    key_shares: int = 5
    key_threshold: int = 3
    token: str | None = None

    scripted_unseal_keys: tuple[str, ...] = SCRIPTED_UNSEAL_KEYS
    scripted_root_token: str = SCRIPTED_ROOT_TOKEN

    mounts: dict[str, MountInfo] = field(default_factory=dict)
    policies: dict[str, str] = field(default_factory=dict)
    init_calls: int = 0
    submitted_keys: list[str] = field(default_factory=list)
    valid_unseal_keys: tuple[str, ...] = ()
    _accepted: set[str] = field(default_factory=set)

    # -- utilidades de escenario -------------------------------------------
    def preset_initialized(
        self,
        *,
        sealed: bool,
        unseal_keys: tuple[str, ...] = SCRIPTED_UNSEAL_KEYS,
        token: str | None = SCRIPTED_ROOT_TOKEN,
    ) -> None:
        """Un Vault que YA existía: el caso del re-bootstrap."""

        self.initialized = True
        self.sealed = sealed
        self.valid_unseal_keys = unseal_keys
        self.token = token

    def preset_mount(self, mount: str, *, engine_type: str, kv_version: str | None) -> None:
        """Un mount que ya estaba ahí (para el caso KV v1 y el «no es KV»)."""

        self.mounts[f"{mount}/"] = MountInfo(engine_type=engine_type, kv_version=kv_version)

    # -- superficie VaultClient --------------------------------------------
    def is_initialized(self) -> bool:
        return self.initialized

    def is_sealed(self) -> bool:
        return self.sealed

    def has_token(self) -> bool:
        return self.token is not None

    def initialize(self, *, secret_shares: int, secret_threshold: int) -> VaultInitResult:
        if self.initialized:
            # Vault real rechaza un segundo init; reproducirlo es lo que respalda
            # la aserción de «no hay doble init».
            raise VaultBootstrapError("Vault ya está inicializado; no se puede re-init.")
        self.init_calls += 1
        self.initialized = True
        self.key_shares = secret_shares
        self.key_threshold = secret_threshold
        keys = self.scripted_unseal_keys[:secret_shares]
        self.valid_unseal_keys = keys
        # Como el adaptador real: el init deja el cliente autenticado con el root
        # token recién acuñado, que es lo único que autoriza lo que viene detrás.
        self.token = self.scripted_root_token
        return VaultInitResult(
            unseal_keys=keys, root_token=self.scripted_root_token, key_threshold=secret_threshold
        )

    def submit_unseal_key(self, key: str) -> bool:
        self.submitted_keys.append(key)
        if not self.sealed:
            return True
        if key in self.valid_unseal_keys:
            self._accepted.add(key)
        if len(self._accepted) >= self.key_threshold:
            self.sealed = False
            self._accepted.clear()
        return not self.sealed

    def list_mounts(self) -> dict[str, MountInfo]:
        return dict(self.mounts)

    def enable_kv_v2(self, *, mount_point: str) -> None:
        self.mounts[f"{mount_point}/"] = MountInfo(engine_type="kv", kv_version="2")

    def write_policy(self, *, name: str, policy_hcl: str) -> None:
        if self.token is None:
            # Vault devuelve 403 sin token. El fake del instalador NO modelaba
            # autorización, y por eso el agujero del re-bootstrap llegó vivo
            # hasta la especificación.
            raise VaultBootstrapError("403 permission denied (el fake no tiene token)")
        self.policies[name] = policy_hcl
