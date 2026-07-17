---
title: Auditoría integral del dominio Proyecto (2026-07-17)
version: 1.0
audit_date: 2026-07-17
last_updated: 2026-07-17
status: published
created_by: claude-fable-5-audit-2026-07-17
docs_language: es
baseline_branch: plan/runs-visor-trabajo
baseline_commit: a8a40714
scope: proyecto-integral (ciclo de vida, config, planes/tareas/board, git/workspace, conocimiento, equipo/tools/MCP, UI, API pública)
---

# Auditoría integral del dominio Proyecto (2026-07-17)

Auditoría read-only de TODO lo que engloba un Proyecto en la plataforma,
ordenada por el operador («qué está implementado y qué se puede mejorar —
perfección, fiabilidad y robustez»). Cuatro frentes en paralelo sobre código
(HEAD `a8a40714`) + BD/stack VIVOS, excluyendo lo que ya tiene dueño
(AUD14 → plan 07-14; AUD16 remediada y verificada; N-01…N-17; prod-08/09/14;
gated). 42 hallazgos nuevos: 11 de ciclo de vida/config (P1-xx), 14 de
planes/tareas/board (PROY2-xx), 9 de git/conocimiento (G-xx) y 8 de
equipo/tools/UI/API (PROJ-xx).

## Veredicto ejecutivo

1. **El pilar de CONOCIMIENTO está muerto en el desplegado (G-01/G-02,
   crítico).** El cliente de ingesta llama a `POST /v1/convert`, ruta que no
   existe en el docling-serve vivo (404): el hot-fix del 2026-06-25 nunca se
   comiteó y un rebuild posterior lo borró — TODO upload futuro fallará.
   Además `knowledge_bases` tiene 0 filas (el seed de ~14 KBs builtin es CLI
   manual y no se re-corrió tras el reset): el auto-RAG está cableado pero
   estéril — 0 `rag_search` en 128 executions. Lección transversal: hot-fix
   sin commit = regresión garantizada.

2. **El ciclo de vida de PLANES tiene una puerta lateral (PROY2-01/02,
   alta).** `POST /plans` acepta nacer en cualquier estado y `PUT /plans/{id}`
   permite a cualquier `tenant_member` aprobar sin rol ni doble firma, entrar
   en validación con tareas sin hacer, o completar sin veredicto humano
   (saltándose el auto-PR). Es el equivalente plan-level del c1 que ya se
   cerró para tasks — y el plan demo varado es evidencia viva del mecanismo.

3. **Controles de proyecto que mienten (P1-01/P1-02, alta).** Pausar o
   archivar un proyecto no detiene NADA (ningún camino de ejecución consulta
   `status`); y el slug no es único por tenant — dos proyectos homónimos
   compartirían bare repo y worktrees (corrupción git silenciosa), y recrear
   un proyecto con el nombre de uno borrado hereda su historia.

4. **El camino por defecto crea proyectos inertes (PROJ-01/P1-05, alta).**
   El wizard adopta plantillas con `fork_team=false` → equipo builtin linked
   que el dispatch nunca puede usar (filtra por tenant): planes `ready` para
   siempre con solo un WARNING en logs. Y la adopción (client-side) no
   transfiere `allowed_commands`/runtime: los proyectos CI4 nacen con su
   toolchain deny-all.

5. **MCP por proyecto es una fachada completa (PROJ-02).** Los 24 templates
   del catálogo son stdio con binarios que no existen en ninguna imagen, el
   token Vault del runtime no lo fija nadie, y no hay camino para escribir el
   secreto: picker→probar→credencial→runtime, los 4 eslabones rotos (0 usos
   en BD, como cabría esperar). Decidir por ADR: empaquetar o retirar de la UI.

6. **Mucho VERDE verificado**: la cadena auto-PR funciona de punta a punta en
   vivo (PR #1 real persistido; los ledgers de cadena-pr-plan y
   tools-y-cierre están DESACTUALIZADOS — varios items ya hechos), trailers
   25/25, doble-dispatch defendido en 3 capas, WS del kanban sano, RLS y
   filtros de memoria por proyecto correctos y NULL-safe, visibilidad RAG con
   grants, API pública (plan 13) implementada y documentada 1:1, adopción de
   equipos con fork profundo versionado, cascada de borrado de proyecto
   completa, approval fail-closed con preset de plataforma.

---

## Frente 1 — Ciclo de vida y configuración del proyecto (P1-xx)

