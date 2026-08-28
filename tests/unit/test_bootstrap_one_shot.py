"""El one-shot `api_server.bootstrap` entero — la segunda mitad del paso 8 del ADR 0161.

Corre una vez, dentro de la red del stack ya levantado (`docker compose run --rm
bootstrap`), y hace cuatro cosas en un orden que NO es cosmético:

    0. pre-flight de ESQUEMA        ← antes de tocar Vault. Barato y ruidoso.
    1. Vault: init → unseal → KV v2 → políticas
    2. init_tenant(...)             ← 3 filas. Devuelve `created_user`.
    3. EMITIR LA LÍNEA DE REVELADO  ← el material sin recuperación, por stdout
    4. run_seeds(...)               ← los 21 pasos. Minutos.

Los dos porqués del orden, que son los que estos tests fijan:

  * **El pre-flight va antes de Vault.** Un fallo de esquema descubierto DESPUÉS
    del `operator init` cuesta unas unseal keys irrecuperables; descubierto antes
    cuesta un mensaje.
  * **El revelado va antes del catálogo.** El catálogo son minutos de ingesta
    contra Ollama; si el proceso muere ahí y el revelado estuviera detrás, esas
    cinco claves no volverían a existir — y un Vault ya inicializado NO se
    re-inicializa.

El contrato de salida lo fijó la otra mitad (`installer_backend.real_step_executor`)
y aquí no se toca: se cruza con SU parser de verdad para que no puedan derivar.
"""

from __future__ import annotations

import ast
import asyncio
import io
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from api_server.bootstrap import runner as runner_module
from api_server.bootstrap.database import SchemaState
from api_server.bootstrap.errors import (
    BootstrapError,
    DatabaseError,
    ExitCode,
    OptionsError,
    SchemaNotReadyError,
)
from api_server.bootstrap.options import (
    ADMIN_EMAIL_ENV,
    ORG_SLUG_MAX_LENGTH,
    TENANT_NAME_ENV,
    UNSEAL_KEYS_ENV,
    VAULT_TOKEN_ENV,
    BootstrapOptions,
    parse_options,
)
from api_server.bootstrap.reveal import BOOTSTRAP_REVEAL_EVENT, Reveal
from api_server.bootstrap.runner import BootstrapDeps, mint_admin_password, run_bootstrap
from api_server.bootstrap.vault import VaultBootstrapError
from api_server.db.models import Organization
from api_server.logging.pii import mask_pii_in_text
from api_server.seeds import PLATFORM_TENANT_SLUG
from api_server.seeds.init_tenant import InitTenantResult

from tests.unit._fake_vault import SCRIPTED_ROOT_TOKEN, SCRIPTED_UNSEAL_KEYS, FakeVaultClient

pytestmark = pytest.mark.unit

_MINTED = "contrasena-minteada-de-mentira"
_TENANT = "Mediapro Innovación"
_EMAIL = "operador@example.com"
_HEAD = "20260828_0161"

_PACKAGE = Path(runner_module.__file__).parent


# --- dobles ----------------------------------------------------------------


@dataclass
class FakeDatabase:
    """La BD detrás de la costura: esquema, `init_tenant` y el catálogo."""

    schema: SchemaState = field(
        default_factory=lambda: SchemaState(missing_tables=(), applied_revisions=(_HEAD,))
    )
    created_user: bool = True
    seed_error: Exception | None = None
    calls: list[str] = field(default_factory=list)
    seen: dict[str, str] = field(default_factory=dict)
    #: El stdout del one-shot, para poder mirar —desde DENTRO del paso largo— si
    #: el revelado ya había salido. Es la única forma honesta de aseverar el
    #: orden: un `assert` sobre la lista de llamadas no distingue «antes» de
    #: «después» del `print`.
    stdout: io.StringIO | None = None
    reveal_seen_at_seed: bool | None = None

    async def schema_state(self) -> SchemaState:
        self.calls.append("schema_state")
        return self.schema

    async def init_tenant(
        self, *, tenant_name: str, slug: str, admin_email: str, admin_password: str
    ) -> InitTenantResult:
        self.calls.append("init_tenant")
        self.seen = {
            "tenant_name": tenant_name,
            "slug": slug,
            "admin_email": admin_email,
            "admin_password": admin_password,
        }
        return InitTenantResult(
            tenant_id=UUID(int=1),
            user_id=UUID(int=2),
            created_org=True,
            created_user=self.created_user,
            created_membership=True,
            is_system_admin=True,
            is_system_owner=True,
        )

    async def seed_catalog(self) -> None:
        self.calls.append("seed_catalog")
        if self.stdout is not None:
            self.reveal_seen_at_seed = BOOTSTRAP_REVEAL_EVENT in self.stdout.getvalue()
        if self.seed_error is not None:
            raise self.seed_error


