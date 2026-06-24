---
title: "Auditoría — memoria de agentes, tools, e inyección de marketplace"
date: 2026-06-24
status: completed
type: audit
scope:
  - subsistema de memoria de agentes (storage, scopes, recall, RLS)
  - catálogo de tools, asignación y enforcement
  - marketplace e inyección en agentes/proyectos
  - inyección en runtime (pegamento de ejecución)
method: "4 auditores en paralelo (lectura profunda del código) + verificación adversaria por hallazgo (intento de refutación leyendo el código citado). Workflow wp31ton5t, 21 agentes."
---

# Auditoría: memoria de agentes · tools · inyección de marketplace

> Cada hallazgo se generó por un auditor y se sometió a un **verificador adversario**
> que abrió el código citado e intentó refutarlo. La columna **severidad** es la
> ajustada por el verificador (autoritativa); el **veredicto** indica si quedó
> `confirmed` / `partial` / `refuted`.

## Resumen ejecutivo

El **núcleo** de los tres subsistemas está bien construido y es coherente extremo a
extremo: RLS real (ENABLE+FORCE) en `memory_entries`, recall híbrido que filtra
**siempre** por `tenant_id` explícito + scope/owner, aislamiento cross-tenant de la
asignación de tools garantizado por RLS, catálogo de tools cerrado a prueba de drift,
e inyección de tool-schemas provider-agnóstica que honra los 4 providers sin
degradación silenciosa. **No se encontró ninguna fuga cross-tenant.**

Los problemas reales se concentran en cuatro puntos:

1. **Endpoints REST `/memories` sin autorización por owner** (intra-tenant): cualquier
   `tenant_member` puede **leer y borrar** memorias `private` de otros usuarios,
   `team_shared` de otros equipos y `project_shared` de otros proyectos **dentro de su
   tenant**. Rompe el principio "scopes que NUNCA se confunden". → **HIGH**
2. **Tools de orquestación silenciadas**: en cuanto un agente recibe **una** tool
   asignada, `kanban_update`/`task_comment`/`agent_invoke` quedan registradas pero
   **rechazadas** en runtime, sin aviso. Rompe a los PM/orquestadores. → **HIGH**
3. **Marketplace incompleto**: instalar un listing **no materializa** nada en el
   catálogo nativo (solo crea una fila contable) y el path de install **fresco no corre
   los gates de seguridad** (firma/análisis estático/sandbox) que sí corre el `update`.
   El docstring promete "usable by the tenant's agents" — hoy es falso. → **HIGH/MEDIUM**
4. **Asimetría read/write de memoria** para agentes `project_shared` sin `project_id`:
   pueden **leer** project_shared de la task pero no **escribirla** (400). Bug funcional
   que falla cerrado. → **MEDIUM**

---

## Estado de remediación (2026-06-24)

| Hallazgo                                                | Estado                         | Dónde                                                                                                                     |
| ------------------------------------------------------- | ------------------------------ | ------------------------------------------------------------------------------------------------------------------------- |
| **H0** (tools de memoria/orquestación no llegan al LLM) | ✅ **arreglado**               | commit `3419b2b` — `build_model_tool_schemas(include_system_tools)` + `register_system_families` + `_effective_allowlist` |
| **H3** (tools de orquestación silenciadas)              | ✅ **arreglado**               | mismo commit (`3419b2b`); mismo root cause unificado con H0                                                               |
| **H1** (`GET /memories` sin owner-auth)                 | ✅ **arreglado**               | router `/memories`: filtro owner para `private`                                                                           |
| **H2** (`DELETE /memories` sin owner-auth)              | ✅ **arreglado (private)**     | `private` de otro usuario → 404; shared/global = modelo de operador existente                                             |
| **M3** (`/similar` + `/merge-into` sin owner-auth)      | ✅ **arreglado (private)**     | `private` de otro usuario → 404; shared sigue guardado por el owner-pointer match                                         |
| **H4** (install marketplace sin gates)                  | 🟡 **diferido con honestidad** | ADR 0081 — cablear los gates regresaría el feature (sandbox sin Docker); copy corregido + plan Fase B/C documentado       |
| **M2** (asimetría store/recall `project_shared`)        | ✅ **arreglado**               | commit `73e7add` — `memory-store` usa el proyecto efectivo (ADR 0054)                                                     |
| **H6** (rag-search rota — side-effect de mem0)          | ✅ **arreglado**               | commit `9571739` — ver "Hallazgo adicional" abajo                                                                         |
| **infra** (cortex-beat figuraba unhealthy)              | ✅ **arreglado**               | commit `81e5b89` — healthcheck propio de beat (PID 1)                                                                     |
| **M1 / L1-L5**                                          | ⏳ pendiente / deuda           | M1 ligado a H4 (ADR 0081); resto = deuda menor, ver abajo                                                                 |

