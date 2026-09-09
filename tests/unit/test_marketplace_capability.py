"""Los tipos diferidos dejan de venderse (`task_mk_10`, ADR 0081 reabierto, opción b).

Dos mitades, las dos sin base de datos:

- `capability_of` dice por dónde llega la capacidad de un listing: fila al
  habilitar (skills y tools de red), al desplegar (servidores MCP y listings con
  validador tipado, como Playwright) o NUNCA mientras no exista el sandbox
  (código arbitrario). Es lo que la pestaña «Instaladas» usa para no pintar
  `enabled` un ítem sin fila (MK-01).
- La publicación privada rechaza, con motivo y ADR, un manifiesto de tool que
  ejecuta código (`implementation.runtime`). Antes se aceptaba y el fallo
  aparecía al habilitar, dos pantallas después.
"""

from __future__ import annotations

import pytest
from api_server.db.marketplace import MarketplaceListingKind
from api_server.marketplace.capability import (
    DEFERRED_REASON,
    TYPED_VALIDATOR_KEY,
    capability_of,
    resolved_implementation_type,
)
from api_server.marketplace.private_listing import (
    PrivateListingFormatError,
    parse_private_listing,
)

# ---------------------------------------------------------------------------
# capability_of
# ---------------------------------------------------------------------------


def test_una_skill_produce_fila_al_habilitar() -> None:
    assert capability_of("skill", {"prompt_fragment": "x"}).kind == "catalog_row"


@pytest.mark.parametrize("impl", ["mcp_tool", "http_endpoint"])
def test_una_tool_de_red_produce_fila(impl: str) -> None:
    assert capability_of("tool", {"implementation_type": impl}).kind == "catalog_row"


def test_un_servidor_mcp_llega_al_desplegar() -> None:
    """ADR 0166 D6: sus filas `<server>.*` las crea el import del despliegue."""
    assert capability_of("mcp_server", {"implementation_type": "mcp_tool"}).kind == "on_deploy"


def test_playwright_llega_al_desplegar_aunque_sea_docker_command() -> None:
    """Su capacidad es la configuración del browser-runtime del proyecto (ADR 0142),
    no una fila: el marcador es el validador tipado de su `config_schema`."""
    manifest = {
        "implementation_type": "docker_command",
        "config_schema": {TYPED_VALIDATOR_KEY: "playwright", "type": "object"},
    }
    assert capability_of("tool", manifest).kind == "on_deploy"


@pytest.mark.parametrize("impl", ["python_function", "docker_command"])
def test_codigo_arbitrario_sin_sandbox_es_diferido_con_motivo(impl: str) -> None:
    cap = capability_of("tool", {"implementation_type": impl})
    assert cap.kind == "deferred"
    assert cap.reason == DEFERRED_REASON
    assert "ADR 0081" in DEFERRED_REASON


def test_el_formato_privado_resuelve_runtime_a_python_function() -> None:
    manifest = {"implementation": {"runtime": "python", "module": "m"}}
    assert resolved_implementation_type(manifest) == "python_function"
    assert capability_of("tool", manifest).kind == "deferred"


def test_sin_tipo_resoluble_tampoco_hay_fila() -> None:
    """`materialize_installation` lo rechaza al habilitar; aquí es diferido, no fila."""
    assert capability_of("tool", {}).kind == "deferred"


# ---------------------------------------------------------------------------
# Publicación privada
# ---------------------------------------------------------------------------
_TOOL_YAML = """\
name: runner
version: 1.0.0
description: Runs things.
kind: tool
entrypoint: runner.main:run
implementation:
  runtime: python
  module: runner.main
input_schema:
  type: object
output_schema:
  type: object
"""

_MCP_YAML = """\
name: my-mcp
version: 1.0.0
description: An MCP server.
kind: mcp_server
entrypoint: server:main
implementation:
  runtime: node
  reference: https://mcp.example.com/mcp
input_schema:
  type: object
output_schema:
  type: object
"""


def test_publicar_una_tool_que_ejecuta_codigo_se_rechaza_con_motivo() -> None:
    with pytest.raises(PrivateListingFormatError) as exc_info:
        parse_private_listing(kind=MarketplaceListingKind.TOOL, manifest_text=_TOOL_YAML)
    msg = str(exc_info.value)
    assert "'python'" in msg
    assert "ADR 0081" in msg
    assert "cannot be published yet" in msg


def test_un_mcp_server_privado_sigue_publicandose() -> None:
    parsed = parse_private_listing(kind=MarketplaceListingKind.MCP_SERVER, manifest_text=_MCP_YAML)
    assert parsed.kind == MarketplaceListingKind.MCP_SERVER
    assert parsed.name == "my-mcp"
