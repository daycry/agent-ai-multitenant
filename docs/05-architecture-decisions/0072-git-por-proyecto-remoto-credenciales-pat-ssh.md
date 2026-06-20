---
adr_id: "0072"
title: "Configuración git por proyecto: remoto + credenciales (PAT/SSH) + clone autenticado"
status: accepted
date: 2026-06-19
authors: [system_architect]
plan_referenced: prod-git-integration
docs_language: es
extends: ["0028"]
---

# ADR 0072 — Git por proyecto: remoto + credenciales (PAT/SSH) + clone autenticado

> **Estado: `accepted`** (delegación autónoma del operador, 2026-06-19). v1:
> **configurar el remoto + credenciales (PAT/SSH) y clonar/push autenticado**.
> **Fase 2 (auto-PR por proveedor) implementada** (opener GitHub/GitLab/Azure +
> push autenticado + task `open_plan_pr`); solo falta su disparo automático al
> cerrar plan, que depende del pipeline de ejecución-git (sección al final).

## Contexto (estado actual — analizado)

- Los repos son **bare locales** por proyecto + worktrees por tarea
  (`workers/git_repos.py`). `ensure_repo(remote_url)` guarda `origin`;
  `fetch_remote`/`push_branch_to_remote` operan contra él.
- **Bloqueantes reales encontrados:**
  1. **`git` NO está instalado** en las imágenes `workers` ni `agent-runtime`
     (`git: not found`). Ninguna operación git funciona en el stack desplegado.
  2. **No hay credenciales**: `_run_git` solo pone `GIT_TERMINAL_PROMPT=0`; no
     inyecta GIT_ASKPASS/SSH/token. No resuelve nada de Vault. → no se puede
     autenticar contra GitHub/GitLab/Azure DevOps.
  3. **No hay config de git en el proyecto**: `repository_config` es JSONB
     libre de **metadatos** (language/framework…), no provider/url/credencial; la
     UI no lo expone.

Sin esto, worktrees/commits/push "no sirven de nada" (no salen del disco local).

## Decisión (v1)

### 1) `git` + `openssh-client` en las imágenes que ejecutan git

`workers` (corre `git_repos`) y `agent-runtime` (los agentes commitean en
worktrees) instalan `git` y `openssh-client` en su stage runtime.

### 2) Config de git tipada por proyecto

Columna nueva **`projects.git_config`** (JSONB nullable) con forma validada
(`GitConfig`): `provider` (`github`|`gitlab`|`azure_devops`|`generic`),
`remote_url`, `default_branch` (def. `main`), `auth_mode` (`none`|`pat`|`ssh`).
**El secreto NO se guarda en la BD** — solo la config + un flag `has_credential`.

### 3) Credencial en Vault (no en BD)

El PAT (o la clave SSH privada) se escribe en **Vault** en un path por proyecto
(`projects/{tenant}/{project}/git`), espejo de `LLMProviderVaultStore` (ADR 0028:
secretos solo en Vault). Para PAT se guarda `{username, token}`; para SSH, la
clave privada. Endpoint `PUT /projects/{id}/git` fija config + (opcional) secreto.

### 4) Inyección de credenciales en `_run_git`

Helper `build_git_auth_env(auth_mode, secret) -> (env, cleanup)`:

- **PAT (HTTPS)**: script `GIT_ASKPASS` temporal que devuelve usuario/token desde
  `GIT_USERNAME`/`GIT_PASSWORD` (el token NO se persiste en `.git/config` ni en la
  URL; `origin` queda limpio).
- **SSH**: clave privada a fichero temporal `0600` + `GIT_SSH_COMMAND="ssh -i …
-o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"`.
- `cleanup()` borra los temporales tras la operación.

`fetch_remote`/`push_branch_to_remote`/`ensure_repo` aceptan un `auth_env` y lo
pasan a `_run_git` vía el `env_extra` ya existente.

### 5) Clone al configurar (y al crear el proyecto)

Task Celery **`clone_project_repo(project_id)`**: resuelve `git_config` + el
secreto de Vault, `ensure_repo(remote_url)` + `fetch_remote` con auth. Se
**encola** cuando se fija/actualiza la config git con remoto (y al crear el
proyecto si ya trae git), y desde una acción manual "Sincronizar". Idempotente.