@dataclass
class FakeLog:
    """Un logger de mentira. El gotcha del repo lo pide: afirmar sobre logs con
    `caplog` es frágil porque la app hace `logging.disable`."""

    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def info(self, event: str, **kwargs: Any) -> None:
        self.events.append((event, kwargs))

    def warning(self, event: str, **kwargs: Any) -> None:
        self.events.append((event, kwargs))

    def error(self, event: str, **kwargs: Any) -> None:
        self.events.append((event, kwargs))

    def event_names(self) -> list[str]:
        return [name for name, _ in self.events]


@dataclass
class Harness:
    reveal: Reveal | None
    stdout: io.StringIO
    vault: FakeVaultClient
    database: FakeDatabase
    log: FakeLog

    def stdout_lines(self) -> list[str]:
        return [line for line in self.stdout.getvalue().splitlines() if line.strip()]


def _options(**overrides: Any) -> BootstrapOptions:
    base: dict[str, Any] = {
        "tenant_name": _TENANT,
        "slug": "mediapro-innovacion",
        "admin_email": _EMAIL,
        "existing_unseal_keys": (),
        "vault_token": None,
    }
    base.update(overrides)
    return BootstrapOptions(**base)


def _run(
    *,
    options: BootstrapOptions | None = None,
    vault: FakeVaultClient | None = None,
    database: FakeDatabase | None = None,
    expected_revisions: tuple[str, ...] = (_HEAD,),
) -> Harness:
    stdout = io.StringIO()
    client = vault if vault is not None else FakeVaultClient()
    db = database if database is not None else FakeDatabase()
    db.stdout = stdout
    log = FakeLog()
    deps = BootstrapDeps(
        database=db,
        vault=client,
        stdout=stdout,
        log=log,
        mint_password=lambda: _MINTED,
        expected_revisions=expected_revisions,
    )
    reveal = asyncio.run(run_bootstrap(options or _options(), deps))
    return Harness(reveal=reveal, stdout=stdout, vault=client, database=db, log=log)


def _reveal_payload(harness: Harness) -> dict[str, Any]:
    lines = [line for line in harness.stdout_lines() if BOOTSTRAP_REVEAL_EVENT in line]
    assert len(lines) == 1, f"tiene que haber EXACTAMENTE una línea de revelado: {lines}"
    payload = json.loads(lines[0])
    assert isinstance(payload, dict)
    return payload


# --- el camino limpio -------------------------------------------------------


def test_un_vault_virgen_revela_las_claves_una_vez_y_dice_que_las_acuno_el() -> None:
    harness = _run()

    payload = _reveal_payload(harness)
    assert payload["event"] == BOOTSTRAP_REVEAL_EVENT
    assert payload["already_initialized"] is False
    assert payload["unseal_keys"] == list(SCRIPTED_UNSEAL_KEYS)
    assert payload["root_token"] == SCRIPTED_ROOT_TOKEN
    assert payload["key_threshold"] == 3
    assert payload["kv_mount"] == "secret"
    assert payload["kv_enabled"] is True
    assert payload["policies_written"] == [
        "api-server",
        "workers",
        "orchestrator",
        "notification-dispatcher",
    ]
    assert payload["admin_password"] == _MINTED
    assert payload["admin_user_created"] is True


