"""La mitad de Vault del one-shot `api_server.bootstrap` (paso 8 del ADR 0161).

El one-shot corre DENTRO de la red del stack (`docker compose run --rm
bootstrap`) porque el servicio `vault` no publica ningún puerto —el único que
publica es Caddy, ADR 0061— y desde el host no era alcanzable. Aquí se fija lo
que hace contra Vault: init una sola vez, desellado, KV v2 y las cuatro
políticas de mínimo privilegio.

Dos agujeros que la especificación del portado dejó nombrados y sin resolver, y
que estos tests cierran porque los dos fallan tarde y en el peor sitio:

  * **El re-bootstrap sin token.** El init es lo ÚNICO que autentica al cliente.
    En un Vault ya inicializado no se ejecuta, así que el `enable_kv_v2` y los
    cuatro `write_policy` salían sin token y Vault contestaba 403 crudo.
  * **El `secret/` montado como KV v1.** `list_mounts` devuelve el tipo `kv`
    para las dos versiones, así que un v1 preexistente pasaba la comprobación y
    las políticas se escribían sobre rutas `secret/data/...` que en v1 no
    existen. El fallo aparecía después, en la primera lectura de un servicio,
    como `permission denied` — a las tres de la mañana y sin relación aparente.
"""

from __future__ import annotations

import pytest
from api_server.bootstrap.errors import ExitCode
from api_server.bootstrap.hvac_client import HvacVaultClient
from api_server.bootstrap.vault import (
    PLATFORM_KV_MOUNT,
    SECRET_PATH_DATABASE,
    SECRET_PATH_ENCRYPTION,
    SECRET_PATH_JWT,
    SECRET_PATH_LLM,
    SECRET_PATH_MINIO,
    VaultBootstrapError,
    VaultBootstrapResult,
    VaultInitResult,
    bootstrap_vault,
    initial_policies,
)

from tests.unit._fake_vault import SCRIPTED_ROOT_TOKEN, SCRIPTED_UNSEAL_KEYS, FakeVaultClient

pytestmark = pytest.mark.unit


# --- el camino limpio ------------------------------------------------------


def test_un_vault_virgen_se_inicializa_se_desella_y_queda_configurado() -> None:
    client = FakeVaultClient()

    result = bootstrap_vault(client)

    assert result.already_initialized is False
    assert result.init is not None
    assert result.init.unseal_keys == SCRIPTED_UNSEAL_KEYS
    assert result.init.root_token == SCRIPTED_ROOT_TOKEN
    assert result.init.key_threshold == 3
    assert client.init_calls == 1
    assert client.sealed is False, "un Vault sellado no contesta nada: sin desellar no hay KV"
    assert result.kv_enabled is True
    assert client.mounts["secret/"].kv_version == "2"
    assert result.policies_written == tuple(p.name for p in initial_policies())


def test_el_desellado_posterior_al_init_usa_solo_el_umbral_de_shares() -> None:
    """Se aplican `key_threshold` shares, no los cinco.

    No es tacañería: cada share que se somete es una llamada más a un Vault que
    acaba de arrancar, y el contrato de Shamir es exactamente ese umbral.
    """

    client = FakeVaultClient()

    bootstrap_vault(client, key_shares=5, key_threshold=3)

    assert len(client.submitted_keys) == 3


# --- idempotencia en el límite del init ------------------------------------


def test_un_vault_ya_inicializado_no_se_re_inicializa() -> None:
    """Un segundo `operator init` fallaría, y peor: se lee como «rotar el
    material raíz», que es destructivo y SIN recuperación."""

    client = FakeVaultClient()
    client.preset_initialized(sealed=False)

    result = bootstrap_vault(client)

    assert result.already_initialized is True
    assert result.init is None, "no hay material nuevo que revelar: no se inventa"
    assert client.init_calls == 0


def test_un_vault_ya_inicializado_reconcilia_kv_y_politicas() -> None:
    """El re-bootstrap CONVERGE la configuración aunque no toque el sello."""

    client = FakeVaultClient()
    client.preset_initialized(sealed=False)

    result = bootstrap_vault(client)

    assert result.kv_enabled is True
    assert set(client.policies) == {p.name for p in initial_policies()}