- **P1-01 · HIGH · S-M · broken** — `status` paused/archived es decorativo:
  la UI lo ofrece, `PUT /projects/{id}` lo persiste, y NINGÚN camino de
  ejecución lo consulta (dispatch/promotor/planes/tareas/planning solo miran
  `deleted_at`; grep `ProjectStatus.PAUSED|ARCHIVED` = solo el enum). Pausar
  no detiene gasto; archivar no cancela nada. Fix: guard `status == active`
  en dispatch/promoción/creación + al archivar reutilizar
  `cancel_tasks_and_executions`.
- **P1-02 · HIGH · S · risk** — Slug de proyecto sin unicidad por tenant
  (`slugify(name)` a secas, sin índice único) y el layout de disco es
  `/projects/{tenant_slug}/{project_slug}/repos`: homónimos comparten bare
  repo/worktrees; recrear un proyecto con nombre de uno soft-borrado hereda
  su historia (patrón `repo_history_lost` reincidente). Fix: sufijo `-{id8}`
  - índice único parcial.
- **P1-03 · MEDIUM · S-M · gap** — Settings APLICADOS pero inconfigurables:
  `execution_budgets` (clamp en dispatch, sin API/UI), `guardrails_config`
  proyecto (merge en worker, sin API/UI), `allowed_domains` (API sí, UI no —
  los 8 proyectos vivos con 0 dominios = tools HTTP deny-all sin decisión
  consciente), `human_task_review_mode`/`budget_*` sin UI de proyecto.
- **P1-04 · MEDIUM · S · gap** — Columnas MUERTAS que la API acepta y nadie
  lee: `rag_knowledge_bases` (lo real es `kb_projects`), `secrets_vault_id`,
  `worker_config.{max,min}_workers/cpu/ram` (sembradas por 9 plantillas y el
  wizard; solo `assignment_policy` y `git_policies` tienen lectores).
- **P1-05 · MEDIUM · S · gap** — Adopción de plantilla incompleta y
  client-side: el backend solo aplica `default_kb_grants`; equipo/worker_config
  /repository_config/approval policy los copia el NAVEGADOR (un consumidor API
  directo no recibe la forma de la plantilla) y `allowed_commands`/
  `default_runtime_template`/`allowed_domains`/`model_config` no se copian
  NUNCA. Evidencia viva: proyecto CI4 con runtime php-phpunit y
  `allowed_commands={}` → composer/phpunit deny-all.
- **P1-06 · MEDIUM · S-M · risk** — `POST /projects/{id}/tasks` acepta
  `plan_id` sin validar visibilidad/proyecto/tenant (el FK bypassea RLS) y
  `status` inicial arbitrario (nacer `ready`/`done` saltándose DAG y estado).
- **P1-07 · MEDIUM · S · risk** — `DELETE /plans` soft-borra sin cancelar:
  un plan `in_progress` borrado sigue siendo seleccionado por
  `promote_ready_plans` (no filtra `deleted_at`) y despacha invisible.
- **P1-08 · LOW · S · gap** — 5 proyectos vivos (+21 agentes, +2 equipos:
  G-04) apuntan a tenants inexistentes (reset del 06-29; sin FK a
  organizations). Purga + integridad referencial periódica.
- **P1-09 · LOW · S · broken(dato)** — La semilla demo crea el plan MVP
  directamente `pending_human_validation` con 4 tareas backlog (estado
  inalcanzable por la máquina real). Arreglar seed (+G-05).
- **P1-10 · LOW · S · risk** — `repository_config` mixto plataforma/cliente:
  un PUT del cliente pisa `last_git_sync`/`review_image`.
- **P1-11 · LOW · M · gap(diseño)** — Sin membresía por-proyecto (asumido);
  nota: policy RLS `projects_template_read` deja leer plantillas de OTRO
  tenant (hoy solo hay platform; la adopción sí lo corta).

**Mapa settings→enforcement** (resumen): aplicados y configurables OK =
`allowed_commands`, `default_runtime_template`, `human_approval_policy`,
`model_config`, `mcp_servers`, `git_config`, `assignment_policy`;
aplicados sin config = `guardrails_config`, `execution_budgets`,
`allowed_domains`(sin UI); configurables sin efecto = `status`,
`rag_knowledge_bases`, `secrets_vault_id`, `worker_config.recursos`.

## Frente 2 — Planes, tareas y board (PROY2-xx)

- **PROY2-01 · ALTA · S** — `POST /plans` acepta cualquier `status` inicial
  (nacer `approved`/`completed` esquivando approve/doble firma).
- **PROY2-02 · ALTA · S-M** — `PUT /plans/{id}` (solo `require_tenant_member`)
  permite: aprobar sin rol/umbral, `in_progress→pending_human_validation` sin
  tareas done (el seed lo exercita; el pipeline le arrancó un review-runtime),
  y `pending_human_validation→completed` sin veredicto y sin auto-PR. Fix:
  gates por rol + reusar las transiciones autoritativas en el PUT.
