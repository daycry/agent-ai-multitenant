"""El bloque generado dentro de `filter.txt` (`task_mk_02`, ADR 0165 D8 + addendum A4).

El filtro del egress-proxy tiene dos mitades que no se gobiernan igual: las
entradas **escritas a mano** —los proveedores LLM del catálogo cerrado, los hosts
del córtex, los comodines legítimos como el de APIM— que viven en el repo con su
PR y su revisor, y el **bloque generado** desde el ajuste de plataforma
`egress.mcp_allowed_hosts`, que un script reescribe entero.

Dos cosas que estos tests fijan y que son la diferencia entre un mecanismo y un
destrozo:

- **Los centinelas existen siempre**, incluso con el bloque vacío. Si
  desapareciesen al quedarse sin hosts, la siguiente pasada del script no sabría
  dónde escribir y añadiría un segundo bloque.
- **Se reescribe EN SITIO**: lo de fuera del bloque no se toca. Un script que
  regenerase el fichero entero borraría las entradas escritas a mano, que son las
  que sostienen que el stack funcione.

Y el addendum A4 corrige al ADR en algo medible: «las dos copias» sólo existen en
el repo. En un host producido por el instalador hay **una sola**, bajo
`{compose_dir}/stack/egress-proxy/`, donde no existe ni `docker/` ni el paquete
`api_server` — por eso el renderizador es stdlib puro y toma las rutas por
argumento.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from api_server.egress.mcp_allowlist import BLOCK_BEGIN, BLOCK_END, render_generated_block

pytestmark = pytest.mark.unit

_RAIZ = Path(__file__).resolve().parents[2]
_FILTRO_REPO = _RAIZ / "docker" / "egress-proxy" / "filter.txt"
_FILTRO_INSTALADOR = (
    _RAIZ
    / "apps"
    / "installer"
    / "backend"
    / "src"
    / "installer_backend"
    / "stack_assets"
    / "egress-proxy"
    / "filter.txt"
)


def _script():
    """Carga el renderizador como módulo. Es stdlib puro a propósito: en un host
    instalado no existe `api_server`, así que no puede importarlo."""
    ruta = _RAIZ / "scripts" / "egress" / "render_mcp_allowlist.py"
    spec = importlib.util.spec_from_file_location("render_mcp_allowlist", ruta)
    assert spec and spec.loader
    modulo = importlib.util.module_from_spec(spec)
    sys.modules["render_mcp_allowlist"] = modulo
    spec.loader.exec_module(modulo)
    return modulo


# --------------------------------------------------------- el fichero del repo


@pytest.mark.parametrize("ruta", [_FILTRO_REPO, _FILTRO_INSTALADOR], ids=["repo", "instalador"])
def test_las_dos_copias_traen_los_centinelas(ruta: Path) -> None:
    texto = ruta.read_text(encoding="utf-8")

    assert texto.count(BLOCK_BEGIN) == 1, f"{ruta.name} no tiene exactamente un centinela de inicio"
    assert texto.count(BLOCK_END) == 1, f"{ruta.name} no tiene exactamente un centinela de fin"
    assert texto.index(BLOCK_BEGIN) < texto.index(BLOCK_END), "los centinelas están al revés"


def test_las_dos_copias_siguen_siendo_identicas() -> None:
    """La imagen que CI construye y escanea con Trivy sale de `docker/`; la que
    corre una instalación sale de `stack_assets`. Si se toca una sola, el stack
    instalado deja de ser el que se auditó."""
    assert _FILTRO_REPO.read_text(encoding="utf-8") == _FILTRO_INSTALADOR.read_text(
        encoding="utf-8"
    )


def test_las_entradas_escritas_a_mano_siguen_estando() -> None:
    """El bloque generado no puede haberse comido lo que sostiene el stack."""
    texto = _FILTRO_REPO.read_text(encoding="utf-8")

    for imprescindible in (
        r"^api\.anthropic\.com$",
        r"^api\.githubcopilot\.com$",
        r"^[a-z0-9-]+\.azure-api\.net$",
        r"^ollama(:[0-9]+)?$",
    ):
        assert imprescindible in texto, f"desapareció del filtro: {imprescindible}"


def test_el_bloque_del_repo_nace_vacio() -> None:
    """Ningún host MCP remoto está permitido por defecto: lo abre un System Admin,
    host a host, y queda en el historial de git."""
    texto = _FILTRO_REPO.read_text(encoding="utf-8")
    dentro = texto.split(BLOCK_BEGIN, 1)[1].split(BLOCK_END, 1)[0]

    assert not [linea for linea in dentro.splitlines() if linea.startswith("^")], (
        "el filtro del repo trae hosts MCP permitidos de fábrica"
    )


# --------------------------------------------------------- el renderizador


def test_reescribe_el_bloque_sin_tocar_lo_de_fuera() -> None:
    mod = _script()
    original = _FILTRO_REPO.read_text(encoding="utf-8")

    nuevo = mod.replace_generated_block(
        original, render_generated_block(["mcp.atlassian.com"]), BLOCK_BEGIN, BLOCK_END
    )

    assert r"^mcp\.atlassian\.com$" in nuevo
    # lo de fuera del bloque, intacto
    assert nuevo.split(BLOCK_BEGIN)[0] == original.split(BLOCK_BEGIN)[0]
    assert nuevo.split(BLOCK_END)[1] == original.split(BLOCK_END)[1]
    assert nuevo.count(BLOCK_BEGIN) == 1


def test_dos_pasadas_seguidas_dejan_el_mismo_fichero() -> None:
    """Idempotencia: sin ella, cada ejecución del script añadiría un bloque más."""
    mod = _script()
    original = _FILTRO_REPO.read_text(encoding="utf-8")
    bloque = render_generated_block(["mcp.atlassian.com"])

    una = mod.replace_generated_block(original, bloque, BLOCK_BEGIN, BLOCK_END)
    dos = mod.replace_generated_block(una, bloque, BLOCK_BEGIN, BLOCK_END)

    assert una == dos


def test_un_fichero_sin_centinelas_los_gana_al_final() -> None:
    mod = _script()
    texto = "^api\\.anthropic\\.com$\n"

    nuevo = mod.replace_generated_block(texto, render_generated_block([]), BLOCK_BEGIN, BLOCK_END)

    assert nuevo.startswith(texto)
    assert nuevo.count(BLOCK_BEGIN) == 1 and nuevo.count(BLOCK_END) == 1


def test_el_renderizador_no_importa_nada_del_repo() -> None:
    """Addendum A4: en un host instalado sólo existe el fichero, no el paquete.

    Se mira el árbol sintáctico y no el texto: el docstring del script MENCIONA
    `api_server` para explicar por qué no lo importa, y un `grep` no sabe
    distinguir una explicación de una dependencia.
    """
    import ast

    fuente = (_RAIZ / "scripts" / "egress" / "render_mcp_allowlist.py").read_text(encoding="utf-8")
    modulos: set[str] = set()
    for nodo in ast.walk(ast.parse(fuente)):
        if isinstance(nodo, ast.Import):
            modulos.update(alias.name.split(".")[0] for alias in nodo.names)
        elif isinstance(nodo, ast.ImportFrom) and nodo.module and nodo.level == 0:
            modulos.add(nodo.module.split(".")[0])

    del_repo = {
        m for m in modulos if m.startswith(("api_server", "shared_", "workers", "installer"))
    }
    assert not del_repo, f"el renderizador dejó de ser stdlib puro: importa {sorted(del_repo)}"


def test_los_dos_renderizadores_escriben_exactamente_lo_mismo() -> None:
    """Hay dos escritores del bloque —el módulo del api-server y el script stdlib—
    y tienen que producir texto IDÉNTICO. Si difiriesen en una coma, cada pasada
    de uno reescribiría lo del otro y el `diff` del filtro dejaría de decir qué
    cambió de verdad. Lo cazó la revisión: uno nombraba el script con guion y el
    otro con guion bajo.
    """
    mod = _script()

    for hosts in ([], ["mcp.atlassian.com"], ["b.example.com", "a.example.com"]):
        assert render_generated_block(hosts) == mod.render_generated_block(hosts), hosts