def test_un_vault_inicializado_y_sellado_se_desella_con_las_claves_del_operador() -> None:
    """El caso de la reinstalación con preservación de datos.

    Las claves las aporta el operador (`AGENTIC_BOOTSTRAP_UNSEAL_KEYS`): el
    one-shot es el brazo, no la decisión.
    """

    client = FakeVaultClient()
    client.preset_initialized(sealed=True)

    result = bootstrap_vault(client, existing_unseal_keys=SCRIPTED_UNSEAL_KEYS)

    assert client.sealed is False
    assert result.already_initialized is True
    assert result.init is None


def test_un_vault_sellado_sin_claves_aportadas_falla_en_vez_de_intentarlo_a_ciegas() -> None:
    client = FakeVaultClient()
    client.preset_initialized(sealed=True)

    with pytest.raises(VaultBootstrapError) as exc:
        bootstrap_vault(client)

    assert "unseal keys" in str(exc.value)
    assert "recuperación" in str(exc.value)
    assert exc.value.exit_code == ExitCode.VAULT


def test_unas_claves_por_debajo_del_umbral_se_rechazan_antes_de_someterlas() -> None:
    client = FakeVaultClient()
    client.preset_initialized(sealed=True)

    with pytest.raises(VaultBootstrapError):
        bootstrap_vault(client, existing_unseal_keys=SCRIPTED_UNSEAL_KEYS[:2], key_threshold=3)

    assert client.submitted_keys == []


# --- agujero (a) de la especificación: el token del re-bootstrap -----------


def test_el_re_bootstrap_sin_token_falla_nombrando_la_variable_que_falta() -> None:
    """Sin token no hay 403 crudo: hay un mensaje que dice qué aportar.

    El init es lo único que autentica al cliente; en un Vault ya inicializado no
    se ejecuta, así que las escrituras de los pasos 3 y 4 salían sin credencial.
    El entorno tampoco lo suplía: el compose deja escrito que `VAULT_TOKEN` NO
    viaja en el env del api-server.
    """

    client = FakeVaultClient()
    client.preset_initialized(sealed=False, token=None)

    with pytest.raises(VaultBootstrapError) as exc:
        bootstrap_vault(client)

    message = str(exc.value)
    assert "AGENTIC_BOOTSTRAP_VAULT_TOKEN" in message
    assert client.policies == {}, "no se intenta escribir a ciegas para cosechar un 403"


def test_un_vault_virgen_no_necesita_token_porque_el_init_lo_acuna() -> None:
    client = FakeVaultClient(token=None)

    bootstrap_vault(client)

    assert client.token == SCRIPTED_ROOT_TOKEN


# --- agujero (b) de la especificación: KV v1 vs KV v2 ----------------------


def test_un_secret_montado_como_kv_v1_se_rechaza_ahora_y_no_a_las_tres_de_la_manana() -> None:
    client = FakeVaultClient()
    client.preset_initialized(sealed=False)
    client.preset_mount(PLATFORM_KV_MOUNT, engine_type="kv", kv_version="1")

    with pytest.raises(VaultBootstrapError) as exc:
        bootstrap_vault(client)

    message = str(exc.value)
    assert "KV v1" in message
    assert "secret/data/" in message, "hay que decir POR QUÉ falla: las rutas v2 no existen en v1"
    assert client.policies == {}


def test_un_mount_que_no_es_kv_se_rechaza() -> None:
    client = FakeVaultClient()
    client.preset_initialized(sealed=False)
    client.preset_mount(PLATFORM_KV_MOUNT, engine_type="transit", kv_version=None)

    with pytest.raises(VaultBootstrapError) as exc:
        bootstrap_vault(client)

    assert "transit" in str(exc.value)


def test_un_kv_v2_ya_montado_no_se_vuelve_a_montar() -> None:
    client = FakeVaultClient()
    client.preset_initialized(sealed=False)
    client.preset_mount(PLATFORM_KV_MOUNT, engine_type="kv", kv_version="2")

    result = bootstrap_vault(client)

    assert result.kv_enabled is False, "no se montó AHORA, y el revelado no puede decir que sí"


