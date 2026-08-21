---
title: Evals de calidad y estadísticas — Referencia
audience: backend-dev, architect, ai-engineer, qa, tenant-admin
phase: 14-evals-estadisticas
updated: 2026-05-31
---

# Evals de calidad y estadísticas — Referencia

Esta página documenta los dos subsistemas del Plan 14: los **evals continuos**
(golden dataset por tenant, LLM-as-judge, métricas, diff, eval-en-CI, shadow,
drift) y las **estadísticas/consumo** (dashboard de calidad, dashboard del
tenant, explorador de runs, export, outliers, comparativa cross-tenant). Para el
ADR de fondo ver
[ADR 0038](../05-architecture-decisions/0038-evals-continuos-llm-as-judge-golden-promote-merge-gate-shadow-cross-tenant.md);
para el aislamiento por RLS [ADR 0001](../05-architecture-decisions/0001-postgres-rls-from-day-one.md);
para el cross-tenant de System Admin [ADR 0010](../05-architecture-decisions/0010-superadmin-cross-tenant.md);
para el catálogo cerrado de proveedores LLM (sujeto + juez) [ADR 0021](../05-architecture-decisions/0021-shared-llm-layer-catalogo-cerrado.md);
para el notificador reusado por las alertas [ADR 0034](../05-architecture-decisions/0034-notificaciones-dispatcher-channeladapter-tres-capas-webhooks-firmados.md).
Para la matriz de roles general ver [`rbac.md`](./rbac.md); para el coste por
ejecución y el snapshot de precio del Plan 11 ver [`pricing.md`](./pricing.md).

## El modelo de evals

Cinco tablas **tenant-owned** (todas `tenant_id` NOT NULL + política FOR ALL de
RLS), creadas en la migración `0058_eval_tables`:

| Tabla                | Para qué                                                                                                                         |
| -------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `eval_datasets`      | Golden dataset por tenant (`kind` `golden`/`shadow`), opcionalmente apuntado a un `target_agent_id`/`target_role`                |
| `eval_dataset_items` | Fila golden: `input`, `expected_output`, procedencia `source_task_id`/`source_execution_id` a la tarea real                      |
| `eval_criteria`      | Criterio de juicio del dataset: `judge_instruction` (rúbrica), `weight`, `pass_threshold`                                        |
| `eval_runs`          | Una corrida del dataset contra un sujeto (`subject_agent_id` / `subject_prompt_version`), `judge_model`, métricas denormalizadas |
| `eval_results`       | Resultado por item del run: `produced_output`, `criterion_scores` (JSONB), `verdict`, uso (latency/tokens/cost)                  |

- **Promoción idempotente**: el UNIQUE **parcial** `(dataset_id, source_task_id)`
  (solo cuando `source_task_id IS NOT NULL AND deleted_at IS NULL`) hace que una
  segunda promoción de la misma tarea al mismo dataset colisione en vez de
  duplicar. Un item escrito a mano (sin tarea origen) nunca colisiona.
