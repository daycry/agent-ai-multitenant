"""Contrato: el backup del stack GENERADO tiene que poder correr (prod-04 task_prod_04_09).

Hermano de [`test_compose_env_contract.py`](./test_compose_env_contract.py) — mismo
patrón (generar el compose + el `.env` del instalador y cruzarlos contra el
`Settings` real), otra pregunta. Aquél comprueba que las CLAVES llegan con el
prefijo correcto; éste comprueba que los VALORES del subsistema de backup
describen el stack que el instalador acaba de generar.

## Por qué existe (hallazgo deploy-4)

El backup diario es la única cosa del stack cuyo fallo no se nota hasta el día
del desastre. Y en una instalación por el instalador fallaba **todas las noches**
por dos motivos independientes, ninguno visible sin ejecutarlo:

1. **DSN del desarrollador**: nadie emitía `WORKERS_BACKUP_DATABASE_URL` en forma
   libpq, así que `pg_dump` recibía o la URL de SQLAlchemy (`postgresql+asyncpg://`,
   que libpq no entiende) o —si la variable faltaba— el default de dev de
   `workers/config.py`: `postgresql://migrations_user:changeme-…@localhost:15432`.
   O sea, la máquina del desarrollador.
2. **Volúmenes fantasma**: el compose generado monta **binds** bajo
   `{data_root}` (`{data_root}/minio:/data`, `{data_root}/redis:/data`, …) y no
   declara NINGÚN named volume; pero el backup recibía la lista de named volumes
   de la máquina de manuales (`agentic-platform_minio_data`, …). `tar` sobre
   `/var/lib/docker/volumes/agentic-platform_minio_data/_data` —un path que no
   existe— devuelve rc≠0, y el contrato clean-failure del motor borra el bundle
   entero, incluido el `pg_dump` bueno.

Ambos son fallos de **coherencia entre dos generadores**, la forma que este repo
repite: el mecanismo estaba entero y lo que no cuadraba era el cableado. Un test
de unidad del motor de backup no puede verlos porque no mira el stack generado;
por eso el contrato vive aquí.

## La invariante

Todo lo que el backup va a capturar tiene que **existir en el layout generado y
ser visible dentro del contenedor que corre `tar`** (la lane
`workers-privileged`, que es quien drena la cola `privileged`). Named volume:
declarado en el compose. Bind path: bajo un montaje de esa lane cuyo origen y
destino coinciden. Y el `pg_dump` tiene que apuntar al servicio `postgres` del
stack, no a `localhost`.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import PurePosixPath
from typing import Any

import pytest
from installer_backend.compose_generator import generate_compose
from installer_backend.config import (
    Environment,
    InstallerConfig,
    OllamaProvider,
    PortsConfig,
    ProvidersConfig,
    ResourceConfig,
    StorageConfig,
    SystemConfig,
    TenantConfig,
)
from installer_backend.config_generators import build_env_vars, generate_secrets
from workers.backup import BackupConfig, libpq_url
from workers.config import Settings as WorkersSettings

pytestmark = pytest.mark.unit

#: La lane que drena la cola `privileged` — la que ejecuta el backup diario y por
#: tanto la única cuyo rootfs importa para las rutas de captura.
_BACKUP_SERVICE = "workers-privileged"

#: Data root DELIBERADAMENTE distinto del default de `workers/config.py`
#: (`/data/agent-platform`). El asistente lo deja elegir, y con el default los
#: fallos de coherencia se esconden: una ruta de captura equivocada coincidiría
#: por accidente con la buena. Con otro data root, cualquier default de dev que
#: sobreviva al generador queda a la vista.
_DATA_ROOT = "/srv/agentic-platform"


def _prod_config() -> InstallerConfig:
    return InstallerConfig(
        system=SystemConfig(domain="agentic.example.com", environment=Environment.PRODUCTION),
        resources=ResourceConfig(
            worker_replicas=2,
            worker_memory_gib=4,
            gpu_enabled=False,
            ollama_mode=None,
            embedding_model="nomic-embed-text",
        ),
        storage=StorageConfig(
            data_root=_DATA_ROOT,
            minio_bucket="agentic-platform",
            minio_access_key="throwaway-access",
            minio_secret_key="throwaway-secret-value-123",
        ),
        providers=ProvidersConfig(ollama=OllamaProvider(enabled=True, endpoint="http://o:11434")),
        tenant=TenantConfig(tenant_name="Acme", admin_email="admin@example.com"),
        ports=PortsConfig(),
    )


def _generated_stack() -> tuple[dict[str, Any], dict[str, str]]:
    """El compose y el `.env` que el instalador escribiría, con los MISMOS flags."""
    cfg = _prod_config()
    compose = generate_compose(cfg, monitoring=True)
    dotenv = build_env_vars(cfg, generate_secrets(), monitoring=True)
    return compose, dotenv


#: `${VAR}`, `${VAR:-default}` y `${VAR:?mensaje}` — las tres formas que emite
#: `compose_generator._env_ref`, con el operador y su argumento separados.
_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:[-?])?([^}]*)\}")


def _resolve(value: str, dotenv: dict[str, str]) -> str:
    """Sustituir las referencias del compose con el `.env` real.

    Modela lo que hace docker compose, **incluida la diferencia que importa**:
    con `:?` una variable ausente ABORTA el proyecto entero; no se resuelve a
    cadena vacía. Este helper sólo entendía `${VAR}` y `${VAR:-default}`, así que
    cuando el instalador pasó a la forma fail-closed (auditoría 2026-08-27)
    devolvía `""` para TODAS las credenciales — y los tests de este fichero
    pasaban a afirmar sobre un entorno que ningún contenedor llega a ver nunca.
    Un resolvedor que traduce «esto aborta» por «esto vale la cadena vacía» no
    modela el sistema: lo suplanta.
    """

    def _sub(match: re.Match[str]) -> str:
        name, op, arg = match.group(1), match.group(2), match.group(3)
        if name in dotenv:
            return dotenv[name]
        if op == ":-":
            return arg
        if op == ":?":
            raise AssertionError(
                f"el compose referencia {name} como obligatoria y el `.env` generado no "
                f"la escribe: `docker compose up` abortaría con «{arg}»"
            )
        return ""

    return _REF.sub(_sub, value)


def _service_env(compose: dict[str, Any], dotenv: dict[str, str], service: str) -> dict[str, str]:
    raw = compose["services"][service]["environment"]
    return {str(k): _resolve(str(v), dotenv) for k, v in raw.items() if v is not None}


def _mounts(compose: dict[str, Any], service: str) -> list[tuple[str, str]]:
    """(origen, destino) de cada montaje corto `src:dst[:mode]` del servicio."""
    out: list[tuple[str, str]] = []
    for entry in compose["services"][service].get("volumes") or []:
        if not isinstance(entry, str):
            continue
        parts = entry.split(":")
        if len(parts) >= 2:
            out.append((parts[0], parts[1]))
    return out


def _effective_settings(env: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> WorkersSettings:
    """El `Settings` que el worker construiría con ESE entorno, y nada más.

    Se limpian las `WORKERS_*` del entorno del test runner (y se desactiva el
    `.env` que pydantic-settings leería del CWD) para que lo que quede sea, o lo
    que emite el instalador, o el default de `workers/config.py` — que es
    justamente la distinción que este test tiene que poder ver.
    """
    for key in list(os.environ):
        if key.startswith("WORKERS_"):
            monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        if key.startswith("WORKERS_"):
            monkeypatch.setenv(key, value)
    return WorkersSettings(_env_file=None)  # type: ignore[call-arg]


def _backup_config(monkeypatch: pytest.MonkeyPatch) -> tuple[BackupConfig, dict[str, str]]:
    compose, dotenv = _generated_stack()
    env = _service_env(compose, dotenv, _BACKUP_SERVICE)
    settings = _effective_settings(env, monkeypatch)
    return BackupConfig.from_settings(settings), env


def _capture_paths(cfg: BackupConfig) -> list[str]:
    """Las rutas del host que el backup taréa (binds + repos + data dir de Redis)."""
    paths = [str(p) for p in cfg.bind_paths]
    if cfg.projects_root:
        paths.append(str(cfg.projects_root))
    if cfg.redis_dir:
        paths.append(str(cfg.redis_dir))
    return paths


def _is_under(path: str, ancestor: str) -> bool:
    a = PurePosixPath(path.rstrip("/"))
    b = PurePosixPath(ancestor.rstrip("/"))
    return a == b or b in a.parents


# --- la guarda no puede pasar vacíamente -------------------------------------


def test_the_generated_stack_still_has_the_backup_lane() -> None:
    """Sin la lane privilegiada no hay backup diario, y este fichero entero
    pasaría en verde sobre un stack que no respalda nada."""
    compose, _ = _generated_stack()
    assert _BACKUP_SERVICE in compose["services"], (
        f"el compose generado ya no declara {_BACKUP_SERVICE}: nadie drenaría la "
        "cola `privileged` y el backup diario no correría"
    )
    assert _mounts(compose, _BACKUP_SERVICE), f"{_BACKUP_SERVICE} no monta nada"


def test_the_installer_emits_the_backup_wiring(monkeypatch: pytest.MonkeyPatch) -> None:
    """Las claves que describen QUÉ se captura tienen que venir del instalador.

    Si desaparecen, los tests de abajo seguirían pasando: heredarían los defaults
    de dev de `workers/config.py`, que son coherentes entre sí (`/data/agent-platform`
    + named volumes) aunque no describan este stack. La guarda es que las emita
    quien conoce el layout.
    """
    compose, dotenv = _generated_stack()
    env = _service_env(compose, dotenv, _BACKUP_SERVICE)
    for key in (
        "WORKERS_BACKUP_DATABASE_URL",
        "WORKERS_BACKUP_VOLUMES",
        "WORKERS_BACKUP_BIND_PATHS",
        "WORKERS_BACKUP_PROJECTS_ROOT",
        "WORKERS_BACKUP_REDIS_DIR",
    ):
        assert key in env, (
            f"{_BACKUP_SERVICE} no recibe {key}: el backup usaría el default de dev "
            "de workers/config.py, que describe otra máquina"
        )
        assert env[key] != "", f"{key} llega vacío (¿referencia ${{...}} sin clave en el .env?)"
    _effective_settings(env, monkeypatch)  # y el Settings los acepta


# --- el DSN del pg_dump -------------------------------------------------------


def test_the_backup_dsn_is_not_the_developers_host(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, _ = _backup_config(monkeypatch)
    dsn = cfg.database_url
    for marker in ("localhost", "127.0.0.1", ":15432", "changeme", "dev-only"):
        assert marker not in dsn.lower(), (
            f"el DSN de backup del stack generado contiene {marker!r}: el pg_dump "
            "diario apuntaría a la máquina del desarrollador (o con credencial de "
            f"dev). DSN saneado: {re.sub(r':[^:@]*@', ':***@', dsn)}"
        )
    assert "@postgres:5432/" in dsn, (
        f"el DSN de backup no apunta al servicio `postgres` del compose: {dsn!r}"
    )


def test_the_backup_dsn_is_libpq_not_sqlalchemy(monkeypatch: pytest.MonkeyPatch) -> None:
    """`pg_dump` habla libpq: el sufijo `+asyncpg` es un URI que no entiende.

    El motor lo sanea (`workers.backup.libpq_url`), que es el cinturón; esto es
    el tirante, y además documenta la forma que el instalador debe emitir. Se
    afirma sobre la variable EMITIDA, no sobre la ya normalizada, o el test
    pasaría vacíamente.
    """
    compose, dotenv = _generated_stack()
    emitted = _service_env(compose, dotenv, _BACKUP_SERVICE)["WORKERS_BACKUP_DATABASE_URL"]
    assert "+" not in emitted.split("://", 1)[0], (
        f"el instalador emite un DSN de SQLAlchemy para pg_dump: {emitted.split('://')[0]!r}"
    )
    assert libpq_url(emitted) == emitted


# --- lo que se captura existe en el layout generado ---------------------------


def test_no_phantom_named_volume_is_captured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cada named volume que el backup taréa está DECLARADO en el compose.

    El fallo real: la lista venía del stack de manuales
    (`agentic-platform_minio_data`, …) y el compose generado no declara ningún
    named volume — los stores son binds bajo `{data_root}`. `tar` sobre un
    `_data` inexistente da rc≠0 y el clean-failure borra el bundle completo.
    """
    compose, _dotenv = _generated_stack()
    cfg, _ = _backup_config(monkeypatch)
    declared = set((compose.get("volumes") or {}).keys())
    # docker prefija los volúmenes con el nombre del proyecto compose; se acepta
    # el nombre declarado o cualquier sufijo suyo tras un `_`/`-`.
    phantom = [
        name
        for name in cfg.volumes
        if name not in declared
        and not any(name.endswith(f"_{d}") or name.endswith(f"-{d}") for d in declared)
    ]
    assert not phantom, (
        f"el backup taréa named volumes que el compose generado NO declara: {phantom}. "
        f"Declarados: {sorted(declared) or '(ninguno: los stores son binds)'}. "
        "tar sobre un _data inexistente devuelve rc≠0 y el bundle entero se borra"
    )


