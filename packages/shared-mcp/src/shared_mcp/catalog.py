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

    # How a project's REQUESTS to this server authenticate (ADR 0127) — drives
    # the picker UX: "static" → token field (bearer/env from Vault), "oauth" →
    # a "Connect" button (interactive consent once, tokens auto-refreshed),
    # "sidecar" → nothing per-request (a self-hosted sidecar authenticates via
    # its OWN env; the operator deploys + configures the sidecar, not a token
    # here), "none" → genuinely no auth (public server). Left blank → derived in
    # ``__post_init__`` from ``secret_keys`` (present → "static", absent →
    # "none"); "oauth"/"sidecar" are always set explicitly.
    auth_kind: str = ""

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
        # Derive auth_kind when left blank (frozen dataclass → object.__setattr__).
        # "oauth" is never derived (it's set explicitly); blank → static/none by
        # whether the template declares any secret.
        if not self.auth_kind:
            object.__setattr__(self, "auth_kind", "static" if self.secret_keys else "none")
        elif self.auth_kind not in ("none", "static", "oauth", "sidecar"):
            raise ValueError(
                f"template {self.id!r}: auth_kind must be none|static|oauth|sidecar, "
                f"got {self.auth_kind!r}"
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
# Catalog extension — bonus templates beyond the roadmap's minimum
# (Plan 05 task_05_11 stretch: user asked for "catalogo amplio").
# ---------------------------------------------------------------------------

# ----- docs (extra) -----
CONFLUENCE_MCP = McpServerTemplate(
    id="confluence-mcp",
    display_name="Atlassian Confluence",
    description=(
        "Read + write Confluence pages, search spaces, manage comments. "
        "Same Atlassian basic-auth as jira-mcp (email + API token + "
        "instance URL) — three Vault fields, no defaults."
    ),
    transport="stdio",
    command="mcp-confluence",
    secret_keys=("CONFLUENCE_API_TOKEN", "CONFLUENCE_EMAIL", "CONFLUENCE_INSTANCE_URL"),
    vault_path_template="vault:secret/data/mcp/confluence/{project_id}",
    maintainer="atlassian (community)",
    repo_url="https://github.com/sooperset/mcp-atlassian",
    docs_url="https://github.com/sooperset/mcp-atlassian#readme",
    category="docs",
)

NOTION_MCP = McpServerTemplate(
    id="notion-mcp",
    display_name="Notion",
    description=(
        "Notion pages + databases: search, read, create, update. The "
        "Notion integration token lives in Vault as `NOTION_API_KEY`. "
        "Alternative to Confluence when the team is on Notion."
    ),
    transport="stdio",
    command="mcp-notion",
    secret_keys=("NOTION_API_KEY",),
    vault_path_template="vault:secret/data/mcp/notion/{project_id}",
    maintainer="modelcontextprotocol (community)",
    repo_url="https://github.com/makenotion/notion-mcp-server",
    docs_url="https://github.com/makenotion/notion-mcp-server#readme",
    category="docs",
)


# ----- scm (extra) -----
BITBUCKET_MCP = McpServerTemplate(
    id="bitbucket-mcp",
    display_name="Bitbucket",
    description=(
        "Repos, pull requests, pipelines on Bitbucket Cloud. Auth is "
        "basic with the user's email + an app password (NOT account "
        "password — Bitbucket deprecated those)."
    ),
    transport="stdio",
    command="mcp-bitbucket",
    secret_keys=("BITBUCKET_USERNAME", "BITBUCKET_APP_PASSWORD"),
    vault_path_template="vault:secret/data/mcp/bitbucket/{project_id}",
    maintainer="bitbucket (community)",
    repo_url="https://github.com/MatanYemini/bitbucket-mcp",
    docs_url="https://github.com/MatanYemini/bitbucket-mcp#readme",
    category="scm",
)


# ----- comms (extra) -----
MSTEAMS_MCP = McpServerTemplate(
    id="ms-teams-mcp",
    display_name="Microsoft Teams",
    description=(
        "Read + post messages to Teams channels, list chats, manage "
        "presence. OAuth via Microsoft Graph: access + refresh tokens "
        "in Vault."
    ),
    transport="stdio",
    command="mcp-ms-teams",
    secret_keys=("MSTEAMS_ACCESS_TOKEN", "MSTEAMS_REFRESH_TOKEN", "MSTEAMS_TENANT_ID"),
    vault_path_template="vault:secret/data/mcp/ms-teams/{project_id}",
    maintainer="ms-graph (community)",
    repo_url="https://github.com/InditexTech/mcp-teams-server",
    docs_url="https://github.com/InditexTech/mcp-teams-server#readme",
    category="comms",
)

DISCORD_MCP = McpServerTemplate(
    id="discord-mcp",
    display_name="Discord",
    description=(
        "Post + read channel messages, manage roles. Single bot token "
        "from the Discord developer portal. The bot must be invited "
        "into the target guild with the right intents enabled."
    ),
    transport="stdio",
    command="mcp-discord",
    secret_keys=("DISCORD_BOT_TOKEN",),
    vault_path_template="vault:secret/data/mcp/discord/{project_id}",
    maintainer="discord (community)",
    repo_url="https://github.com/v-3/discordmcp",
    docs_url="https://github.com/v-3/discordmcp#readme",
    category="comms",
)


# ----- observability (NEW category) -----
SENTRY_MCP = McpServerTemplate(
    id="sentry-mcp",
    display_name="Sentry",
    description=(
        "Read Sentry issues, events, releases. Useful for agents that "
        "triage errors in production. Auth via an organization-scoped "
        "auth token (`sntrys_...`)."
    ),
    transport="stdio",
    command="mcp-sentry",
    secret_keys=("SENTRY_AUTH_TOKEN", "SENTRY_ORG_SLUG"),
    vault_path_template="vault:secret/data/mcp/sentry/{project_id}",
    maintainer="sentry (community)",
    repo_url="https://github.com/getsentry/sentry-mcp",
    docs_url="https://github.com/getsentry/sentry-mcp#readme",
    category="observability",
)

GRAFANA_MCP = McpServerTemplate(
    id="grafana-mcp",
    display_name="Grafana",
    description=(
        "Query Grafana dashboards + Loki/Tempo/Prometheus through one "
        "tool surface. Service-account API key + the Grafana URL "
        "(self-hosted or Grafana Cloud)."
    ),
    transport="stdio",
    command="mcp-grafana",
    secret_keys=("GRAFANA_API_KEY", "GRAFANA_URL"),
    vault_path_template="vault:secret/data/mcp/grafana/{project_id}",
    maintainer="grafana (community)",
    repo_url="https://github.com/grafana/mcp-grafana",
    docs_url="https://github.com/grafana/mcp-grafana#readme",
    category="observability",
)


# ----- search (NEW category) -----
BRAVE_SEARCH_MCP = McpServerTemplate(
    id="brave-search-mcp",
    display_name="Brave Search",
    description=(
        "Web search via the Brave Search API. Useful as the "
        "general-purpose 'go look it up' tool for an agent. Free tier "
        "exists but rate-limited; pin a usage budget in the project's "
        "guardrails to avoid surprise spend."
    ),
    transport="stdio",
    command="mcp-brave-search",
    secret_keys=("BRAVE_API_KEY",),
    vault_path_template="vault:secret/data/mcp/brave/{project_id}",
    maintainer="modelcontextprotocol",
    repo_url="https://github.com/modelcontextprotocol/servers/tree/main/src/brave-search",
    docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/brave-search#readme",
    category="search",
)

TAVILY_MCP = McpServerTemplate(
    id="tavily-mcp",
    display_name="Tavily Search",
    description=(
        "AI-optimised web search: returns ranked passages instead of "
        "raw URLs. Better than Brave for RAG-style 'look it up and "
        "summarise' tasks. Single API key."
    ),
    transport="stdio",
    command="mcp-tavily",
    secret_keys=("TAVILY_API_KEY",),
    vault_path_template="vault:secret/data/mcp/tavily/{project_id}",
    maintainer="tavily (community)",
    repo_url="https://github.com/tavily-ai/tavily-mcp",
    docs_url="https://github.com/tavily-ai/tavily-mcp#readme",
    category="search",
)


# ----- browser (NEW category) -----
PUPPETEER_BROWSER_MCP = McpServerTemplate(
    id="puppeteer-browser-mcp",
    display_name="Puppeteer browser",
    description=(
        "Headless Chrome via Puppeteer: navigate URLs, click, type, "
        "extract DOM, take screenshots. Runs in a sandboxed subprocess "
        "(no Vault credentials — the sandbox itself is the security "
        "boundary). Heaviest tool in the catalog — gate behind project "
        "allowlist."
    ),
    transport="stdio",
    command="mcp-puppeteer",
    # No Vault — the threat model is "agent navigates to URL", not
    # "agent authenticates with our credentials". If a particular site
    # needs auth, the operator wires headers via static_headers above.
    default_timeout_s=60.0,
    maintainer="modelcontextprotocol",
    repo_url="https://github.com/modelcontextprotocol/servers/tree/main/src/puppeteer",
    docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/puppeteer#readme",
    category="browser",
)


# ----- meta (NEW category) — agent-side helpers, no external API -----
MEMORY_MCP = McpServerTemplate(
    id="memory-mcp",
    display_name="Long-term memory (mcp-memory)",
    description=(
        "Key/value memory the agent can write to and read from across "
        "sessions. Persists to a local SQLite file inside the worker. "
        "Distinct from the platform's RAG memory (Plan 04) — that one "
        "is structured KB + embeddings; this is short notes the agent "
        "keeps for itself."
    ),
    transport="stdio",
    command="mcp-memory",
    default_timeout_s=10.0,
    maintainer="modelcontextprotocol",
    repo_url="https://github.com/modelcontextprotocol/servers/tree/main/src/memory",
    docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/memory#readme",
    category="meta",
)

SEQUENTIAL_THINKING_MCP = McpServerTemplate(
    id="sequential-thinking-mcp",
    display_name="Sequential thinking",
    description=(
        "A meta-tool that exposes 'think' as a tool call: the agent "
        "logs intermediate reasoning steps that subsequent calls can "
        "reference. Improves long-horizon planning on models without "
        "native chain-of-thought streams. No external API, no secret."
    ),
    transport="stdio",
    command="mcp-sequential-thinking",
    default_timeout_s=10.0,
    maintainer="modelcontextprotocol",
    repo_url="https://github.com/modelcontextprotocol/servers/tree/main/src/sequentialthinking",
    docs_url=(
        "https://github.com/modelcontextprotocol/servers/tree/main/" "src/sequentialthinking#readme"
    ),
    category="meta",
)


# ---------------------------------------------------------------------------
# HTTP templates (ADR 0117). The agent-runtime cannot spawn stdio binaries, so
# the OFFERED catalog is HTTP-only: remote servers (Context7, GitHub) and the
# Atlassian sidecar. The stdio templates above stay catalogued (conduct-time
# validation + audit) but are withheld from the picker (see mcp_catalog.py).
# ---------------------------------------------------------------------------
CONTEXT7_MCP = McpServerTemplate(
    id="context7",
    display_name="Context7 (docs de librerías al día)",
    description=(
        "Documentación ACTUALIZADA de frameworks/librerías (resolve-library-id + "
        "get-library-docs): los agentes consultan la API real en vez de recordarla. "
        "Remoto público — funciona SIN credencial (con rate limits); una API key opcional "
        "sube el límite (guárdala en Vault y añádela como cabecera). ⚠️ Requiere abrir "
        "`mcp.context7.com` en los dominios permitidos del proyecto (egress deny-by-default)."
    ),
    transport="streamable_http",
    url="https://mcp.context7.com/mcp",
    default_timeout_s=60.0,
    maintainer="Upstash (Context7)",
    repo_url="https://github.com/upstash/context7",
    docs_url="https://context7.com",
    category="docs",
)

ATLASSIAN_MCP = McpServerTemplate(
    id="atlassian",
    display_name="Atlassian (Jira + Confluence)",
    description=(
        "UN solo server para Jira (buscar/comentar/transicionar issues) y Confluence "
        "(crear/editar páginas). Se despliega como SIDECAR self-hosted "
        "(`ghcr.io/sooperset/mcp-atlassian`, `--transport streamable-http`) en la red "
        "`agentic-agents`; el token de API de Atlassian va en el ENV del sidecar, no en la "
        "petición — por eso el server no declara auth aquí. Al ser hostname interno no pasa "
        "por egress. Sustituye a las plantillas stdio jira-mcp/confluence-mcp (validado e2e)."
    ),
    transport="streamable_http",
    url="http://mcp-atlassian:9000/mcp",
    default_timeout_s=60.0,
    # A sidecar authenticates itself via its OWN env (the Atlassian API
    # token lives in the sidecar container, not in the request). So there
    # is no per-request auth to configure here — but it is NOT "none"
    # either (the picker must tell the operator "deploy the sidecar",
    # not "this is a public server"). See ADR 0127.
    auth_kind="sidecar",
    maintainer="sooperset (community)",
    repo_url="https://github.com/sooperset/mcp-atlassian",
    docs_url="https://github.com/sooperset/mcp-atlassian#readme",
    category="issues",
)

ATLASSIAN_REMOTE_MCP = McpServerTemplate(
    id="atlassian-remote",
    display_name="Atlassian (MCP remoto oficial · OAuth)",
    description=(
        "Servidor MCP OFICIAL y HOSPEDADO por Atlassian (`mcp.atlassian.com`) para Jira + "
        "Confluence, autenticado con OAuth 2.1 (ADR 0127): el operador pulsa «Conectar» UNA "
        "vez, consiente en Atlassian, y la plataforma refresca el token sola. Multi-tenant "
        "limpio (cada tenant autoriza SU cuenta; sin sidecar ni bot compartido). Alternativa "
        "al sidecar `atlassian` para quien no quiera desplegar infra. ⚠️ Requiere abrir "
        "`mcp.atlassian.com` en los dominios permitidos del proyecto (egress)."
    ),
    # Endpoint OFICIAL streamable-HTTP (`/v1/sse` está deprecado, retirada jun-2026).
    transport="streamable_http",
    url="https://mcp.atlassian.com/v1/mcp",
    default_timeout_s=60.0,
    auth_kind="oauth",
    maintainer="Atlassian",
    repo_url="https://www.atlassian.com/platform/remote-mcp-server",
    docs_url="https://support.atlassian.com/rovo/docs/setting-up-ides/",
    category="issues",
)

GITHUB_REMOTE_MCP = McpServerTemplate(
    id="github-remote",
    display_name="GitHub (MCP remoto oficial)",
    description=(
        "Servidor MCP HOSPEDADO por GitHub (issues, PRs, repos, Actions) sobre HTTP — no "
        "necesita binario local (a diferencia de github-mcp stdio). Auth con un PAT (o token "
        "OAuth) en la cabecera `Authorization`: guárdalo en Vault como el valor completo "
        "`Bearer <token>`. ⚠️ Requiere abrir `api.githubcopilot.com` en los dominios "
        "permitidos del proyecto (egress)."
    ),
    transport="streamable_http",
    url="https://api.githubcopilot.com/mcp/",
    secret_keys=("Authorization",),
    vault_path_template="vault:secret/data/mcp/github/{project_id}",
    default_timeout_s=60.0,
    maintainer="GitHub",
    repo_url="https://github.com/github/github-mcp-server",
    docs_url="https://docs.github.com/copilot/using-github-copilot/using-the-github-mcp-server",
    category="scm",
)


# ---------------------------------------------------------------------------
# Registry — a dict so callers can ``CATALOG[id]`` without scanning.
# ---------------------------------------------------------------------------
CATALOG: dict[str, McpServerTemplate] = {
    template.id: template
    for template in (
        # Roadmap minimum (task_05_08..task_05_11)
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
        # Stretch templates beyond the roadmap (user-requested
        # "catalogo amplio").
        CONFLUENCE_MCP,
        NOTION_MCP,
        BITBUCKET_MCP,
        MSTEAMS_MCP,
        DISCORD_MCP,
        SENTRY_MCP,
        GRAFANA_MCP,
        BRAVE_SEARCH_MCP,
        TAVILY_MCP,
        PUPPETEER_BROWSER_MCP,
        MEMORY_MCP,
        SEQUENTIAL_THINKING_MCP,
        # HTTP templates (ADR 0117) — the only ones OFFERED in the picker.
        CONTEXT7_MCP,
        ATLASSIAN_MCP,
        GITHUB_REMOTE_MCP,
        # OAuth remote (ADR 0127) — catalogued but WITHHELD from the picker
        # until the interactive consent flow is verified against the live
        # provider (see mcp_catalog._UNAVAILABLE_TEMPLATE_IDS).
        ATLASSIAN_REMOTE_MCP,
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
    "ATLASSIAN_MCP",
    "ATLASSIAN_REMOTE_MCP",
    "AZURE_DEVOPS_MCP",
    "BITBUCKET_MCP",
    "BRAVE_SEARCH_MCP",
    "CATALOG",
    "CONFLUENCE_MCP",
    "CONTEXT7_MCP",
    "GITHUB_REMOTE_MCP",
    "DISCORD_MCP",
    "DOCLING_MCP",
    "FILESYSTEM_MCP",
    "GCALENDAR_MCP",
    "GDRIVE_MCP",
    "GITHUB_MCP",
    "GITLAB_MCP",
    "GMAIL_MCP",
    "GRAFANA_MCP",
    "JIRA_MCP",
    "LINEAR_MCP",
    "MEMORY_MCP",
    "MSTEAMS_MCP",
    "McpServerTemplate",
    "NOTION_MCP",
    "POSTGRES_MCP",
    "PUPPETEER_BROWSER_MCP",
    "SENTRY_MCP",
    "SEQUENTIAL_THINKING_MCP",
    "SLACK_MCP",
    "TAVILY_MCP",
    "Transport",
    "render_vault_path",
]