- **Costes en USD canónico** (`eval_runs.mean_cost_usd`, `eval_results.cost_usd`,
  `Numeric(14, 6)`). No hay conversión a moneda del tenant (ver
  [Limitaciones](#limitaciones-y-gaps)).

Dos tablas auxiliares más (también tenant-owned + RLS):

| Tabla                 | Migración                  | Para qué                                                                                                         |
| --------------------- | -------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `eval_shadow_records` | `0059_eval_shadow_records` | Registro de shadow eval: enlaza la tarea/ejecución real muestreada con su run shadow + veredicto + `sample_rate` |
| `eval_drift_state`    | `0060_eval_drift_state`    | Debounce de la alerta de drift, una fila por `(tenant, dataset)` (`last_alerted_at`)                             |

## LLM-as-judge

- El juez puntúa cada `eval_criterion` de un dataset y agrega un veredicto +
  score global **ponderado** por `weight`; un único criterio que falla fuerza un
  veredicto `fail`.
- **El juez DEBE ser un modelo distinto al evaluado** (evita el auto-sesgo).
  Correr con `judge_model == subject_model` se rechaza con `SameModelJudgeError`.
- Juez (`JudgeModel`) y sujeto (`SubjectModel`) son **seams Protocol**
  inyectables: los tests usan un juez/sujeto scripted (sin LLM real); producción
  adapta un `shared_llm.LLMProvider` (ADR 0021) detrás de la misma superficie.
- El motor opera sobre una `AsyncSession` ya tenant-bound (RLS), así que toda
  lectura/escritura se queda dentro del tenant.

## Métricas y diff

- **Métricas estándar** (`evals/metrics.py`, funciones puras): **pass rate**
  (items-weighted: `sum(passed_items) / sum(total_items)`, nunca media ingenua de
  rates por run), **latencia p50/p95**, **coste medio** y **tokens medios**. Se
  denormalizan sobre `eval_runs` al cerrar la corrida (`status → completed`).
- **Diff** (`GET /eval-runs/diff?base=&candidate=`): prompt viejo (`base`) vs
  nuevo (`candidate`) sobre el **mismo dataset** → deltas por métrica + items que
  **regresan** (pass→fail) / **mejoran** (fail→pass) + veredicto
  `regressed`/`improved`/`unchanged`. Función pura; ambos runs bajo la RLS del
  caller (404 ajeno); diff cross-dataset → **422**.

## Eval-en-CI y merge-gate

- Workflow `.github/workflows/eval-on-prompt-change.yml` (**aditivo**, no toca
  `ci.yml`), disparado al cambiar `seeds/builtin_agents.py`,
  `seeds/qa_e2e_automator.py` o `evals/**`.
- Corre el dataset golden contra el prompt nuevo, lo diffea contra el baseline y
  aplica el gate. `gate_decision` es **pura**: `REGRESSED` más allá del umbral →
  **exit no-cero que bloquea el merge**; `IMPROVED`/`UNCHANGED` → exit 0.
- **Gateado a secreto de proveedor**: sin claves LLM (el default de CI) corre la
  CLI en `--dry-run` y hace **skip-with-notice**; el merge-gate real solo se
  ejercita con un proveedor configurado.

## La otra mitad del gate: `PUT /agents/{id}` (`task_gov_05`)

El workflow de arriba vigila **dos ficheros del repo**. La vía por la que un
tenant cambia un prompt de verdad —la pantalla de Agentes— no pasa por ningún
fichero versionado, así que tiene su propio gate en el api-server
(`evals/prompt_edit_gate.py` + `evals/prompt_edit_enforce.py`).

- **Se dispara sólo si el prompt CAMBIA** (campo plano o `model_config.system_prompts`,
  comparados en crudo — mismo criterio que el historial de `task_gov_02`).
- **Cuatro resultados**: `passed` / `blocked` / `inconclusive` —los mismos tres
  valores que `GateOutcome` en CI, para que las dos mitades se lean juntas— más
  `not_gated`, que es «este agente no tiene golden set: no hay nada que medir».
- **Qué se hace con el resultado depende del preset de validación humana del
  proyecto**: `production` / `customer-external` **rechazan la escritura** con
  `409` (`error: prompt_eval_regression`) y el mensaje NOMBRA los escenarios que
  empeoraron; `development` / `sandbox` guardan y devuelven el aviso en
  `AgentResponse.eval_gate`.
- **Preset de una plantilla de tenant**: el más estricto de los proyectos de sus
  equipos, para que editar la plantilla no esquive el gate del proyecto estricto.
- **La corrida candidata se persiste en su propia transacción**, así que
  sobrevive al rechazo y se puede abrir en el dashboard de calidad. El juez es el
  MISMO que el de la corrida base (con otro, el diff mediría al juez).
- **Válvula de escape**: `eval_gate_override.reason` (≥ 80 caracteres) en el
  cuerpo del `PUT`. Abre **sólo** un `inconclusive`, nunca una regresión medida, y
  deja una fila en `audit_log` con `action='prompt_eval_gate'` y el motivo
  verbatim. Detalle operativo en
  [`03-guides/persona-y-system-prompt.md`](../03-guides/persona-y-system-prompt.md).
- Usa el mismo `EVAL_REGRESSION_THRESHOLD` que el merge-gate de CI, y el mismo
  techo `MAX_SYNC_EVAL_CALLS` = 200 que `POST /eval-runs` (por encima, el
  resultado es `inconclusive` con el número concreto).

## Shadow evals y drift

- **Shadow** (`evals/shadow.py`): muestra aleatoria (5% default,
  `EVAL_SHADOW_SAMPLE_RATE`) de tareas reales completadas, replicada en background
  para **registrar** señal. **NUNCA bloquea ni altera la ejecución real**: no
  escribe filas `tasks`/`executions`; produce su propio `eval_run` (dataset
  `shadow`) + un `eval_shadow_record`. Muestreo **hash determinista semillado**.
- **Drift** (`evals/drift.py`): alerta solo ante una caída **SOSTENIDA** (últimas
  `EVAL_DRIFT_WINDOW`=3 ventanas caen cada una ≥ `EVAL_DRIFT_DROP_THRESHOLD`=0.1),
  no un bache puntual. `detect_drift` es pura; una alerta al Tenant Admin vía el
  notificador del Plan 10 (ADR 0034), debounced por `(tenant, dataset)`.

## Dashboards, explorador de runs y export

### Dashboard de calidad (`/eval-quality`, `tenant_admin`, RLS)

Agrega los roll-ups `eval_runs` / `eval_results`:

| Endpoint                      | Para qué                                                                                                                                                                  |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET /eval-quality/dashboard` | Totales + tendencia diaria de pass rate + desgloses por **agente** / **release de prompt** / **dataset** / **criterio**. Filtros `agent_id`/`dataset_id`/`prompt_version` |
| `GET /eval-quality/runs`      | Historial de eval runs filtrable + paginado (etiquetas de agente/dataset resueltas)                                                                                       |

> Un `eval_run` es **dataset-scoped** (no project-scoped): el "por proyecto" del
> plan mapea a "por dataset" (el benchmark golden por tenant). Pass rate
> items-weighted.

### Dashboard del tenant, consumo y explorador de runs (`/tenant-stats`, `tenant_admin`, RLS)

Agrega la tabla `Execution` (coste/tokens denormalizados + el snapshot de precio
por step del Plan 11; ver [`pricing.md`](./pricing.md)):

> **Ninguna de estas tres consultas toca `steps_log`** desde la migración
> `0139_executions_steps_rollup` (prod-13 `task_prod13_18`). El modelo de la
> última llamada y el reparto de tokens de entrada/salida son **columnas**
> (`executions.last_model` / `tokens_in` / `tokens_out`), no un
> `jsonb_array_elements(steps_log)` por fila: ese JSONB es el 76 % del peso de la
> tabla y se expandía tanto en el listado como en el predicado de `?model=`. Las
> mantiene honestas la regla de que **todo el que asigna `steps_log` llama acto
> seguido a `db/execution_repo.py::apply_steps_rollup`** — el repositorio
> (`record_execution` / `finalize_execution` / `create_running_execution`) y
> `workers.execution._mark_commit_failed`, que anexa el paso del conflicto de
> rebase en su propia sesión BYPASSRLS. La definición es la misma que tenían las
> expresiones SQL (`last_model` NULL = el run no llamó a ningún modelo), y el
> backfill de la migración las rellenó para todo el histórico.

| Endpoint                        | Para qué                                                                                                                                                                                                          |
| ------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET /tenant-stats/dashboard`   | Tasa de éxito / tiempo / coste medios, **top/bottom** agentes por tasa de éxito, tendencia diaria. Filtros `agent_id`/`role`/`plan_id`                                                                            |
| `GET /tenant-stats/consumption` | Coste acumulado, tokens in/out (cached = 0), run count, coste medio, run más costoso                                                                                                                              |
| `GET /tenant-stats/runs`        | Explorador (una fila por ejecución): etiquetas plan/tarea/agente, modelo de la última llamada, verdict, `retry_count`, duración, tokens, coste USD. Filtros ventana/agente/rol/plan/tarea/verdict/modelo/min-cost |
| `GET /tenant-stats/runs/export` | Exporta el explorador a `?format=csv\|xlsx\|pdf`                                                                                                                                                                  |

**Export** (`stats/export.py`, serializadores puros DB-free):

- `csv` — stdlib, UTF-8-BOM (Excel en Windows abre acentos sin import manual).
- `xlsx` — `openpyxl` (wheel pure-Python pip-clean). Si el wheel falta, el endpoint
  devuelve un **501** limpio (nunca un 500).
- `pdf` — **degradado** a un `text/html` listo para imprimir ("Guardar como PDF"
  del navegador); la imagen del api-server no incorpora un renderizador PDF nativo
  (mismo criterio que el docs-viewer). Lleva una cabecera + un bloque de consumo +
  la tabla de runs.
- Acotado (no streaming) a `MAX_EXPORT_ROWS = 5000`.
- **Sin fugas**: la exportación lleva **solo** los campos operativos que el
  explorador JSON ya expone — ningún prompt / completion / credencial / `steps_log`.

## Outliers y alertas configurables

- `stats/outliers.py` + `outlier_alert_rules` (migración `0061_outlier_alert_rules`,
  tenant-owned + RLS).
- Dos nociones de outlier, ambas como **reglas configurables** (nunca números
  mágicos): **floor de tasa de éxito** ("si el agente X baja del 70%, avisa") o
  **desviación estadística** (`stddev_k` desviaciones por encima de la media del
  tenant en coste/latencia, sobre una ventana `window_days`).
- `detect_outliers` es **pura**; al romperse una regla dispara **una** alerta por
  agente vía el notificador del Plan 10, con **debounce** por regla (`last_fired_at`)
  — mismo patrón que las alertas de guardrail (Plan 11) y de drift.
- Un agente bajo `min_runs` nunca se marca (muestra no significativa); la rama
  `stddev` exige ≥ 2 agentes cualificados.

## Comparativa cross-tenant y RBAC

| Superficie                                                           | Rol mínimo       | Sesión / aislamiento                                                                             |
| -------------------------------------------------------------------- | ---------------- | ------------------------------------------------------------------------------------------------ |
| `/eval-quality/*`, `/tenant-stats/*`, export, CRUD de evals, promote | `tenant_admin`   | `get_tenant_session` → **RLS** tenant-scoped + predicado `tenant_id ==` (defensa en profundidad) |
| `/admin/cross-tenant-stats`                                          | **System Admin** | `get_admin_session` → **BYPASSRLS**; estrictamente agregada                                      |

- **TODO** dashboard / stats / export es tenant-scoped: un tenant nunca ve filas
  de otro (tests `@pytest.mark.cross_tenant`).
- La **única** superficie cross-tenant es `/admin/cross-tenant-stats`
  (`task_14_15`): un operador de plataforma compara tasa de éxito / coste /
  throughput por tenant lado a lado. Gateada a `require_system_admin` (un
  `tenant_admin`/`member` recibe **403** antes de cualquier query) y ejecutada
  sobre BYPASSRLS. Estrictamente **agregada** (`GROUP BY` tenant → counts / rates /
  sums, joined a `organizations` para la etiqueta): **ninguna** fila de ejecución,
  prompt, completion, `steps_log`, credencial ni PII cruza tenants.

## Configuración (env / tunables)

| Variable                    | Default | Para qué                                                                                    |
| --------------------------- | ------- | ------------------------------------------------------------------------------------------- |
| `EVAL_REGRESSION_THRESHOLD` | `0`     | Caída máx. de pass rate tolerada antes de que el merge-gate bloquee (`0` = cualquier caída) |
| `EVAL_SHADOW_SAMPLE_RATE`   | `0.05`  | Fracción de tareas reales replicadas en shadow                                              |
| `EVAL_DRIFT_WINDOW`         | `3`     | Nº de ventanas consecutivas en declive antes de declarar drift                              |
| `EVAL_DRIFT_DROP_THRESHOLD` | `0.1`   | Caída mínima de pass rate por ventana que cuenta como declive                               |

> Todos son **named constants** en `api_server/evals/constants.py`,
> operator-overridable por env. Los dashboards usan además
> `DEFAULT_*_WINDOW_DAYS = 90` / `MAX_*_WINDOW_DAYS = 730` y `MAX_EXPORT_ROWS =
5000` (una petición fuera de rango es un **422** limpio, no un clamp silencioso).

## Limitaciones y gaps

- **Moneda del tenant / conversión FX no construida.** El explorador menciona un
  toggle "ver en moneda del tenant" / "ver en USD" con el rate aplicado. El sistema
  FX (`exchange_rates` / `display_currency`) **no tiene tarea numerada y no se
  construyó** (gap del Plan 11). Los costes se muestran en **USD canónico**; no se
  fabrica conversión. La columna "coste moneda tenant" queda fuera.
- **Eval-en-CI necesita un secreto de proveedor LLM** para correr vivo; sin él hace
  dry-run + skip-with-notice.
- **Specs Playwright e2e escritos-no-ejecutados** (`promote-to-dataset.spec.ts`,
  `eval-dashboard.spec.ts`, `tenant-stats.spec.ts`): el runtime node-playwright de
  este entorno no trae navegador.
- **Export PDF degradado** a HTML imprimible; **tokens cacheados** reportan 0 (el
  snapshot del Plan 11 congela el precio cacheado, no el contador).
