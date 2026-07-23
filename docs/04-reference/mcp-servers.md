---
title: Catálogo de MCP servers verificados (Plan 05)
audience: backend-dev, devops, technical-writer, system-admin
phase: 05-mcp-tools-avanzadas
updated: 2026-07-23
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

## Qué se OFRECE en el picker (ADR 0117)

> **Importante.** El agent-runtime **no empaqueta binarios stdio**, así que una
> plantilla `stdio` (`command=...`) **nunca arrancaría** (el agente daría vueltas
> buscando el binario y escalaría a un humano). Por eso el picker
> (`GET /mcp-catalog` → `offered_catalog()`) **ofrece SOLO plantillas de transporte
> HTTP** (`streamable_http`/`sse`). Todas las plantillas `stdio` históricas siguen
> en `CATALOG` (validación en tiempo de ejecución + auditoría) pero **están ocultas**
> del picker. Regla de oro: **usa siempre servers HTTP** — remotos o sidecars.

Las tres plantillas **ofrecibles** hoy:

| id              | transporte        | URL                                  | auth                                    | notas                                                                                                                        |
| --------------- | ----------------- | ------------------------------------ | --------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `context7`      | `streamable_http` | `https://mcp.context7.com/mcp`       | opcional (key en cabecera)              | docs de librerías al día. Abrir `mcp.context7.com` en dominios permitidos (egress).                                          |
| `atlassian`     | `streamable_http` | `http://mcp-atlassian:9000/mcp`      | en el ENV del sidecar                   | Jira+Confluence en un sidecar (`ghcr.io/sooperset/mcp-atlassian`) en la red `agentic-agents`; hostname interno → sin egress. |
| `github-remote` | `streamable_http` | `https://api.githubcopilot.com/mcp/` | PAT en cabecera `Authorization` (Vault) | MCP remoto oficial de GitHub. Abrir `api.githubcopilot.com` en dominios permitidos (egress).                                 |

Sustituyen a las plantillas stdio equivalentes (jira-mcp/confluence-mcp → `atlassian`; github-mcp → `github-remote`), que quedan ocultas.

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

## Plantillas extendidas (stretch)

Añadidas al catálogo como ampliación más allá del mínimo del roadmap
(task_05_11 stretch). Mismo shape de validación que el set anterior.

### Documentación (`category=docs`) — extendido

#### `confluence-mcp`

Páginas de Confluence: leer, escribir, buscar, comentar. Auth Atlassian
basic (igual que `jira-mcp`): API token + email + URL de la instancia,
los tres en Vault.

| Campo            | Valor                                                                 |
| ---------------- | --------------------------------------------------------------------- |
| `transport`      | `stdio`                                                               |
| `command`        | `mcp-confluence`                                                      |
| Vault keys       | `CONFLUENCE_API_TOKEN`, `CONFLUENCE_EMAIL`, `CONFLUENCE_INSTANCE_URL` |
| Vault path shape | `vault:secret/data/mcp/confluence/{project_id}`                       |

#### `notion-mcp`

Páginas + bases de datos de Notion. Token único (`NOTION_API_KEY`).
Alternativa a Confluence para equipos en Notion.

### SCM (`category=scm`) — extendido

#### `bitbucket-mcp`

Repos + PRs + pipelines de Bitbucket Cloud. Auth basic con el email +
una **app password** (no la contraseña de cuenta — Bitbucket las
deprecó).

| Campo      | Valor                                          |
| ---------- | ---------------------------------------------- |
| Vault keys | `BITBUCKET_USERNAME`, `BITBUCKET_APP_PASSWORD` |

### Comunicación (`category=comms`) — extendido

#### `ms-teams-mcp`

Lee + postea mensajes en canales Teams. OAuth Microsoft Graph: access +
refresh token + tenant id, los tres en Vault.

#### `discord-mcp`

Mensajes + roles en servidores Discord. Bot token. El bot debe estar
invitado al guild con los intents correctos.

### Observabilidad (`category=observability`)

#### `sentry-mcp`

Issues, events y releases de Sentry. Útil para agentes de triage de
errores en producción. Auth token (`sntrys_...`) + org slug.

#### `grafana-mcp`

Dashboards + Loki/Tempo/Prometheus a través de Grafana. Service-account
API key + URL (self-hosted o Grafana Cloud).

### Search (`category=search`)

#### `brave-search-mcp`

Búsqueda web vía la API de Brave. Útil como tool genérica "ve a buscar
esto" para el agente. Pin un budget de uso en los guardrails del
proyecto para evitar sorpresas — Brave es de pago por encima del free
tier.

#### `tavily-mcp`

Búsqueda web optimizada para IA: devuelve pasajes ranqueados en lugar
de URLs crudas. Mejor que Brave para "buscar + resumir" estilo RAG. API
key única.

### Browser (`category=browser`)

#### `puppeteer-browser-mcp`

Chrome headless vía Puppeteer: navegar URLs, click, type, extraer DOM,
screenshots. _No usa Vault_ — el sandbox del subprocess es la frontera
de seguridad. Es la tool **más pesada** del catálogo (60s de timeout
por defecto); restringir a proyectos con allowlist explícito.

### Meta-tools del agente (`category=meta`)

#### `memory-mcp`

Memoria clave/valor que el agente escribe y lee entre sesiones.
Persiste a un fichero SQLite local dentro del worker. **Distinto** de
la memoria RAG del Plan 04 — esa es KB estructurada con embeddings;
ésta son notas cortas que el agente se guarda a sí mismo.

#### `sequential-thinking-mcp`

Meta-tool que expone `think` como una tool: el agente loguea pasos
intermedios de razonamiento que llamadas posteriores pueden referenciar.
Mejora planning de horizonte largo en modelos sin chain-of-thought
nativo. Sin auth, sin API externa.

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

## Próximas plantillas (candidatos no integrados aún)

El catálogo está pensado para crecer. Candidatos identificados que
aún no entran:

- **OneDrive / Outlook / SharePoint** — paridad de Microsoft con la
  suite Google ya cubierta (Drive/Gmail/Calendar).
- **Datadog / Loki** — observabilidad alternativa a Sentry/Grafana.
- **Redis / SQLite** — bases de datos extra.
- **Perplexity** — alternativa a Tavily / Brave para search.
- **Stripe / HubSpot / Salesforce** — el dominio de "business systems".

Añadir una son ~10 líneas de `McpServerTemplate` + una sección aquí.
Ver "Cómo añadir una plantilla nueva" arriba.
