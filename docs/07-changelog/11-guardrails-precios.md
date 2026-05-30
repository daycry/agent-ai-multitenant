---
plan_id: 11-guardrails-precios
title: Guardrails Declarativos y Catálogo de Precios
completed_at: null
docs_language: es
---

# Plan 11 — Guardrails Declarativos y Catálogo de Precios

## Resumen

Endurece la plataforma con dos capacidades transversales: un **motor de
guardrails declarativos** y un **catálogo global de precios de modelos**.

El **motor de guardrails** (`packages/shared-guardrails`, paquete nuevo) es
una `GuardrailPipeline` pura (sin DB, sin I/O) que evalúa, en **cuatro puntos
del ciclo LLM/tool** — `pre_llm`, `post_llm`, `pre_tool`, `post_tool` — una
lista ordenada y declarativa (YAML/dict) de guardrails. Cada guardrail
implementa el Protocol `Guardrail` (`check(context) -> GuardrailResult`),
resuelto por `type` en un `GuardrailRegistry`. La config se compone en **tres
capas** plataforma → tenant → proyecto con **campos lockable**: un guardrail
que la plataforma marca _locked_ no puede ser debilitado ni eliminado por una
capa inferior (los baselines PII / secret-leakage / prompt-injection son
obligatorios). Al disparar, una de **seis acciones** decide el efecto (`block`,
`redact`, `warn`, `retry_with_feedback`, `escalate_to_human`, `transform`),
con precedencia `block > escalate > retry > transform > redact > warn`. Se
implementaron los **12 tipos built-in**: `pii`, `secret_leakage`,
`prompt_injection`, `content_safety`, `code_safety`, `output_structure`,
`allowed_domains`, `cost_ceiling`, `factuality_citations`, `topic_restriction`,
`rate_per_agent` y `forbidden_actions`. Las integraciones pesadas son **extras
opcionales y lazy** (`shared-guardrails[pii]` = Presidio; `[content-safety]` =
guard model vía `shared-llm`): ausentes, el guardrail degrada a un fallback
puro o a un resultado tipado _unavailable_ — nunca finge un veredicto.

El **catálogo de precios** (`model_prices`) es **platform-global** (sin
`tenant_id`), **siempre en USD canónico** (CHECK `currency='USD'`), con
**vigencia** `effective_from`/`effective_to` (NULL == periodo abierto/actual) y
un índice parcial único que garantiza un solo periodo abierto por
`(provider, model_id, modality)`. Soporta **prompt caching**
(`cached_input_price` nullable, fallback ~10% del input). La RLS es de **lectura
global** (cualquier sesión tenant lee todo el catálogo; solo la sesión
System-Admin BYPASSRLS escribe). Cada **model_call** congela un **snapshot del
precio vigente** (input/output/cached + `price_snapshot_at` + coste USD) en el
`steps_log` JSONB de `executions`, de modo que un cambio posterior del catálogo
NO altera el coste histórico. El botón **"Sincronizar precios"** lee el JSON
público que la comunidad de LiteLLM mantiene
(`model_prices_and_context_window.json`) **solo como fuente de datos** — NO
añade `litellm` como dependencia ni como runtime (el catálogo de proveedores
sigue cerrado, ADR 0021). El sync ofrece **dry-run con diff** + **confirmación
obligatoria** si algún precio sube **>10%**, **detección de modelos nuevos y
descontinuados**, **sincronización programada configurable** (Celery Beat) y
un **audit log append-only** de cada corrida.

La **observabilidad** persiste cada guardrail que dispara como una fila
**tenant-scoped + RLS** en `guardrail_events` (detalle SIEMPRE enmascarado —
nunca PII/secreto en crudo) que alimenta el dashboard del tenant; y se cablean
**guardrails específicos del chat de planning** (topic adherence, hallucination
check sobre números, gate estructural antes de "Generar Plan") reutilizando el
motor de Fase A/B.

Las 23 tareas se desarrollaron en cinco fases (A — motor; B — 12 built-in;
C — catálogo; D — sincronización; E — observabilidad y cierre).

