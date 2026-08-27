"""El compose que genera el instalador, ¿se puede levantar donde se escribe?

Hasta aquí la suite comprobaba mucho sobre el compose generado —imágenes
pineadas, healthchecks honestos, sin defaults de desarrollo, hardening— pero
nada sobre lo único que decide si la instalación arranca: **que los ficheros y
directorios que el compose monta EXISTAN en el sitio donde el compose acaba**.

El instalador escribe el compose bajo la raíz de DATOS
(``cli.py`` → ``compose_dir = config.storage.data_root``), no bajo el árbol del
repo. Cada ``./algo`` del compose se resuelve, por tanto, contra
``/data/agent-platform/``, donde no hay ningún checkout: lo que hay ahí es lo que
la propia instalación haya creado. Y lo que la instalación crea son cuatro
ficheros (``real_step_executor.py::_generate_config``) más el plan de directorios
de datos (``config_generators.py::build_data_tree_plan``). Nada más. No hay
``copytree``, ni ``rsync``, ni un paso que baje el árbol ``docker/`` del repo:
``scripts/install.sh`` es un ``exec`` al mismo CLI de Python.

Lo que se afirma aquí, y por qué duele cada cosa:

1. **Ninguna ruta relativa del compose queda sin producir.** Es la comprobación
   de fondo: el conjunto de rutas ``./…`` sale del PROPIO compose y el conjunto
   de rutas producidas sale de EJECUTAR el paso real ``GENERATE_CONFIG`` contra
   los seams de memoria. Ninguna de las dos listas está escrita a mano: una lista
   a mano envejece en cuanto alguien añade un montaje, y ése es exactamente el
   modo de fallo que este repo ya pagó — el generador heredó por copia las rutas
   relativas del compose canónico, que sí vive junto a ``docker/``.

2. **Ningún bind relativo cae dentro de otro bind de datos.** No es sólo que
   falte: Docker, ante el lado host ausente de un bind, **lo inventa como
   directorio vacío**. Así que ``./postgres/init`` no da error, se materializa
   DENTRO del PGDATA — que deja de estar vacío antes del ``initdb``, con lo que
   los scripts reales de ``docker/postgres/init/`` (pgvector, roles, logging)
   no corren jamás. Un fallo que no se ve al levantar y se cobra a la primera
   consulta que necesita la extensión. La misma mecánica convierte
   ``./vault/config.hcl`` en un DIRECTORIO donde el binario espera un fichero.

3. **Ningún servicio del NÚCLEO lleva ``pull_policy: build`` sin contexto
   garantizado en destino.** ``build`` significa literalmente «no bajes la
   imagen, constrúyela». Sin contexto de build en el destino, ``docker compose
   up`` aborta al resolver el proyecto —antes de arrancar un solo contenedor—,
   así que no cae un servicio: no arranca NADA. Y son dos servicios del núcleo
   (``egress-proxy``, ``registry-proxy``), no dos overlays opcionales.

Nada de esto toca el host: el generador es puro y el paso de instalación se
ejecuta contra ``FakeEnvFileWriter`` / ``FakeDataTreeProvisioner``, que es la
forma de derivar «qué crea la instalación» del código en vez de de una lista.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

import pytest
from installer_backend.command_runner import FakeCommandRunner
from installer_backend.compose_generator import CORE_SERVICES, generate_compose
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
from installer_backend.config_generators import (
    FakeDataTreeProvisioner,
    FakeEnvFileWriter,
    generate_secrets,
)
from installer_backend.install import InstallStep
from installer_backend.real_step_executor import RealStepExecutor
from installer_backend.vault_bootstrap import FakeVaultClient

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Config realista de instalación. Mismo patrón (y mismos placeholders de usar y
# tirar) que tests/unit/test_compose_generator.py y tests/unit/installer/conftest.py:
# producción, raíz de datos por defecto, un proveedor habilitado.
# ---------------------------------------------------------------------------
def _config(
    *,
    environment: Environment = Environment.PRODUCTION,
    data_root: str = "/data/agent-platform",
) -> InstallerConfig:
    return InstallerConfig(
        system=SystemConfig(domain="agentic.example.com", environment=environment),
        resources=ResourceConfig(
            worker_replicas=2,
            worker_memory_gib=4,
            gpu_enabled=False,
            ollama_mode=None,
            embedding_model="nomic-embed-text",
        ),
        storage=StorageConfig(
            data_root=data_root,
            minio_bucket="agentic-platform",
            minio_access_key="throwaway-access",
            minio_secret_key="throwaway-secret-value-123",
        ),
        providers=ProvidersConfig(ollama=OllamaProvider(enabled=True, endpoint="http://o:11434")),
        tenant=TenantConfig(tenant_name="Acme Corp", admin_email="admin@acme.com"),
        ports=PortsConfig(),
    )


# ---------------------------------------------------------------------------
# Lado A — lo que el compose EXIGE que exista junto a él.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class HostPathRef:
    """Una referencia del compose a una ruta del host, con quién la pide.

    ``origin`` se arrastra sólo para que el fallo nombre al servicio y la clave
    (``postgres.volumes``), no una ruta suelta que haya que ir a buscar.
    """

    service: str
    key: str
    raw: str
    source: str

    @property
    def origin(self) -> str:
        return f"{self.service}.{self.key}"

    @property
    def is_relative(self) -> bool:
        return not self.source.startswith("/")


def _build_context(build: Any) -> str | None:
    """El contexto de un ``build:``, en sintaxis corta (str) o larga (dict)."""

    if isinstance(build, str):
        return build
    if isinstance(build, dict) and build.get("context") is not None:
        return str(build["context"])
    return None


def _bind_source(volume: Any) -> str | None:
    """El lado HOST de un volumen, en sintaxis corta (``src:dst:ro``) o larga.

    Devuelve ``None`` para los volúmenes NOMBRADOS (``whisper_models:/x``), que
    Docker crea solo y no dependen de nada en disco: sólo interesan los binds,
    que son los que exigen que algo exista en el host.
    """

    if isinstance(volume, dict):
        if volume.get("type") not in (None, "bind"):
            return None
        source = volume.get("source")
        return str(source) if source is not None else None
    if not isinstance(volume, str):
        return None
    source = volume.split(":", 1)[0]
    # Un volumen nombrado no lleva ni '/' ni './' — no es una ruta del host.
    if not source.startswith(("/", "./", "../")):
        return None
    return source


def _host_path_refs(compose: dict[str, Any]) -> Iterator[HostPathRef]:
    """Toda ruta del host que el compose referencia, con su origen.

    Sale del PROPIO compose (``build`` + ``volumes`` de cada servicio), no de una
    lista escrita a mano: un montaje nuevo entra aquí solo.
    """

    services = compose["services"]
    assert isinstance(services, dict)
    for name, svc in sorted(services.items()):
        context = _build_context(svc.get("build"))
        if context is not None:
            yield HostPathRef(service=name, key="build", raw=context, source=context)
        for volume in svc.get("volumes", []) or []:
            source = _bind_source(volume)
            if source is not None:
                yield HostPathRef(service=name, key="volumes", raw=str(volume), source=source)


def _resolve(compose_dir: str, source: str) -> PurePosixPath:
    """Resuelve una ruta del compose contra el directorio donde el compose vive.

    ``PurePosixPath`` a propósito: las rutas son del host Linux de destino, no
    del host donde corre la suite (que puede ser Windows).
    """

    return PurePosixPath(compose_dir) / source


# ---------------------------------------------------------------------------
# Lado B — lo que la instalación PRODUCE de verdad, derivado del código.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class InstallProducts:
    """Lo que queda en disco tras el paso ``GENERATE_CONFIG`` de la instalación."""

    files: frozenset[PurePosixPath]
    dirs: frozenset[PurePosixPath]

    @property
    def everything(self) -> frozenset[PurePosixPath]:
        return self.files | self.dirs


def _install_products(cfg: InstallerConfig, *, monitoring: bool) -> InstallProducts:
    """Ejecuta el paso REAL ``GENERATE_CONFIG`` y recoge lo que deja en disco.

    Se ejecuta el ejecutor de verdad (``RealStepExecutor``) contra los seams de
    memoria, en vez de copiar aquí la lista de ficheros: si mañana alguien añade
    un quinto ``env_writer.write(...)`` o una entrada al plan de directorios,
    este conjunto lo recoge sin que nadie tenga que acordarse de tocar el test.
    """

    writer = FakeEnvFileWriter()
    tree = FakeDataTreeProvisioner()
    executor = RealStepExecutor(
        # La MISMA elección que hace el instalador real: el compose se escribe
        # bajo la raíz de datos (cli.py::build_default_installer).
        compose_dir=cfg.storage.data_root,
        runner=FakeCommandRunner(),
        env_writer=writer,
        tree=tree,
        vault_client_factory=lambda _cfg: FakeVaultClient(),
        cfg=cfg,
        secrets=generate_secrets(),
        monitoring=monitoring,
    )
    executor.execute(InstallStep.GENERATE_CONFIG, {})

    files = {PurePosixPath(path) for path in writer.written}
    dirs = {PurePosixPath(entry.path) for entry in tree.provisioned}
    # Los dos bindings reales hacen `mkdir(parents=True)` (real_bindings.py), así
    # que todo ancestro de algo producido existe también — como DIRECTORIO. Ojo
    # con la tentación de contar un ancestro como si cubriera a su hijo: que
    # exista `{root}/postgres` no hace existir `{root}/postgres/init`.
    root = PurePosixPath(cfg.storage.data_root)
    for path in files | dirs:
        for parent in path.parents:
            if parent == root or root in parent.parents:
                dirs.add(parent)
    return InstallProducts(files=frozenset(files), dirs=frozenset(dirs))


def _inventory(compose_dir: str, refs: list[HostPathRef], products: InstallProducts) -> str:
    """Inventario legible: cada ruta relativa, quién la pide y quién la produce."""

    lines = [
        f"Directorio del compose: {compose_dir}",
        "",
        f"{'RUTA RESUELTA':<52} {'QUIÉN LA PRODUCE':<22} PEDIDA POR",
    ]
    for ref in refs:
        target = _resolve(compose_dir, ref.source)
        if target in products.files:
            producer = "GENERATE_CONFIG"
        elif target in products.dirs:
            producer = "árbol de datos"
        else:
            producer = "NADIE"
        lines.append(f"{target!s:<52} {producer:<22} {ref.origin}  ({ref.source})")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 1. Ninguna ruta relativa queda sin producir.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("monitoring", [False, True], ids=["core", "monitoring"])
def test_every_relative_path_in_the_compose_is_produced_by_the_install(
    monitoring: bool,
) -> None:
    """Toda ruta ``./…`` del compose debe existir en destino tras la instalación.

    El compose no se escribe junto al repo sino bajo la raíz de datos, así que
    ``./x`` es ``{data_root}/x`` y ahí sólo hay lo que la propia instalación puso.
    Una ruta que nadie produce no da un error limpio: Docker la INVENTA como
    directorio vacío, y el servicio arranca leyendo la nada.
    """

    cfg = _config()
    compose_dir = cfg.storage.data_root
    compose = generate_compose(cfg, monitoring=monitoring)
    products = _install_products(cfg, monitoring=monitoring)

    relative = [ref for ref in _host_path_refs(compose) if ref.is_relative]
    assert relative, "el compose no tiene rutas relativas: el test estaría midiendo el vacío"

    orphans = sorted(
        {
            str(_resolve(compose_dir, ref.source))
            for ref in relative
            if _resolve(compose_dir, ref.source) not in products.everything
        }
    )
    assert not orphans, (
        f"{len(orphans)} ruta(s) que el compose monta y la instalación NO crea:\n  "
        + "\n  ".join(orphans)
        + "\n\n"
        + _inventory(compose_dir, relative, products)
    )


# ---------------------------------------------------------------------------
# 2. Ningún bind relativo cae dentro de un volumen de datos.
# ---------------------------------------------------------------------------
def _data_volume_roots(compose: dict[str, Any], compose_dir: str) -> dict[PurePosixPath, str]:
    """Los binds ABSOLUTOS que el compose usa como almacén de estado.

    Se excluye la propia raíz de datos: los workers montan ``{root}:{root}``
    entero a propósito (los bare repos y los worktrees tienen que resolver con la
    misma ruta dentro y fuera), así que contarla haría «colisionar» todo con
    todo y el test no diría nada. Lo que importa es el estado de UN servicio:
    PGDATA, ``vault/file``, ``vault/logs``, ``minio``, ``caddy/data``…
    """

    root = PurePosixPath(compose_dir)
    roots: dict[PurePosixPath, str] = {}
    for ref in _host_path_refs(compose):
        if ref.is_relative:
            continue
        path = PurePosixPath(ref.source)
        if path != root and root in path.parents:
            roots.setdefault(path, ref.origin)
    return roots


@pytest.mark.parametrize("monitoring", [False, True], ids=["core", "monitoring"])
def test_no_relative_bind_lands_inside_a_data_volume(monitoring: bool) -> None:
    """Un bind relativo no puede resolver POR DEBAJO del almacén de otro servicio.

    Es la mitad destructiva del defecto. Docker no falla cuando el lado host de
    un bind no existe: lo crea como directorio vacío. Si esa ruta cae dentro de
    un almacén de datos, la instalación no sólo pierde el fichero que esperaba —
    además **corrompe el almacén ajeno**. El caso concreto: ``./postgres/init``
    resuelve a ``{data_root}/postgres/init``, o sea DENTRO del PGDATA, que deja
    de estar vacío antes del ``initdb``; los scripts reales de
    ``docker/postgres/init/`` no llegan a ejecutarse nunca y la base queda sin
    pgvector ni los roles ``migrations``/``app``.
    """

    cfg = _config()
    compose_dir = cfg.storage.data_root
    compose = generate_compose(cfg, monitoring=monitoring)
    stores = _data_volume_roots(compose, compose_dir)
    assert stores, "no se detectó ningún volumen de datos: el test estaría midiendo el vacío"

    collisions: list[str] = []
    for ref in _host_path_refs(compose):
        if not ref.is_relative:
            continue
        target = _resolve(compose_dir, ref.source)
        for store, owner in sorted(stores.items()):
            if store in target.parents:
                collisions.append(
                    f"{ref.origin} monta {ref.source} -> {target}, "
                    f"DENTRO del almacén {store} (de {owner})"
                )

    assert not collisions, (
        "bind(s) relativo(s) que Docker materializaría dentro de un almacén de datos:\n  "
        + "\n  ".join(sorted(set(collisions)))
    )


# ---------------------------------------------------------------------------
# 3. `pull_policy: build` en el núcleo exige contexto en destino.
# ---------------------------------------------------------------------------
def test_no_core_service_pins_pull_policy_build_without_a_shipped_context() -> None:
    """``pull_policy: build`` sin contexto en destino no degrada: aborta el ``up``.

    ``build`` significa literalmente «no bajes la imagen de ningún registro,
    constrúyela aquí». Es lo correcto para una imagen que sólo existe en este
    repo — y por eso se puso: sin ella, ``docker compose pull`` (el paso
    PULL_IMAGES del propio wizard) intentaba bajar ``agentic-platform/egress-proxy``
    de Docker Hub y devolvía rc=1. Pero la política y el contexto son una sola
    decisión: declarar ``build`` obliga a que el contexto EXISTA donde el compose
    vive. Si no existe, ``docker compose up`` falla al resolver el proyecto,
    antes de arrancar un solo contenedor: no se cae un servicio, no arranca NADA.

    Y son servicios del NÚCLEO. El ``egress-proxy`` es la única salida de los
    agent-runtimes hacia los proveedores LLM (ADR 0019) y el ``registry-proxy``
    la de los runtime-templates hacia los registros de paquetes (ADR 0094): no
    hay instalación sin ellos, ni modo degradado en el que falten.
    """

    cfg = _config()
    compose_dir = cfg.storage.data_root
    compose = generate_compose(cfg, monitoring=False)
    products = _install_products(cfg, monitoring=False)
    services = compose["services"]
    assert isinstance(services, dict)

    offenders: list[str] = []
    for name in CORE_SERVICES:
        svc = services[name]
        if svc.get("pull_policy") != "build":
            continue
        context = _build_context(svc.get("build"))
        if context is None:
            offenders.append(f"{name}: pull_policy=build y NINGÚN `build:` que lo respalde")
            continue
        target = _resolve(compose_dir, context)
        if target not in products.everything:
            offenders.append(
                f"{name}: pull_policy=build con contexto {context} -> {target}, "
                "que la instalación no crea"
            )

    assert not offenders, (
        "servicio(s) del NÚCLEO con `pull_policy: build` y sin contexto garantizado "
        "en destino (el `up` aborta antes de arrancar nada):\n  " + "\n  ".join(offenders)
    )