def test_every_capture_path_is_visible_inside_the_backup_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Las rutas de captura existen DENTRO del contenedor que corre `tar`.

    El worker taréa rutas del host, pero las lee a través de su propio rootfs: si
    la ruta no cae bajo un montaje cuyo origen y destino coinciden, o no existe
    (rc≠0 de tar) o —peor— existe vacía en el rootfs efímero y el bundle sale
    correcto y vacío.
    """
    compose, _ = _generated_stack()
    cfg, _ = _backup_config(monkeypatch)
    same_path_mounts = [dst for src, dst in _mounts(compose, _BACKUP_SERVICE) if src == dst]
    assert same_path_mounts, f"{_BACKUP_SERVICE} no monta ninguna ruta del host en su mismo path"

    invisible = [
        path
        for path in _capture_paths(cfg)
        if not any(_is_under(path, mount) for mount in same_path_mounts)
    ]
    assert not invisible, (
        f"el backup taréa rutas que {_BACKUP_SERVICE} no ve: {invisible}. "
        f"Montajes host==contenedor: {same_path_mounts}"
    )


def test_the_backup_covers_the_stores_pg_dump_does_not(monkeypatch: pytest.MonkeyPatch) -> None:
    """No-vacuidad con dientes: vaciar la lista de captura haría pasar los dos
    tests de arriba y dejaría un «backup» que solo trae la base de datos.

    Los tres stores cuyo estado NO está en el dump son MinIO (los binarios de la
    KB), Redis y el file backend de Vault (sin él, ningún secreto del stack
    restaurado se puede descifrar). Se comprueban contra los binds que el compose
    generado declara de verdad, no contra una lista escrita a mano aquí.
    """
    compose, _ = _generated_stack()
    cfg, _ = _backup_config(monkeypatch)
    captured = _capture_paths(cfg)

    for service, keyword in (("minio", "minio"), ("redis", "redis"), ("vault", "vault")):
        host_paths = [src for src, _dst in _mounts(compose, service) if src.startswith("/")]
        assert host_paths, f"el compose ya no bind-montea nada en {service}"
        store = next((p for p in host_paths if keyword in p), None)
        assert store is not None, f"no encuentro el bind de datos de {service} en {host_paths}"
        assert any(_is_under(store, path) for path in captured), (
            f"el estado de {service} ({store}) no entra en el bundle: {captured}. "
            "El pg_dump no lo cubre — se perdería en un restore"
        )

    # Y los repos bare, que son el producto de la plataforma (principios 4 y 5).
    assert cfg.projects_root, "sin projects_root el bundle no lleva los repos de los proyectos"


def test_redis_is_captured_coherently_not_as_a_plain_bind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Redis va por `redis_dir`, no por `bind_paths` (task_prod_04_06).

    No es una preferencia de estilo: la ruta de `redis_dir` es la única que pide un
    `BGREWRITEAOF` completado antes de tarear. Como bind se copiaría el
    `appendonlydir` mientras el servidor le escribe — y con el añadido de que un
    base file capturado a mitad de un rewrite no es restaurable.
    """
    compose, _ = _generated_stack()
    cfg, _ = _backup_config(monkeypatch)
    redis_bind = next(src for src, _dst in _mounts(compose, "redis") if src.startswith("/"))

    assert cfg.redis_dir, "el stack generado no declara WORKERS_BACKUP_REDIS_DIR"
    assert _is_under(redis_bind, cfg.redis_dir), (
        f"redis_dir ({cfg.redis_dir}) no cubre el bind de datos de redis ({redis_bind})"
    )
    swallowed = [p for p in cfg.bind_paths if _is_under(redis_bind, str(p))]
    assert not swallowed, (
        f"el data dir de Redis entra ADEMÁS como bind ({swallowed}): se capturaría dos "
        "veces y la segunda en caliente, sin BGREWRITEAOF previo"
    )


