"""Lo que el instalador lleva dentro, ¿sigue siendo lo que dice llevar?

`installer_backend.stack_assets` resuelve una avería concreta —el compose
generado montaba seis familias de rutas que la instalación no escribía— haciendo
que esos ficheros **viajen dentro del paquete Python**, porque en el destino no
hay ningún ``docker/`` del que copiarlos (el Dockerfile del instalador copia sólo
``pyproject.toml`` y ``src/``).

El precio de esa decisión es una copia, y una copia sin guarda envejece. Este
fichero es la guarda, y mira las cuatro caras del mismo trato:

1. **Que la copia siga siendo la misma** que su original bajo ``docker/``. No es
   pulcritud: el ``egress-proxy`` que corre en una instalación se construye desde
   el contexto que escribe el instalador, mientras que el que CI construye y pasa
   por Trivy sale de ``docker/egress-proxy/``
   (``tests/unit/test_infra_images_are_scanned.py``). Si las dos versiones se
   separan, la imagen escaneada deja de ser la imagen que corre — y el
   ``egress-proxy`` es la ÚNICA salida a internet del contenedor donde corre
   código no confiable (ADR 0019, Principio Rector 2). La misma herencia vale
   para el pin por digest del ``FROM``: la guarda que lo exige
   (``test_supply_chain_config``) sólo mira Dockerfiles bajo ``docker/``, así que
   la copia sólo está pineada mientras sea idéntica.

2. **Que no se quede nada fuera.** Lo caro no es que un fichero copiado se
   desactualice —eso lo ve el diff— sino que alguien añada
   ``06-lo-que-sea.sql`` a ``docker/postgres/init/`` y la instalación no lo
   ejecute nunca.

3. **Que no sobre nada dentro.** La otra dirección de lo mismo: un fichero en el
   paquete que ya no tiene original bajo ``docker/`` viaja en la imagen
   publicada, no lo escribe nadie —el ejecutor escribe POR manifiesto— y engaña
   al siguiente que abra el paquete.

4. **Que el empaquetado siga metiéndolos en el wheel**, que es de lo que depende
   todo lo anterior y no está escrito en ningún ``.py``.

Sobre el alcance de (2): el conjunto de la derecha se deriva del **árbol**,
recorrido recursivamente, dentro de un conjunto de **raíces declarado**
(``_SHIPPED_ROOTS``). La distinción importa y la versión fuerte —«se deriva del
árbol», a secas— es falsa: hasta el 2026-08-27 los directorios a vigilar salían
del propio manifiesto (``iter_source_dirs()``), de modo que la guarda sólo miraba
donde ya había algo declarado. El hueco no era teórico: ``docker/monitoring/loki``
y ``docker/monitoring/promtail`` llevaban tiempo dentro de él, con sus dos
ficheros de configuración sin viajar y la suite en verde.

Lo que las raíces NO cubren —un ``docker/algo-nuevo/`` de primer nivel que el
compose generado monte— lo caza la otra guarda, que es la que de verdad protege
contra el defecto original: ``tests/unit/test_generated_compose_is_installable.py``
deriva las dos listas del código (las rutas ``./…`` del compose generado contra
lo que produce el paso real ``GENERATE_CONFIG``). Ésta es complementaria, no
sustituta.

Sobre las exclusiones, que son de dos clases y viven en dos sitios a propósito:

* ``stack_assets.NOT_SHIPPED`` — el **paquete** declara, fichero a fichero, qué
  vive junto a lo que envía y no envía. Viaja con el manifiesto porque es parte
  de su contrato.
* ``_Root.out_of_scope`` — la **guarda** declara qué subárboles de una raíz
  vigilada no son asunto de la instalación. Es una propiedad del alcance de esta
  comprobación, no del manifiesto: son directorios enteros que el manifiesto no
  menciona ni tiene por qué mencionar.

Las dos exigen un motivo escrito de 80+ caracteres y que lo apuntado siga
existiendo, porque una exclusión huérfana afirma una excepción donde ya no la
hay. Y de la lista de ficheros que existen POR git (``.gitkeep`` /
``.gitignore``) salen enumerados, no por patrón amplio.
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, Protocol

import pytest
from installer_backend import stack_assets
from installer_backend.stack_assets import MODE_CONFIG, MODE_SCRIPT, StackAsset

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INSTALLER_PYPROJECT = _REPO_ROOT / "apps" / "installer" / "backend" / "pyproject.toml"

#: Ficheros que existen para git y no para el stack: un directorio vacío no se
#: puede versionar, y el buzón de credenciales de Alertmanager se versiona con un
#: `.gitignore` que lo ignora todo menos a sí mismo. Ninguno debe viajar.
_GIT_ONLY = frozenset({".gitkeep", ".gitignore"})

#: Longitud mínima de un motivo de exclusión. La misma vara que usa el
#: `gate_override` del roadmap (CLAUDE.md): un motivo que no se puede auditar
#: dentro de seis meses no es un motivo.
_MIN_MOTIVO = 80


@dataclass(frozen=True)
class _Root:
    """Un árbol de ``docker/`` del que la instalación toma auxiliares.

    ``out_of_scope`` enumera subárboles que caen dentro de la raíz y que la
    instalación no consume, cada uno con su motivo. Existe para que añadir un
    directorio hermano bajo una raíz vigilada obligue a una decisión EXPLÍCITA
    —o entra en el manifiesto, o entra aquí— en vez de pasar en verde.
    """

    path: str
    why: str
    out_of_scope: tuple[tuple[str, str], ...] = ()


_SHIPPED_ROOTS: tuple[_Root, ...] = (
    _Root(
        path="docker/postgres",
        why=(
            "`initdb` ejecuta en orden alfabético todo lo que encuentre en `init/`, y "
            "sólo en el PRIMER arranque del cluster: pgvector, los roles y el logging. "
            "Lo que no viaje aquí no se ejecuta nunca, y el fallo aparece lejos."
        ),
        out_of_scope=(
            (
                "docker/postgres/upgrade",
                "Scripts que aplican a una base de datos QUE YA EXISTE lo que `init/` "
                "sólo haría en un cluster nuevo; los lanza un operador a mano siguiendo "
                "`docker/postgres/upgrade/README.md`, no un entrypoint. Una instalación "
                "recién hecha nace con el esquema al día y no tiene nada que actualizar, "
                "así que llevarlos sería enviar un remedio sin su enfermedad.",
            ),
        ),
    ),
    _Root(
        path="docker/vault",
        why=(
            "`config.hcl` es un bind de FICHERO: si no existe, Docker lo inventa como "
            "directorio y `vault server` arranca sin encontrar su configuración. La "
            "avería no es un error limpio, es un servicio que no levanta."
        ),
    ),
    _Root(
        path="docker/egress-proxy",
        why=(
            "Contexto de build de un servicio del NÚCLEO con `pull_policy: build`: sin "
            "él `docker compose up` aborta AL RESOLVER el proyecto, antes de arrancar "
            "nada. Es además la única salida a internet de los runtimes (ADR 0019)."
        ),
    ),
    _Root(
        path="docker/registry-proxy",
        why=(
            "Contexto de build del otro servicio del NÚCLEO con `pull_policy: build` "
            "(ADR 0094): la salida allowlisted de los runtime-templates hacia los "
            "registros de paquetes. Mismo modo de fallo que el egress-proxy si falta."
        ),
    ),
    _Root(
        path="docker/seccomp",
        why=(
            "Perfiles que el worker pinea sobre los runtimes NO confiables que lanza "
            "(Principio Rector 2). Los lee del bind en /etc/agentic/seccomp; sin "
            "fichero, los contenedores de código ajeno se lanzarían sin perfil."
        ),
    ),
    _Root(
        path="docker/monitoring",
        why=(
            "Configuración de la superposición de observabilidad: scrape jobs, reglas "
            "de alerta, enrutado de Alertmanager y el provisioning de Grafana. Se "
            "escribe sólo con `monitoring=True`, pero la avería sería idéntica."
        ),
        out_of_scope=(
            (
                "docker/monitoring/loki",
                "El compose que genera el instalador NO emite un servicio `loki` "
                "(`compose_generator.py`, `selected_services`): sólo lo levanta el "
                "overlay canónico del repo, `docker/docker-compose.monitoring.yml`. "
                "Enviar su configuración sería escribir en el host del operador un "
                "fichero que ningún contenedor de su stack va a leer. El día que el "
                "generador emita el servicio, esta entrada tiene que desaparecer y el "
                "fichero entrar en el manifiesto — es justo la decisión que esta lista "
                "obliga a tomar en vez de dejar pasar en silencio.",
            ),
            (
                "docker/monitoring/promtail",
                "Mismo caso que `loki` y por la misma razón: el generador no emite un "
                "servicio `promtail` (grep sobre `compose_generator.py` = 0), así que su "
                "configuración no tendría lector en una instalación. Promtail sólo vive "
                "en el overlay de desarrollo, alimentando al Loki que tampoco viaja. Los "
                "dos entran o no entran juntos: un promtail sin Loki al que escribir no "
                "arranca, y un Loki sin promtail no tiene nada que enseñar.",
            ),
            (
                "docker/monitoring/alertmanager/secrets",
                "Buzón de credenciales, no configuración: el operador deja aquí el "
                "webhook de Slack del receiver de respaldo, y su propio `.gitignore` lo "
                "ignora TODO menos a sí mismo. La instalación no copia contenido, crea "
                "el directorio vacío con modo 0755 (`config_generators.py`, `DataDir` de "
                "`monitoring/alertmanager/secrets`) para que el bind no lo invente Docker "
                "como root y Alertmanager, que corre como nobody, pueda atravesarlo.",
            ),
        ),
    ),
)


def _source_path(asset: StackAsset) -> Path:
    return _REPO_ROOT / asset.source


# ---------------------------------------------------------------------------
# 1. La copia no ha derivado.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("asset", stack_assets.ALL_ASSETS, ids=lambda a: a.path)
def test_every_shipped_asset_is_identical_to_its_source_under_docker(asset: StackAsset) -> None:
    """El fichero empaquetado y el de ``docker/`` son el mismo texto.

    Se comparan como TEXTO (finales de línea normalizados) porque es como los lee
    y los escribe el instalador: un checkout CRLF en Windows no debe delatar una
    deriva que no existe, ni taparla.
    """

    source = _source_path(asset)
    assert source.is_file(), (
        f"{asset.source} ya no existe. Si el fichero se movió o se retiró, la "
        f"copia empaquetada `{asset.path}` se quedó sin fuente de verdad y hay "
        "que actualizar el manifiesto de stack_assets, no borrar esta guarda."
    )
    esperado = source.read_text(encoding="utf-8").replace("\r\n", "\n")
    obtenido = stack_assets.read_text(asset)
    assert obtenido == esperado, (
        f"`{asset.path}` ha derivado de `{asset.source}`. La instalación escribiría "
        "una versión distinta de la que corre en el stack de desarrollo y —para los "
        "dos tinyproxy— distinta de la que CI construye y escanea. Sincronízalos "
        "con `python scripts/dev/sync_installer_stack_assets.py`."
    )


def test_the_drift_guard_is_not_vacuous() -> None:
    """Un manifiesto vacío haría pasar en vacío todo lo de arriba."""

    assert len(stack_assets.ALL_ASSETS) >= 20, (
        f"el manifiesto declara {len(stack_assets.ALL_ASSETS)} auxiliares; eran 23 "
        "cuando se escribió esta guarda (5 de postgres/init, la config de Vault, "
        "los 2 contextos de tinyproxy, 2 perfiles seccomp y 9 de monitorización)."
    )


# ---------------------------------------------------------------------------
# 2. No se ha quedado nada fuera: del árbol al manifiesto.
# ---------------------------------------------------------------------------
def _sources_under(root_dir: Path, root_name: str) -> list[str]:
    """Todo fichero bajo ``root_dir``, recursivo, en la forma ``docker/…``.

    Recursivo a propósito. La versión anterior usaba ``iterdir()``, que no baja
    de nivel: un fichero en un subdirectorio de un directorio que sí viaja pasaba
    en verde. Hoy no rompe nada —el entrypoint de Postgres no baja de nivel y el
    provisioning de Grafana declara ``foldersFromFilesStructure: false``—, pero
    es gratis cerrarlo y caro descubrirlo el día que deje de ser cierto.
    """

    return sorted(
        f"{root_name}/{path.relative_to(root_dir).as_posix()}"
        for path in root_dir.rglob("*")
        if path.is_file() and path.name not in _GIT_ONLY
    )


def _is_under(source: str, subtree: str) -> bool:
    """¿``source`` cuelga de ``subtree``? Comparación por segmento, no por prefijo.

    Por segmento para que ``docker/monitoring/loki-viejo/x.yml`` NO se dé por
    excluido con una entrada para ``docker/monitoring/loki``: un ``startswith``
    a secas convertiría cada exclusión en un comodín silencioso.
    """

    return source == subtree or source.startswith(f"{subtree}/")


def _orphan_sources(
    present: Iterable[str],
    *,
    declared: frozenset[str],
    not_shipped: frozenset[str],
    out_of_scope: Iterable[str],
) -> list[str]:
    """Lo que está en el árbol y no está ni declarado ni excluido."""

    subtrees = tuple(out_of_scope)
    return sorted(
        source
        for source in present
        if source not in declared
        and source not in not_shipped
        and not any(_is_under(source, subtree) for subtree in subtrees)
    )


_DECLARED_SOURCES = frozenset(asset.source for asset in stack_assets.ALL_ASSETS)
_NOT_SHIPPED_SOURCES = frozenset(source for source, _motivo in stack_assets.NOT_SHIPPED)


@pytest.mark.parametrize("root", _SHIPPED_ROOTS, ids=lambda r: r.path)
def test_no_file_of_a_shipped_root_is_left_behind(root: _Root) -> None:
    """Si un árbol viaja, viaja ENTERO — o la excepción está escrita.

    El fallo que persigue no lo ve nadie al leer un diff: alguien añade un
    ``06-*.sql`` a ``docker/postgres/init/`` —que en el stack de desarrollo
    funciona el mismo día— y la instalación no lo ejecuta jamás, porque su
    manifiesto sigue enumerando cinco.
    """

    root_dir = _REPO_ROOT / root.path
    assert root_dir.is_dir(), (
        f"`{root.path}` está declarada como raíz de auxiliares y ya no es un "
        "directorio del repo. Si el árbol se movió, mueve la raíz con él; si "
        "desapareció, retírala en vez de dejarla vigilando el vacío."
    )

    presentes = _sources_under(root_dir, root.path)
    assert presentes, (
        f"`{root.path}` no tiene ni un fichero: la guarda estaría pasando en vacío "
        "sobre una raíz que se vació sin que nadie retirara su declaración."
    )

    huerfanos = _orphan_sources(
        presentes,
        declared=_DECLARED_SOURCES,
        not_shipped=_NOT_SHIPPED_SOURCES,
        out_of_scope=(subtree for subtree, _motivo in root.out_of_scope),
    )
    assert not huerfanos, (
        f"{len(huerfanos)} fichero(s) bajo `{root.path}` que el stack de desarrollo "
        f"usa y la instalación NO lleva: {', '.join(huerfanos)}. Decide: o entran en "
        "el manifiesto de `installer_backend.stack_assets` (y comprueba si el "
        "generador tiene que montarlos), o entran en `NOT_SHIPPED` / en el "
        "`out_of_scope` de su raíz con un motivo escrito."
    )


def test_the_declared_roots_still_cover_the_whole_manifest() -> None:
    """Las raíces no pueden quedarse atrás del manifiesto que dicen vigilar.

    Si alguien añade al manifiesto un auxiliar de un árbol nuevo —pongamos
    ``docker/caddy/Caddyfile``— sin declarar su raíz, la guarda de huérfanos
    seguiría en verde para ese árbol: exactamente el agujero que esta lista vino
    a cerrar, sólo que reintroducido desde el otro lado.
    """

    sin_raiz = sorted(
        directory
        for directory in stack_assets.iter_source_dirs()
        if not any(_is_under(directory, root.path) for root in _SHIPPED_ROOTS)
    )
    assert not sin_raiz, (
        f"el manifiesto declara auxiliares en {sin_raiz}, que no cuelgan de ninguna "
        "raíz de `_SHIPPED_ROOTS`. Añade la raíz —con su motivo— para que un fichero "
        "nuevo de ese árbol tampoco pueda quedarse fuera en silencio."
    )


def test_every_package_declared_exclusion_falls_under_a_watched_root() -> None:
    """Una entrada de ``NOT_SHIPPED`` fuera de toda raíz no excluye nada.

    ``NOT_SHIPPED`` sólo tiene sentido como excepción a la regla «si un árbol
    viaja, viaja entero». Si lo que enumera no cae bajo ninguna raíz vigilada, la
    regla nunca lo habría reclamado: la entrada no protege, decora.
    """

    fuera = sorted(
        source
        for source in _NOT_SHIPPED_SOURCES
        if not any(_is_under(source, root.path) for root in _SHIPPED_ROOTS)
    )
    assert not fuera, (
        f"`NOT_SHIPPED` excluye {fuera}, que no cuelga de ninguna raíz vigilada. "
        "Ninguna guarda lo habría pedido, así que la exclusión afirma una excepción "
        "que no existe: retírala, o declara la raíz que la hace necesaria."
    )


@pytest.mark.parametrize(
    ("source", "motivo"),
    stack_assets.NOT_SHIPPED,
    # ids explícitos: el motivo son 300 caracteres de prosa y, metido en el id,
    # deja un nombre de test ilegible en el que no se distingue QUÉ falló.
    ids=[source for source, _motivo in stack_assets.NOT_SHIPPED],
)
def test_a_declared_exclusion_still_points_at_something_real(source: str, motivo: str) -> None:
    """Una exclusión huérfana afirma una excepción donde ya no la hay.

    Si el fichero excluido desaparece o se mueve, la entrada deja de proteger
    nada y pasa a estorbar: el siguiente que lea la lista creerá que hay un
    motivo vivo para no llevarlo.
    """

    assert (_REPO_ROOT / source).is_file(), (
        f"`{source}` está declarado como «no viaja» y ya no existe. Retira la "
        "entrada de `NOT_SHIPPED` en vez de dejarla apuntando al vacío."
    )
    assert len(motivo) >= _MIN_MOTIVO, (
        f"la exclusión de `{source}` se justifica con {len(motivo)} caracteres. "
        "Un motivo que no se puede auditar dentro de seis meses no es un motivo."
    )


_OUT_OF_SCOPE = [
    (subtree, motivo) for root in _SHIPPED_ROOTS for subtree, motivo in root.out_of_scope
]


@pytest.mark.parametrize(
    ("subtree", "motivo"), _OUT_OF_SCOPE, ids=[subtree for subtree, _motivo in _OUT_OF_SCOPE]
)
def test_an_out_of_scope_subtree_still_points_at_something_real(subtree: str, motivo: str) -> None:
    """Lo mismo, para los subárboles que la guarda declara fuera de alcance.

    Con un añadido que la versión por fichero no necesita: el subárbol tiene que
    seguir colgando de una raíz. Uno que no cuelga de ninguna no excluye nada — y
    leerlo hace creer que un árbol está considerado cuando no lo mira nadie.
    """

    assert (_REPO_ROOT / subtree).exists(), (
        f"`{subtree}` está declarado fuera de alcance y ya no existe. Retira la "
        "entrada de `out_of_scope` en vez de dejarla apuntando al vacío."
    )
    assert any(_is_under(subtree, root.path) for root in _SHIPPED_ROOTS), (
        f"`{subtree}` se excluye pero no cuelga de ninguna raíz vigilada: nadie lo "
        "habría reclamado, así que la exclusión no exime de nada."
    )
    assert len(motivo) >= _MIN_MOTIVO, (
        f"la exclusión de `{subtree}` se justifica con {len(motivo)} caracteres. "
        "Un motivo que no se puede auditar dentro de seis meses no es un motivo."
    )


def test_the_orphan_walk_sees_nested_files_and_sibling_directories(tmp_path: Path) -> None:
    """La guarda de huérfanos, probada sobre un árbol de mentira. Meta-guarda.

    Afirmar que «esto lo cazaría» sin ejecutarlo es exactamente el modo de fallo
    que ya se pagó: la versión anterior de la guarda no cazaba ni el subdirectorio
    ni el directorio hermano, y nadie lo supo hasta que se probó. Esta prueba
    construye los tres casos —el fichero suelto, el anidado y el del hermano— y
    exige que salgan los tres, además de que las dos clases de exclusión y los
    ficheros que existen sólo por git no salgan ninguno.
    """

    root_name = "docker/fingido"
    root_dir = tmp_path / "docker" / "fingido"
    (root_dir / "init").mkdir(parents=True)
    (root_dir / "init" / "01-declarado.sql").write_text("SELECT 1;\n", encoding="utf-8")
    (root_dir / "init" / "06-nuevo.sql").write_text("SELECT 2;\n", encoding="utf-8")
    (root_dir / "init" / "extra").mkdir()
    (root_dir / "init" / "extra" / "07-anidado.sql").write_text("SELECT 3;\n", encoding="utf-8")
    (root_dir / "hermano").mkdir()
    (root_dir / "hermano" / "config.yml").write_text("a: 1\n", encoding="utf-8")
    (root_dir / "excluido").mkdir()
    (root_dir / "excluido" / "secreto").write_text("no viaja\n", encoding="utf-8")
    (root_dir / "excluido" / ".gitignore").write_text("*\n", encoding="utf-8")
    (root_dir / "vacio").mkdir()
    (root_dir / "vacio" / ".gitkeep").write_text("", encoding="utf-8")
    (root_dir / "declarado-no-viaja.sh").write_text("#!/bin/sh\n", encoding="utf-8")

    presentes = _sources_under(root_dir, root_name)
    assert presentes == [
        f"{root_name}/declarado-no-viaja.sh",
        f"{root_name}/excluido/secreto",
        f"{root_name}/hermano/config.yml",
        f"{root_name}/init/01-declarado.sql",
        f"{root_name}/init/06-nuevo.sql",
        f"{root_name}/init/extra/07-anidado.sql",
    ], "el recorrido del árbol no ve lo que debe ver (o ve ficheros que son sólo de git)"

    huerfanos = _orphan_sources(
        presentes,
        declared=frozenset({f"{root_name}/init/01-declarado.sql"}),
        not_shipped=frozenset({f"{root_name}/declarado-no-viaja.sh"}),
        out_of_scope=(f"{root_name}/excluido",),
    )
    assert huerfanos == [
        f"{root_name}/hermano/config.yml",
        f"{root_name}/init/06-nuevo.sql",
        f"{root_name}/init/extra/07-anidado.sql",
    ], (
        "la guarda de huérfanos no caza los tres casos que motivan su existencia: el "
        "fichero añadido a un directorio que ya viaja, el anidado un nivel más abajo y "
        "el del directorio hermano que nadie había declarado."
    )


def test_an_exclusion_is_not_a_silent_wildcard() -> None:
    """Excluir ``…/loki`` no puede excluir de propina ``…/loki-viejo``.

    Con comparación por prefijo de cadena en vez de por segmento, cada entrada de
    exclusión se convertiría en un comodín: el directorio que alguien cree
    mañana con un nombre que empiece igual entraría gratis en la excepción.
    """

    assert _is_under("docker/monitoring/loki/loki-config.yml", "docker/monitoring/loki")
    assert _is_under("docker/monitoring/loki", "docker/monitoring/loki")
    assert not _is_under("docker/monitoring/loki-viejo/config.yml", "docker/monitoring/loki")


# ---------------------------------------------------------------------------
# 3. No sobra nada dentro: del paquete al manifiesto.
# ---------------------------------------------------------------------------
class _Traversable(Protocol):
    """Lo mínimo de ``importlib.resources`` que necesita el recorrido.

    Declarado a mano —y no como ``importlib.abc.Traversable``— para que la
    meta-guarda pueda pasarle un ``pathlib.Path`` de ``tmp_path`` y recorrer un
    árbol de mentira con el MISMO código que recorre el paquete de verdad.
    """

    name: str

    def is_dir(self) -> bool: ...

    def iterdir(self) -> Iterable[Any]: ...


def _package_files(node: _Traversable, prefix: str = "") -> Iterator[str]:
    """Ficheros del paquete de auxiliares, en la forma de ``StackAsset.path``.

    Salta ``__pycache__`` (lo fabrica el intérprete, no viaja en el wheel) y los
    ``.py``, que son el código del manifiesto y no auxiliares del stack.
    """

    for child in sorted(node.iterdir(), key=lambda item: item.name):
        if child.is_dir():
            if child.name == "__pycache__":
                continue
            yield from _package_files(child, f"{prefix}{child.name}/")
        elif not child.name.endswith(".py"):
            yield f"{prefix}{child.name}"


def test_nothing_travels_in_the_package_that_the_manifest_does_not_declare() -> None:
    """El polizón: un fichero en el paquete sin entrada en el manifiesto.

    Se cuela solo, sin que nadie lo escriba: alguien retira un auxiliar de
    ``docker/`` y su entrada del manifiesto y olvida borrar la copia. El daño no
    es una ejecución inesperada —el ejecutor escribe POR manifiesto
    (``real_step_executor``, iterando ``assets_for()``), así que el polizón nunca
    llega al disco del destino— sino engaño: viaja en el wheel y en la imagen
    publicada, y el siguiente que abra el paquete creerá que forma parte de lo
    que la instalación deja en ``{data_root}/stack/``.

    Es la mitad que faltaba del par «no se queda nada fuera / no sobra nada
    dentro», y en un paquete que además se publica como imagen conviene que lo
    que viaja sea exactamente lo que se declara.
    """

    declarados = {asset.path for asset in stack_assets.ALL_ASSETS}
    dentro = set(_package_files(files(stack_assets.__name__)))

    polizones = sorted(dentro - declarados)
    assert not polizones, (
        f"{len(polizones)} fichero(s) viajan en `installer_backend.stack_assets` sin "
        f"estar en el manifiesto: {', '.join(polizones)}. Nadie los escribe en el "
        "destino. Bórralos, o decláralos con su `source` bajo `docker/` para que la "
        "guarda de deriva los vigile."
    )

    sin_copia = sorted(declarados - dentro)
    assert not sin_copia, (
        f"{len(sin_copia)} auxiliar(es) declarados en el manifiesto sin fichero en el "
        f"paquete: {', '.join(sin_copia)}. La instalación fallaría al escribirlos. "
        "Copia el original con `python scripts/dev/sync_installer_stack_assets.py`."
    )


def test_the_package_walk_finds_stowaways_at_any_depth(tmp_path: Path) -> None:
    """La guarda del polizón, probada sobre un paquete de mentira. Meta-guarda.

    Se probó que el agujero era real antes de taparlo: con un
    ``stack_assets/postgres/init/99-rogue.sql`` metido en una copia del paquete y
    apuntada por ``PYTHONPATH``, la suite anterior pasaba entera (63 passed). Esto
    fija que el recorrido lo ve, y que no confunde con un polizón ni el código del
    manifiesto ni el ``__pycache__`` del intérprete de quien corre los tests.
    """

    (tmp_path / "postgres" / "init").mkdir(parents=True)
    (tmp_path / "postgres" / "init" / "01-extensions.sql").write_text("x\n", encoding="utf-8")
    (tmp_path / "postgres" / "init" / "99-rogue.sql").write_text("x\n", encoding="utf-8")
    (tmp_path / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "__init__.cpython-312.pyc").write_bytes(b"\x00")

    assert sorted(_package_files(tmp_path)) == [
        "postgres/init/01-extensions.sql",
        "postgres/init/99-rogue.sql",
    ]


# ---------------------------------------------------------------------------
# 4. Los modos son los que el contenedor necesita.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("asset", stack_assets.ALL_ASSETS, ids=lambda a: a.path)
def test_shipped_assets_are_readable_by_the_container_and_never_world_writable(
    asset: StackAsset,
) -> None:
    """Configuración 0644, scripts 0755, y nada con el bit de escritura ajeno.

    Los lee un proceso de DENTRO del contenedor que no corre como el usuario que
    instaló (postgres es uid 999, prometheus y alertmanager son 65534, vault es
    ``vault``), así que un 0640 de dueño ``root`` —el modo que este mismo
    instalador usa para el compose y el ``config/global.yaml``, que sólo lee
    Docker desde el host— dejaría al servicio sin poder leer su configuración.
    Ninguno de estos ficheros lleva secretos: los secretos van a Vault o al
    ``.env`` (0600).
    """

    esperado = MODE_SCRIPT if asset.path.endswith(".sh") else MODE_CONFIG
    assert asset.mode == esperado, f"{asset.path} declara {asset.mode:#o}, no {esperado:#o}"
    assert not asset.mode & 0o022, f"{asset.path} sería escribible por otros: {asset.mode:#o}"


# ---------------------------------------------------------------------------
# 5. Lo que viaja es el contenido REAL, no un hueco con el nombre correcto.
# ---------------------------------------------------------------------------
def test_the_postgres_init_scripts_still_create_the_extension_and_the_roles() -> None:
    """La razón por la que estos cinco ficheros importan, afirmada sobre su texto.

    Un auxiliar vacío pasaría todas las guardas de rutas —el fichero existe, el
    bind resuelve— y la base seguiría naciendo sin ``pgvector`` y sin los roles.
    El daño no se ve al instalar: se ve en la primera consulta que necesita la
    extensión, lejos de la causa.
    """

    texto = "\n".join(
        stack_assets.read_text(asset)
        for asset in stack_assets.CORE_ASSETS
        if asset.path.startswith("postgres/init/")
    )
    for esperado in (
        "CREATE EXTENSION IF NOT EXISTS vector",
        "CREATE EXTENSION IF NOT EXISTS pgcrypto",
        "migrations_user",
        "app_user",
        "service_user",
    ):
        assert esperado in texto, (
            f"los scripts de inicialización empaquetados no contienen `{esperado}`. "
            "La base nacería sin eso y el `docker compose up` no diría nada."
        )


# ---------------------------------------------------------------------------
# 6. El empaquetado: que los auxiliares lleguen al wheel, que es de lo que
#    depende todo lo anterior y no está escrito en ningún `.py`.
# ---------------------------------------------------------------------------
#: Claves de hatchling que cambian QUÉ ficheros entran en el artefacto o CÓMO se
#: mapean. Ninguna es ilegítima por sí misma; lo que no es legítimo es añadir una
#: sin volver a mirar dentro del wheel, porque el estropicio que producen no
#: rompe ningún test: el paquete sigue importando y falla en el host del
#: operador, a mitad de instalación, escribiendo un contexto de build vacío.
_HATCH_FILE_SELECTION_KEYS = frozenset(
    {
        "exclude",
        "only-include",
        "artifacts",
        "sources",
        "force-include",
        "ignore-vcs",
        "skip-excluded-dirs",
    }
)


def _hatch_build_tables(pyproject_text: str) -> Iterator[tuple[str, dict[str, Any]]]:
    """Cada tabla bajo ``[tool.hatch.build…]``, con su nombre completo."""

    data = tomllib.loads(pyproject_text)
    root = data.get("tool", {}).get("hatch", {}).get("build")
    if not isinstance(root, dict):
        return

    pendientes = [("tool.hatch.build", root)]
    while pendientes:
        nombre, tabla = pendientes.pop()
        yield nombre, tabla
        for clave, valor in tabla.items():
            if isinstance(valor, dict):
                pendientes.append((f"{nombre}.{clave}", valor))


def _file_selection_declarations(pyproject_text: str) -> list[str]:
    """Las claves de selección de ficheros declaradas, en notación con puntos."""

    return sorted(
        f"{nombre}.{clave}"
        for nombre, tabla in _hatch_build_tables(pyproject_text)
        for clave in tabla
        if clave in _HATCH_FILE_SELECTION_KEYS
    )


def test_the_wheel_target_still_packages_everything_under_the_package() -> None:
    """Que los auxiliares VIAJEN depende de cómo se empaqueta, no del manifiesto.

    Todo esto se sostiene sobre una propiedad que no está en ningún ``.py``:
    hatchling mete en el wheel **todos** los ficheros del directorio del paquete,
    no sólo los ``.py``.

    Comprobado empíricamente el 2026-08-27, construyendo el wheel desde una copia
    del contexto REAL del Dockerfile (``pyproject.toml`` + ``src/``, sin ``.git``,
    que es exactamente lo que ve ``docker build``)::

        python -m build --wheel <copia-del-contexto>
        # installer_backend-1.0.0-py3-none-any.whl → 24 entradas bajo
        # installer_backend/stack_assets/ (los 23 auxiliares + __init__.py)

    Y comprobado también el estropicio, que es lo que esta guarda vigila: con
    ``exclude=["**/stack_assets/postgres/**"]`` añadido al mismo target, el wheel
    sale con 19 entradas —sin los cinco scripts de ``postgres/init``—, el paquete
    IMPORTA igual y ningún test se entera. La instalación resultante montaría
    ``./stack/postgres/init`` como directorio vacío dentro del PGDATA: la avería
    original del ADR 0161, de vuelta, y muda.

    Reconstruir el wheel en cada corrida de la suite sería caro (necesita red para
    el entorno aislado de build); lo que se vigila aquí es el cambio que produciría
    ese estropicio. Se afirma sobre el TOML **parseado** y no sobre subcadenas
    porque la versión por subcadenas era esquivable con un espacio: ``exclude =``
    la disparaba y ``exclude=`` pasaba en verde, igual que un ``exclude`` puesto
    en la tabla global ``[tool.hatch.build]`` en vez de en la del target.
    """

    pyproject = _INSTALLER_PYPROJECT.read_text(encoding="utf-8")
    tablas = dict(_hatch_build_tables(pyproject))

    wheel = tablas.get("tool.hatch.build.targets.wheel")
    assert wheel is not None, (
        "el `pyproject.toml` del instalador ya no declara "
        "`[tool.hatch.build.targets.wheel]`. Sin ese target hatchling adivina el "
        "layout, y `src/` no es el que adivina."
    )
    assert wheel.get("packages") == ["src/installer_backend"], (
        f"el target de wheel del instalador empaqueta {wheel.get('packages')!r}, no "
        "`['src/installer_backend']`. Comprueba que `installer_backend/stack_assets/**` "
        "sigue dentro del wheel (`python -m build --wheel apps/installer/backend`) "
        "antes de tocar esta guarda."
    )

    seleccion = _file_selection_declarations(pyproject)
    assert not seleccion, (
        f"el empaquetado del instalador declara {seleccion}. Esas claves deciden qué "
        "ficheros entran en el wheel y cómo se mapean, y pueden dejar fuera los "
        "auxiliares de `stack_assets/`, que NO son `.py`, sin romper ni un test. Si el "
        "cambio es legítimo: construye el wheel, comprueba que las 23 entradas de "
        "`installer_backend/stack_assets/` siguen dentro, y actualiza esta guarda con "
        "lo que hayas medido."
    )


def test_the_packaging_guard_is_not_dodged_by_whitespace_or_by_a_global_table() -> None:
    """La guarda de empaquetado, probada contra los dos sabotajes que la esquivaban.

    Meta-guarda con historia: la versión anterior buscaba las subcadenas
    ``only-include``, ``exclude `` (con espacio) y ``artifacts`` en el texto del
    fichero. Un ``exclude=[…]`` sin espacio —TOML perfectamente válido— pasaba en
    verde, y un ``exclude`` en la tabla global ``[tool.hatch.build]`` también.
    Medido: los dos sabotajes daban VERDE, y el segundo saca de verdad los cinco
    scripts de Postgres del wheel.
    """

    base = (
        "[build-system]\n"
        'requires = ["hatchling>=1.21"]\n'
        'build-backend = "hatchling.build"\n\n'
        "[tool.hatch.build.targets.wheel]\n"
        'packages = ["src/installer_backend"]\n'
    )
    assert _file_selection_declarations(base) == []

    sin_espacio = f'{base}exclude=["**/stack_assets/postgres/**"]\n'
    assert _file_selection_declarations(sin_espacio) == [
        "tool.hatch.build.targets.wheel.exclude"
    ], "un `exclude=` sin espacio vuelve a esquivar la guarda de empaquetado"

    tabla_global = (
        "[tool.hatch.build]\n"
        'exclude = ["**/stack_assets/**"]\n\n'
        "[tool.hatch.build.targets.wheel]\n"
        'packages = ["src/installer_backend"]\n'
    )
    assert _file_selection_declarations(tabla_global) == ["tool.hatch.build.exclude"], (
        "un `exclude` en la tabla global `[tool.hatch.build]` vuelve a esquivar la "
        "guarda: hatchling lo aplica a TODOS los targets"
    )

    otro_target = f'{base}\n[tool.hatch.build.targets.sdist]\nonly-include = ["src"]\n'
    assert _file_selection_declarations(otro_target) == [
        "tool.hatch.build.targets.sdist.only-include"
    ]

    # Un comentario que MENCIONE la palabra no es una declaración: la guarda
    # anterior no sabía distinguirlos, y por eso el `pyproject.toml` no podía
    # llevar escrito el aviso justo donde está la tentación de añadirla.
    assert _file_selection_declarations(f"# nada de exclude = [...] aquí\n{base}") == []


@pytest.mark.parametrize("proxy", ["egress-proxy", "registry-proxy"])
def test_each_build_context_ships_a_dockerfile_and_its_filter(proxy: str) -> None:
    """Un contexto de build sin ``Dockerfile`` aborta el proyecto entero.

    Los dos son servicios del NÚCLEO con ``pull_policy: build`` (ADR 0019 y ADR
    0094): ``docker compose up`` falla al RESOLVER el proyecto, antes de arrancar
    un solo contenedor. Y el ``filter.txt`` no es accesorio — es la allowlist de
    hosts, o sea la política de egress: sin ella tinyproxy arranca con
    ``FilterDefaultDeny`` y no sale nada.
    """

    llevados = {
        asset.path.split("/", 1)[1]
        for asset in stack_assets.CORE_ASSETS
        if asset.path.startswith(f"{proxy}/")
    }
    assert {
        "Dockerfile",
        "filter.txt",
        "tinyproxy.conf",
    } <= llevados, f"el contexto de build de `{proxy}` viaja incompleto: {sorted(llevados)}"
