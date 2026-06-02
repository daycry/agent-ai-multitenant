---
plan_id: docs-comprehensive-update
title: Actualización integral de la documentación (capa transversal, diagramas, gotchas, coherencia)
completed_at: null
docs_language: es
---

# Plan docs-comprehensive-update — Documentación integral al día

## Resumen

Plan **documental** (el último del backlog; no produce features ni código). Cada
plan previo ya dejó sus docs por-plan al día (47 ADRs, 37 changelogs, 20 guías,
17 runbooks); lo que faltaba era la **capa transversal/holística**: los docs de
`docs/context/` estaban desfasados (apenas mencionaban human agents, proveedores
LLM, marketplace, guardrails, presupuestos/FX, webhooks, evals, comandos por
proyecto…), faltaban **diagramas Mermaid** del sistema final, había **gotchas
sin documentar** surgidos en esta tanda, y la coherencia + cross-links entre las
7 carpetas necesitaba una pasada.

> **Holístico, no redundante (GUARDRAIL DURO).** No se reescriben los
> changelogs/ADRs/guías/runbooks por-plan ya existentes (están al día; solo se
> cross-linkan). No se toca código (el visor `/admin/docs` lee la carpeta `docs/`
> directamente, así que recoge los cambios sin tocar el frontend). Fiel al
> código/ADRs: no se inventan features.

## Cambios por tarea

### Fase A — Context transversal + diagramas

- ✅ **`task_doc_01`** — **Reescribir el context transversal + diagramas
  Mermaid.** `docs/context/`:
  - **`architecture-overview.md`** reescrito como visión **end-to-end del
    sistema final**: topología de contenedores (ingress, frontends, plano de
    control, workers Celery, runtimes efímeros, datos, observabilidad, Vault),
    multi-tenancy + RLS (`app_user` NOBYPASSRLS vs migraciones/admin BYPASSRLS,
    `set_config('app.tenant_id', …)`), agentes IA **y humanos** (`agent_type`,
    `human_agent_config`, `HumanWorkSession`, review modes), teams, proyectos
    (`allowed_commands` + `default_runtime_template`), Plan=DAG (rama `plan/`,
    trailers), orquestador + workers, aislamiento por contenedor
    (seccomp/AppArmor trusted-vs-untrusted), runtime templates políglotas,
    tools (builtin + custom + MCP + `shell_exec`/`run_command` con allowlist),
    KB/RAG (pgvector, Docling), memoria por scopes + memorizer, chat/planning,
    proveedores LLM (Claude SDK / Copilot / Azure Foundry APIM / Ollama),
    guardrails, precios + budgets/FX + snapshot por llamada, marketplace,
    SSO/MFA, backup/restore, API pública + webhooks, evals + stats,
    notificaciones + asistente personal, visor de docs, instalador. Con
    **diagramas Mermaid**: topología de contenedores, flujo de un plan
    (chat→planning→DAG→ejecución→tests→PR), y aislamiento multi-tenant (RLS).
  - **`glossary.md`** ampliado con los términos nuevos: Human Agent,
    `agent_type`, HumanWorkSession, HumanTaskAssignment, review modes
    (`auto_approve`/`peer_human_reviewer`), coste humano, `llm_providers`,
    `allowed_commands`, runtime template, marketplace listing / trust tier,
    budget/FX, snapshot de coste por llamada, guardrails (capas + eventos),
    webhooks, evals, etc.
  - **`tech-stack.md`** y **`conventions.md`** revisados y puestos al día
    (proveedores LLM ADR 0021/0028, LiteLLM solo como feed de precios, seccomp/
    AppArmor, prettier scoped en Windows, trailers de commit).
  - Cross-links a los ADRs/guías/referencia relevantes (enlaces relativos).

### Fase B — Overview + referencia

- ✅ **`task_doc_02`** — **01-overview + 04-reference al día + cross-links.**
  - **`docs/01-overview/`** (`01-introduction.md`, `02-architecture.md`,
    `README.md`): el overview de producto lista **todas las capacidades** del
    sistema final (incl. human agents, proveedores LLM platform-global,
    marketplace, guardrails, budgets/FX, webhooks, evals, comandos/runtime por
    proyecto).
  - **`docs/04-reference/`** (`domain-model.md`, `rbac.md`, `README.md`):
    la referencia refleja lo construido — entidades nuevas (human agents,
    `llm_providers`, `allowed_commands`/runtime, marketplace, presupuestos/FX),
    la matriz RBAC con la superficie platform-global del System Admin, e índices
    sin enlaces rotos.