- **PROY2-03 · MEDIA · S** — `POST /tasks` con status inicial arbitrario y
  sin `assert_dependencies_done` (trigger solo ON UPDATE; el beat honra el
  done falso).
- **PROY2-04 · MEDIA · M** — Sin detección de ciclos en `task_dependencies`
  (solo self-loop): un ciclo = plan `in_progress` eterno SIN señal (la red
  c3/T7 exige tareas `blocked`; un ciclo es todo backlog). La vía
  generate/accept-corrections tampoco pasa `validate_dag`.
- **PROY2-05 · MEDIA · S** — Deps cross-plan legales pero el snapshot de
  bloqueo las trata como satisfechas → `in_progress` eterno sin escalar.
  Decidir: prohibir o incluir en snapshot.
- **PROY2-06 · MEDIA · S** — `task.human_validation_required` (principio 7 de
  CLAUDE.md) NO existe (ni columna ni código). Implementar o retirar promesa.
- **PROY2-07 · MEDIA · S** — `review_sessions` `suspended` inmortales (el
  sweep solo expira `running`); 2 zombis vivos; el reconciler las cuenta como
  activas → jamás re-spawnea sesión fresca para ese plan.
- **PROY2-08 · MEDIA · M** — Boards truncados en silencio a 100 filas (front
  sin paginar, DEFAULT_PAGE_SIZE=100): un plan de 200 tareas pinta 100 sin
  aviso; >100 planes desaparecen del kanban gerencial.
- **PROY2-09 · BAJA-MEDIA · M** — `sync_to_kanban` no es
  concurrencia-idempotente (read-then-insert sin unique/lock).
- **PROY2-10 · BAJA-MEDIA · S** — Re-sync con scope más ancho pierde aristas
  DAG para siempre (solo cablea deps de los recién creados).
- **PROY2-11 · BAJA · S** — Plan sin tareas ejecuta el ciclo entero y quema
  un review-runtime para validar nada.
- **PROY2-12 · BAJA · S** — Spec de chat esquiva Pydantic; id duplicado del
  LLM → 500 (no 422); dos formas de `summary` conviven.
- **PROY2-13 · BAJA · S** — `create_free_task` sin gate de `plan.status`;
  `create_task` acepta plan de OTRO proyecto del tenant.
- **PROY2-14 · BAJA · S** — Slugs con tildes mutiladas y corte a mitad de
  palabra acaban en ramas git y PRs públicos.

**Estado real de planes correctivos**: cadena-pr-plan T2/T4 YA implementados
(doc desactualizado; quedan grep-guard CI, helpers muertos, botón sync);
changelog/docs al cierre (c4/T8) sigue sin cablear — dueño tools-y-cierre.

## Frente 3 — Git/workspace y conocimiento (G-xx)

- **G-01 · HIGH · S · REGRESIÓN** — Ingesta KB muerta: `docling.py:99` llama
  `POST /v1/convert` (404 en docling-serve 1.20.0; el vivo solo expone
  `/v1/convert/source|file` y `/v1/chunk/*`) — idéntico en repo E imágenes
  vivas. El hot-fix del 06-25 nunca se comiteó. Todo upload → `failed`.
  Fix TDD + contract-test contra el openapi de la imagen pineada.
- **G-02 · MEDIUM-HIGH · S** — 0 KBs en BD (ni las ~14 builtin del seed, que
  es CLI manual); 6 plantillas conceden KBs por slug → punteros muertos;
  auto-RAG estéril (0 `rag_search` en 128 runs). Re-seed + seed en
  arranque/instalador + smoke-check.
- **G-03 · MEDIUM · S-M** — Sin GC físico de conocimiento: `delete_kb` solo
  soft-delete (ni chunks ni blobs); el «GC job» que el docstring promete no
  existe; ya hay 8 objetos huérfanos en MinIO. Beat de GC + ILM.
- **G-04 · MEDIUM · S** — Restos de tenants borrados (ver P1-08).
- **G-05 · MEDIUM · S** — Plan demo en estado imposible (ver P1-09) + guard
  en validación humana (todas done) + reconciler para
  `pending_human_validation` inconsistente.
- **G-06 · MEDIUM · S** — Los 3 contenedores workers permanentemente
  `unhealthy` por TIMEOUT del healthcheck (`celery inspect ping` a todos los
  nodos, 10s), no por fallo real — unhealthy crónico que ya no alerta a
  nadie. Fix: ping solo al nodo propio o timeout 30s.