# --- el umbral --------------------------------------------------------------


@pytest.mark.parametrize(("shares", "threshold"), [(5, 0), (5, 6), (3, 4)])
def test_un_umbral_imposible_se_rechaza_antes_de_tocar_vault(shares: int, threshold: int) -> None:
    """Un init con umbral imposible es IRREVERSIBLE: se valida antes de llamar."""

    client = FakeVaultClient()

    with pytest.raises(VaultBootstrapError):
        bootstrap_vault(client, key_shares=shares, key_threshold=threshold)

    assert client.init_calls == 0


# --- las políticas ----------------------------------------------------------


def test_las_cuatro_politicas_son_de_solo_lectura() -> None:
    """Los servicios CONSUMEN secretos; nunca los escriben.

    Las escrituras ocurren en el bootstrap (políticas) y en la rotación
    (`workers.credential_rotation`), no en el camino de lectura de un servicio.
    """

    for policy in initial_policies():
        for rule in policy.rules:
            assert rule.capabilities == ("read",), f"{policy.name} pide más que leer"


def test_el_reparto_de_dominios_por_servicio_es_el_de_minimo_privilegio() -> None:
    grants = {p.name: {r.path for r in p.rules} for p in initial_policies()}

    assert grants["api-server"] == {
        SECRET_PATH_DATABASE,
        SECRET_PATH_MINIO,
        SECRET_PATH_JWT,
        SECRET_PATH_ENCRYPTION,
        SECRET_PATH_LLM,
    }
    assert grants["workers"] == {SECRET_PATH_DATABASE, SECRET_PATH_MINIO, SECRET_PATH_LLM}
    assert grants["orchestrator"] == {SECRET_PATH_DATABASE}
    assert grants["notification-dispatcher"] == {SECRET_PATH_DATABASE, SECRET_PATH_ENCRYPTION}


def test_el_hcl_expande_la_ruta_data_de_kv_v2() -> None:
    hcl = next(p for p in initial_policies() if p.name == "orchestrator").render_hcl()

    assert 'path "secret/data/platform/database"' in hcl
    assert 'capabilities = ["read"]' in hcl
    assert "metadata" not in hcl, "sin `list` no hay grant sobre metadata"


def test_el_layout_del_kv_es_el_que_ya_consume_la_rotacion_de_prod_10() -> None:
    """Si el portado cambiara el mount o una ruta, la rotación se rompería EN
    SILENCIO: `workers.credential_rotation` reproduce el layout a mano y
    `scripts/rotate-platform-secret.sh` lee `secret/platform/jwt` y
    `secret/platform/minio` por literal."""

    from workers.credential_rotation import PLATFORM_KV_MOUNT as ROTATION_MOUNT
    from workers.credential_rotation import STATIC_SECRET_PATHS

    assert PLATFORM_KV_MOUNT == ROTATION_MOUNT
    assert STATIC_SECRET_PATHS["jwt"] == SECRET_PATH_JWT
    assert STATIC_SECRET_PATHS["minio"] == SECRET_PATH_MINIO


def test_el_catalogo_de_politicas_no_puede_derivar_del_que_vigila_el_instalador() -> None:
    """La guarda contra la deriva entre las DOS copias del catálogo.

    `tests/unit/test_vault_service_tokens.py` cruza los nombres de política con
    `scripts/vault-mint-service-tokens.sh` importando el catálogo del
    INSTALADOR. El one-shot no puede importarlo —el api-server no depende del
    instalador, que es una app de arranque y no viaja en su imagen—, así que
    reimplementa el catálogo. El precedente está escrito:
    `workers/credential_rotation.py` hace lo mismo con el layout del KV y lo dice.

    Reimplementar sin esta guarda es lo que devolvería la deriva por donde aquel
    test la cerró: los tokens se mintearían contra un nombre que Vault no
    conoce, y el fallo aparecería al arrancar el servicio, no aquí.
    """

    from installer_backend.vault_bootstrap import initial_policies as installer_policies

    mine = {p.name: {(r.path, r.capabilities) for r in p.rules} for p in initial_policies()}
    theirs = {p.name: {(r.path, r.capabilities) for r in p.rules} for p in installer_policies()}

    assert mine == theirs, (
        "el catálogo de políticas del one-shot y el del instalador han derivado; "
        "los tokens de scripts/vault-mint-service-tokens.sh se mintearían contra "
        "políticas que Vault no conoce"
    )