def test_el_orden_de_los_cuatro_pasos_es_el_de_la_especificacion() -> None:
    """Esquema → Vault → tenant → REVELADO → catálogo, y el revelado en medio.

    `init_tenant` va ANTES del catálogo, invirtiendo el orden del instalador
    viejo, porque el revelado tiene que llevar `admin_user_created` dentro y ese
    dato sólo existe después de `init_tenant`. Con el catálogo delante, el
    revelado quedaría al otro lado de los minutos de ingesta contra Ollama.
    """

    harness = _run()

    assert harness.database.calls == ["schema_state", "init_tenant", "seed_catalog"]
    assert harness.database.reveal_seen_at_seed is True, (
        "el material sin recuperación tiene que estar YA fuera cuando empieza el "
        "paso largo y frágil"
    )


def test_la_contrasena_del_admin_la_mintea_el_one_shot_y_no_viaja_en_el_entorno() -> None:
    """`INIT_ADMIN_PASSWORD` NO está en el compose a propósito: ponerla ahí la
    escribiría en el `.env` del host, y eso no es un revelado único sino un
    secreto en un fichero."""

    harness = _run()

    assert harness.database.seen["admin_password"] == _MINTED
    assert "INIT_ADMIN_PASSWORD" not in _code_string_literals(), (
        "si el one-shot la leyera del entorno, el compose tendría que ponerla, y "
        "eso la escribe en el `.env` del host: un secreto en un fichero, no un "
        "revelado único"
    )


def test_la_contrasena_por_defecto_es_csprng_y_de_longitud_util() -> None:
    minted = {mint_admin_password() for _ in range(16)}

    assert len(minted) == 16, "una contraseña que se repite no es CSPRNG"
    assert all(len(p) >= 20 for p in minted)


# --- idempotencia: Vault ya inicializado ------------------------------------


def test_un_vault_ya_inicializado_no_inventa_claves_en_el_revelado() -> None:
    vault = FakeVaultClient()
    vault.preset_initialized(sealed=False)

    harness = _run(vault=vault)

    payload = _reveal_payload(harness)
    assert payload["already_initialized"] is True
    assert payload["unseal_keys"] == []
    assert payload["root_token"] == ""
    assert vault.init_calls == 0


def test_las_claves_del_operador_desellan_en_vez_de_inicializar() -> None:
    """El caso de la reinstalación con preservación: llegan por
    `AGENTIC_BOOTSTRAP_UNSEAL_KEYS`, jamás por `argv`."""

    vault = FakeVaultClient()
    vault.preset_initialized(sealed=True)

    harness = _run(vault=vault, options=_options(existing_unseal_keys=SCRIPTED_UNSEAL_KEYS))

    assert vault.sealed is False
    assert vault.init_calls == 0
    assert _reveal_payload(harness)["already_initialized"] is True


# --- idempotencia: admin que ya existía -------------------------------------


def test_un_admin_que_ya_existia_no_recibe_una_contrasena_que_no_abre_la_cuenta() -> None:
    """`init_tenant` es idempotente y su docstring lo dice: «the password of an
    existing user is left untouched». Revelar igualmente la contraseña recién
    minteada sería enseñar una que la base de datos no ha visto nunca: el
    operador la guarda, el instalador se autodestruye y en el primer login
    recibe credenciales inválidas sin ninguna pista."""

    harness = _run(database=FakeDatabase(created_user=False))

    payload = _reveal_payload(harness)
    assert payload["admin_user_created"] is False
    assert payload["admin_password"] == ""
    assert _MINTED not in harness.stdout.getvalue()


def test_init_tenant_completed_se_sigue_emitiendo_como_respaldo_del_revelado() -> None:
    """El instalador lee `admin_user_created` del revelado y, si no está, cae al
    marcador que `init_tenant` emite por su cuenta. Son dos vías al mismo dato a
    propósito: la segunda sobrevive a que el revelado cambie."""

    harness = _run(database=FakeDatabase(created_user=False))

    marker = next(kw for name, kw in harness.log.events if name == "init_tenant.completed")
    assert marker["created_user"] is False


