"""Catalog of verified MCP server templates (Plan 05 Fase C).

A *template* is a pre-cooked recipe for one third-party MCP server:
which transport it uses, what binary or URL talks to it, which env
vars or headers carry credentials, and what the Vault path looks
like. The operator picks a template in the admin-panel UI and gets a
form pre-filled with this shape; the per-project name + Vault path
they add on top.

Why a catalog at all? Three reasons:

1. Onboarding: the operator doesn't need to remember that
   ``github-mcp`` wants the token in ``GITHUB_TOKEN`` and runs over
   stdio, or that ``slack-mcp`` is sse with an ``Authorization: Bearer``
   header. The template carries that knowledge.
2. Validation: tests assert each template renders to a valid
   :class:`MCPServerConfigModel`. If a template drifts away from the
   Pydantic schema, CI catches it before the UI breaks.
3. Documentation: ``docs/04-reference/mcp-servers.md`` reads from
   this catalog; humans see what's verified, AI agents see the
   exact shape to wire.

The catalog lives in ``shared_mcp`` (not ``api_server``) because
agent-runtime also wants to validate at conduct time that a
project's declared ``mcp_servers`` still match the catalog — if the
template was removed (security advisory, upstream broke), the agent
loop refuses to open the session.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# Mirror of shared_mcp.types.Transport — duplicated here so this module
# doesn't import the full types module at definition time (catalog is
# data only; types pulls in dataclasses + literals).
Transport = Literal["stdio", "sse", "streamable_http"]


@dataclass(frozen=True)
class McpServerTemplate:
    """One verified server entry. Frozen — the catalog is immutable
    at runtime; updates ship via code change + ADR."""

    # Stable identifier — `id` is used in URLs, audit logs, and the
    # UI's "Add from catalog" picker. Renaming breaks projects whose
    # mcp_servers entries reference it.
    id: str

    # Human-facing label and one-paragraph blurb (shown in the
    # picker). Markdown not allowed — it has to render in plain text
    # in audit logs and CLI output too.
    display_name: str
    description: str

    # Transport + transport-specific fields. Exactly one of
    # `command` (stdio) or `url` (sse/streamable_http) must be set,
    # mirroring MCPServerConfig invariants.
    transport: Transport
    command: str | None = None
    args: tuple[str, ...] = ()
    url: str | None = None

    # Which env vars (stdio) or header names (sse/streamable_http)
    # carry the resolved Vault secret. The catalog declares them so
    # tests can verify the Vault payload format upstream.
    secret_keys: tuple[str, ...] = ()

    # Suggested Vault path shape — ``{project_id}`` is substituted at
    # render time. None means "no auth needed" (rare; mostly local
    # docling-mcp).
    vault_path_template: str | None = None

    # Per-tool timeout default (overrides shared_mcp.MCPServerConfig's
    # 30s default when the server is known to be slow, e.g. doc
    # ingestion or LLM-backed servers).
    default_timeout_s: float = 30.0

    # Free-form static env vars / headers the template needs that are
    # NOT secret (e.g. ``GITHUB_HOST`` for GHES). The operator can
    # override these per project; defaults live here.
    static_env: dict[str, str] = field(default_factory=dict)
    static_headers: dict[str, str] = field(default_factory=dict)

    # Upstream maintainer + repo URL — surfaced in the picker so
    # operators can verify the source before granting credentials.
    maintainer: str = ""
    repo_url: str = ""
    docs_url: str = ""

    # Categorisation for the picker grouping. One of: "docs", "scm",
    # "data", "files", "comms", "issues", "other". Free-form; the UI
    # falls back to "other" for unknown values.
    category: str = "other"

    def __post_init__(self) -> None:
        # Mirror MCPServerConfig.__post_init__ — keeping these in
        # lockstep means a bad template fails at import time, not at
        # the operator's first attempt to use it.
        if self.transport == "stdio":
            if not self.command:
                raise ValueError(f"template {self.id!r}: stdio requires `command`")
            if self.url is not None:
                raise ValueError(f"template {self.id!r}: stdio must not set `url`")
        else:
            if not self.url:
                raise ValueError(
                    f"template {self.id!r}: transport={self.transport!r} requires `url`"
                )
            if self.command is not None:
                raise ValueError(
                    f"template {self.id!r}: transport={self.transport!r} must not set `command`"
                )


# ---------------------------------------------------------------------------
# task_05_08 — docling-mcp
# ---------------------------------------------------------------------------
DOCLING_MCP = McpServerTemplate(
    id="docling-mcp",
    display_name="Docling (IBM)",
    description=(
        "PDF + DOCX + HTML parsing with bounding-box-aware citations. "
        "Already used by the KB ingestion pipeline in Plan 04 — this "
        "template surfaces it as an agent-callable tool too."
    ),
    transport="stdio",
    command="docling-mcp",
    # docling-mcp talks to docling-serve over the local network; no
    # secret needed inside the platform's compose network.
    vault_path_template=None,
    default_timeout_s=120.0,  # OCR + layout extraction is slow
    maintainer="IBM Research",
    repo_url="https://github.com/docling-project/docling-mcp",
    docs_url="https://docling-project.github.io/docling-mcp/",
    category="docs",
)


# ---------------------------------------------------------------------------
# task_05_09 — SCM family (github / gitlab / azure-devops)
# ---------------------------------------------------------------------------
GITHUB_MCP = McpServerTemplate(
    id="github-mcp",
    display_name="GitHub",
    description=(
        "Search repos, read/create issues, list and review PRs, "
        "manage workflow runs. Recommended for agents that operate "
        "on the team's source code."
    ),
    transport="stdio",
    command="github-mcp",
    secret_keys=("GITHUB_TOKEN",),
    vault_path_template="vault:secret/data/mcp/github/{project_id}",
    static_env={"GITHUB_HOST": "https://api.github.com"},
    maintainer="GitHub",
    repo_url="https://github.com/github/github-mcp-server",
    docs_url="https://github.com/github/github-mcp-server#readme",
    category="scm",
)

GITLAB_MCP = McpServerTemplate(
    id="gitlab-mcp",
    display_name="GitLab",
    description=(
        "Same primitives as github-mcp for GitLab projects: issues, "
        "merge requests, pipelines, repository search. Self-hosted "
        "GitLab works too — override GITLAB_URL on the operator side."
    ),
    transport="stdio",
    command="mcp-gitlab",
    secret_keys=("GITLAB_PERSONAL_ACCESS_TOKEN",),
    vault_path_template="vault:secret/data/mcp/gitlab/{project_id}",
    static_env={"GITLAB_URL": "https://gitlab.com"},
    maintainer="modelcontextprotocol",
    repo_url="https://github.com/modelcontextprotocol/servers/tree/main/src/gitlab",
    docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/gitlab#readme",
    category="scm",
)

AZURE_DEVOPS_MCP = McpServerTemplate(
    id="azure-devops-mcp",
    display_name="Azure DevOps",
    description=(
        "Work items, repos, pull requests and pipelines from Azure "
        "DevOps. The PAT must have at least Code (read) + Work Items "
        "(read/write) scopes."
    ),
    transport="stdio",
    command="mcp-azure-devops",
    secret_keys=("AZURE_DEVOPS_PAT",),
    vault_path_template="vault:secret/data/mcp/azure-devops/{project_id}",
    static_env={
        # The operator overrides ORG to their own DevOps tenant; we
        # leave a placeholder so the picker shows the field.
        "AZURE_DEVOPS_ORG": "",
    },
    maintainer="Microsoft (community)",
    repo_url="https://github.com/Tiberriver256/mcp-azure-devops",
    docs_url="https://github.com/Tiberriver256/mcp-azure-devops#readme",
    category="scm",
)


# ---------------------------------------------------------------------------
# task_05_10 — postgres-mcp
# ---------------------------------------------------------------------------
POSTGRES_MCP = McpServerTemplate(
    id="postgres-mcp",
    display_name="PostgreSQL",
    description=(
        "Read-only SQL queries against a Postgres database. The "
        "connection string is resolved from Vault — the agent never "
        "sees the cleartext credentials, only the tool surface "
        "(query, list_tables, describe_table)."
    ),
    transport="stdio",
    command="mcp-postgres",
    secret_keys=("DATABASE_URI",),
    vault_path_template="vault:secret/data/mcp/postgres/{project_id}",
    maintainer="modelcontextprotocol",
    repo_url="https://github.com/modelcontextprotocol/servers/tree/main/src/postgres",
    docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/postgres#readme",
    category="data",
)


# ---------------------------------------------------------------------------
# task_05_11 — filesystem / gdrive / gmail / gcalendar / slack / jira / linear
# ---------------------------------------------------------------------------
FILESYSTEM_MCP = McpServerTemplate(
    id="filesystem-mcp",
    display_name="Filesystem",
    description=(
        "Read + write files inside a bind-mounted directory. The "
        "ALLOWED_DIRS env var is enforced by the server: paths "
        "outside the allowlist raise a tool error."
    ),
    transport="stdio",
    command="mcp-filesystem",
    # ALLOWED_DIRS isn't a secret — it's a static config the operator
    # sets per project. No Vault needed.
    static_env={"ALLOWED_DIRS": "/workspace"},
    maintainer="modelcontextprotocol",
    repo_url="https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem",
    docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem#readme",
    category="files",
)

GDRIVE_MCP = McpServerTemplate(
    id="gdrive-mcp",
    display_name="Google Drive",
    description=(
        "Search + read files from Google Drive. Auth via Google "
        "OAuth: the resolved Vault secret carries `access_token` "
        "(refreshable upstream) + `refresh_token`."
    ),
    transport="stdio",
    command="mcp-gdrive",
    secret_keys=("GDRIVE_ACCESS_TOKEN", "GDRIVE_REFRESH_TOKEN"),
    vault_path_template="vault:secret/data/mcp/gdrive/{project_id}",
    maintainer="modelcontextprotocol",
    repo_url="https://github.com/modelcontextprotocol/servers/tree/main/src/gdrive",
    docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/gdrive#readme",
    category="files",
)

GMAIL_MCP = McpServerTemplate(
    id="gmail-mcp",
    display_name="Gmail",
    description=(
        "Read, search, draft and send Gmail messages. Same OAuth "
        "shape as gdrive-mcp. Granting send permission is the riskiest "
        "tool surface in the catalog — keep guardrails tight."
    ),
    transport="stdio",
    command="mcp-gmail",
    secret_keys=("GMAIL_ACCESS_TOKEN", "GMAIL_REFRESH_TOKEN"),
    vault_path_template="vault:secret/data/mcp/gmail/{project_id}",
    maintainer="modelcontextprotocol (community)",
    repo_url="https://github.com/modelcontextprotocol/servers/tree/main/src/gmail",
    docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/gmail#readme",
    category="comms",
)

GCALENDAR_MCP = McpServerTemplate(
    id="gcalendar-mcp",
    display_name="Google Calendar",
    description=("Read + create calendar events. Same OAuth shape as " "gdrive-mcp / gmail-mcp."),
    transport="stdio",
    command="mcp-gcalendar",
    secret_keys=("GCAL_ACCESS_TOKEN", "GCAL_REFRESH_TOKEN"),
    vault_path_template="vault:secret/data/mcp/gcalendar/{project_id}",
    maintainer="modelcontextprotocol (community)",
    repo_url="https://github.com/modelcontextprotocol/servers/tree/main/src/gcalendar",
    docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/gcalendar#readme",
    category="comms",
)

SLACK_MCP = McpServerTemplate(
    id="slack-mcp",
    display_name="Slack",
    description=(
        "Post messages, read channels, search history. The bot token "
        "(xoxb-...) needs `chat:write`, `channels:read` and "
        "`channels:history` for the full tool surface."
    ),
    transport="stdio",
    command="mcp-slack",
    secret_keys=("SLACK_BOT_TOKEN", "SLACK_TEAM_ID"),
    vault_path_template="vault:secret/data/mcp/slack/{project_id}",
    maintainer="modelcontextprotocol",
    repo_url="https://github.com/modelcontextprotocol/servers/tree/main/src/slack",
    docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/slack#readme",
    category="comms",
)

JIRA_MCP = McpServerTemplate(
    id="jira-mcp",
    display_name="Atlassian Jira",
    description=(
        "Search, read, comment and transition Jira issues. The "
        "Atlassian API token plus the user's email act as basic auth "
        "(`email:token`); both live in Vault."
    ),
    transport="stdio",
    command="mcp-jira",
    secret_keys=("JIRA_API_TOKEN", "JIRA_EMAIL", "JIRA_INSTANCE_URL"),
    vault_path_template="vault:secret/data/mcp/jira/{project_id}",
    maintainer="modelcontextprotocol (community)",
    repo_url="https://github.com/modelcontextprotocol/servers/tree/main/src/jira",
    docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/jira#readme",
    category="issues",
)

LINEAR_MCP = McpServerTemplate(
    id="linear-mcp",
    display_name="Linear",
    description=(
        "Same shape as jira-mcp for Linear: search issues, comment, "
        "transition. Single API key. Pick this OR jira-mcp depending "
        "on which tracker the team uses."
    ),
    transport="stdio",
    command="mcp-linear",
    secret_keys=("LINEAR_API_KEY",),
    vault_path_template="vault:secret/data/mcp/linear/{project_id}",
    maintainer="modelcontextprotocol (community)",
    repo_url="https://github.com/modelcontextprotocol/servers/tree/main/src/linear",
    docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/linear#readme",
    category="issues",
)


# ---------------------------------------------------------------------------
# Registry — a dict so callers can ``CATALOG[id]`` without scanning.
# ---------------------------------------------------------------------------
CATALOG: dict[str, McpServerTemplate] = {
    template.id: template
    for template in (
        DOCLING_MCP,
        GITHUB_MCP,
        GITLAB_MCP,
        AZURE_DEVOPS_MCP,
        POSTGRES_MCP,
        FILESYSTEM_MCP,
        GDRIVE_MCP,
        GMAIL_MCP,
        GCALENDAR_MCP,
        SLACK_MCP,
        JIRA_MCP,
        LINEAR_MCP,
    )
}


def render_vault_path(template: McpServerTemplate, *, project_id: str) -> str | None:
    """Substitute ``{project_id}`` in the template's Vault path shape.

    Returns None if the template declares no auth. The caller (the
    api-server's catalog endpoint, or task_05_15's diagnostic panel)
    uses this to pre-fill the operator's form.
    """
    if template.vault_path_template is None:
        return None
    return template.vault_path_template.replace("{project_id}", project_id)


__all__ = [
    "AZURE_DEVOPS_MCP",
    "CATALOG",
    "DOCLING_MCP",
    "FILESYSTEM_MCP",
    "GCALENDAR_MCP",
    "GDRIVE_MCP",
    "GITHUB_MCP",
    "GITLAB_MCP",
    "GMAIL_MCP",
    "JIRA_MCP",
    "LINEAR_MCP",
    "McpServerTemplate",
    "POSTGRES_MCP",
    "SLACK_MCP",
    "Transport",
    "render_vault_path",
]
