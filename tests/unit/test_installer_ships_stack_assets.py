"""Lo que el instalador lleva dentro, ¿sigue siendo lo que dice llevar?

`installer_backend.stack_assets` resuelve una avería concreta —el compose
generado montaba seis familias de rutas que la instalación no escribía— haciendo
que esos ficheros **viajen dentro del paquete Python**, porque en el destino no
hay ningún ``docker/`` del que copiarlos (el Dockerfile del instalador copia sólo
``pyproject.toml`` y ``src/``).

El precio de esa decisión es una copia, y una copia sin guarda envejece. Este
fichero es la guarda, y mira las dos mitades:

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
   ejecute nunca. Por eso el conjunto de la derecha se **deriva del árbol**, no
   de una lista escrita a mano: el mismo modo de fallo que el `watchdog` ya pagó
   una vez en este repo.

Sobre la lista de exclusiones: sólo salen los ficheros que existen POR git
(``.gitkeep`` / ``.gitignore``), y salen enumerados, no por patrón amplio.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from installer_backend import stack_assets
from installer_backend.stack_assets import MODE_CONFIG, MODE_SCRIPT, StackAsset

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: Ficheros que existen para git y no para el stack: un directorio vacío no se
#: puede versionar, y el buzón de credenciales de Alertmanager se versiona con un
#: `.gitignore` que lo ignora todo menos a sí mismo. Ninguno debe viajar.
_GIT_ONLY = frozenset({".gitkeep", ".gitignore"})


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
# 2. No se ha quedado nada fuera.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("directory", sorted(stack_assets.iter_source_dirs()))
def test_no_file_of_a_shipped_directory_is_left_behind(directory: str) -> None:
    """Si un directorio viaja, viaja ENTERO.

    El fallo que persigue no lo ve nadie al leer un diff: alguien añade un
    ``06-*.sql`` a ``docker/postgres/init/`` —que en el stack de desarrollo
    funciona el mismo día— y la instalación no lo ejecuta jamás, porque su
    manifiesto sigue enumerando cinco.
    """

    origen = _REPO_ROOT / directory
    assert origen.is_dir(), f"{directory} ya no es un directorio del repo"

    en_disco = {
        path.name for path in origen.iterdir() if path.is_file() and path.name not in _GIT_ONLY
    }
    declarados = {
        asset.source.rsplit("/", 1)[1]
        for asset in stack_assets.ALL_ASSETS
        if asset.source.rsplit("/", 1)[0] == directory
    }
    excluidos = {
        source.rsplit("/", 1)[1]
        for source, _motivo in stack_assets.NOT_SHIPPED
        if source.rsplit("/", 1)[0] == directory
    }
    huerfanos = sorted(en_disco - declarados - excluidos)
    assert not huerfanos, (
        f"{len(huerfanos)} fichero(s) de `{directory}` que el stack de desarrollo "
        f"usa y la instalación NO lleva: {', '.join(huerfanos)}. Añádelos al "
        "manifiesto de `installer_backend.stack_assets` (y comprueba si el "
        "generador tiene que montarlos)."
    )


@pytest.mark.parametrize(
    ("source", "motivo"), stack_assets.NOT_SHIPPED, ids=lambda x: x if "/" in str(x) else ""
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
    assert len(motivo) >= 80, (
        f"la exclusión de `{source}` se justifica con {len(motivo)} caracteres. "
        "Un motivo que no se puede auditar dentro de seis meses no es un motivo."
    )


# ---------------------------------------------------------------------------
# 3. Los modos son los que el contenedor necesita.
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
# 4. Lo que viaja es el contenido REAL, no un hueco con el nombre correcto.
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


def test_the_wheel_target_still_packages_everything_under_the_package() -> None:
    """Que los auxiliares VIAJEN depende de cómo se empaqueta, no del manifiesto.

    Todo esto se sostiene sobre una propiedad que no está en ningún ``.py``:
    hatchling mete en el wheel **todos** los ficheros del directorio del paquete,
    no sólo los ``.py``. Verificado el 2026-08-27 construyendo el wheel — las 23
    entradas están dentro—, pero un ``exclude`` o un ``only-include`` añadido
    después los dejaría fuera **sin romper ni un test**: el instalador seguiría
    importando, y fallaría en el host del operador, a mitad de instalación,
    escribiendo un contexto de build vacío.

    Reconstruir el wheel en cada corrida de la suite sería caro; lo que se vigila
    aquí es el cambio que produciría ese estropicio.
    """

    pyproject = (_REPO_ROOT / "apps" / "installer" / "backend" / "pyproject.toml").read_text(
        encoding="utf-8"
    )
    assert "[tool.hatch.build.targets.wheel]" in pyproject
    assert 'packages = ["src/installer_backend"]' in pyproject, (
        "el target de wheel del instalador ya no empaqueta `src/installer_backend` "
        "entero. Comprueba que `installer_backend/stack_assets/**` sigue dentro "
        "del wheel (`python -m build --wheel apps/installer/backend`) antes de "
        "tocar esta guarda."
    )
    for narrowing in ("only-include", "exclude ", "artifacts"):
        assert narrowing not in pyproject, (
            f"el empaquetado del instalador declara `{narrowing.strip()}`. Eso puede "
            "dejar fuera del wheel los auxiliares de `stack_assets/`, que NO son "
            "`.py`: verifica el contenido del wheel antes de dar esto por bueno."
        )


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