### Hallazgo adicional (durante la remediación) — H6: `rag-search` rota por el entity-match de mem0

**Área:** RAG · **Severidad:** HIGH (endpoint roto) · **Veredicto:** `confirmed`
**Ficheros:** [search.py:237](apps/api-server/src/api_server/rag/search.py#L237), [search.py:321](apps/api-server/src/api_server/rag/search.py#L321), [recall.py:84-119](apps/api-server/src/api_server/memorizer/recall.py#L84) (`fuse_rankings`)

Al "resolver unos flakys" resultó que **no eran flakys**: `rag-search` / `semantic_search`
daban **500 en cualquier búsqueda con resultados** (`ValueError: too many values to unpack`).

**Causa raíz — trazada al añadido de mem0 (ADR 0059):** la idea "entity linking" de mem0
_("implementar las mejoras de mem0 sin la librería, solo las ideas")_ añadió una **tercera
señal** al RRF (migración **0084**, `memory_entries.entities`). `fuse_rankings` pasó de
devolver una 3-tupla `(score, bm25, vector)` a una **4-tupla** `(score, bm25, vector, entity)`.
El consumidor de **memoria** (`recall.py:389`, que pasa `entity_ids`) se actualizó; los **dos
consumidores de `rag/search.py`** (que ni usan entidades — pasan 2 listas) se quedaron
desempaquetando 3 → crash. Verificado que esos dos eran los **únicos** afectados.

**Fix:** desempacar la 4-tupla en ambos sitios, descartando `entity_rank` (RAG no hace
entity-match; `ChunkHit` no lo expone). La feature de entidades en el recall de memoria es
correcta y testeada; el bug era solo que rag-search **comparte** `fuse_rankings`. Regresión
cubierta por `test_global_agent_project_context.py`. **Observación de diseño:**
`fuse_rankings` devuelve una tupla posicional cuya aridad cambió silenciosamente; una
estructura nombrada evitaría que un futuro cambio rompa consumidores sin aviso (deuda menor).

> **Decisión de política `/memories`** (respetando agentes≠humanos): `memory_entries` es
> una tabla compartida por **agentes** (team/project/global), **asistente** (`private`,
> user_id=humano) y **córtex** (`private`, user_id=owner). Solo `private` es dato personal
> sensible → se restringe **estrictamente al dueño** (read/delete/similar/merge); otro
> usuario lo ve como 404. Los scopes **compartidos** (team/project/global) siguen siendo
> gestionables por cualquier miembro del tenant (modelo de operador existente; el `merge`
> ya está guardado contra fugas cross-owner por el match de owner-pointer). NO se inventó
> pertenencia humana a equipos (los `team_members` son AGENTES). Córtex y asistente usan
> su propia vía interna (`cortex/memory.py`, `assistant/memory.py`), no este router, así
> que el fix los protege sin romperles nada.

---

## Hallazgos por severidad

### 🔴 HIGH

#### H0 · Las tools de memoria (`memory_recall`/`memory_store`) NUNCA se anuncian al LLM en ejecución de tareas → los agentes no pueden recordar mientras trabajan

**Área:** runtime / configuración de memoria de agentes · **Veredicto:** `confirmed` (traza end-to-end en el path orchestrator-dispatch → agent-runtime)
**Ficheros:** [agent_tool_schemas.py:119-158](apps/workers/src/workers/agent_tool_schemas.py#L119), [execution.py:282](apps/workers/src/workers/execution.py#L282), [dispatch.py:451-452](apps/orchestrator/src/orchestrator/dispatch.py#L451), [combine_tool_allowlists](apps/api-server/src/api_server/agent_tools_enforcement.py#L189), [builtin_tools.py:64-67](apps/api-server/src/api_server/seeds/builtin_tools.py#L64), [builtin_families.py:177-181](docker/agent-runtimes/agent-runtime/agent_runtime/builtin_families.py#L177)

> Este es el hallazgo que responde a la pregunta "¿está bien configurada la **memoria de
> los agentes**?". La configuración de _scopes_ es correcta (built-in agents con
> `project_shared`/`team_shared`; default plataforma `memory.default_scope`), pero las
> **herramientas** de memoria nunca llegan al modelo, así que esa configuración es inerte
> durante la ejecución de tareas.

**Cadena verificada:**

1. `memory_recall`/`memory_store` **no están en el catálogo asignable** (las 15
   `BUILTIN_TOOLS`); son "runtime-only" registradas por la _memory family_. No hay fila
   `Tool` → **no pueden estar en `agent_tools`**.
2. En dispatch, `allowed_tools = combine_tool_allowlists(agent_tool_names, None)` =
   **exactamente** los nombres asignados (memoria nunca entra).
3. El worker anuncia al LLM solo `build_model_tool_schemas(allowed_tools, tool_specs)`, que
   **solo emite schema para nombres del allowlist** (`for name in tool_names`). El propio
   módulo prepara `_RUNTIME_ONLY_SCHEMAS` con memory_recall/memory_store pero **se filtran**
   porque sus nombres no están en el allowlist.
4. Resultado: el LLM **nunca ve** memory*recall/memory_store. Y si el agente tiene ≥1 tool
   asignada, `set_allowed_tools` además las **rechazaría** en call-time (mismo eje que H3).
   Si el agente no tiene tools, la \_memory family* ni siquiera se registra (gate
   `if "tool_specs" in spec`, ver L5).

**Impacto:** ningún agente puede usar memoria cognitiva mientras ejecuta una tarea, sea
cual sea su `memory_scope`. El docstring de [agent_tool_schemas.py:6-8](apps/workers/src/workers/agent_tool_schemas.py#L6)
afirma que el fix "agentes #2" resolvió justo esto, pero el fix solo cubre tools que están
en el allowlist — y memoria/orquestación no pueden estarlo. **Mismo root cause que H3**
(tools runtime-only no exentas del allowlist), generalizado: afecta a memoria _y_
orquestación. (Coherente con la nota de trabajo "agentes #2/#3 pendientes".)

**Fix (unifica con H3):** tratar las tools de **familia runtime-only** (memoria +
orquestación, y `send_notification` si aplica) como **capacidades de sistema siempre
disponibles**: (a) **anunciar** sus schemas al LLM —añadir las de memoria/orquestación al
output de `build_model_tool_schemas` con criterio (p.ej. memoria gated por `memory_scope`
configurado o un flag base), y (b) **eximirlas del allowlist** en el `ToolRegistry`
(conjunto "siempre permitido"). Además, asegurar que la _memory family_ se registre aunque
el agente no tenga `agent_tools` (quitar la dependencia del gate `tool_specs` para las
familias de sistema). Tests: agente con `memory_scope=project_shared` y (i) sin tools, (ii)
con `read_file` asignada → en ambos el LLM recibe el schema de `memory_recall` y la llamada
se ejecuta.

#### H1 · `GET /memories` no filtra por owner (lectura cross-owner intra-tenant)

**Área:** memoria · **Veredicto:** `confirmed`
**Ficheros:** [memories.py:241](apps/api-server/src/api_server/routers/memories.py#L241), [memories.py:228](apps/api-server/src/api_server/routers/memories.py#L228), [deps.py:403](apps/api-server/src/api_server/auth/deps.py#L403)

`list_memories` construye `select(MemoryEntry).where(MemoryEntry.deleted_at.is_(None))`
y solo añade filtros **opcionales** tomados del query string (scope/type/project_id/
team_id). No hay ningún predicado que ate el resultado a `principal.user_id`, a los
`team_id` del usuario ni a los proyectos accesibles. El gate es `require_tenant_member`
(no admin), y el comentario reconoce "RLS handles the tenant boundary" — pero la única
política RLS de la tabla (`memory_entries_tenant_isolation`, migración 0020) aísla
**solo por tenant**, no por owner. `_to_response` devuelve el `content` completo.

**Impacto:** `GET /memories?scope=private` devuelve memorias privadas de otros usuarios
del mismo tenant (idem team_shared/project_shared). El propio `recall.py:393-402`
documenta este riesgo y lo mitiga con el owner-pointer; el endpoint REST no.

**Fix:** filtrar por owner según el principal — `private ⇒ user_id = principal.user_id`;
`team_shared ⇒ team_id ∈ equipos del usuario`; `project_shared ⇒ proyectos accesibles`;
`global` libre. **Alternativa** (decisión de producto): si `/memories` es una **vista de
operador**, gatearla con `require_tenant_admin` (como ya se hace para escribir `global`)
y documentarlo. Hoy no es ni una cosa ni la otra.

#### H2 · `DELETE /memories/{id}` permite borrar memorias de otros owners

**Área:** memoria · **Veredicto:** `confirmed`
**Ficheros:** [memories.py:309-328](apps/api-server/src/api_server/routers/memories.py#L309)

`delete_memory` carga la fila solo por `id + deleted_at IS NULL` y hace
`row.deleted_at = now`. No comprueba owner (ni user_id de private, ni pertenencia al
team_shared, ni acceso al project_shared). Gate: `require_tenant_member`. Los ids se
obtienen trivialmente vía H1. Es **soft-delete** (recuperable, audita), por eso HIGH y
no critical, y **no** es fuga cross-tenant (RLS por tenant intacta). El propio módulo
(comentario líneas 46-57) reconoce el patrón y lo aplica en `/similar` y `/merge-into`,
pero **lo omite en delete**.

**Fix:** verificar autorización por scope antes del soft-delete; 403/404 en caso
contrario. Mismo criterio que H1.

#### H3 · Asignar cualquier tool silencia las tools de orquestación del agente

**Área:** tools · **Veredicto:** `confirmed` (condicional: ≥1 fila en `agent_tools`)
**Ficheros:** [dispatch.py:451](apps/orchestrator/src/orchestrator/dispatch.py#L451), [**main**.py:305](docker/agent-runtimes/agent-runtime/agent_runtime/__main__.py#L305), [builtin_families.py:155](docker/agent-runtimes/agent-runtime/agent_runtime/builtin_families.py#L155), [tool_names.py:57](packages/shared-domain/src/shared_domain/tool_names.py#L57)

`combine_tool_allowlists(agent_tool_names, None)` fija el allowlist **exactamente** a los
nombres de `agent_tools`. Las tools de orquestación (`kanban_update`, `task_comment`,
`agent_invoke`) viven en el bucket `ORCHESTRATION` (runtime-only, ADR 0048) y **no están
en el seed asignable** (`BUILTIN_TOOLS` son 15, ninguna de orquestación), así que el
operador no puede añadirlas a `agent_tools`. `register_builtin_families` las registra
siempre, pero `is_allowed()` las rechaza en call time si no están en el set. El
workaround de crear una tool custom homónima está cerrado (`tools.py:68` lanza 409 sobre
`CANONICAL_TOOL_NAMES`). `compute_effective_tools` solo exime `shell_exec`.

**Impacto:** en cuanto un PM/orquestador recibe **una** tool asignada (config realista),
pierde silenciosamente la capacidad de mover el Kanban / comentar / invocar subagentes.
Sin asignaciones (allowlist = None) funcionan, por eso es condicional.

**Fix:** eximir `_ORCHESTRATION_TOOL_NAMES` (y `send_notification` si aplica) del
allowlist por-agente — unirlas al set tras `set_allowed_tools` en `__main__`, o un
conjunto "siempre permitido" en `ToolRegistry`. Test runtime: agente con `read_file`
asignado debe seguir pudiendo llamar `kanban_update`.

#### H4 · `POST /marketplace/installations` no ejecuta los gates de seguridad

**Área:** marketplace · **Veredicto:** `confirmed`
**Ficheros:** [marketplace.py:902-903](apps/api-server/src/api_server/routers/marketplace.py#L902), [marketplace.py:200](apps/api-server/src/api_server/routers/marketplace.py#L200), [install.py:314](apps/api-server/src/api_server/marketplace/install.py#L314)

El install fresco inlinea la persistencia y nunca referencia `InstallOrchestrator`; el
único rastro de los gates es un `# TODO(Plan 09 Fase B/C)`. `get_install_orchestrator`
solo se inyecta en `perform_installation_update`. `InstallOrchestrator.install()`
**existe y corre `_run_security_gates()`** pero ningún endpoint lo invoca.

**Impacto:** un install fresco de un listing community/experimental se persiste **sin
verificación de firma, sin análisis estático ni sandbox**. Paradójicamente, **actualizar**
un install sí re-corre todos los gates. Mitigación parcial: el gate de **consent** sí
aplica (un listing no-verified aterriza `DISABLED` sin permisos), pero un listing
`verified` (consent_required=false) instala `ENABLED` saltándose los gates de código.

**Fix:** cablear `InstallOrchestrator.install()` en `POST /installations` igual que está
`update()`. Unificar la lógica duplicada de consent/persistencia entre router
([marketplace.py:913-961](apps/api-server/src/api_server/routers/marketplace.py#L913)) y
orquestador ([install.py:338-395](apps/api-server/src/api_server/marketplace/install.py#L338)).

---

### 🟠 MEDIUM

#### M1 · La instalación de marketplace no materializa capacidades (no hay inyección)

**Área:** marketplace + runtime · **Veredicto:** `partial` (rebajado de high → es _ausencia_ de inyección, no inyección incorrecta; sin fuga)
**Ficheros:** [marketplace.py:921](apps/api-server/src/api_server/routers/marketplace.py#L921), [install.py:350-361](apps/api-server/src/api_server/marketplace/install.py#L350), [marketplace.py:131-134](apps/api-server/src/api_server/db/marketplace.py#L131), [dispatch.py:451-462](apps/orchestrator/src/orchestrator/dispatch.py#L451), [agent_tools_enforcement.py:90](apps/api-server/src/api_server/agent_tools_enforcement.py#L90)

Instalar un listing solo crea `MarketplaceInstallation` + `MarketplaceAuditEntry`. No hay
ningún `session.add(Tool/Skill/Agent)` en todo `marketplace/`, ni columna de provenance,
ni puente desde la instalación hacia el catálogo nativo (`tools`/`skills`/`agents`). La
resolución de tools en runtime lee **exclusivamente** las tablas nativas vía
`resolve_agent_tool_names`/`serialize_agent_tool_specs`; grep de `MarketplaceInstallation`
en `apps/orchestrator` y `apps/workers` → sin coincidencias. El docstring de
`InstallationStatus.ENABLED` afirma "installed and usable by the tenant's agents" — **hoy
es falso**: el agente en ejecución jamás ve lo instalado.

Está **parcialmente documentado como diferido** (`install.py:20` "the live path is
pending the registry runtime"), por eso medium y no high.

**Fix:** construir el paso de materialización al pasar a `ENABLED` (crear/upsert fila en
`tools`/`skills` con `category` válida del catálogo cerrado, nombre no colisionante y
**provenance** `listing_id`/`installation_id`), y desmontarla en `uninstall`/`revoke` (ver
L2). Si se difiere a propósito, **corregir el docstring** de `InstallationStatus.ENABLED`
para no prometer usabilidad inexistente, y exponerlo como gap explícito (ADR).

#### M2 · Asimetría store vs recall para `project_shared` sin `project_id`

**Área:** runtime · **Veredicto:** `partial` (el disparador no es "agente global" sino `memory_scope == project_shared` + `project_id IS NULL`)
**Ficheros:** [internal_agent.py:769-784](apps/api-server/src/api_server/routers/internal_agent.py#L769), [internal_agent.py:217-227](apps/api-server/src/api_server/routers/internal_agent.py#L217), [internal_agent.py:327-336](apps/api-server/src/api_server/routers/internal_agent.py#L327)

`memory_recall` resuelve el proyecto efectivo de la task (`_resolve_effective_project`,
ADR 0054) y declara "read = write = task.project_id". Pero `memory_store` usa
`agent.project_id` directamente: si es `None`, devuelve owner `None` → **400**. Un agente
con `memory_scope == project_shared` y `project_id is None` puede **leer** pero no
**escribir** project_shared, rompiendo el invariante read=write. **No es fuga**: falla
cerrado, sin cruce de tenant ni de proyecto.

**Fix:** que `memory_store` use `_resolve_effective_project` para resolver el owner de
project_shared cuando `agent.project_id is None` pero el token porta `task_id` y el flag
está ON (alineando store con recall); o documentar que ese store no está soportado y que
la captura post-run del Memorizer (que sí usa `task.project_id`) es la vía.

#### M3 · `/similar` y `/merge-into` no verifican que el principal sea owner del source

**Área:** memoria · **Veredicto:** `partial` (es transversal a TODO el router de memorias, no exclusivo de estos dos endpoints; se resuelve junto con H1/H2)
**Ficheros:** [memories.py:369-373](apps/api-server/src/api_server/routers/memories.py#L369), [memories.py:479-490](apps/api-server/src/api_server/routers/memories.py#L479)

El source/target se cargan solo por `id + deleted_at`. El owner-match implementado
protege contra operar **entre** owners distintos, pero no contra que un principal que no
pertenece al owner pida "similares" de una private ajena o fusione team_shared de un
equipo al que no pertenece. **Misma raíz que H1/H2**: falta autorización por-scope a
nivel de router. Sin fuga cross-tenant.

**Fix:** aplicar la misma autorización por owner sobre source (y target en merge) que H1/H2.

---

### 🟡 LOW

#### L1 · `required_tools` (Skill) no se valida ni se aplica en runtime

**Área:** tools · **Veredicto:** `confirmed`
**Ficheros:** [domain.py:505-510](apps/api-server/src/api_server/db/domain.py#L505), [skills.py:100](apps/api-server/src/api_server/routers/skills.py#L100), [agent_skills_enforcement.py:37](apps/api-server/src/api_server/agent_skills_enforcement.py#L37)

`required_tools` es JSONB de UUIDs documentado como "recommendation, not a hard FK".
Acepta cualquier UUID (inexistente / soft-deleted / de otro tenant) sin lookup, y
`resolve_agent_skill_prompt_fragments` solo lee `prompt_fragment` — `required_tools` nunca
se materializa en tools ejecutables (el enforcement real es 100% vía `agent_tools`). **No
es fuga** (un UUID ajeno nunca concede una tool); es estado inconsistente que confunde a
la UI del Capability Hub. Es deuda consciente (seed lo documenta).

**Fix:** o resolver los UUIDs a nombres en la respuesta marcando los no-resolubles, o
validar en `create/update_skill` que cada UUID es una tool viva visible al tenant (422 si no).

#### L2 · `uninstall`/`revoke` no desmonta capacidades (corolario de M1)

**Área:** marketplace · **Veredicto:** `partial` (info: no hay bug actual porque no hay materialización)
**Ficheros:** [marketplace.py:219](apps/api-server/src/api_server/routers/marketplace.py#L219), [marketplace.py:1272](apps/api-server/src/api_server/routers/marketplace.py#L1272)

`_revoke_installation` solo flipea status + soft-delete + audit. El docstring dice
"disable it for agents/projects", pero como M1 nunca materializa nada, no hay huérfanos
**hoy**. Riesgo **futuro**: si se añade materialización (M1) sin tocar el teardown,
quedará basura.

**Fix:** al implementar M1, extender `_revoke_installation` para soft-delete/disable de la
fila `tools`/`skills` materializada en la misma transacción, con test de no-orfandad.

#### L3 · Sin provenance (`forked_from`/origen) en lo instalado (condicionado a M1)

**Área:** marketplace · **Veredicto:** `partial` (low: mejora preventiva)
**Ficheros:** [marketplace.py:322-407](apps/api-server/src/api_server/db/marketplace.py#L322)

Hoy la trazabilidad existe a nivel de installation (`listing_id` FK + `version` + audit).
La plataforma ya tiene el idioma listo para reutilizar: `forked_from_agent_id`/
`forked_from_version` en `Agent` ([domain.py:461](apps/api-server/src/api_server/db/domain.py#L461))
y `Team` ([domain.py:686](apps/api-server/src/api_server/db/domain.py#L686)).

**Fix:** al materializar (M1), estampar `source_listing_id`/`source_installation_id` +
version en la fila creada para propagar updates y auditar origen.

#### L4 · Race en deduplicación de install para tenant-wide (`project_id IS NULL`)

**Área:** marketplace · **Veredicto:** `confirmed`
**Ficheros:** [marketplace.py:883-900](apps/api-server/src/api_server/routers/marketplace.py#L883), [marketplace.py:337-344](apps/api-server/src/api_server/db/marketplace.py#L337)

El índice parcial `uq_marketplace_installations_live` no cubre `project_id IS NULL`
(Postgres trata NULLs como distintos), así que para installs tenant-wide la unicidad
depende **solo** del SELECT-then-insert del router → TOCTOU: dos requests concurrentes
pasan el SELECT y ambos insertan. `except IntegrityError` no salva este caso. Daño máximo:
fila duplicada same-tenant, ventana estrecha, endpoint admin-gated.

**Fix:** índice de expresión único con `COALESCE(project_id, '00000000-…')` (o
`NULLS NOT DISTINCT` en PG15+) para que el `IntegrityError` sea la barrera real también
para tenant-wide.

#### L5 · Agente sin filas `agent_tools` no recibe `memory_recall`/`memory_store`

**Área:** runtime · **Veredicto:** `confirmed` (comportamiento deliberado backward-compat 06.15)
**Ficheros:** [execution.py:282-284](apps/workers/src/workers/execution.py#L282), [agent_tool_schemas.py:131-132](apps/workers/src/workers/agent_tool_schemas.py#L131), [**main**.py:265-266](docker/agent-runtimes/agent-runtime/agent_runtime/__main__.py#L265)

Sin filas `agent_tools`, el allowlist y `tool_specs` quedan ausentes, `_wire_assigned_tools`
no corre y `register_builtin_families` (que cablea memory) tampoco. Un agente recién
creado corre **sin poder recordar nada**, silenciosamente. **No es bug de aislamiento**;
es footgun de configuración/UX.

**Fix:** tratar `memory_recall`/`memory_store` como capacidades base siempre cableadas
(salvo exclusión por modo), o asignarlas por defecto en la UI de creación de agente + aviso
en el Capability Hub. Como mínimo, documentarlo.

---

### ⚪ INFO / refutados (no accionables)

- **Doc 15 vs "19" tools** (`partial`): el único punto erróneo es el docstring de
  `ToolCategory` ([domain.py:175](apps/api-server/src/api_server/db/domain.py#L175)) que dice
  "19 built-in seed rows" cuando el seed tiene **15** (familia git retirada,
  `task_06_18_06`). La migración 0077 **no** hardcodea "19" (deriva las categorías del
  seed real), así que la recomendación de tocar 0077 es improcedente. Arreglo trivial:
  cambiar el docstring a "las categorías del seed actual".
- **`memory-recall` honra `payload.scopes` verbatim** (`refuted` → info): no hay fuga; el
  owner-pointer (resuelto server-side) y el filtro `tenant_id` cubren cross-tenant/owner.
  Endurecimiento opcional: intersectar con `_default_readable_scopes(agent.memory_scope)`.
- **kinds del marketplace sin `agent`/`team`** (`partial` → info): por diseño explícito; el
  marketplace modela solo capacidades (skill/tool/mcp_server). La **adopción de equipos**
  ya tiene su vía propia y funcional: `POST /teams/{id}/adopt`
  ([teams.py:185](apps/api-server/src/api_server/routers/teams.py#L185), ADR 0066). No hay
  UI de marketplace que ofrezca instalar agente/equipo. No es defecto.
- **Asimetría read/write project_shared para agente global** (`confirmed` info): un agente
  global puede leer project_shared de la task pero no escribirla — defendible (no
  "pertenece" al proyecto). Distinto de M2 (que es scope `project_shared` + `project_id` NULL).

---

## Lo que SÍ está bien (no tocar)

- **RLS real** en `memory_entries`: ENABLE + FORCE, política `tenant_id = current_setting('app.tenant_id')` en USING y WITH CHECK (migración 0020). Sin fuga cross-tenant.
- **Recall híbrido** (BM25+vector+entity / RRF) filtra `tenant_id` explícito en las 3 ramas y en el detail-fetch, **más** scope+owner-pointer correcto. Buenos tests cross-tenant/cross-scope.
- **Punteros de scope** coherentes con el CHECK `ck_memory_entries_scope_pointer`; `persistence._owner_kwargs` falla pronto si falta el owner.
- **Back-fill de embeddings** genuinamente idempotente y NULL-only (`FOR UPDATE SKIP LOCKED`, tope de lotes).
- **Asignación de tools cross-tenant** bloqueada por RLS (tool de otro tenant → 422, transaccional), con test `@cross_tenant`.
- **Catálogo de tools cerrado a prueba de drift**: el CHECK de `tools.category` (0077) se deriva del seed + enum; no puede desalinearse.
- **Enforcement de tools en 2 capas**: allowlist canónico al anunciar al LLM + `ToolRegistry.call` rechaza en call time.
- **Inyección de tool-schemas provider-agnóstica**: los 4 providers (claude_sdk/copilot/azure_foundry/ollama) honran `tools` sin degradación silenciosa.
- **Marketplace data substrate**: RLS correcta en las 4 tablas tenant-owned, modelo híbrido NULL=global, sharing cross-tenant opt-in y auditado, install sellada con el `tenant_id` del instalador, auditoría append-only transaccional.

---

## Plan de acción recomendado (orden)

1. **H1 + H2 + H3-memoria (M3)** — autorización por-owner en el router `/memories`
   (list/delete/similar/merge). **Requiere una decisión de producto**: ¿`/memories` es una
   vista per-usuario (filtrar por owner) o una vista de operador (gatear con
   `require_tenant_admin`)? Tests cross-owner intra-tenant.
2. **H3-tools** — eximir las tools de orquestación del allowlist por-agente + test runtime.
3. **H4** — cablear los gates de seguridad en `POST /installations` (o, si Fase B/C sigue
   diferida, marcar el endpoint como no productivo y corregir el docstring que promete
   usabilidad).
4. **M1 (+L2/L3)** — materialización del marketplace (o ADR que documente el diferimiento
   - corregir docstring de `InstallationStatus.ENABLED`).
5. **M2** — alinear `memory_store` con `memory_recall` para project_shared.
6. **L1, L4, L5, doc 15/19** — endurecimientos y limpieza.