- **G-07 · LOW-MEDIUM · M** — Poda de worktrees ciega al estado (solo mtime,
  TTL 30d): 19 worktrees = 2.9GB para 0 tasks vivas; y puede borrar el único
  resto de un `rebase_conflict` (commit fuera de rama). Poda por estado + ref
  de rescate.
- **G-08 · LOW · S** — Higiene bare repo: lock `initializing` huérfano desde
  el 03-07, 590 objetos prune-packables, sin `git gc` programado, sin poda de
  ramas `plan/*` post-merge.
- **G-09 · LOW · S** — Re-sync automático del remoto sigue OFF
  (`git_fetch_sweep_enabled` ni existe en platform_settings; último sync
  manual 07-09) — constatación viva de P5 de cadena-pr-plan.

## Frente 4 — Equipo/agentes/tools/MCP + UI + API (PROJ-xx)

- **PROJ-01 · HIGH · M** — El default del wizard (`fork_team=false`) linka el
  equipo BUILTIN, que el dispatch nunca puede usar (`Agent.tenant_id ==
task.tenant_id`; `_GLOBAL_SCOPES` es código muerto para tenants reales):
  proyectos silenciosamente inertes — caso vivo en BD. Fix: default fork=true
  (o ADR para eximir builtin del filtro) + surfacing UI de «sin candidatos».
- **PROJ-02 · HIGH · L (S si se retira)** — MCP por proyecto: fachada
  completa — 24 templates stdio sin binarios en ninguna imagen, test-connection
  falla el 100%, `AGENT_VAULT_TOKEN` que nadie fija, sin camino para escribir
  el secreto, validación conduct-time prometida y ausente. 0 usos en BD.
  ADR: empaquetar servers (imagen mcp-runners / npx vía registry-proxy +
  secretos worker-side) o marcar no-disponible.
- **PROJ-03 · MEDIUM · S** — El restore per-tenant desactiva FKs
  (`session_replication_role=replica`) y no re-valida: 10 filas `agent_tools`
  violando FK en la BD viva. Sweep post-restore + limpiar.
- **PROJ-04 · MEDIUM · S** — El pool de candidatos del dispatch ignora
  `team_members`: tareas de un proyecto con equipo A pueden caer en agentes
  del equipo B del tenant. Restringir a los miembros del equipo cuando exista.
- **PROJ-05 · MEDIUM-LOW · S** — Preset de agente soft-borrado → no-op
  eterno (`ready` para siempre, solo WARNING, sin auto-reparación).
- **PROJ-06 · LOW · S** — UX equipo↔proyecto: UUID pelado sin nombre/enlace;
  cambiar team_id sin aviso de impacto.
- **PROJ-07 · LOW · S** — Export docs PDF/ZIP sin consumidor UI. (El cruce
  inverso limpio: 169 rutas front / 287 OpenAPI, 100% resueltas.)
- **PROJ-08 · LOW · S** — Ledger tools-y-cierre desincronizado (T5-anuncio y
  T7 ya hechos); residuo REAL de g4: 51 filas `search_code` + 4
  `summarize_text` asignadas vía seeds (que esquivan el 422) y la UI de
  asignación no usa `is_runtime_wired`; seeds aún siembran apply-patch/
  search-code.

**Notas estructurales**: `apps/web-app/` está VACÍO (el admin-panel sirve
ambos roles) — divergencia con CLAUDE.md sin dueño; i18n del chrome del
proyecto hardcoded ES (prod-16, con dueño); H3 de 2026-06-24 confirmado
CERRADO por AUD16-02.

## Top-6 global (impacto/esfuerzo)

1. **G-01 + G-02** — resucitar el pilar de conocimiento (ingesta + seed +
   contract-test): sin él, KBs/RAG/plantillas-con-KB son fachada.
2. **PROY2-01/02** — cerrar la puerta lateral del ciclo de vida de planes
   (gates por rol + transiciones semánticas en PUT/POST).
3. **P1-01** — hacer real pausar/archivar (guard en dispatch + cancelar al
   archivar).
4. **P1-02** — slug único por tenant (evita corrupción git).
5. **PROJ-01 + P1-05** — que el camino por defecto produzca proyectos
   OPERATIVOS (fork por defecto, adopción server-side completa con
   allowlist/runtime).
6. **G-06 + PROY2-07** — quitar los dos «siempre-gris» de operación (workers
   unhealthy crónico, review_sessions zombis).

El plan de remediación derivado está en
[`remediacion-proyecto-integral-2026-07-17.md`](./remediacion-proyecto-integral-2026-07-17.md)
(`pending_approval`).