def test_the_vault_tree_capture_is_verified_stable(monkeypatch: pytest.MonkeyPatch) -> None:
    """El file backend de Vault se captura con verificación de estabilidad.

    Una copia tomada a mitad de una escritura puede dejar el barrel de claves
    inconsistente, y eso no da NINGUNA señal hasta que alguien intenta desellar el
    Vault restaurado — en pleno DR. Y MinIO NO puede estar en esa lista: se escribe
    todo el rato por diseño y exigirle estabilidad convertiría el backup nocturno
    en un fallo nocturno.
    """
    compose, _ = _generated_stack()
    cfg, _ = _backup_config(monkeypatch)
    vault_bind = next(src for src, _dst in _mounts(compose, "vault") if "vault" in src)
    minio_bind = next(src for src, _dst in _mounts(compose, "minio") if src.startswith("/"))

    assert any(_is_under(vault_bind, str(p)) for p in cfg.stable_snapshot_paths), (
        f"el árbol de Vault ({vault_bind}) no está en stable_snapshot_paths "
        f"({list(cfg.stable_snapshot_paths)}): su copia rota no daría señal"
    )
    assert not any(_is_under(minio_bind, str(p)) for p in cfg.stable_snapshot_paths), (
        f"MinIO ({minio_bind}) está en stable_snapshot_paths: se escribe por diseño "
        "y el backup nocturno fallaría por «el árbol cambió» todas las noches"
    )


