"""Los ficheros auxiliares que el stack instalado monta, y que VIAJAN con el instalador.

Por qué existe este paquete
---------------------------
El compose que escribe el instalador **no se escribe en el repo**: se escribe
bajo la raíz de datos (``cli.py`` → ``compose_dir = config.storage.data_root``).
Por tanto cada ``./algo`` de ese fichero resuelve contra ``/data/agent-platform/``,
donde no hay ningún checkout — sólo lo que la propia instalación haya puesto ahí.

Hasta el 2026-08-27 el generador heredó por copia las rutas relativas del compose
canónico (``docker/docker-compose.yml``), que sí vive junto a ``docker/``. El
resultado eran **seis familias de rutas que el compose montaba y nadie escribía**,
y el modo de fallo NO era un error limpio: Docker materializa como *directorio
vacío* el lado host ausente de un bind, así que

* ``./postgres/init`` se creaba **dentro del PGDATA** → el ``initdb`` encontraba el
  directorio no vacío, no inicializaba el cluster y los cinco scripts de
  inicialización (``CREATE EXTENSION vector``, los roles ``migrations``/``app``,
  el logging) **no corrían jamás**. Postgres arrancaba ``healthy`` y la avería se
  cobraba en la primera consulta que necesitaba la extensión o el rol;
* ``./vault/config.hcl`` se creaba como **directorio** donde el binario de Vault
  espera un fichero de configuración;
* y los dos contextos de build de los tinyproxy —servicios del NÚCLEO, ADR 0019 y
  ADR 0094— faltaban con ``pull_policy: build``, que aborta ``docker compose up``
  **al resolver el proyecto**: no se cae un servicio, no arranca nada.

Cómo se arregla, y por qué así
------------------------------
El contenido viaja **dentro del paquete Python del instalador**, no se copia de un
árbol ``docker/`` que en el destino puede no existir. Es la única forma que
funciona en los dos caminos que hoy tiene el producto:

* desde un clon (``scripts/install.sh`` → ``python -m installer_backend.cli``), y
* desde la imagen del instalador, cuyo Dockerfile copia **sólo** ``pyproject.toml``
  y ``src/`` (``apps/installer/backend/Dockerfile``). Un ``copytree`` de
  ``../../docker`` fallaría ahí, y fallaría tarde: en mitad de la instalación.

Es exactamente el «bloqueante 1» que mide el ADR 0161
(``docs/05-architecture-decisions/0161-distribucion-e-instalacion-de-la-plataforma.md``)
(«el instalador lee cosas del árbol que hoy no viajan en ninguna imagen»), y que
ese documento presupuesta como suelo **común a las cuatro opciones**: se paga con
clon y sin él, porque la geometría de rutas no depende del clon.

La copia y su guarda
--------------------
Estos ficheros son copia **byte a byte** de su original bajo ``docker/``, que sigue
siendo la fuente de verdad del stack de desarrollo. La duplicación es el precio de
que el artefacto sea autosuficiente, y **no se sostiene con una promesa**: cada
entrada del manifiesto declara su ``source`` y
``tests/unit/test_installer_ships_stack_assets.py`` pone en rojo cualquier
divergencia, además de exigir que ningún fichero nuevo de esos directorios se quede
fuera del manifiesto. Sin esa guarda la copia envejecería en silencio, que es una
avería peor que la que vino a arreglar: el ``egress-proxy`` que corre en producción
dejaría de ser el que CI construye y escanea con Trivy.

Nada aquí toca el host: :func:`read_text` lee del propio paquete, y el que escribe
en disco es el seam ``EnvFileWriter`` del ejecutor de instalación.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from importlib.resources import files

#: Modo POSIX de un fichero de configuración del stack. **World-readable a
#: propósito**: lo lee el proceso de DENTRO del contenedor, que no corre como el
#: usuario que instaló (postgres es uid 999, alertmanager y prometheus son
#: ``nobody``/65534, vault es ``vault``). Un 0o640 con dueño ``root`` deja al
#: contenedor sin poder leer su propia configuración. Ninguno de estos ficheros
#: lleva secretos —los secretos van a Vault o al ``.env`` 0600— y ninguno es
#: world-WRITABLE, que es la línea que sí importa aquí.
MODE_CONFIG = 0o644

#: Modo de un script de inicialización. El entrypoint de la imagen de Postgres
#: **ejecuta** un ``.sh`` ejecutable y **hace source** de uno que no lo es; se
#: elige ejecutar porque hacer source mete el ``set -euo pipefail`` de los scripts
#: en el shell del entrypoint, que sigue corriendo después. En ``docker/`` los dos
#: scripts tienen hoy modos distintos (uno 100755 y otro 100644) por descuido, no
#: por diseño: aquí se unifican.
MODE_SCRIPT = 0o755


@dataclass(frozen=True)
class StackAsset:
    """Un fichero que la instalación deja junto al compose.

    ``path`` es relativo al directorio de auxiliares (``{compose_dir}/stack/``);
    ``source`` es su original bajo ``docker/``, y lo usa la guarda de deriva para
    comparar sin que nadie tenga que mantener el emparejamiento a mano.
    """

    path: str
    mode: int
    source: str
    why: str

    @property
    def is_monitoring(self) -> bool:
        """Los auxiliares de la superposición de observabilidad."""

        return self.path.startswith("monitoring/")


def _postgres_init() -> tuple[StackAsset, ...]:
    why = (
        "initdb los ejecuta en orden alfabético en el PRIMER arranque: pgvector, "
        "los roles migrations/app y service_user, y el logging. Sin ellos la base "
        "nace sin extensión ni roles y el fallo aparece en la primera consulta."
    )
    return tuple(
        StackAsset(
            path=f"postgres/init/{name}",
            mode=MODE_SCRIPT if name.endswith(".sh") else MODE_CONFIG,
            source=f"docker/postgres/init/{name}",
            why=why,
        )
        for name in (
            "01-extensions.sql",
            "02-roles.sh",
            "03-logging.sql",
            "04-service-role.sql",
            "05-service-role-password.sh",
        )
    )


def _tinyproxy(name: str, why: str) -> tuple[StackAsset, ...]:
    return tuple(
        StackAsset(
            path=f"{name}/{filename}",
            mode=MODE_CONFIG,
            source=f"docker/{name}/{filename}",
            why=why,
        )
        for filename in ("Dockerfile", "filter.txt", "tinyproxy.conf")
    )


_EGRESS_WHY = (
    "Contexto de build del egress-proxy, servicio del NÚCLEO: la ÚNICA salida de "
    "los agent-runtimes hacia los proveedores LLM (ADR 0019). Con `pull_policy: "
    "build` y sin contexto, `docker compose up` aborta al resolver el proyecto."
)
_REGISTRY_WHY = (
    "Contexto de build del registry-proxy, servicio del NÚCLEO: la salida "
    "allowlisted de los runtime-templates hacia los registros de paquetes "
    "(ADR 0094). Mismo modo de fallo que el egress-proxy si falta."
)

#: Auxiliares que necesita CUALQUIER instalación.
CORE_ASSETS: tuple[StackAsset, ...] = (
    *_postgres_init(),
    StackAsset(
        path="vault/config.hcl",
        mode=MODE_CONFIG,
        source="docker/vault/config.hcl",
        why=(
            "Configuración de Vault. Es un bind de FICHERO: si no existe, Docker lo "
            "inventa como directorio y `vault server` no encuentra su config."
        ),
    ),
    *_tinyproxy("egress-proxy", _EGRESS_WHY),
    *_tinyproxy("registry-proxy", _REGISTRY_WHY),
    StackAsset(
        path="seccomp/agent-runtime.json",
        mode=MODE_CONFIG,
        source="docker/seccomp/agent-runtime.json",
        why=(
            "Perfil default-deny que el worker pinea sobre los runtimes NO "
            "confiables que lanza (Principio Rector 2). El worker lo lee del bind "
            "en /etc/agentic/seccomp; sin fichero, se lanzarían sin perfil."
        ),
    ),
    StackAsset(
        path="seccomp/default.json",
        mode=MODE_CONFIG,
        source="docker/seccomp/default.json",
        why="Perfil de endurecimiento extra, OPT-IN documentado (ADR 0040).",
    ),
)

#: Auxiliares que sólo se escriben con la superposición de observabilidad
#: (``monitoring=True``). Hoy el CLI nunca la activa, pero el generador ya la
#: emite y la avería sería idéntica el día que se active.
MONITORING_ASSETS: tuple[StackAsset, ...] = (
    StackAsset(
        path="monitoring/prometheus/prometheus.yml",
        mode=MODE_CONFIG,
        source="docker/monitoring/prometheus/prometheus.yml",
        why="Scrape jobs + `rule_files` + destino Alertmanager.",
    ),
    StackAsset(
        path="monitoring/prometheus/rules/app_alerts.yml",
        mode=MODE_CONFIG,
        source="docker/monitoring/prometheus/rules/app_alerts.yml",
        why="Reglas de alerta de aplicación (colas Celery, errores, presupuesto).",
    ),
    StackAsset(
        path="monitoring/prometheus/rules/host_alerts.yml",
        mode=MODE_CONFIG,
        source="docker/monitoring/prometheus/rules/host_alerts.yml",
        why="Reglas de host: disco, RAM, swap, OOM, último backup fallido.",
    ),
    StackAsset(
        path="monitoring/alertmanager/alertmanager.yml",
        mode=MODE_CONFIG,
        source="docker/monitoring/alertmanager/alertmanager.yml",
        why=(
            "Enrutado de alertas y receiver de respaldo. Su `api_url_file` apunta a "
            "/etc/alertmanager/secrets, el buzón que provisiona el árbol de datos."
        ),
    ),
    StackAsset(
        path="monitoring/grafana/provisioning/dashboards/dashboards.yml",
        mode=MODE_CONFIG,
        source="docker/monitoring/grafana/provisioning/dashboards/dashboards.yml",
        why="Provisioning: de dónde carga Grafana los dashboards del bind.",
    ),
    StackAsset(
        path="monitoring/grafana/provisioning/datasources/prometheus.yml",
        mode=MODE_CONFIG,
        source="docker/monitoring/grafana/provisioning/datasources/prometheus.yml",
        why="Datasource Prometheus preconfigurado.",
    ),
    StackAsset(
        path="monitoring/grafana/provisioning/datasources/loki.yml",
        mode=MODE_CONFIG,
        source="docker/monitoring/grafana/provisioning/datasources/loki.yml",
        why="Datasource Loki preconfigurado.",
    ),
    StackAsset(
        path="monitoring/grafana/dashboards/agentic-platform.json",
        mode=MODE_CONFIG,
        source="docker/monitoring/grafana/dashboards/agentic-platform.json",
        why="Dashboard de plataforma que el provisioning carga del bind.",
    ),
    StackAsset(
        path="monitoring/grafana/dashboards/host-overview.json",
        mode=MODE_CONFIG,
        source="docker/monitoring/grafana/dashboards/host-overview.json",
        why="Dashboard de host que el provisioning carga del bind.",
    ),
)

#: Todo el manifiesto, para las guardas.
ALL_ASSETS: tuple[StackAsset, ...] = (*CORE_ASSETS, *MONITORING_ASSETS)

#: Ficheros que viven en un directorio que SÍ viaja y que, a propósito, NO viajan.
#: Van enumerados y con su motivo porque la guarda de arriba exige lo contrario
#: —«si un directorio viaja, viaja entero»— y una excepción sin escribir sería
#: indistinguible de un olvido. La guarda comprueba además que cada entrada siga
#: existiendo: una exclusión huérfana afirma una excepción donde ya no la hay.
NOT_SHIPPED: tuple[tuple[str, str], ...] = (
    (
        "docker/vault/auto-unseal.sh",
        "Compañero de auto-init/auto-unseal del stack de desarrollo (Camino B). "
        "Deja las 5 claves de Shamir en un volumen local junto a Vault, y su "
        "propia cabecera lo marca como «NOT the production posture». El ADR 0145 "
        "decidió desellado MANUAL en producción: llevarlo en el instalador "
        "convertiría ese trade-off de desarrollo en la postura por defecto de "
        "todas las instalaciones.",
    ),
)


def assets_for(*, monitoring: bool) -> tuple[StackAsset, ...]:
    """Los auxiliares que escribe una instalación con/sin observabilidad.

    Espeja la misma condición con la que el generador decide emitir los servicios
    de monitorización: escribir la configuración de Grafana en una instalación sin
    Grafana sería ruido, y no escribirla en una CON Grafana es la avería.
    """

    return ALL_ASSETS if monitoring else CORE_ASSETS


def read_text(asset: StackAsset) -> str:
    """Contenido del fichero empaquetado, como texto.

    Lee del propio paquete (``importlib.resources``), nunca del árbol del repo: en
    la imagen del instalador el repo no está. Los finales de línea se normalizan a
    ``\\n`` en la lectura, así que un checkout CRLF en Windows no acaba escribiendo
    un ``.sh`` con retornos de carro en el host Linux de destino.
    """

    resource = files(__name__)
    for part in asset.path.split("/"):
        resource = resource.joinpath(part)
    return resource.read_text(encoding="utf-8").replace("\r\n", "\n")


def iter_source_dirs() -> Iterator[str]:
    """Directorios de ``docker/`` de los que sale el manifiesto, sin repetir.

    Los recorre la guarda de deriva para exigir lo contrario de la copia: que
    ningún fichero NUEVO de esos directorios se quede sin viajar.
    """

    seen: set[str] = set()
    for asset in ALL_ASSETS:
        directory = asset.source.rsplit("/", 1)[0]
        if directory not in seen:
            seen.add(directory)
            yield directory
