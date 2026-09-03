"""El runbook de la allowlist de MCP remotos dice lo que el stack hace de verdad.

Lo pide D10 del ADR 0165, y no como trámite: el ADR decide que **ni el ajuste ni
el fichero pueden afirmar «permitido»** —sólo el proxy, preguntándole—, y ese
«preguntar» no vive en ningún código: vive en este documento. Un runbook
equivocado aquí no da error, da un operador convencido de haber cerrado un egress
que sigue abierto.

## Por qué casi todo se descubre en vez de escribirse

Las guardas de subcadena fija envejecen calladas: el día que el script gane una
opción, que tinyproxy cambie de puerto o que los centinelas del bloque generado se
renombren, el runbook pasaría a mentir y una lista de literales copiada aquí
seguiría verde. Así que lo que se puede leer del repo se lee del repo —las
opciones del `argparse` del renderizador, las dos directivas `ConnectPort`, el
puerto de escucha, las rutas de las dos copias del filtro, los centinelas, el
comando de aplicación que el propio script imprime— y cada descubrimiento afirma
primero que **encontró algo**
(`docs/03-guides/verificar-antes-de-implementar.md` §4).

## Las dos excepciones, y por qué son literales

Las dos líneas de log de tinyproxy (`Proxying refused on filtered domain` y
`Unauthorized connection from`) no están en este repo: son del demonio. Se
midieron el **2026-09-03** contra la imagen real (`docker build
docker/egress-proxy/`, tinyproxy 1.11, un `curl` cliente en una red docker de
usuario), y son el ÚNICO discriminante entre las dos causas del mismo 403 que ve
el operador: host fuera de la allowlist, o cliente fuera del `Allow` de tinyproxy
(D4 del ADR). Si un día tinyproxy cambia esas cadenas este test se cae y hay que
volver a medir — que es exactamente lo que queremos que pase.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit

_RAIZ = Path(__file__).resolve().parents[2]
_RUNBOOK = _RAIZ / "docs" / "06-runbooks" / "egress-mcp-allowlist.md"
_INDICE = _RAIZ / "docs" / "06-runbooks" / "README.md"
_SCRIPT = _RAIZ / "scripts" / "egress" / "render_mcp_allowlist.py"
_TINYPROXY_CONF = _RAIZ / "docker" / "egress-proxy" / "tinyproxy.conf"
_COMPOSE_GENERATOR = (
    _RAIZ / "apps" / "installer" / "backend" / "src" / "installer_backend" / "compose_generator.py"
)

#: Los dos rótulos cuyo ORDEN decide el ADR: revocar es lo urgente (la ventana en
#: la que el host sigue alcanzable), abrir es lo que puede esperar.
_H_REVOCAR = "## 1. Revocar un host"
_H_ABRIR = "## 2. Abrir un host"

#: Enlace Markdown a documento del repo, sin imágenes, sin URLs y sin anclas puras.
_ENLACE_RE = re.compile(r"(?<!!)\[[^\]]*\]\((?!https?:|mailto:)([^)\s#]+)")


@pytest.fixture(scope="module")
def runbook() -> str:
    assert _RUNBOOK.is_file(), (
        "falta docs/06-runbooks/egress-mcp-allowlist.md: el ADR 0165 (D10) lo exige, "
        "y sin él el paso de aplicación de la allowlist no está escrito en ninguna parte"
    )
    return _RUNBOOK.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def renderizador() -> ModuleType:
    """El script de aplicación, importado por ruta (no es un paquete instalable)."""
    spec = importlib.util.spec_from_file_location("_render_mcp_allowlist", _SCRIPT)
    assert spec is not None and spec.loader is not None, f"no se pudo cargar {_SCRIPT}"
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_el_runbook_existe_y_el_indice_de_su_carpeta_lo_enlaza(runbook: str) -> None:
    """Un runbook fuera del índice no lo encuentra quien lo necesita a las 3 de la mañana."""
    assert runbook.strip(), "el runbook está vacío"
    assert f"./{_RUNBOOK.name}" in _INDICE.read_text(encoding="utf-8"), (
        f"{_RUNBOOK.name} no está enlazado desde docs/06-runbooks/README.md"
    )


def test_revocar_va_antes_que_abrir(runbook: str) -> None:
    """El ADR lo ordena así por la asimetría: quitar un host del ajuste NO cierra el
    egress hasta el rebuild+recreate, así que lo urgente va primero en la página."""
    for rotulo in (_H_REVOCAR, _H_ABRIR):
        assert rotulo in runbook, f"falta la sección {rotulo!r}"
    assert runbook.index(_H_REVOCAR) < runbook.index(_H_ABRIR), (
        "el procedimiento de apertura va antes que el de revocación: el ADR 0165 "
        "manda lo contrario porque revocar es lo que no puede esperar"
    )


def test_documenta_todas_las_opciones_del_script(runbook: str) -> None:
    """Descubrimiento: las opciones salen del `argparse`, no de la memoria del autor."""
    fuente = _SCRIPT.read_text(encoding="utf-8")
    opciones = sorted(set(re.findall(r"add_argument\(\s*\"(--[a-z-]+)\"", fuente)))
    assert len(opciones) >= 4, f"el descubrimiento dejó de encontrar opciones: {opciones}"
    faltan = [o for o in opciones if o not in runbook]
    assert not faltan, f"el runbook no documenta {faltan} de scripts/egress/render_mcp_allowlist.py"


def test_el_comando_de_aplicacion_es_el_que_imprime_el_script(runbook: str) -> None:
    """El script termina diciendo qué falta por hacer; el runbook tiene que decir lo
    mismo, palabra por palabra. Dos redacciones del mismo comando es cómo una de las
    dos se queda vieja sin que nadie lo note."""
    fuente = _SCRIPT.read_text(encoding="utf-8")
    candidatos = re.findall(r"docker compose build egress-proxy[^\"\\\n]*", fuente)
    assert candidatos, "el script ya no imprime el comando de aplicación: ¿se movió a otro sitio?"
    comando = max(candidatos, key=len).strip()
    assert "--force-recreate" in comando, f"el comando descubierto no recrea nada: {comando!r}"
    assert comando in runbook, f"el runbook no trae el comando de aplicación tal cual: {comando!r}"


def test_nombra_los_dos_puertos_de_connectport_y_el_de_escucha(runbook: str) -> None:
    """Descubrimiento sobre `tinyproxy.conf`: `ConnectPort` son DOS directivas
    globales (D3), y un host que escuche fuera de esos puertos no se puede habilitar
    desde el ajuste por mucho que su nombre entre en el filtro."""
    conf = _TINYPROXY_CONF.read_text(encoding="utf-8")
    connect = re.findall(r"^ConnectPort\s+(\d+)", conf, re.MULTILINE)
    escucha = re.findall(r"^Port\s+(\d+)", conf, re.MULTILINE)
    assert len(connect) == 2, f"tinyproxy.conf ya no declara dos ConnectPort: {connect}"
    assert len(escucha) == 1, f"tinyproxy.conf ya no declara un único Port: {escucha}"
    faltan = [p for p in connect + escucha if p not in runbook]
    assert not faltan, f"el runbook no nombra estos puertos del proxy: {faltan}"


def test_dice_donde_esta_el_filtro_en_el_repo_y_en_un_host_instalado(
    runbook: str, renderizador: ModuleType
) -> None:
    """Dos copias en el repo, UNA en un host instalado. Confundirlas es editar un
    fichero que la imagen que corre no lleva."""
    copias = [Path(p).relative_to(_RAIZ).as_posix() for p in renderizador.FILTROS_DEL_REPO]
    assert len(copias) == 2, f"el renderizador ya no apunta a dos copias: {copias}"
    faltan = [c for c in copias if c not in runbook]
    assert not faltan, f"el runbook no nombra estas copias del filtro: {faltan}"

    fuente = _COMPOSE_GENERATOR.read_text(encoding="utf-8")
    asset_dir = re.search(r'STACK_ASSETS_DIR_NAME\s*=\s*"([a-z]+)"', fuente)
    assert asset_dir is not None, "no se pudo descubrir el directorio de auxiliares del instalador"
    instalada = f"{asset_dir.group(1)}/egress-proxy"
    assert instalada in runbook, (
        f"el runbook no dice dónde vive el filtro en un host instalado ({instalada}/filter.txt)"
    )


def test_cita_los_centinelas_del_bloque_generado(runbook: str, renderizador: ModuleType) -> None:
    """Lo de dentro lo escribe el script; lo de fuera, una persona con un PR. El
    runbook es donde esa frontera se explica, así que tiene que citarla literal."""
    assert renderizador.BLOCK_BEGIN in runbook, "el runbook no cita el centinela de apertura"
    assert renderizador.BLOCK_END in runbook, "el runbook no cita el centinela de cierre"


def test_separa_las_dos_causas_del_mismo_403(runbook: str) -> None:
    """D4: un cliente fuera del `Allow` de tinyproxy y un host fuera de la allowlist
    dan el MISMO 403 al cliente. Sólo el log del proxy los separa (medido 2026-09-03)."""
    for linea in ("Proxying refused on filtered domain", "Unauthorized connection from"):
        assert linea in runbook, (
            f"el runbook no trae la línea de log {linea!r}: sin ella, el operador no "
            "puede distinguir un host no permitido de un cliente no autorizado"
        )


def test_niega_por_escrito_que_esto_sea_control_de_exfiltracion(runbook: str) -> None:
    """El malentendido que el ADR pide repetir dos veces: se filtra el destino del
    CONNECT, no el contenido de la sesión TLS."""
    assert "exfiltración" in runbook, (
        "el runbook no dice que la allowlist NO es control de exfiltración; cada host "
        "abierto es un canal de salida completamente escribible"
    )


def test_sus_enlaces_relativos_resuelven_y_uno_es_el_adr_0165(runbook: str) -> None:
    """Un runbook que enlaza a un fichero inexistente no manda a nadie a ninguna parte."""
    destinos = _ENLACE_RE.findall(runbook)
    assert destinos, "el runbook no enlaza a ningún documento del repo"
    rotos = [d for d in destinos if not (_RUNBOOK.parent / d).exists()]
    assert not rotos, f"enlaces rotos en el runbook: {rotos}"
    assert any("0165-allowlist-de-hosts-mcp-remotos-en-el-egress.md" in d for d in destinos), (
        "el runbook no enlaza al ADR 0165, que es quien decide todo lo que aquí se opera"
    )