def test_the_live_pgdata_is_not_tarred(monkeypatch: pytest.MonkeyPatch) -> None:
    """El directorio de datos de PostgreSQL NO entra en los tars.

    `pg_dump` ya lo cubre, en forma consistente y restaurable. Tarearlo además es
    caro, produce una copia rota (`tar` lee mientras postgres escribe) y sobre
    todo puede tirar el backup entero: GNU tar devuelve rc≠0 con «file changed as
    we read it», y el contrato clean-failure borra el bundle bueno.
    """
    compose, _ = _generated_stack()
    cfg, _ = _backup_config(monkeypatch)
    pgdata = next(
        src
        for src, dst in _mounts(compose, "postgres")
        if dst.startswith("/var/lib/postgresql") and src.startswith("/")
    )
    swallowed = [path for path in _capture_paths(cfg) if _is_under(pgdata, path)]
    assert not swallowed, (
        f"el PGDATA vivo ({pgdata}) entra en los tars vía {swallowed}: copia rota, "
        "y el «file changed as we read it» de tar puede tirar el backup entero"
    )


# --- cifrado: si está encendido, tiene que poder cifrar -----------------------


def test_encryption_is_either_off_or_fully_wired(monkeypatch: pytest.MonkeyPatch) -> None:
    """Coherencia del cifrado en el stack generado.

    El motor es fail-closed por diseño (task_prod_04_07): con
    `encryption_enabled` y sin huella de custodia declarada, el backup falla
    ANTES de gastar una hora en el dump — y con razón, porque un bundle cifrado
    cuya clave no está custodiada es irrecuperable. Pero eso significa que un
    instalador que enciende el cifrado sin emitir la clave ni la huella produce
    un stack cuyo backup falla TODAS las noches.

    O apagado (y el runbook explica el opt-in en dos pasos), o completo. Nunca
    encendido a medias.
    """
    cfg, env = _backup_config(monkeypatch)
    if not cfg.encryption_enabled:
        return
    assert env.get("WORKERS_BACKUP_ENCRYPTION_KEY"), (
        "el stack generado enciende el cifrado del backup pero no emite "
        "WORKERS_BACKUP_ENCRYPTION_KEY: el EnvSecretsProvider no resuelve la clave "
        "y el backup falla cada noche"
    )
    assert cfg.key_custody_fingerprint, (
        "el stack generado enciende el cifrado sin WORKERS_BACKUP_KEY_CUSTODY_FINGERPRINT: "
        "el motor es fail-closed fuera de dev y el backup falla cada noche"
    )