# --- el contrato con la otra mitad ------------------------------------------


def test_el_revelado_lo_parsea_el_parser_de_verdad_del_instalador() -> None:
    """Las dos mitades no pueden derivar: se cruza con SU parser, no con uno
    escrito aquí. Y con el prefijo que antepone Compose, que es lo que se ve de
    verdad en la salida de `docker compose run`."""

    from installer_backend.real_step_executor import _parse_bootstrap_reveal

    harness = _run()
    line = next(line for line in harness.stdout_lines() if BOOTSTRAP_REVEAL_EVENT in line)

    parsed = _parse_bootstrap_reveal([f"bootstrap-1  | {line}"])

    assert parsed is not None
    assert parsed.already_initialized is False
    assert parsed.unseal_keys == SCRIPTED_UNSEAL_KEYS
    assert parsed.root_token == SCRIPTED_ROOT_TOKEN
    assert parsed.key_threshold == 3
    assert parsed.kv_mount == "secret"
    assert parsed.kv_enabled is True
    assert parsed.policies_written == (
        "api-server",
        "workers",
        "orchestrator",
        "notification-dispatcher",
    )
    assert parsed.admin_password == _MINTED
    assert parsed.admin_user_created is True
    assert parsed.as_init() is not None


def test_el_instalador_lee_un_re_bootstrap_como_sin_material_nuevo() -> None:
    from installer_backend.real_step_executor import _parse_bootstrap_reveal

    vault = FakeVaultClient()
    vault.preset_initialized(sealed=False)
    harness = _run(vault=vault)
    line = next(line for line in harness.stdout_lines() if BOOTSTRAP_REVEAL_EVENT in line)

    parsed = _parse_bootstrap_reveal([f"bootstrap-1  | {line}"])

    assert parsed is not None
    assert parsed.as_init() is None, "no hay init nuevo: el instalador no debe depositar nada"


def test_el_marcador_de_respaldo_lo_lee_el_parser_de_verdad_del_instalador(capsys) -> None:
    """El respaldo funciona con el logging REAL, no sólo con el doble.

    El instalador cae a `init_tenant.completed` cuando el revelado no declara
    `admin_user_created`, y lo busca en la salida cruda del `docker compose run`.
    O sea que el respaldo depende de que la línea sobreviva a la cadena entera de
    structlog —incluido el enmascarado de PII— con el campo `created_user`
    intacto y en el nivel superior. Con un logger de mentira eso no se prueba: se
    asume. Aquí se corre el pipeline de verdad.

    De paso queda fijado lo contrario para el revelado: sale por el stream y NO
    por stdout del logging, así que el masker no lo toca.
    """

    import structlog
    from api_server.logging import configure_logging
    from installer_backend.real_step_executor import _admin_user_existed_from

    configure_logging(service="bootstrap")
    stdout = io.StringIO()
    db = FakeDatabase(created_user=False)
    db.stdout = stdout
    deps = BootstrapDeps(
        database=db,
        vault=FakeVaultClient(),
        stdout=stdout,
        log=structlog.get_logger("api-server.bootstrap"),
        mint_password=lambda: _MINTED,
        expected_revisions=(_HEAD,),
    )

    asyncio.run(run_bootstrap(_options(), deps))

    logged = [f"bootstrap-1  | {line}" for line in capsys.readouterr().out.splitlines()]
    assert _admin_user_existed_from(logged) is True

    for secret in (SCRIPTED_ROOT_TOKEN, _MINTED, *SCRIPTED_UNSEAL_KEYS):
        assert not any(secret in line for line in logged), (
            f"el material de una sola vez salió por el logging: {secret}"
        )


