"""`GET /mcp-catalog` — expose the static MCP server catalog.

The catalog itself lives in `shared_mcp.catalog.CATALOG` as 22 frozen
`McpServerTemplate` instances (Plan 05 task_05_08..11 + stretch). It's
static (changes only when an ADR adds/removes a template), but the
admin-panel still needs a JSON view of it to render the picker in the
"Add MCP server" dialog.

The endpoint is project-agnostic on purpose — the catalog is the
same across tenants; per-project state (which templates are
configured, with which Vault paths) lives in `Project.mcp_servers`.
Auth: any authenticated user can read it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from shared_mcp.catalog import CATALOG, McpServerTemplate

from api_server.auth.deps import AuthPrincipal, require_tenant_member

router = APIRouter(prefix="/mcp-catalog", tags=["mcp-catalog"])


class McpTemplateDto(BaseModel):
    """JSON projection of `shared_mcp.catalog.McpServerTemplate`."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    display_name: str
    description: str
    transport: str
    command: str | None
    args: list[str]
    url: str | None
    secret_keys: list[str]
    vault_path_template: str | None
    default_timeout_s: float
    static_env: dict[str, str]
    static_headers: dict[str, str]
    maintainer: str
    repo_url: str
    docs_url: str
    category: str
    requires_auth: bool = Field(description="True when the template declares a secret_keys list.")


def _to_dto(template: McpServerTemplate) -> McpTemplateDto:
    return McpTemplateDto(
        id=template.id,
        display_name=template.display_name,
        description=template.description,
        transport=template.transport,
        command=template.command,
        args=list(template.args),
        url=template.url,
        secret_keys=list(template.secret_keys),
        vault_path_template=template.vault_path_template,
        default_timeout_s=template.default_timeout_s,
        static_env=dict(template.static_env),
        static_headers=dict(template.static_headers),
        maintainer=template.maintainer,
        repo_url=template.repo_url,
        docs_url=template.docs_url,
        category=template.category,
        requires_auth=bool(template.secret_keys),
    )


# Templates the platform KNOWS about but must NOT offer as assignable MCP
# servers because they cannot start out-of-the-box (g5, audit 2026-07-03).
# `docling-mcp` is stdio `command="docling-mcp"`, a binary upstream does not
# publish an image for (docs/03-guides/gotchas/docling-mcp-no-public-image.md);
# the service is commented out in docker-compose. The operative Docling path is
# `docling-serve` HTTP used by KB ingestion — a different code path — so the
# template stays in CATALOG (referenced there) but is filtered from the picker.
_UNAVAILABLE_TEMPLATE_IDS: frozenset[str] = frozenset({"docling-mcp"})


@router.get("", response_model=list[McpTemplateDto])
async def list_mcp_catalog(
    _principal: AuthPrincipal = Depends(require_tenant_member),
) -> list[McpTemplateDto]:
    """Return every ASSIGNABLE MCP template the platform knows about, in stable
    insertion order so the admin-panel can group by category without a second
    sort step. Templates with no runnable image (``_UNAVAILABLE_TEMPLATE_IDS``)
    are withheld so the picker never offers a server that cannot start.
    """
    return [_to_dto(t) for t in CATALOG.values() if t.id not in _UNAVAILABLE_TEMPLATE_IDS]