def test_the_emitted_lists_are_json_the_settings_can_parse() -> None:
    """Las listas viajan como JSON en una variable de entorno. Un formato que
    pydantic-settings no sepa parsear rompe el ARRANQUE del worker, no el backup.
    """
    compose, dotenv = _generated_stack()
    env = _service_env(compose, dotenv, _BACKUP_SERVICE)
    for key in ("WORKERS_BACKUP_VOLUMES", "WORKERS_BACKUP_BIND_PATHS"):
        parsed = json.loads(env[key])
        assert isinstance(parsed, list), f"{key} no es una lista JSON: {env[key]!r}"


# --- quiesce: los servicios que se paran tienen que existir (ADR 0149) --------


def test_every_quiesced_service_is_declared_in_the_generated_compose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un servicio fantasma en la lista de quiesce degrada el backup cada noche.

    Es el mismo modo de fallo que el ADR 0117 (c) cazó en `restore_app_services`
    (`web-app`, que no existe en ningún compose), con una diferencia que lo hace
    MÁS insidioso: el restore abortaba con rc≠0 y se veía; el quiesce del ADR
    0149 **degrada a propósito** y sigue adelante, así que un nombre mal escrito
    no rompe nada — solo deja de parar a ese escritor y anota `partial` en un
    acta que nadie lee hasta el día del desastre.
    """
    compose, dotenv = _generated_stack()
    env = _service_env(compose, dotenv, _BACKUP_SERVICE)
    settings = _effective_settings(env, monkeypatch)
    declared = set(compose["services"])

    missing = [s for s in settings.backup_quiesce_services if s not in declared]
    assert not missing, (
        f"estos servicios de `backup_quiesce_services` NO están en el compose "
        f"generado: {missing}. El quiesce no los parará y el bundle se capturará "
        f"con esos escritores en pie, registrando `partial` cada noche."
    )


def test_the_backup_lane_is_never_in_the_effective_quiesce_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`workers-privileged` corre el backup: pararlo lo mata a mitad de la captura."""
    compose, dotenv = _generated_stack()
    env = _service_env(compose, dotenv, _BACKUP_SERVICE)
    settings = _effective_settings(env, monkeypatch)

    assert _BACKUP_SERVICE in settings.backup_quiesce_never_stop, (
        f"{_BACKUP_SERVICE} tiene que estar en `backup_quiesce_never_stop`: es la "
        f"lane que ejecuta este backup y pararla lo mata a mitad de la captura"
    )