def test_el_revelado_no_puede_pasar_por_structlog() -> None:
    """El masker de PII enmascara recursivamente TODO string del `event_dict`, y
    su regex de claves de API incluye `hvs\\.`: el root token saldría como
    `hvs.***REDACTED***`. Por eso la línea va a stdout con `print`, fuera de la
    cadena de structlog, aunque el resto del one-shot loguee normal."""

    harness = _run()
    line = next(line for line in harness.stdout_lines() if BOOTSTRAP_REVEAL_EVENT in line)

    assert mask_pii_in_text(SCRIPTED_ROOT_TOKEN) != SCRIPTED_ROOT_TOKEN, (
        "si el masker dejara de tocar los `hvs.`, este test deja de proteger nada"
    )
    assert SCRIPTED_ROOT_TOKEN in line
    assert "REDACTED" not in line


def test_ninguna_linea_de_log_lleva_el_material_de_una_sola_vez() -> None:
    harness = _run()
    logged = json.dumps(
        [[name, {k: str(v) for k, v in kw.items()}] for name, kw in harness.log.events]
    )

    for secret in (SCRIPTED_ROOT_TOKEN, _MINTED, *SCRIPTED_UNSEAL_KEYS):
        assert secret not in logged, f"secreto en un log: {secret}"


# --- el guardarraíl del slug ------------------------------------------------


def test_el_slug_se_corta_al_ancho_de_la_columna_organizations_slug() -> None:
    """`organizations.slug` es `String(64)` y PostgreSQL NO trunca: un slug más
    largo levanta `value too long for type character varying(64)` en el ÚLTIMO
    paso, que en este diseño es después de que Vault haya emitido unas unseal
    keys que se muestran exactamente una vez."""

    largo = " ".join(["Departamento de Innovacion Tecnologica"] * 5)

    options = parse_options({TENANT_NAME_ENV: largo, ADMIN_EMAIL_ENV: _EMAIL})

    assert len(options.slug) <= ORG_SLUG_MAX_LENGTH
    assert Organization.__table__.c.slug.type.length == ORG_SLUG_MAX_LENGTH, (
        "el cap y el ancho de la columna se han separado; el guardarraíl dejó de "
        "proteger de lo que protegía"
    )


def test_el_slug_translitera_los_acentos_en_vez_de_perderlos() -> None:
    options = parse_options({TENANT_NAME_ENV: "Dirección Técnica", ADMIN_EMAIL_ENV: _EMAIL})

    assert options.slug == "direccion-tecnica"


def test_un_nombre_sin_caracteres_ascii_cae_al_slug_de_respaldo_y_no_revienta() -> None:
    options = parse_options({TENANT_NAME_ENV: "!!!", ADMIN_EMAIL_ENV: _EMAIL})

    assert options.slug == "untitled", "feo, pero instala: reventar aquí sería peor"


def test_un_nombre_que_slugifica_a_platform_se_rechaza_en_vez_de_arreglarse_solo() -> None:
    """`init_tenant` resuelve la org POR SLUG y `PLATFORM_TENANT_SLUG` es
    literalmente `platform`: el primer System Owner colgaría del tenant de
    PLATAFORMA en vez del suyo, y sin error, porque para `init_tenant` ése es el
    camino idempotente normal."""

    with pytest.raises(OptionsError) as exc:
        parse_options({TENANT_NAME_ENV: "Platform", ADMIN_EMAIL_ENV: _EMAIL})

    assert PLATFORM_TENANT_SLUG in str(exc.value)
    assert exc.value.exit_code == ExitCode.BAD_INPUT


# --- el entorno -------------------------------------------------------------


def test_los_dos_argumentos_del_one_shot_son_obligatorios() -> None:
    with pytest.raises(OptionsError) as exc:
        parse_options({ADMIN_EMAIL_ENV: _EMAIL})
    assert TENANT_NAME_ENV in str(exc.value)

    with pytest.raises(OptionsError) as exc:
        parse_options({TENANT_NAME_ENV: _TENANT})
    assert ADMIN_EMAIL_ENV in str(exc.value)


