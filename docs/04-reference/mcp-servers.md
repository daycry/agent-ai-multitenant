---
title: Catálogo de MCP servers verificados (Plan 05)
audience: backend-dev, devops, technical-writer, system-admin
phase: 05-mcp-tools-avanzadas
updated: 2026-05-28
---

# Catálogo de MCP servers verificados

Esta página es la referencia humana del catálogo que vive en código en
[`packages/shared-mcp/src/shared_mcp/catalog.py`](../../packages/shared-mcp/src/shared_mcp/catalog.py).
Cada entrada del catálogo es una plantilla pre-cocinada — el operador del
proyecto, en `/admin/projects/{id}/mcp-servers`, la elige del picker y el
formulario se rellena con el transport correcto, el comando o URL, y el
shape del `auth_ref` apuntando a Vault.

> Convención: el campo `auth_ref` es siempre un puntero `vault:...` (CLAUDE.md
> regla dura). Los tokens nunca viajan en JSON ni se quedan en la BD; Vault
> es el único almacén de credenciales del platform.

---

## Cómo se valida el catálogo

Cada plantilla es un `McpServerTemplate` (dataclass frozen). El dataclass
fuerza los invariantes transport-specific (stdio requiere `command`, http
requiere `url`, sin cruces). Los tests
[`tests/integration/test_github_mcp.py`](../../tests/integration/test_github_mcp.py),
[`tests/integration/test_postgres_mcp.py`](../../tests/integration/test_postgres_mcp.py)
y [`tests/integration/test_mcp_integrations.py`](../../tests/integration/test_mcp_integrations.py)
recorren las entradas, las pasan por el validador Pydantic de
`MCPServerConfigModel` (task_05_04) y simulan la inyección Vault con
`StaticVaultResolver` (task_05_05) — si una plantilla deriva del esquema
del cliente MCP, CI lo coge antes que el operador.

---

## Plantillas por categoría

### Documentación (`category=docs`)

#### `docling-mcp`