### Fase C — Gotchas + coherencia

- ✅ **`task_doc_03`** — **Gotchas nuevos + índices de carpeta + visor.**
  Añadidos a `docs/03-guides/gotchas/` (síntoma + causa raíz + fix + cómo
  verificar, sin duplicar), con su `README.md`/índice actualizado:
  - [`prettier-all-files-libuv-windows.md`](../03-guides/gotchas/prettier-all-files-libuv-windows.md)
    — `prettier --all-files` crashea en Windows por libuv (`UV_HANDLE_CLOSING`,
    exit `3221226505`); usar prettier **scoped** (`--files <cambiados>`).
  - [`alembic-revision-id-32-chars.md`](../03-guides/gotchas/alembic-revision-id-32-chars.md)
    — `alembic_version.version_num` es `varchar(32)`: un revision id > 32 chars
    revienta con `StringDataRightTruncationError`.
  - [`minio-dev-volume-xl-meta-version.md`](../03-guides/gotchas/minio-dev-volume-xl-meta-version.md)
    — `decodeXLHeaders: Unknown xl meta version N`: el volumen dev lo escribió
    una build de MinIO más nueva que el pin; recrear el volumen dev o subir el
    pin. Revisados los READMEs/índices de las 7 carpetas (sin enlaces rotos);
    confirmado que el visor `/admin/docs` recoge categorías y docs nuevos (lee
    la carpeta `docs/`, no hay índice estático que mantener).

### Fase D — Changelog + verificación final

- ✅ **`task_doc_04`** — **Changelog + verificación final de enlaces** (esta
  entrada). Creado `docs/07-changelog/docs-comprehensive-update.md`; añadida la
  fila del plan a `docs/roadmap/README.md` (sección de planes documentales).
  **Verificación global de enlaces internos** de `docs/**/*.md`: todos los
  enlaces relativos `.md`/de carpeta resuelven a un archivo existente. Prettier
  scoped (`pre_commit run prettier --files <docs tocados>`) en verde.

## Diagramas Mermaid añadidos

| Diagrama                  | Dónde                                   | Qué muestra                                                                              |
| ------------------------- | --------------------------------------- | ---------------------------------------------------------------------------------------- |
| Topología de contenedores | `docs/context/architecture-overview.md` | Ingress, frontends, plano de control, workers Celery, runtimes efímeros, datos, Vault.   |
| Flujo de un plan          | `docs/context/architecture-overview.md` | chat → planning → DAG de tareas → ejecución (IA/humano) → tests → PR de la rama `plan/`. |
| Aislamiento multi-tenant  | `docs/context/architecture-overview.md` | RLS por `tenant_id`, `app_user` NOBYPASSRLS vs migraciones/admin BYPASSRLS.              |

(El resto de carpetas mantiene sus diagramas por-plan ya existentes; este plan
no los reescribe.)

## Migraciones

**Ninguna.** Plan puramente documental; no toca esquema, backend ni frontend.
La cabeza única de Alembic sigue siendo `0075`.

## Verificación

- **Enlaces internos** de todos los `.md` bajo `docs/`: verificados todos los
  enlaces relativos (`.md` y de carpeta) — **0 rotos** en la pasada completa.
- **Estructura:** `docs/07-changelog/docs-comprehensive-update.md` existe; fila
  del plan presente en `docs/roadmap/README.md`.
- **Diagramas Mermaid** presentes en `docs/context/architecture-overview.md`
  (bloques ` ```mermaid `).
- `pre-commit` **prettier scoped** (`--files <docs tocados>`) ✅ en cada commit
  (sin `--no-verify`; el hook repo-wide `--all-files` crashea en Windows por
  libuv — ver la gotcha correspondiente —, por eso se acota siempre a los
  ficheros cambiados).

## Pendiente

- **Tests humanos del plan** (`human_doc_01`) — pendientes de ejecutar por un
  humano: que `architecture-overview` describa todos los subsistemas con
  diagramas Mermaid que renderizan; que el glosario y la referencia
  (domain-model/rbac) reflejen lo construido; que los gotchas nuevos estén
  documentados; que el visor `/admin/docs` muestre las categorías/docs
  actualizados; que no haya enlaces internos rotos en `docs/`.
- **Merge del PR de `plan/docs-comprehensive-update` a `main`** — lo gestiona el
  humano tras los tests humanos. El plan no se marca `completed` aquí.

## PR

Pendiente de apertura/merge a `main` (lo gestiona el humano tras validar los
tests humanos del plan).