def test_las_unseal_keys_llegan_separadas_por_coma_y_sin_espacios_de_mas() -> None:
    options = parse_options(
        {
            TENANT_NAME_ENV: _TENANT,
            ADMIN_EMAIL_ENV: _EMAIL,
            UNSEAL_KEYS_ENV: " a , b ,, c ",
        }
    )

    assert options.existing_unseal_keys == ("a", "b", "c")


def test_el_repr_de_las_opciones_no_lleva_el_token_ni_las_claves() -> None:
    options = _options(existing_unseal_keys=("share-secreta",), vault_token="hvs.secreta")

    text = repr(options)
    assert "share-secreta" not in text
    assert "hvs.secreta" not in text
    assert _TENANT in text, "lo que no es secreto sí se ve: si no, no se puede diagnosticar"


def test_el_token_del_re_bootstrap_tambien_viaja_por_entorno() -> None:
    options = parse_options(
        {TENANT_NAME_ENV: _TENANT, ADMIN_EMAIL_ENV: _EMAIL, VAULT_TOKEN_ENV: "hvs.aportado"}
    )

    assert options.vault_token == "hvs.aportado"


def test_el_one_shot_no_acepta_argumentos_de_linea_de_comandos() -> None:
    """Un secreto en `argv` queda a la vista en `ps` y en el historial del shell.
    La superficie se cierra entera: no hay parser de argumentos que rellenar."""

    from api_server.bootstrap.__main__ import main

    stderr = io.StringIO()
    code = main(["--admin-password", "loquesea"], stderr=stderr)

    assert code == ExitCode.BAD_INPUT
    assert "entorno" in stderr.getvalue().lower()
    assert "loquesea" not in stderr.getvalue(), (
        "ni siquiera se hace eco del argumento: podría ser justo el secreto que "
        "no debería haberse escrito en la línea de comandos"
    )


# --- el pre-flight de esquema -----------------------------------------------


def test_sin_esquema_migrado_se_para_antes_de_tocar_vault() -> None:
    """Sembrar antes de Alembic falla con `relation "organizations" does not
    exist`. El `depends_on: migrations service_completed_successfully` lo cubre
    en el camino normal, pero un `run --rm --no-deps` lo evapora entero."""

    vault = FakeVaultClient()
    db = FakeDatabase(schema=SchemaState(missing_tables=("organizations",), applied_revisions=()))

    with pytest.raises(SchemaNotReadyError) as exc:
        _run(vault=vault, database=db)

    assert "organizations" in str(exc.value)
    assert vault.init_calls == 0, "un fallo de esquema después del init cuesta las unseal keys"
    assert exc.value.exit_code == ExitCode.SCHEMA_NOT_READY


def test_un_esquema_por_detras_del_head_de_esta_imagen_tambien_para() -> None:
    """`service_completed_successfully` prueba que el contenedor `migrations`
    salió con 0, NO que el esquema esté en el head de ESTA imagen."""

    vault = FakeVaultClient()
    db = FakeDatabase(schema=SchemaState(missing_tables=(), applied_revisions=("0100_viejo",)))

    with pytest.raises(SchemaNotReadyError) as exc:
        _run(vault=vault, database=db, expected_revisions=(_HEAD,))

    assert "0100_viejo" in str(exc.value)
    assert _HEAD in str(exc.value)
    assert vault.init_calls == 0


def test_un_head_que_no_se_puede_resolver_avisa_pero_no_bloquea() -> None:
    """«No lo sé» no es «está mal». Si las migraciones no son localizables desde
    este proceso, se dice y se sigue: bloquear la instalación por no poder
    comprobar algo sería peor que la comprobación que falta."""

    harness = _run(expected_revisions=())

    assert harness.reveal is not None
    assert "bootstrap.schema.head_unknown" in harness.log.event_names()


# --- fallos: mensaje, no traza ----------------------------------------------