> **⚠ Hueco de alcance respecto al Resumen/Alcance del plan.** El plan
> describe en su Alcance un **sistema de Budgets** (campos de presupuesto en
> Organization/Project, umbrales platform-global `[80, 90, 100]`, pausado
> automático al 100%, override manual con audit), una tabla **`exchange_rates`**
> con job diario ECB y **`Organization.display_currency`** (conversión
> USD→moneda del tenant). **NO existe ninguna tarea numerada
> (`task_11_01`..`task_11_23`) para ello y NO se implementó en este plan.** Ver
> [Pendiente](#pendiente) — requiere decisión humana sobre un plan/tareas de
> seguimiento.

## Cambios por tarea

### Fase A — Motor de Guardrails

- ✅ **`task_11_01`** — **Pipeline declarativo YAML** con los cuatro puntos
  `pre_llm` / `post_llm` / `pre_tool` / `post_tool` en
  `packages/shared-guardrails` (paquete nuevo). `GuardrailPipeline` carga una
  config declarativa (YAML/dict) → ejecuta, por hook, la lista ordenada de
  guardrails (Protocol `Guardrail` resuelto por `type` en `GuardrailRegistry`)
  → agrega en un `PipelineDecision` con la acción decisiva por precedencia. El
  motor es **puro** (sin DB ni I/O): el host le pasa un `GuardrailContext`.
- ✅ **`task_11_02`** — **Configuración por capas** plataforma → tenant →
  proyecto con **campos lockable** (`layers.py`): una capa más específica
  reemplaza un guardrail por su `key`, EXCEPTO si la plataforma lo marcó
  _locked_ (override ignorado en modo default o rechazado con
  `LockedFieldOverrideError` en modo `strict`, siempre con trazabilidad de
  provenance). Un guardrail locked es además **obligatorio** (no se puede
  remover). Pura: opera sobre el `PipelineConfig` parseado de cada capa.
- ✅ **`task_11_03`** — **6 acciones** (`actions.py`): `block`, `redact`
  (enmascara los spans), `warn`, `retry_with_feedback` (re-ejecuta el LLM con
  feedback correctivo), `escalate_to_human` (pausa para validación humana) y
  `transform` (reescribe vía un `Transformer`). El motor surfacea la acción en
  la decisión; el host aplica el efecto.

### Fase B — Guardrails Built-in (12 tipos)

- ✅ **`task_11_04`** — **`pii`** (hooks `pre_llm`+`post_llm`,
  `checks/pii.py`). Presidio (`presidio-analyzer`, arrastra spaCy + NER) es el
  extra **OPCIONAL** `shared-guardrails[pii]`, importado **lazy**: ausente
  degrada a un fallback regex de alta confianza (email / tarjeta con Luhn /
  teléfono / IBAN / IPv4 / SSN) o, en modo `backend: presidio` estricto, a un
  resultado tipado _unavailable_. Default `redact` en `post_llm`, `block` en
  `pre_llm`.
- ✅ **`task_11_05`** — **`secret_leakage`** (`post_llm`+`post_tool`,
  `checks/secret_leakage.py`). Detección pura (regex + entropía Shannon):
  familias bien conocidas (AWS / Google / GitHub/GitLab / Slack / bloque PEM /
  JWT / connection string con password) + asignaciones genéricas de alta
  entropía. Default `redact`: la redacción enmascara cada span con
  `[REDACTED:{type}]` y **nunca** vuelca el secreto en el resultado (los spans
  llevan solo offsets + familia).
- ✅ **`task_11_06`** — **`prompt_injection`** (`pre_llm`+`pre_tool`,
  `checks/prompt_injection.py`). Heurística + patrones (pura, es+en) en 6
  categorías: `instruction_override`, `role_switch` (jailbreak/DAN),
  `system_prompt_exfiltration`, `delimiter_smuggling`, `encoding_smuggling`,
  `tool_credential_coercion`. En `pre_tool` escanea también `tool_args`.
  Detector tras un Protocol `InjectionDetector` (futuro backend de modelo).
  Default `block`, `warn` en `learning_mode`.
- ✅ **`task_11_07`** — **`content_safety`** (`pre_llm`+`post_llm`,
  `checks/content_safety.py`). Clasifica en categorías de seguridad mediante un
  guard model (LlamaGuard / ShieldGemma) tras una seam inyectable
  `SafetyClassifier` + el extra **OPCIONAL** `shared-guardrails[content-safety]`
  (`shared-llm`, lazy). Sin guard model configurado → resultado tipado
  _unavailable_ (NUNCA un "safe" fingido). Default `block`.
- ✅ **`task_11_08`** — **`code_safety`** (`post_llm`+`post_tool`,
  `checks/code_safety.py`). Análisis estático puro: (1) **AST de Python**
  (`eval`/`exec`, `subprocess(shell=True)`, import dinámico, deserialización
  insegura, escritura fuera del workspace, exfiltración de red) + (2) **regex de
  shell** que siempre corre (`rm -rf /`, pipe-to-shell, `chmod 777`, fork bomb,
  `dd`/`mkfs`). Severidad por constructo con suelo configurable; default
  `block`.
- ✅ **`task_11_09`** — **Siete tipos restantes** (`checks/`):
  `output_structure` (JSON-Schema con `jsonschema`, default
  `retry_with_feedback`), `allowed_domains` (allowlist de hosts con
  suffix-match, default `block`), `cost_ceiling` (umbral por llamada/acumulado,
  default `block`), `factuality_citations` (afirmaciones numéricas/citadas sin
  cita, default `warn`), `topic_restriction` (adherencia/lejanía por keyword con
  seam `TopicMatcher`, default `warn`), `rate_per_agent` (ventana deslizante con
  `RateStore`/`clock` inyectables, default `block`) y `forbidden_actions`
  (deny/allowlist de tools en `pre_tool` — el enforcement de `allowed_tools` que
  difirió la auditoría 06.14, default `block`).

### Fase C — Catálogo de Precios

- ✅ **`task_11_10`** — **Modelo ORM `ModelPrice`**
  (`db/model_prices.py`): catálogo **platform-global** (sin `tenant_id`),
  **USD canónico** (`CANONICAL_CURRENCY="USD"` + CHECK), `modality` enum,
  `input_price`/`output_price` (`Numeric(18,10)`), `cached_input_price`
  **nullable** (prompt caching; helper `cached_input_price_or_default()` ~10% de
  input), `source` enum (litellm/manual/provider_api), vigencia
  `effective_from`/`effective_to`. Índice parcial único `uq_model_prices_current`
  (un periodo abierto por clave). Helper puro `select_current_price(...)`.
- ✅ **`task_11_11`** — **Migración `0049_model_prices` + RLS de lectura
  global**: tabla sin `tenant_id`; `ENABLE` + `FORCE` + una sola política
  `model_prices_global_read` `FOR SELECT USING (true)` y **ninguna política de
  escritura**, de modo que una sesión tenant (NOBYPASSRLS) lee todo pero no
  escribe, y la sesión System-Admin (BYPASSRLS) escribe libremente (espeja
  `marketplace_listings_*_read` 0041). Reversible (up/down/up probado).
- ✅ **`task_11_12`** — **Endpoints CRUD** (`routers/model_prices.py` +
  `schemas/model_prices.py`): **split lectura/escritura**. Escrituras en
  `admin_router` (`/admin/model-prices`, `require_system_admin` sobre
  `get_admin_session` BYPASSRLS — tenant_admin/member 403); lecturas en `router`
  (`/model-prices`, abiertas a cualquier autenticado). `POST` create (409 si ya
  hay periodo abierto), `PATCH` update (clave inmutable), `DELETE` supersede
  (cierra `effective_to=now()`, sin hard-delete), `GET` list (filtros +
  paginación), `GET /{id}`, `GET /current`. **USD-only** (ningún endpoint acepta
  `currency`).
- ✅ **`task_11_13`** — **Snapshot del precio por model_call**
  (`db/price_snapshot.py`): el model_call es un step en `executions.steps_log`
  (no hay tabla aparte). `compute_price_snapshot` congela los precios unitarios
  vigentes (USD) + `price_snapshot_at` + `cost_usd` (cached al rate cacheado con
  fallback ~10%). Columnas roll-up nullable en `executions`. Precio ausente →
  snapshot tipado `unknown` (nunca un cero/fake). Migración reversible
  **`0050_execution_price_snapshot`**.
- ✅ **`task_11_14`** — **Pantalla 'Modelos & Precios'** (System Admin,
  `apps/admin-panel/app/admin/model-prices/page.tsx`): listado + filtros + toggle
  `current_only`, crear/editar/superseder en diálogo (clave inmutable),
  histórico por modelo + gráfica SVG de precio-en-el-tiempo (sparkline, sin
  añadir recharts). Escrituras tras `<RoleGuard min="system_admin">`. E2E
  `admin-models-prices.spec.ts` **escrito, no ejecutado**.

### Fase D — Sincronización de Precios

- ✅ **`task_11_15`** — **Servicio de sync** (`pricing/litellm_sync.py`): lee el
  JSON comunitario de LiteLLM **solo como fuente de datos** (ADR 0021 — sin dep
  `litellm`, sin runtime). `parse_feed`/`map_entry` mapean a `MappedPrice`
  normalizando a **USD per-1M** (coste per-token × 1.000.000). UPSERT con
  **effective-dating** de Fase C; una fila `source=manual` no se pisa salvo
  `overwrite_manual`. Guard de **subida >10%** difiere el cambio. Fetch tras un
  Protocol `PriceFeedFetcher` inyectable (red MOCKEADA en tests). Endpoint
  `POST /admin/model-prices/sync`.
- ✅ **`task_11_16`** — **Diff visual + confirmación obligatoria si >10%**: flujo
  en dos pasos. **(1) DRY-RUN** `compute_sync_diff(...)` devuelve un diff por
  modelo (added/updated/unchanged/increased/removed + %) **sin escribir**.
  **(2) APPLY** `apply_sync_from_litellm(..., confirm=False)` **rechaza el apply
  completo** (`LargeIncreaseNotConfirmedError`) si algún precio sube >10% salvo
  `confirm=True`. Endpoints `.../sync/diff` (200) y `.../sync/apply` (409 si no
  confirmado). Frontend: diálogo con tabla old→new y **gate de confirmación**
  (checkbox) que solo aparece con `has_large_increase`. E2E `prices-diff.spec.ts`
  **escrito, no ejecutado**.
- ✅ **`task_11_17`** — **Detección de modelos nuevos y descontinuados**:
  `classify_models(...)` pura y determinista etiqueta cada modelo
  `new`/`discontinued`/`changed`/`unchanged`. Un modelo del catálogo ausente del
  feed → DISCONTINUED (**marcado, nunca borrado** — histórico + snapshots siguen
  válidos). Lado escritura opcional `discontinue_dropped_models(...)` + flag
  `discontinue_missing` (cierra el periodo abierto respetando `source=manual`).
- ✅ **`task_11_18`** — **Sincronización programada configurable**: job de Celery
  Beat `workers.sync_model_prices` (`apps/workers/src/workers/price_sync.py`).
  Cadencia configurable vía `Settings.price_sync_cron` (env
  `WORKERS_PRICE_SYNC_CRON`, default `0 4 * * *`; cron malformado degrada al
  default). Palanca live `price_sync_enabled` (`platform_settings`, solo System
  Admin) leída al inicio de cada corrida (OFF → no-op). Guard >10% incluso
  programado (difiere para confirmación manual). El catálogo es global; el job
  escribe con el rol BYPASSRLS del worker (un tenant no puede dispararlo).
- ✅ **`task_11_19`** — **Audit log de cada sync**: tabla append-only
  `price_sync_audit` (`db/price_sync_audit.py`), **platform-global**, misma RLS
  que `model_prices` (`FOR SELECT USING (true)` + ninguna política de escritura
  → inmutable/append-only). Una fila por corrida: `actor` (`user:<uuid>` o
  `scheduler`), `trigger`, `source`, `feed_url`, contadores,
  `held_large_increases`, `confirmed`, `diff` JSONB compacto. Cableado en la
  MISMA transacción que las escrituras del catálogo. Endpoint
  `GET /admin/model-prices/sync/audit`. Migración reversible
  **`0051_price_sync_audit`**.

### Fase E — Observabilidad de Guardrails y Cierre

- ✅ **`task_11_20`** — **Tabla `guardrail_events` + dashboard del tenant**.
  Tabla **tenant-owned** (`tenant_id` NOT NULL + RLS `FOR ALL`, migración
  **`0052_guardrail_events`**), append-only/inmutable (solo `created_at`).
  **Invariante de enmascarado**: el detalle (`detail` + `detail_payload`) lleva
  SOLO un resumen enmascarado — el secreto/PII en crudo NUNCA se persiste; el
  recorder (`api_server.guardrails.events`) filtra el payload por un
  **allowlist** de claves seguras y dropea cualquier clave de contenido crudo
  (defensa en profundidad). Endpoints `GET /guardrails/events` (paginado +
  filtros type/severity/hook*point/since/until) y `GET /guardrails/dashboard`
  (agregados by_type / by_severity / serie por día + recientes), ambos
  `require_tenant_admin` sobre `get_tenant_session` (RLS). Página
  `apps/admin-panel/app/admin/guardrails/page.tsx`
  (`<RoleGuard min="tenant_admin">`). E2E `guardrails-dashboard.spec.ts`
  **escrito, no ejecutado**. \_Estado real: implementado en el working tree;
  pendiente de commit y de tick de checkbox — ver [Pendiente](#pendiente).*
- ⏳ **`task_11_21`** — **Alertas configurables (X violaciones/hora dispara
  alerta)**. **NO IMPLEMENTADA en este plan.** El recorder/dashboard de 11_20
  dejan los `guardrail_events` listos como substrato (los docstrings de
  `db/guardrail_event.py` y `guardrails/events.py` referencian explícitamente
  "the configurable alerts of task_11_21"), pero **no existe** ni el modelo de
  configuración de alertas, ni el endpoint, ni el evaluador de umbral
  por-ventana, ni el `tests/integration/test_guardrail_alerts.py` que nombra la
  tarea. El despacho debía reutilizar el notificador del Plan 10
  (`notification-dispatcher`). Ver [Pendiente](#pendiente).
- ✅ **`task_11_22`** — **Guardrails del chat de planning**
  (`guardrails/planning.py`): cablea el motor de Fase A/B en la ruta del chat de
  planning de Plan 03 con tres guardrails que **reutilizan** built-ins (ningún
  check nuevo): (1) **topic adherence** vía `topic_restriction` en
  `pre_llm`+`post_llm` (default `warn`); (2) **hallucination check sobre
  NÚMEROS** vía `factuality_citations` (`require_document_citation=True`) en
  `post_llm` (default `warn`); (3) **gate estructural antes de "Generar Plan"**
  vía `output_structure` (JSON-Schema `PLAN_DRAFT_SCHEMA`) como `post_llm` con
  `action=block` — un borrador inválido BLOQUEA y devuelve feedback accionable.
  Cada guardrail disparado se persiste como `guardrail_events` **tenant-scoped +
  RLS** (recorder 11_20, detalle enmascarado) con `agent_label`
  `planning_chat`/`plan_generation`. LLM mockeado en tests
  (`@pytest.mark.cross_tenant`).
- ✅ **`task_11_23`** — **Documentación + ADRs + changelog** (esta entrada, la
  **ADR 0035**, y las referencias `docs/04-reference/guardrails.md` +
  `docs/04-reference/pricing.md`). Documenta lo implementado y **flagea el hueco
  de alcance Budgets/FX** + el estado real de 11_20/11_21.

## Endpoints nuevos

| Endpoint                         | Método          | Rol mínimo                |
| -------------------------------- | --------------- | ------------------------- |
| `/model-prices`                  | GET             | autenticado (lectura RLS) |
| `/model-prices/{id}`             | GET             | autenticado               |
| `/model-prices/current`          | GET             | autenticado               |
| `/admin/model-prices`            | POST            | `system_admin`            |
| `/admin/model-prices/{id}`       | PATCH, DELETE   | `system_admin`            |
| `/admin/model-prices/sync`       | POST            | `system_admin`            |
| `/admin/model-prices/sync/diff`  | POST (dry-run)  | `system_admin`            |
| `/admin/model-prices/sync/apply` | POST (409 >10%) | `system_admin`            |
| `/admin/model-prices/sync/audit` | GET             | `system_admin`            |
| `/guardrails/events`             | GET             | `tenant_admin`            |
| `/guardrails/dashboard`          | GET             | `tenant_admin`            |

> Detalle completo (forma de request/response, RBAC, RLS y notas de
> seguridad) en [`docs/04-reference/guardrails.md`](../04-reference/guardrails.md)
> y [`docs/04-reference/pricing.md`](../04-reference/pricing.md).

## Migraciones (todas reversibles, single head)

| Revisión | Contenido                                                                                                                                                                      |
| -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **0049** | `model_prices` (platform-global, **USD canónico**, `cached_input_price` nullable, vigencia) + RLS de **lectura global** (`FOR SELECT USING (true)`, sin política de escritura) |
| **0050** | Columnas roll-up de **snapshot de precio** en `executions` (nullable/backfill-safe); el snapshot por step va en `steps_log` JSONB                                              |
| **0051** | `price_sync_audit` (platform-global, **append-only** vía misma RLS SELECT-only) — bitácora de cada sincronización                                                              |
| **0052** | `guardrail_events` (**tenant-owned** `tenant_id` NOT NULL + RLS `FOR ALL`, append-only/inmutable, detalle SIEMPRE enmascarado)                                                 |

Single head **`0052_guardrail_events`**. El objetivo de downgrade para probar
el rollback completo del plan es la revisión **`0040_sso_email_domains`**. La
identidad de capas de guardrails (config plataforma/tenant/proyecto) no añadió
migración propia en las tareas cubiertas.

## Paquete + dependencias + configuración nueva

| Item                                | Tipo             | Para qué                                                                                                   |
| ----------------------------------- | ---------------- | ---------------------------------------------------------------------------------------------------------- |
| `packages/shared-guardrails`        | paquete nuevo    | Motor de guardrails declarativo (pipeline + config en capas + 6 acciones + 12 built-in + registry)         |
| `PyYAML>=6.0,<7`                    | dep base         | Parseo de la config declarativa YAML del pipeline                                                          |
| `jsonschema>=4.18,<5`               | dep base         | Guardrail `output_structure` (pura Python, sin modelo) — base, importable en CI                            |
| `shared-guardrails[pii]`            | extra opcional   | Presidio (`presidio-analyzer` + spaCy + NER) para `pii`; lazy, degrada a fallback regex / _unavailable_    |
| `shared-guardrails[content-safety]` | extra opcional   | Guard model (LlamaGuard/ShieldGemma) vía `shared-llm` para `content_safety`; lazy, degrada a _unavailable_ |
| `WORKERS_PRICE_SYNC_CRON`           | env var          | Cadencia del sync programado (`Settings.price_sync_cron`, default `0 4 * * *`; malformado → default)       |
| `price_sync_enabled`                | platform setting | Palanca live enable/disable del sync programado (solo System Admin, leída al inicio de cada corrida)       |
| `litellm_price_feed_url`            | setting          | URL del JSON comunitario de LiteLLM (fuente de datos; override por llamada)                                |
| `LARGE_INCREASE_THRESHOLD`          | constante        | Umbral de subida que exige confirmación (+10%); nunca un número mágico inline                              |

## Decisiones

- **Motor de guardrails declarativo en capas con baseline de plataforma
  bloqueable.** Pipeline puro (sin DB/I/O) en cuatro hooks
  (`pre_llm`/`post_llm`/`pre_tool`/`post_tool`); config YAML editable; 3 capas
  plataforma → tenant → proyecto con campos _lockable_ (los baselines PII /
  secret-leakage / prompt-injection son obligatorios e inviolables desde
  abajo); 6 acciones con precedencia. Registrado en **ADR 0035**.
- **Eventos de guardrail tenant-scoped con detalle SIEMPRE enmascarado.**
  `guardrail_events` es tenant-owned + RLS; el recorder filtra el payload por un
  allowlist de claves seguras y dropea cualquier contenido crudo — el
  secreto/PII nunca llega a la BD. Registrado en **ADR 0035**.
- **Catálogo de precios USD-canónico con snapshot effective-dated por
  llamada.** Catálogo platform-global, USD-only (CHECK), vigencia con un solo
  periodo abierto por clave (índice parcial único), RLS de lectura global;
  cada model_call congela el precio vigente para auditoría histórica correcta.
  Registrado en **ADR 0035**.
- **JSON de LiteLLM como fuente de datos (reafirma ADR 0021).** El sync lee el
  `model_prices_and_context_window.json` comunitario solo como _data feed_;
  NO añade `litellm` ni como dependencia ni como runtime (el catálogo cerrado
  de proveedores de la ADR 0021 sigue intacto). Registrado en **ADR 0035**.
- **Gate de confirmación obligatoria ante subida >10% en el sync.** Una subida
  por encima de `LARGE_INCREASE_THRESHOLD` se difiere (modo summary) o rechaza
  el apply completo (modo two-step) salvo confirmación explícita, también en el
  sync programado. Registrado en **ADR 0035**.

## Pendiente

### ⚠ Hueco de alcance — Budgets, exchange_rates y display_currency (NO implementado)

El **Resumen Ejecutivo y el Alcance** del plan describen tres subsistemas que
**no tienen ninguna tarea numerada** (`task_11_01`..`task_11_23`) y por tanto
**NO se construyeron en este plan**:

1. **Sistema de Budgets** — campos en `Organization`
   (`tenant_budget_amount/_currency/_period/_period_start_day/_period_length_days`)
   y `Project` (`budget_amount/_currency/_period/_paused_by_budget`), umbrales
   de alerta platform-global (default `[80, 90, 100]`), notificaciones a Tenant
   Admins + asistente personal, **pausado automático** de nuevos arranques al
   100% sin matar ejecuciones activas, y **override manual con audit_log**
   (.docx §28.7).
2. **Tabla `exchange_rates`** + job diario `exchange-rates-fetcher` (Celery
   Beat, 06:00 UTC) contra ECB (.docx §29.9).
3. **`Organization.display_currency`** (default EUR) + conversión on-the-fly
   USD→moneda del tenant usando el rate del día de cada execution.

> Esto deja un cabo suelto conocido del Plan 10: `tenant_budget_status` en el
> asistente personal era un **stub tipado** "no disponible todavía" a la espera
> de este motor de presupuesto — sigue siendo un stub.

**Decisión requerida (humana):** definir si se añaden estas tareas como una
fase adicional al Plan 11 o como un **plan de seguimiento** dedicado. No se
debe cerrar el plan como `completed` sin resolver este hueco o aceptarlo
explícitamente.

### Estado real de las tareas de Fase E

- **`task_11_20`** (guardrail_events + dashboard) está **implementado en el
  working tree** (modelo ORM, migración 0052, recorder, endpoints, página de
  admin, e2e spec, `tests/integration/test_guardrail_events.py`) pero **no
  estaba commiteado** al iniciar la tarea de docs y su checkbox sigue `[ ]`. Su
  test automático es node-playwright (sin navegador aquí), igual que el resto de
  e2e del plan.
- **`task_11_21`** (alertas configurables) **NO está implementada** — falta el
  modelo de config de alertas, el endpoint, el evaluador de umbral por-ventana y
  el `tests/integration/test_guardrail_alerts.py`. El despacho debía reutilizar
  el `notification-dispatcher` del Plan 10.

Por ambos motivos, **el plan NO está completo** y NO procede marcar todas las
tareas `[x]` ni declarar el cierre. La tarea de documentación (`task_11_23`) se
entrega igualmente porque su criterio (`test -f
docs/07-changelog/11-guardrails-precios.md`) se cumple y la documentación de lo
ya construido es independiente.

### Otros pendientes

- **Dependencias opcionales de guardrails no instaladas en CI** — los extras
  `shared-guardrails[pii]` (Presidio + spaCy + modelo NER) y `[content-safety]`
  (guard model vía `shared-llm`) son pesados; sus tests están **skip-guardados**
  (`pytest.importorskip`) y la lógica se prueba con fallback regex / clasificador
  mockeado. `[skip-guarded]`.
- **e2e Playwright escritos-no-ejecutados** — `admin-models-prices.spec.ts`,
  `prices-diff.spec.ts` y `guardrails-dashboard.spec.ts` están **escritos pero
  PENDIENTES DE VERIFICACIÓN HUMANA**: el runtime node-playwright de este
  entorno no tiene navegador. El typecheck/lint/build del admin-panel sí pasan y
  el backend está cubierto por pytest.
- **Cableado incremental del motor** — el motor está cableado en el chat de
  planning (`task_11_22`); su integración en **cada** ruta de llamada LLM/tool de
  todos los servicios (workers de ejecución, etc.) es **incremental** y se irá
  completando.
- **Tests humanos pendientes** — `human_11_01`…`human_11_04` (enmascarado PII
  end-to-end, redacción de secret leakage + alerta, cost ceiling aborta,
  sincronización de precios con diff/confirmación/audit) quedan **pendientes de
  ejecutar por un humano**.

## Verificación

- `pre-commit run --files <cambiados>` (prettier/markdown) ✅ en la tarea de
  docs.
- Suite del motor + Fase C/D/E en verde por tarea (pytest), con la red MOCKEADA
  en los tests de sync y `@pytest.mark.cross_tenant` en el aislamiento de
  `guardrail_events` y del chat de planning.
- Migraciones 0049..0052 reversibles (up/down/up) con single head; downgrade
  completo del plan a `0040_sso_email_domains`.
- admin-panel: `npm run typecheck && lint && build` ✅; e2e Playwright
  **pendiente de verificación humana**.

## PR

Pendiente de apertura/merge a `main` (lo gestiona el humano tras resolver el
hueco de alcance Budgets/FX, completar `task_11_21` y validar los tests
humanos del plan).
