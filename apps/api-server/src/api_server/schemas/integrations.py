"""`project.integrations`: las anclas de integración del proyecto (`task_mk_20`).

Plan `remediacion-marketplace-mcp-2026-09-02` (MK-05). Un proyecto que trabaja
contra Jira y Confluence tiene un epic padre y una página raíz bajo los que va
todo. Hoy eso vive, si acaso, en la descripción de cada plan; con este esquema
es un ajuste del proyecto que el run recibe en su preámbulo (`task_mk_21`).

Contrato del JSONB, por proveedor y **cerrado** (`extra="forbid"` en todos los
niveles): una clave desconocida es un 422, no un dato que se guarda y nadie lee.

    {
      "jira":       {"project_key": "PLAT", "parent_issue_key": "PLAT-120"},
      "confluence": {"space_key": "ENG", "root_page_id": "123456"}
    }

Sin secretos: las credenciales viven en el servidor MCP (Vault/OAuth). Aquí sólo
van identificadores que el propio Jira/Confluence enseña en su URL.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

__all__ = [
    "ConfluenceIntegration",
    "JiraIntegration",
    "ProjectIntegrations",
    "validate_integrations_payload",
]

_STRICT = ConfigDict(extra="forbid", str_strip_whitespace=True)

# Clave de proyecto Jira: mayúsculas, dígitos y `_`, empieza por letra (`PLAT`).
_JIRA_PROJECT_KEY = r"^[A-Z][A-Z0-9_]{0,63}$"
# Clave de issue: `<PROJECT>-<n>` (`PLAT-120`).
_JIRA_ISSUE_KEY = r"^[A-Z][A-Z0-9_]{0,63}-[0-9]{1,10}$"
# Clave de espacio Confluence: alfanumérica (los espacios personales llevan `~`).
_CONFLUENCE_SPACE_KEY = r"^~?[A-Za-z0-9_]{1,255}$"
# Id de página Confluence: numérico (es lo que va en la URL `/pages/<id>/`).
_CONFLUENCE_PAGE_ID = r"^[0-9]{1,20}$"


class JiraIntegration(BaseModel):
    """Ancla Jira: el proyecto y, opcionalmente, el epic/issue padre."""

    model_config = _STRICT

    project_key: str = Field(pattern=_JIRA_PROJECT_KEY)
    parent_issue_key: str | None = Field(default=None, pattern=_JIRA_ISSUE_KEY)


class ConfluenceIntegration(BaseModel):
    """Ancla Confluence: el espacio y, opcionalmente, la página raíz."""

    model_config = _STRICT

    space_key: str = Field(pattern=_CONFLUENCE_SPACE_KEY)
    root_page_id: str | None = Field(default=None, pattern=_CONFLUENCE_PAGE_ID)


class ProjectIntegrations(BaseModel):
    """El JSONB completo. Cada proveedor es opcional; el conjunto es cerrado."""

    model_config = _STRICT

    jira: JiraIntegration | None = None
    confluence: ConfluenceIntegration | None = None


def validate_integrations_payload(value: dict[str, Any] | None) -> dict[str, Any]:
    """Valida y NORMALIZA el JSONB que llega por la API.

    Devuelve sólo los proveedores presentes, sin claves ``None`` — así el JSONB
    guardado es el mínimo que el preámbulo del run va a leer, y ``{}`` significa
    exactamente «sin anclas». Lanza ``ValueError`` (Pydantic lo convierte en 422
    dentro de un ``field_validator``) con el detalle de la clave que sobra o del
    formato que no cuadra.
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("integrations must be a JSON object keyed by provider")
    try:
        parsed = ProjectIntegrations.model_validate(value)
    except ValidationError as exc:
        # Un mensaje por error, legible por quien rellena el formulario.
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'integrations'}: {err['msg']}"
            for err in exc.errors()
        )
        raise ValueError(f"invalid integrations: {problems}") from exc
    return parsed.model_dump(exclude_none=True)