def test_un_vault_que_no_responde_sale_como_mensaje_con_la_direccion_dentro() -> None:
    class _Muerto(FakeVaultClient):
        def is_initialized(self) -> bool:
            raise ConnectionError("[Errno 111] Connection refused")

    with pytest.raises(VaultBootstrapError) as exc:
        _run(vault=_Muerto())

    message = str(exc.value)
    assert "vault:8200" in message
    assert "Connection refused" in message
    assert "Traceback" not in message


def test_un_fallo_de_la_siembra_no_se_lleva_el_revelado_por_delante() -> None:
    """El catálogo son minutos contra Ollama y es lo más frágil del one-shot. Si
    revienta, el material irrecuperable YA salió por stdout y el instalador ya lo
    ha depositado: lo que se pierde es el catálogo, que se re-siembra."""

    stdout = io.StringIO()
    db = FakeDatabase(seed_error=RuntimeError("ollama unreachable\n[parameters: no-mirar]"))
    deps = BootstrapDeps(
        database=db,
        vault=FakeVaultClient(),
        stdout=stdout,
        log=FakeLog(),
        mint_password=lambda: _MINTED,
        expected_revisions=(_HEAD,),
    )

    with pytest.raises(DatabaseError) as exc:
        asyncio.run(run_bootstrap(_options(), deps))

    assert BOOTSTRAP_REVEAL_EVENT in stdout.getvalue()
    assert "ollama unreachable" in str(exc.value)
    assert "no-mirar" not in str(exc.value), "sólo la PRIMERA línea: los parámetros no viajan"


@pytest.mark.parametrize("created_user", [True, False])
def test_un_fallo_nunca_repite_un_secreto_que_ya_salio_por_stdout(created_user: bool) -> None:
    """El instalador filtra por valor sus líneas de progreso precisamente porque
    no se fía de esta mitad. Esta mitad tampoco se fía de sí misma.

    Los dos casos, porque no son el mismo: con el admin YA EXISTENTE el revelado
    NO lleva contraseña —a propósito—, pero la minteada sí ha existido dentro de
    este proceso. Taparse sólo con lo que se reveló dejaría fuera justo ésa.
    """

    stdout = io.StringIO()
    db = FakeDatabase(
        created_user=created_user,
        seed_error=RuntimeError(f"el fallo llevaba {_MINTED} dentro"),
    )
    deps = BootstrapDeps(
        database=db,
        vault=FakeVaultClient(),
        stdout=stdout,
        log=FakeLog(),
        mint_password=lambda: _MINTED,
        expected_revisions=(_HEAD,),
    )

    with pytest.raises(DatabaseError) as exc:
        asyncio.run(run_bootstrap(_options(), deps))

    assert _MINTED not in str(exc.value)


def test_el_pool_se_cierra_aunque_el_one_shot_falle() -> None:
    """El `finally` del punto de entrada, y por qué existe.

    `asyncio.run` cierra el bucle al salir; con el pool de asyncpg todavía vivo,
    Python escribe `Exception ignored in: ...` por stderr. Eso saldría JUSTO
    debajo del revelado —y en la cola de salida que el instalador enseña cuando
    algo falla—, así que un one-shot que ha terminado bien parecería roto. El
    camino que más lo necesita es precisamente el del fallo, que es el que se
    guioniza aquí.
    """

    from api_server.bootstrap.__main__ import _amain

    closed: list[bool] = []

    class _Db(FakeDatabase):
        async def aclose(self) -> None:
            closed.append(True)

    stdout = io.StringIO()
    db = _Db(schema=SchemaState(missing_tables=("organizations",), applied_revisions=()))
    deps = BootstrapDeps(
        database=db,
        vault=FakeVaultClient(),
        stdout=stdout,
        log=FakeLog(),
        mint_password=lambda: _MINTED,
        expected_revisions=(_HEAD,),
    )

    with pytest.raises(SchemaNotReadyError):
        asyncio.run(_amain(_options(), deps, db))

    assert closed == [True]


