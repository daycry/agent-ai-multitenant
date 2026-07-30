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
    auth_kind: str = Field(
        description="How requests authenticate: 'static' (token field), 'oauth' "
        "(Connect button), 'sidecar' (deploy a self-hosted sidecar; no per-request "
        "token), or 'none' (public server). ADR 0127."
    )


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
        auth_kind=template.auth_kind,
    )


# Explicit deny-list for HTTP templates that must NOT be offered (e.g. a server
# retired by a security advisory). stdio templates are withheld WHOLESALE by
# transport (see ``offered_catalog``), so they don't need listing here.
# ``docling-mcp`` stays here for the historical g5 guarantee/test, though the
# transport rule below would withhold it anyway.
# ``atlassian-remote`` (ADR 0127, auth_kind="oauth") is now OFFERED: the
# interactive «Connect» flow (routers/mcp_oauth.py + frontend McpOAuthConnect)
# is wired end-to-end. The final consent still happens in the operator's
# browser against the live Atlassian authorization server.
_UNAVAILABLE_TEMPLATE_IDS: frozenset[str] = frozenset({"docling-mcp"})


def offered_catalog() -> list[McpServerTemplate]:
    """The MCP templates the picker OFFERS as assignable, in stable insertion
    order (the admin-panel groups by category without re-sorting).

    Two filters (ADR 0117):

    * **Transport** — only HTTP transports (``sse`` / ``streamable_http``) are
      offered. The agent-runtime image packages NO stdio binaries, so every
      ``stdio`` template would fail at ``mcp_wire`` (the agent loops looking for
      a binary and escalates). They stay in ``CATALOG`` for conduct-time
      validation + audit, but are never offered.
    * **Explicit deny-list** — ``_UNAVAILABLE_TEMPLATE_IDS`` withholds specific
      HTTP templates if one is ever retired.
    """
    return [
        t
        for t in CATALOG.values()
        if t.transport != "stdio" and t.id not in _UNAVAILABLE_TEMPLATE_IDS
    ]


@router.get("", response_model=list[McpTemplateDto])
async def list_mcp_catalog(
    _principal: AuthPrincipal = Depends(require_tenant_member),
) -> list[McpTemplateDto]:
    """Return every ASSIGNABLE MCP template (HTTP-only; see ``offered_catalog``)
    so the picker never offers a server that cannot start in the runtime.
    """
    return [_to_dto(t) for t in offered_catalog()]