# --- seguridad: el material de una sola vez no se escapa por un repr -------


def test_el_repr_del_init_no_lleva_el_material_dentro() -> None:
    init = VaultInitResult(unseal_keys=("a", "b"), root_token="hvs.x", key_threshold=1)

    for text in (repr(init), str(init), f"{init}"):
        assert "hvs.x" not in text
        assert "redacted" in text


def test_el_repr_del_resultado_deja_pasar_lo_que_no_es_secreto() -> None:
    result = VaultBootstrapResult(
        init=VaultInitResult(unseal_keys=("a",), root_token="hvs.x", key_threshold=1),
        already_initialized=False,
        kv_mount="secret",
        kv_enabled=True,
        policies_written=("api-server",),
    )

    text = repr(result)
    assert "hvs.x" not in text
    assert "'secret'" in text
    assert "api-server" in text


# --- el adaptador hvac ------------------------------------------------------


class _StubSys:
    """La superficie de `hvac.Client.sys` que toca el adaptador."""

    def __init__(self, mounts: dict[str, object]) -> None:
        self._mounts = mounts
        self.enabled: list[tuple[str, dict[str, str]]] = []

    def is_initialized(self) -> bool:
        return False

    def is_sealed(self) -> bool:
        return True

    def initialize(self, *, secret_shares: int, secret_threshold: int) -> dict[str, object]:
        return {
            "keys_base64": [f"share-{i}" for i in range(secret_shares)],
            "root_token": "hvs.acunado-por-el-init",
        }

    def submit_unseal_key(self, key: str) -> dict[str, object]:
        return {"sealed": False, "key": key}

    def list_mounted_secrets_engines(self) -> dict[str, object]:
        return {"data": self._mounts}

    def enable_secrets_engine(
        self, *, backend_type: str, path: str, options: dict[str, str]
    ) -> None:
        self.enabled.append((f"{backend_type}:{path}", options))

    def create_or_update_policy(self, *, name: str, policy: str) -> None:
        self.enabled.append((f"policy:{name}", {"len": str(len(policy))}))


class _StubHvac:
    def __init__(self, mounts: dict[str, object] | None = None) -> None:
        self.sys = _StubSys(mounts or {})
        self.token: str | None = None


def test_el_adaptador_autentica_el_cliente_con_el_root_token_recien_acunado() -> None:
    """Es lo ÚNICO que autoriza el `enable_kv_v2` y los cuatro `write_policy`.

    Sin esta línea el camino limpio también daría 403, y el fake no lo destaparía
    porque no modela autorización.
    """

    stub = _StubHvac()
    adapter = HvacVaultClient(stub)

    assert adapter.has_token() is False
    init = adapter.initialize(secret_shares=5, secret_threshold=3)

    assert init.root_token == "hvs.acunado-por-el-init"
    assert stub.token == "hvs.acunado-por-el-init"
    assert adapter.has_token() is True


def test_el_adaptador_lee_la_version_de_kv_de_las_options_y_no_solo_el_tipo() -> None:
    stub = _StubHvac(
        {
            "secret/": {"type": "kv", "options": {"version": "1"}},
            "cubbyhole/": {"type": "cubbyhole", "options": None},
            "request_id": "no-es-un-mount",
        }
    )

    mounts = HvacVaultClient(stub).list_mounts()

    assert mounts["secret/"].engine_type == "kv"
    assert mounts["secret/"].kv_version == "1"
    assert mounts["cubbyhole/"].kv_version is None
    assert "request_id" not in mounts, "la metadata de la respuesta no es un mount"


def test_el_adaptador_monta_el_kv_en_la_version_2() -> None:
    stub = _StubHvac()

    HvacVaultClient(stub).enable_kv_v2(mount_point="secret")

    assert stub.sys.enabled == [("kv:secret", {"version": "2"})]