def test_todo_fallo_del_one_shot_lleva_un_codigo_de_salida_distinto_de_cero() -> None:
    from api_server.bootstrap.__main__ import report_failure

    for error in (
        OptionsError("mal"),
        SchemaNotReadyError("mal"),
        VaultBootstrapError("mal"),
        DatabaseError("mal"),
        BootstrapError("mal"),
    ):
        stderr = io.StringIO()
        code = report_failure(error, stderr=stderr)
        assert code != ExitCode.OK
        assert "mal" in stderr.getvalue()
        assert "Traceback" not in stderr.getvalue()


# --- guardas de código ------------------------------------------------------


def _package_sources() -> Iterable[Path]:
    return sorted(_PACKAGE.glob("*.py"))


def _source_of_package() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in _package_sources())


def _docstring_ids(tree: ast.AST) -> set[int]:
    """Los `id()` de los nodos que son docstrings, para no confundirlos con código.

    Sin esto, estas guardas se dispararían con la PROSA que explica de qué
    protegen — que es justo lo que hay que escribir. Una guarda que castiga
    documentar el porqué acaba con el porqué sin documentar.
    """

    holders = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, holders):
            continue
        first = node.body[0] if node.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            ids.add(id(first.value))
    return ids


def _code_string_literals() -> list[str]:
    """Todas las cadenas del paquete que son CÓDIGO: sin docstrings ni comentarios."""

    literals: list[str] = []
    for path in _package_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        skip = _docstring_ids(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if id(node) in skip:
                continue
            literals.append(node.value)
    return literals


def _imported_names() -> set[str]:
    names: set[str] = set()
    for path in _package_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom | ast.Import):
                names.update(alias.name for alias in node.names)
    return names


def test_el_one_shot_no_escribe_nada_en_el_sistema_de_ficheros() -> None:
    """Es efímero: quien guarda las unseal keys es el instalador, que tiene su
    escrow. Escribirlas dentro del contenedor las manda a una capa que se va con
    el `--rm`, y de paso deja una segunda copia donde nadie la borra."""

    prohibidas = {"open", "write_text", "write_bytes", "mkstemp", "NamedTemporaryFile", "mkdir"}
    culpables: list[str] = []
    for path in _package_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in prohibidas:
                culpables.append(f"{path.name}:{node.lineno} {name}()")

    assert culpables == [], f"el one-shot no puede escribir a disco: {culpables}"


def test_la_siembra_usa_el_rol_bypassrls_y_no_el_de_las_peticiones() -> None:
    """`organizations` tiene `FORCE ROW LEVEL SECURITY` con `org_self_only` sin
    `WITH CHECK`, así que insertar con `app_user` exigiría conocer el UUID de la
    org ANTES de crearla. La tabla está diseñada para que sólo la escriba un rol
    BYPASSRLS — que es `service_user`, el de `get_admin_sessionmaker()`."""

    imported = _imported_names()

    assert "get_admin_sessionmaker" in imported
    assert "get_sessionmaker" not in imported, (
        "el sessionmaker de las peticiones es NOBYPASSRLS: la siembra del "
        "catálogo de plataforma no puede pasar por ahí"
    )
    sql = " ".join(_code_string_literals())
    assert "set_config" not in sql
    assert "current_setting" not in sql, (
        "BYPASSRLS exime de la RLS con independencia de FORCE; fijar app.tenant_id "
        "sugeriría, en falso, que la siembra está acotada a un tenant"
    )


def test_el_one_shot_no_mintea_tokens_de_servicio_ni_escribe_secretos_en_el_kv() -> None:
    """El bootstrap de Vault escribe POLÍTICAS, no datos. Los tokens por servicio
    son `scripts/vault-mint-service-tokens.sh`, un paso posterior y humano que
    necesita el root token que este one-shot acaba de revelar."""

    literals = _code_string_literals()

    assert "API_SERVER_VAULT_TOKEN" not in literals
    assert not any("create_token" in text for text in literals)
    assert not any("secrets/data" in text for text in literals), (
        "escribir un secreto de plataforma en el KV no es cosa de este one-shot"
    )
