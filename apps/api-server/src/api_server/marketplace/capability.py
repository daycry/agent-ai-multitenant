"""Qué capacidad produce de verdad una instalación (`task_mk_10`, ADR 0081 reabierto).

`InstallationStatus.ENABLED` significa «autorizada», no «viva» (ADR 0081): hay
instalaciones habilitadas que no producen nada que un agente pueda usar, y la
pestaña «Instaladas» las pintaba igual que a las que sí. Este módulo responde,
para un listing, POR DÓNDE llega su capacidad — y es puro a propósito, porque la
respuesta se decide por la forma del manifiesto y no por el estado de la fila:

- ``catalog_row``: una skill, o una tool de RED (`mcp_tool` / `http_endpoint`),
  cuya fila `skills`/`tools` la crea `materialize_installation` al habilitar
  (ADR 0100).
- ``on_deploy``: la capacidad nace al DESPLEGAR en un proyecto, no al instalar —
  un servidor MCP (sus filas `<server>.*` las crea el import del despliegue, ADR
  0166 D6) o un listing con validador tipado en su `config_schema` (Playwright,
  ADR 0142: su capacidad es la configuración del browser-runtime del proyecto).
- ``deferred``: ejecuta código arbitrario (`python_function`, `docker_command`, o
  un manifiesto privado con `implementation.runtime`) y NO hay sandbox
  out-of-process que lo materialice (ADR 0081 Fase B/C). Se instala `enabled`
  como INTENCIÓN y no produce nada: es el hallazgo MK-01, y lo que esta
  clasificación permite es dejar de venderlo como habilitado.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from api_server.db.domain import ToolImplementationType
from api_server.db.marketplace import MarketplaceListingKind

__all__ = [
    "DEFERRED_IMPL_TYPES",
    "DEFERRED_REASON",
    "NETWORK_IMPL_TYPES",
    "TYPED_VALIDATOR_KEY",
    "Capability",
    "CapabilityKind",
    "capability_of",
    "resolved_implementation_type",
]

#: Copia de `config_schema.TYPED_VALIDATOR_KEY`, aquí como literal para que este
#: módulo no arrastre el validador de esquemas al importarse desde un schema.
TYPED_VALIDATOR_KEY = "x-typed-validator"

NETWORK_IMPL_TYPES = frozenset(
    {ToolImplementationType.MCP_TOOL.value, ToolImplementationType.HTTP_ENDPOINT.value}
)
DEFERRED_IMPL_TYPES = frozenset(
    {ToolImplementationType.PYTHON_FUNCTION.value, ToolImplementationType.DOCKER_COMMAND.value}
)

DEFERRED_REASON = (
    "este listing ejecuta código arbitrario y la plataforma aún no tiene el sandbox "
    "out-of-process que lo materialice (ADR 0081, Fase B/C, reabierto el 2026-09-08 por "
    "task_mk_10): la instalación está autorizada pero no produce ninguna capacidad que "
    "un agente pueda usar"
)

CapabilityKind = Literal["catalog_row", "on_deploy", "deferred"]


@dataclass(frozen=True)
class Capability:
    kind: CapabilityKind
    reason: str | None = None


def resolved_implementation_type(manifest: dict[str, Any]) -> str:
    """El `implementation_type` efectivo de un manifiesto de tool.

    El catálogo oficial lo escribe tal cual; el formato privado (`tool.yaml`,
    Plan 09) trae en su lugar `implementation.runtime` (python, node…), que es
    código ejecutable por definición del formato: se resuelve a
    `python_function` para que la decisión de arriba sea una sola.
    """
    explicit = str(manifest.get("implementation_type") or "").strip()
    if explicit:
        return explicit
    implementation = manifest.get("implementation")
    if isinstance(implementation, dict) and str(implementation.get("runtime") or "").strip():
        return ToolImplementationType.PYTHON_FUNCTION.value
    return ""


def capability_of(kind: str, manifest: dict[str, Any] | None) -> Capability:
    """Por dónde llega la capacidad de un listing de ``kind`` con ``manifest``."""
    data = dict(manifest or {})
    if kind == MarketplaceListingKind.SKILL.value:
        return Capability("catalog_row")
    if kind == MarketplaceListingKind.MCP_SERVER.value:
        return Capability("on_deploy")
    schema = data.get("config_schema")
    if isinstance(schema, dict) and schema.get(TYPED_VALIDATOR_KEY):
        return Capability("on_deploy")
    impl = resolved_implementation_type(data)
    if impl in NETWORK_IMPL_TYPES:
        return Capability("catalog_row")
    if impl in DEFERRED_IMPL_TYPES:
        return Capability("deferred", DEFERRED_REASON)
    # Sin tipo resoluble: `materialize_installation` lo rechaza al habilitar
    # (`MaterializeError`), así que tampoco produce fila.
    return Capability("deferred", DEFERRED_REASON)