### 6) UI

Formulario "Repositorio Git" en (a) el **wizard de creación** (plantilla **y**
blanco) y (b) la **ficha del proyecto** (Editar): provider, URL, rama, modo de
auth (PAT/SSH) + credencial. Al guardar: `PUT /projects/{id}/git` → config +
secreto en Vault → encola el clone.

## Seguridad

- Secreto SOLO en Vault (nunca en BD ni en respuestas API; la respuesta expone
  `auth_mode` + `has_credential`, jamás el token/clave).
- Token NUNCA persistido en `.git/config`/URL del remoto (ASKPASS efímero).
- `StrictHostKeyChecking=accept-new` (TOFU) para SSH; sin prompts (`GIT_TERMINAL_PROMPT=0`).
- Por-tenant: cada proyecto resuelve su propio path de Vault; aislamiento RLS en la config.

## Proveedores

**Agnóstico por URL** para clone/fetch/push (cualquier remoto git autenticable
con PAT-HTTPS o SSH). `provider` se guarda para (a) elegir el username por
defecto del PAT (p.ej. `x-access-token` en GitHub, `oauth2` en GitLab) y (b) la
API de PR en fase 2.

## Alternativas

- **Token en la URL del remoto**: rechazada — se persiste en `.git/config` en
  claro y aparece en `ps`. ASKPASS efímero es más seguro.
- **`repository_config` para git**: rechazada — es metadatos; mezclar ensucia.
  `git_config` dedicado y tipado.
- **Credencial en BD**: rechazada (ADR 0028: secretos solo en Vault).

## Consecuencias

- **+** Se puede apuntar un proyecto a GitHub/GitLab/Azure DevOps/self-hosted y
  **clonar/pushear autenticado** (PAT o SSH), configurándolo al crear o editar.
- **+** Imágenes con git/ssh → las operaciones git por fin funcionan en el stack.
- **−** Más superficie (Vault path por proyecto, task de clone). Mitigado con la
  forma tipada + tests.

## Tests (v1)

`build_git_auth_env` (PAT→ASKPASS con user/token; SSH→keyfile 0600 + GIT_SSH_COMMAND;
cleanup borra temporales); validación de `GitConfig`; `PUT /projects/{id}/git`
(config + secreto a Vault, nunca devuelve el secreto); la task `clone_project_repo`
resuelve cred + llama ensure/fetch (con fakes).

## Fase 2 — Auto-PR por proveedor (IMPLEMENTADO)

- **`workers/pr_openers.py`**: `open_pull_request(provider, remote_url, token,
head, base, title, body)` despachado por proveedor — GitHub REST `/pulls`
  (Bearer; api.github.com o `/api/v3` en GHE), GitLab `/merge_requests`
  (PRIVATE-TOKEN; path URL-encoded), Azure DevOps `/pullrequests` (Basic). Parseo
  de owner/repo/host desde URL https o ssh. Tests con transporte mockeado.
- **Push autenticado**: `PlanGitWorkflow` acepta `auth_env` y lo aplica en
  `push_branch_to_remote` (la rama del plan sube al remoto con PAT/SSH).
- **Task `workers.open_plan_pr(project_id, plan_branch, title, body)`**: resuelve
  `git_config` + el PAT de Vault, construye el opener + el `auth_env`, fuerza el
  push y abre el PR/MR vía `PlanGitWorkflow.open_plan_pr`. Best-effort.
  `enqueue_open_plan_pr` en el api-server. El PR/MR requiere PAT (la API REST no
  va por SSH); con SSH se hace el push pero no la apertura del PR.

**Pendiente (no de git-config)**: el DISPARO automático al cerrar un plan
(encolar `open_plan_pr`) y, antes, conectar la ejecución real (agent-runtime/
LangGraph) al pipeline bare→worktree→commit→push — hoy ese pipeline es andamiaje
(solo lo ejercita el driver de demo `plan_runner`, no la ejecución productiva).
Esa integración es un plan de roadmap propio; cuando exista, solo tiene que
llamar a `enqueue_open_plan_pr` al cierre del plan.