Servidor MCP que envuelve [docling](https://github.com/docling-project/docling)
(IBM Research). Expone parsing de PDF/DOCX/HTML con citas que mantienen las
bounding boxes — la misma capa que la KB ingestion del Plan 04 usa
internamente, ahora invocable por agentes vía tool.

| Campo               | Valor                                          |
| ------------------- | ---------------------------------------------- |
| `transport`         | `stdio`                                        |
| `command`           | `docling-mcp`                                  |
| Vault path          | _(sin auth)_                                   |
| `default_timeout_s` | `120.0` (OCR es lento)                         |
| Maintainer          | IBM Research                                   |
| Repo                | https://github.com/docling-project/docling-mcp |

---

### Source control management (`category=scm`)

#### `github-mcp`

Servidor MCP oficial mantenido por GitHub. Cubre la API REST + GraphQL:
búsqueda de repos, issues, PRs, runs de workflow, comentarios. El PAT
necesita scopes según las tools que active el operador (mínimo `repo` para
lectura de issues, `workflow` para CI).

| Campo            | Valor                                                            |
| ---------------- | ---------------------------------------------------------------- |
| `transport`      | `stdio`                                                          |
| `command`        | `github-mcp`                                                     |
| Vault keys       | `GITHUB_TOKEN`                                                   |
| Vault path shape | `vault:secret/data/mcp/github/{project_id}`                      |
| Static env       | `GITHUB_HOST=https://api.github.com` (sobreescribible para GHES) |

#### `gitlab-mcp`

Mismas primitivas que github-mcp para GitLab. Funciona con SaaS y con
self-hosted (override `GITLAB_URL`).

| Campo            | Valor                                       |
| ---------------- | ------------------------------------------- |
| `transport`      | `stdio`                                     |
| `command`        | `mcp-gitlab`                                |
| Vault keys       | `GITLAB_PERSONAL_ACCESS_TOKEN`              |
| Vault path shape | `vault:secret/data/mcp/gitlab/{project_id}` |
| Static env       | `GITLAB_URL=https://gitlab.com`             |

#### `azure-devops-mcp`

Work items, repos, PRs y pipelines de Azure DevOps. El PAT necesita
scopes Code (read) + Work Items (read/write) como mínimo.

| Campo            | Valor                                             |
| ---------------- | ------------------------------------------------- |
| `transport`      | `stdio`                                           |
| `command`        | `mcp-azure-devops`                                |
| Vault keys       | `AZURE_DEVOPS_PAT`                                |
| Vault path shape | `vault:secret/data/mcp/azure-devops/{project_id}` |
| Static env       | `AZURE_DEVOPS_ORG=` _(rellenar por proyecto)_     |

---

### Bases de datos (`category=data`)

#### `postgres-mcp`

Queries SQL **read-only** contra una BD Postgres. El connection string
viaja en el secret de Vault como `DATABASE_URI`; el agente nunca lo ve en
claro, sólo la superficie de tools (`query`, `list_tables`, `describe_table`).

| Campo            | Valor                                         |
| ---------------- | --------------------------------------------- |
| `transport`      | `stdio`                                       |
| `command`        | `mcp-postgres`                                |
| Vault keys       | `DATABASE_URI`                                |
| Vault path shape | `vault:secret/data/mcp/postgres/{project_id}` |

---

### Ficheros (`category=files`)

#### `filesystem-mcp`

Lee y escribe ficheros dentro de un directorio bind-mounted. El env var
`ALLOWED_DIRS` actúa como allowlist server-side: paths fuera de la lista
devuelven tool error. _No usa Vault_ (sólo config estática por proyecto).

#### `gdrive-mcp`

Busca y lee ficheros de Google Drive. OAuth: el secret de Vault carga
`access_token` + `refresh_token`.

---

### Comunicación (`category=comms`)

#### `gmail-mcp`

Leer, buscar, redactar y enviar Gmail. La tool `send` es la más
arriesgada de toda la categoría — combinar con guardrails estrictos.

#### `gcalendar-mcp`

Eventos de Google Calendar (lectura + creación). Mismo shape OAuth que
gdrive-mcp / gmail-mcp.

#### `slack-mcp`

Postear mensajes, leer canales, search en historial. Bot token
(`xoxb-...`) con scopes mínimos `chat:write`, `channels:read`, y
`channels:history` para la superficie completa.

---

### Issues / tracking (`category=issues`)

#### `jira-mcp`

Search, read, comment y transition de issues Jira. Auth Atlassian: el
token de API + el email del user actúan como basic auth (`email:token`);
ambos viven en Vault.

#### `linear-mcp`

Equivalente a jira-mcp para Linear. Una sola API key. Pickear uno u otro
según tracker del equipo — no tiene sentido tener los dos activos en el
mismo proyecto.

---

## Cómo añadir una plantilla nueva

1. Editar `packages/shared-mcp/src/shared_mcp/catalog.py`, añadir un
   `McpServerTemplate(id=..., display_name=..., ...)` y meterlo en el
   tuple de `CATALOG`.
2. Documentar la entrada en este mismo fichero, en la sección de
   categoría que corresponda.
3. Si introduces una categoría nueva, añadirla al picker UI
   (`apps/admin-panel/.../mcp-servers/page.tsx`).
4. Los tests de Fase C iteran sobre `CATALOG`, así que validan tu
   entrada sin tocar el código de tests.
5. Si el servidor cambia su shape de auth (un nuevo header, una API
   diferente), bump `vault_path_template` con el formato nuevo y abre un
   ADR documentando la migración.

## Cómo se consume el catálogo

- **UI admin-panel** (task_05_06 / task_05_07): el picker "Añadir desde
  catálogo" muestra `display_name` agrupado por `category`. Al elegir,
  el form se rellena con `command`/`url`, los placeholders de `env`
  para los `secret_keys`, y `auth_ref` con `vault_path_template`
  renderizado para el `project_id` actual.
- **Tests**: `tests/integration/test_*_mcp.py` validan que cada entrada
  pasa el validador Pydantic y produce un runtime config bien formado.
- **Agent-runtime** (task_05_15, panel diagnóstico): cuando el agente
  loop abre una sesión, puede cross-checkear el `command`/`url` del
  proyecto contra el catálogo y rechazar conexiones cuyo template fue
  removido (security advisory upstream, deprecation).

## Próximas plantillas (extensión post-task_05_11)

El catálogo está pensado para crecer. Candidatos que ya se han
identificado pero no entran todavía:

- **Atlassian Confluence** — wiki + páginas.
- **Notion** — alternativa a Confluence + project mgmt.
- **Bitbucket** — completa la familia SCM.
- **Microsoft Teams / Outlook / OneDrive** — paridad con la suite Google.
- **Sentry / Grafana / Datadog** — observability.
- **Brave Search / Tavily / Perplexity / Puppeteer-browser** — web + search.
- **Redis / SQLite** — bases de datos extra.
- **mcp-memory / sequential-thinking** — meta-tools para el agente.

Cada una entra cuando un proyecto real la pida — añadirla son ~10 líneas
de `McpServerTemplate` + una sección aquí. Ver "Cómo añadir una plantilla
nueva" arriba.
