---
plan_id: 14-evals-estadisticas
title: Sistema de Evaluación de Calidad y Estadísticas
completed_at: null
docs_language: es
---

# Plan 14 — Sistema de Evaluación de Calidad y Estadísticas

## Resumen

Cierra el lazo de **calidad y coste** del sistema: sin evals no hay forma de
saber si los agentes mejoran o empeoran con cada cambio de prompt; sin
estadísticas no hay forma de medir el ROI ni el consumo. El plan añade **dos
subsistemas nuevos** —los **evals continuos** y las **estadísticas/consumo del
tenant**— sobre cinco tablas tenant-owned y un puñado de endpoints de
agregación de solo lectura.

Los **evals** parten de un **golden dataset por tenant** (Decisiones Clave del
plan: "sus datos, sus criterios"): una tarea real **APROBADA** se **promociona**
a fila golden con un click (`POST /tasks/{id}/promote-to-dataset`), idempotente
por un UNIQUE parcial `(dataset_id, source_task_id)` —una segunda promoción de
la misma tarea colisiona en vez de duplicar—. Cada dataset lleva **criterios**
custom (rúbrica + peso + umbral de paso) que un **LLM-as-judge** puntúa. El juez
**usa un modelo distinto al evaluado** (Decisiones Clave: evita el auto-sesgo);
correr con `judge_model == subject_model` se rechaza con `SameModelJudgeError`.
El juez y el sujeto viven detrás de **seams Protocol** inyectables, de modo que
los tests dirigen el motor con un juez/sujeto **scripted** sin tocar un LLM
real. Un `EvalRun` materializa una corrida del dataset contra un sujeto (versión
de prompt / agente) y un `EvalResult` el resultado por item; sobre estos se
calculan las **métricas estándar** (pass rate, latencia p50/p95, coste y tokens
medios) y la **comparativa entre dos runs** (diff por métrica + items que
regresan/mejoran + veredicto `regressed`/`improved`/`unchanged`).

Ese diff alimenta el **eval-en-CI**: un workflow nuevo
(`.github/workflows/eval-on-prompt-change.yml`) se dispara cuando un PR/push
cambia una **definición de prompt de agente** (o el propio harness), corre el
dataset golden contra la versión nueva, la **diffea contra el baseline** y
aplica el **merge-gate** —si la pass rate cae más del **umbral configurable**
(`EVAL_REGRESSION_THRESHOLD`, default `0` = "cualquier caída regresa") el job
sale con código no-cero y **bloquea el merge**—. CI no trae claves de proveedor
LLM por defecto, así que el paso vivo está **gateado** a que exista un secreto
de proveedor (cualquiera de los cuatro caminos del catálogo cerrado, ADR 0021) y
en su ausencia corre la CLI en `--dry-run` (valida config + sale 0) y
**skip-with-notice**. En paralelo, los **shadow evals** replican una **muestra
aleatoria** (5% default, operator-configurable) de tareas reales completadas a
través del revisor/juez para registrar una señal de calidad **sin bloquear ni
alterar la ejecución real** (Decisiones Clave); el muestreo es un hash
determinista y semillado. La **detección de drift** alerta solo ante una caída
**sostenida** (N ventanas consecutivas que caen ≥ umbral, default 3 × 0.1), no
un bache puntual, con **debounce** por `(tenant, dataset)` y una sola alerta al
Tenant Admin vía el notificador del Plan 10.

Las **estadísticas** son la cara de lectura del consumo operativo. El
**dashboard de calidad** (`/eval-quality`) agrega los roll-ups `EvalRun` /
`EvalResult` por agente / release de prompt / dataset / criterio + tendencia
diaria. El **dashboard de estadísticas del tenant + consumo + explorador de
runs** (`/tenant-stats`) agrega la tabla `Execution` (coste/tokens
denormalizados por ejecución + el snapshot de precio por step del Plan 11):
tasa de éxito / tiempo / coste medios, top/bottom agentes, resumen de consumo
(coste acumulado, tokens in/out, run más costoso) y un **explorador de runs**
filtrable (ventana / agente / rol / plan / tarea / verdict / modelo / umbral de
coste) con **exportación CSV / XLSX / PDF**. La **identificación de outliers**
marca agentes que destacan o flaquean (floor de tasa de éxito o desviación
estadística sobre la media del tenant) con **alertas configurables** por regla,
debounced, vía el notificador del Plan 10. Todo lo anterior es **tenant-scoped
(RLS)**; la **única** superficie cross-tenant es la **comparativa para System
Admin** (`/admin/cross-tenant-stats`), gateada a `require_system_admin` y
ejecutada sobre la sesión **BYPASSRLS** —estrictamente agregada (counts / rates
/ sums por tenant), nunca una fila de ejecución, prompt, completion o secreto—.

Las 16 tareas se desarrollaron en cuatro fases (A — datasets + promote + CRUD;
B — LLM-as-judge + métricas + diff; C — eval-en-CI + merge-gate + shadow +
drift; D — dashboards + explorador de runs + outliers + export + cross-tenant +
cierre).

> **⚠ Gaps conocidos que NO cierran en este plan.** El **toggle de moneda del
> tenant** del explorador de runs **NO está construido** (depende del sistema FX
> `exchange_rates`/`display_currency` que el Plan 11 marcó como gap de alcance
> **sin tarea numerada**): los costes se muestran en **USD canónico** únicamente.
> El **eval-en-CI** necesita **secretos de proveedor LLM** para correr vivo (sin
> ellos hace skip-with-notice / dry-run). Los specs **Playwright e2e** de los
> dashboards están **escritos pero NO ejecutados** (el runtime node-playwright
> de este entorno no trae navegador). La exportación **PDF está degradada** a un
> HTML listo para imprimir. Los tests humanos `human_14_*` + el **PR a `main`**
> son **human-owned**. Ver [Pendiente](#pendiente).

## Cambios por tarea

### Fase A — Datasets y Eval Runs

- ✅ **`task_14_01`** — **Modelos `EvalDataset` / `EvalDatasetItem` /
  `EvalCriterion` / `EvalRun` / `EvalResult`** (`db/evals.py`). Cinco modelos ORM
  tenant-owned que sostienen los evals continuos: el golden dataset por tenant,
  sus items (con procedencia `source_task_id` / `source_execution_id` a la tarea
  real promocionada), los criterios de juicio (rúbrica / peso / umbral), la
  corrida del dataset contra un sujeto y el resultado por item (scores por
  criterio en JSONB, veredicto, uso). La migración la materializa `task_14_02`
  (`0058_eval_tables`) — el modelo y la migración se aterrizan juntos.
- ✅ **`task_14_02`** — **Promoción de tarea real → golden**
  (`POST /tasks/{task_id}/promote-to-dataset`, `evals/promote.py`, migración
  `0058_eval_tables`). Una tarea **APROBADA** se promociona a fila golden de un
  dataset; la promoción es **idempotente** por el UNIQUE parcial
  `(dataset_id, source_task_id)` (una segunda promoción de la misma tarea
  colisiona en vez de duplicar). El item copia el input / output de referencia +
  la procedencia a la tarea/ejecución origen. Spec Playwright
  `promote-to-dataset.spec.ts` **escrito, no ejecutado**.
- ✅ **`task_14_03`** — **CRUD de datasets, criterios e items** (`routers/evals.py`).
  CRUD completo tenant-scoped (`require_tenant_admin` + `get_tenant_session` →
  RLS) de `eval-datasets`, sus `criteria` y sus `items`; un id ajeno / inexistente
  es un **404** limpio (el filtro `tenant_id` dentro de `get_writable_or_404` es
  el guard por tenant). El subconjunto GET/POST de datasets respalda también la UI
  de "Promote to dataset".

### Fase B — LLM-as-Judge y Métricas

- ✅ **`task_14_04`** — **LLM-as-judge con criterios custom + modelo distinto**
  (`evals/judge.py`). Para cada item golden, el motor toma (o produce) el output
  del sujeto y lo **juzga** contra cada criterio con un **juez LLM que DEBE ser un
  modelo distinto al evaluado** (Decisiones Clave: evita auto-sesgo; usar el mismo
  modelo de juez que el sujeto → `SameModelJudgeError`). Agrega los scores por criterio en un
  veredicto + score global ponderado (un criterio que falla fuerza `fail`) y
  persiste un `EvalResult`. Juez y sujeto son **seams Protocol** inyectables
  (juez/sujeto scripted en tests — sin LLM real). Tenant-scoped (sesión RLS del
  caller).
- ✅ **`task_14_05`** — **Métricas estándar** (`evals/metrics.py`). Funciones
  puras sobre los resultados de un run: **pass rate** (items-weighted, nunca media
  ingenua de rates por run), **latencia p50/p95**, **coste medio** y **tokens
  medios**, que se denormalizan sobre el `EvalRun` al cerrar la corrida (status →
  `completed`).
- ✅ **`task_14_06`** — **Diff entre dos eval runs** (`evals/diff.py` +
  `GET /eval-runs/diff`). El caso canónico: prompt **viejo** (`base`) vs **nuevo**
  (`candidate`) sobre el MISMO dataset → deltas por métrica (pass_rate / latencia /
  coste / tokens), items que **regresan** (pass→fail) o **mejoran** (fail→pass) y
  un veredicto `regressed` / `improved` / `unchanged`. Es una **función pura** (sin
  proveedor, sin cross-tenant); ambos runs se resuelven bajo la RLS del caller
  (404 ajeno) y un diff cross-dataset se rechaza con **422**.

### Fase C — CI y Shadow Evals

- ✅ **`task_14_07`** — **Eval-en-CI** (`.github/workflows/eval-on-prompt-change.yml`
  - `evals/ci_run.py`). Workflow **aditivo** (no toca `ci.yml`) que se dispara al
    cambiar una **definición de prompt de agente** (`seeds/builtin_agents.py`,
    `seeds/qa_e2e_automator.py`) o el propio harness (`evals/**`): corre el dataset
    golden contra la versión nueva, la diffea contra el baseline y aplica el gate. CI
    no trae claves LLM por defecto → el paso vivo está **gateado** a un secreto de
    proveedor (cualquiera de los cuatro caminos de ADR 0021) y, en su ausencia, corre
    la CLI en `--dry-run` + **skip-with-notice**. La decisión del gate
    (`gate_decision`) es una **función pura** unit-testeable.
- ✅ **`task_14_08`** — **Bloqueo de merge si regresión > umbral** (`evals/ci_run.py`,
  exit code). La CLI traduce el veredicto del diff a código de salida: `REGRESSED`
  más allá del **umbral configurable** (`EVAL_REGRESSION_THRESHOLD`, default `0` =
  "cualquier caída regresa") → exit no-cero que **FALLA el job y bloquea el merge**;
  `IMPROVED` / `UNCHANGED` → exit 0. El umbral es un **named constant**
  operator-overridable, nunca un número mágico.
- ✅ **`task_14_09`** — **Shadow evals** (`evals/shadow.py` + migración
  `0059_eval_shadow_records`). Replica una **muestra aleatoria** (5% default,
  `EVAL_SHADOW_SAMPLE_RATE`) de tareas reales **completadas** a través del
  revisor/juez para **registrar** una señal de calidad. **NUNCA bloquea ni altera
  la ejecución real** (Decisiones Clave): no escribe ninguna fila `tasks` /
  `executions`; produce su propio `EvalRun` (contra un dataset de tipo `shadow`) +
  un `EvalShadowRecord` que enlaza la tarea muestreada con su run + veredicto. El
  muestreo es un **hash determinista semillado** (re-correr elige el MISMO set).
- ✅ **`task_14_10`** — **Detección de drift** (`evals/drift.py` + migración
  `0060_eval_drift_state`). Alerta solo ante una caída **SOSTENIDA**: sobre las
  pass rates de las últimas N corridas del dataset (oldest→newest), declara drift
  cuando las últimas `window` (default 3) caen **cada una** ≥ `drop_threshold`
  (default 0.1) vs su predecesora —un bache puntual rodeado de recuperación no
  dispara—. `detect_drift` es una **función pura**; al declarar drift dispara
  **una** alerta al Tenant Admin vía el notificador del Plan 10, con **debounce**
  por `(tenant, dataset)` (`eval_drift_state.last_alerted_at`).

### Fase D — Dashboards y Cierre

- ✅ **`task_14_11`** — **Dashboard de calidad** (`routers/eval_quality.py`
  `/eval-quality` + frontend admin-panel). Agrega los roll-ups `EvalRun` /
  `EvalResult` (no las `Execution`): `GET /eval-quality/dashboard` (totales,
  tendencia diaria de pass rate y desgloses por **agente** / **release de prompt**
  / **dataset** / **criterio**) + `GET /eval-quality/runs` (historial filtrable y
  paginado). Pass rate **items-weighted**. Como un `EvalRun` es **dataset-scoped**
  (no project-scoped), el "por proyecto" del plan mapea a "por dataset". Spec
  Playwright `eval-dashboard.spec.ts` **escrito, no ejecutado**.
- ✅ **`task_14_12`** — **Dashboard de estadísticas del tenant + consumo +
  explorador de runs** (`routers/tenant_stats.py` `/tenant-stats` + frontend).
  Agrega la tabla `Execution`: `GET /tenant-stats/dashboard` (tasa de éxito / tiempo
  / coste medios, top/bottom agentes por tasa de éxito, tendencia diaria),
  `GET /tenant-stats/consumption` (coste acumulado, tokens in/out, run más costoso)
  y `GET /tenant-stats/runs` (explorador filtrable por ventana / agente / rol / plan
  / tarea / verdict / modelo / min-cost, con etiquetas resueltas + retry_count de la
  tarea + modelo de la última llamada). Tenant-scoped (RLS); USD canónico. Spec
  Playwright `tenant-stats.spec.ts` **escrito, no ejecutado**.
- ✅ **`task_14_13`** — **Outliers + alertas configurables** (`stats/outliers.py`
  - `db/outlier_alert_rule.py` + migración `0061_outlier_alert_rules`). Identifica
    agentes outlier por **dos nociones** expresadas como reglas configurables (nunca
    números mágicos): un **floor de tasa de éxito** ("si el agente X baja del 70%,
    avisa") o una **desviación estadística** (`stddev_k` desviaciones por encima de la
    media del tenant en coste/latencia). `detect_outliers` es una **función pura**; al
    romperse una regla dispara **una** alerta por agente vía el notificador del Plan
    10, con **debounce** por regla (`last_fired_at`). Tenant-scoped (RLS) — los agentes
    del tenant A jamás alertan al tenant B.
- ✅ **`task_14_14`** — **Exportación CSV / XLSX / PDF** (`stats/export.py` +
  `GET /tenant-stats/runs/export`). Serializadores puros (DB-free) del explorador de
  runs: **CSV** (stdlib, UTF-8-BOM para Excel), **XLSX** (openpyxl, wheel pure-Python
  pip-clean) y **PDF degradado** a un `text/html` listo para imprimir (la imagen del
  api-server no trae renderizador PDF nativo — mismo criterio que el docs-viewer; el
  "Guardar como PDF" del navegador cierra el lazo). Acotado (no streaming) a
  `MAX_EXPORT_ROWS = 5000`. Si falta el wheel `openpyxl`, `format=xlsx` devuelve un
  **501** limpio (nunca un 500). **Sin fugas**: la exportación solo lleva los campos
  operativos que el explorador JSON ya expone — ningún prompt / completion / credencial
  / `steps_log`. Coste en **USD canónico** (la columna de moneda del tenant queda fuera
  — gap FX del Plan 11).
- ✅ **`task_14_15`** — **Comparativa cross-tenant (System Admin)**
  (`routers/cross_tenant_stats.py` `GET /admin/cross-tenant-stats`). La **ÚNICA**
  superficie deliberadamente cross-tenant del plan: un operador de plataforma compara
  tasa de éxito / coste / throughput por tenant lado a lado. Gateada en capas:
  `require_system_admin` rechaza con **403** a un `tenant_admin` / `tenant_user`
  **antes** de cualquier query, y `get_admin_session` corre la agregación sobre el
  rol **BYPASSRLS** (una sesión tenant estaría RLS-clamped a un tenant y no podría
  producir la comparativa). Estrictamente **agregada** (`GROUP BY` tenant → counts /
  rates / sums, joined a `organizations` para la etiqueta): ninguna fila de ejecución,
  prompt, completion, `steps_log`, credencial ni PII cruza tenants. Pura agregación
  sobre filas existentes — **sin migración**.
- ✅ **`task_14_16`** — **Documentación + ADRs + changelog** (esta entrada, la
  **ADR 0038**, y la referencia
  [`evals-stats.md`](../04-reference/evals-stats.md)). Documenta lo implementado y
  **flagea los gaps conocidos** (toggle de moneda del tenant no construido — gap FX
  del Plan 11; eval-en-CI requiere secretos de proveedor; specs Playwright
  escritos-no-ejecutados; PDF degradado; tests humanos + PR pendientes).

## Endpoints nuevos

### Evals — datasets / criterios / items / promote / runs (JWT + `tenant_admin`, RLS)

| Endpoint                               | Método             | Para qué                                     |
| -------------------------------------- | ------------------ | -------------------------------------------- |
| `/eval-datasets`                       | GET / POST         | Listar / crear golden datasets del tenant    |
| `/eval-datasets/{dataset_id}`          | GET / PUT / DELETE | Leer / editar / borrar (soft) un dataset     |
| `/eval-datasets/{dataset_id}/criteria` | GET / POST         | Listar / crear criterios de un dataset       |
| `/eval-criteria/{criterion_id}`        | GET / PUT / DELETE | Leer / editar / borrar un criterio           |
| `/eval-datasets/{dataset_id}/items`    | GET / POST         | Listar / crear items golden de un dataset    |
| `/eval-dataset-items/{item_id}`        | GET / PUT / DELETE | Leer / editar / borrar un item               |
| `/eval-runs/{run_id}`                  | GET                | Leer una corrida + sus métricas              |
| `/eval-runs/diff`                      | GET                | Diff base vs candidate (`?base=&candidate=`) |
| `/tasks/{task_id}/promote-to-dataset`  | POST               | Promocionar una tarea aprobada a fila golden |

### Dashboards de calidad y estadísticas (JWT + `tenant_admin`, RLS)

| Endpoint                    | Método | Para qué                                                             |
| --------------------------- | ------ | -------------------------------------------------------------------- |
| `/eval-quality/dashboard`   | GET    | Calidad agregada por agente / release / dataset / criterio + trend   |
| `/eval-quality/runs`        | GET    | Historial de eval runs filtrable + paginado                          |
| `/tenant-stats/dashboard`   | GET    | Tasa éxito / tiempo / coste medios + top/bottom agentes + trend      |
| `/tenant-stats/consumption` | GET    | Resumen de consumo (coste acumulado, tokens in/out, run más costoso) |
| `/tenant-stats/runs`        | GET    | Explorador de runs (una fila por ejecución) filtrable + paginado     |
| `/tenant-stats/runs/export` | GET    | Exportar el explorador a `?format=csv\|xlsx\|pdf`                    |

### Comparativa cross-tenant (JWT + **System Admin** + BYPASSRLS)

| Endpoint                    | Método | Para qué                                                   |
| --------------------------- | ------ | ---------------------------------------------------------- |
| `/admin/cross-tenant-stats` | GET    | Comparativa por tenant (success rate / coste / throughput) |

## Migraciones nuevas

| Revision                   | Tabla(s)                                                                                            | Para qué                                                                                                                                                                                 |
| -------------------------- | --------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `0058_eval_tables`         | `eval_datasets`, `eval_dataset_items`, `eval_criteria`, `eval_runs`, `eval_results` (+ RLS FOR ALL) | Fundación de los evals: golden dataset por tenant + items (procedencia) + criterios + corridas + resultados. UNIQUE parcial `(dataset_id, source_task_id)` para la promoción idempotente |
| `0059_eval_shadow_records` | `eval_shadow_records` (+ RLS FOR ALL)                                                               | Registro de shadow evals: enlaza la tarea/ejecución real muestreada con su `eval_run` shadow + veredicto + `sample_rate`                                                                 |
| `0060_eval_drift_state`    | `eval_drift_state` (+ RLS FOR ALL)                                                                  | Estado/debounce de la alerta de drift, una fila por `(tenant, dataset)` (`last_alerted_at`)                                                                                              |
| `0061_outlier_alert_rules` | `outlier_alert_rules` (+ RLS FOR ALL)                                                               | Regla configurable de alerta de outlier (floor de éxito o `stddev_k`) + debounce (`last_fired_at`)                                                                                       |

> Cadena de migraciones: head previo `0057_webhook_event_replay` → `0058` →
> `0059` → `0060` → `0061`. Todas reversibles (probado por un ciclo up / down a
> `0040_sso_email_domains` / up). **Single head** en **`0061_outlier_alert_rules`**.
> La comparativa cross-tenant (`task_14_15`) es **pura agregación sobre filas
> existentes — sin migración**.

## CI nuevo

| Workflow                                      | Disparador                                                                                                             | Para qué                                                                                                                                                                                   |
| --------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `.github/workflows/eval-on-prompt-change.yml` | `push`/`pull_request` que toca `seeds/builtin_agents.py`, `seeds/qa_e2e_automator.py`, `evals/**` o el propio workflow | Corre el harness de eval, diffea contra el baseline y aplica el merge-gate. **Aditivo** (no toca `ci.yml`); paso vivo gateado a secreto de proveedor, si no `--dry-run` + skip-with-notice |

## Configuración nueva (env / tunables)

| Variable / setting          | Default | Para qué                                                                                                                 |
| --------------------------- | ------- | ------------------------------------------------------------------------------------------------------------------------ |
| `EVAL_REGRESSION_THRESHOLD` | `0`     | Caída máx. de pass rate tolerada antes de que el diff cuente como REGRESIÓN que bloquea el merge (`0` = cualquier caída) |
| `EVAL_SHADOW_SAMPLE_RATE`   | `0.05`  | Fracción de tareas reales completadas que se replican en shadow (nunca bloquea la ejecución real)                        |
| `EVAL_DRIFT_WINDOW`         | `3`     | Nº de ventanas consecutivas en declive que se exigen antes de declarar drift                                             |
| `EVAL_DRIFT_DROP_THRESHOLD` | `0.1`   | Caída mínima de pass rate por ventana que cuenta como declive                                                            |

> Todos son **named constants** en `api_server/evals/constants.py`,
> operator-overridable por env — nunca números mágicos en el call site. El
> dashboard de calidad / estadísticas usa además constantes de ventana
> (`DEFAULT_*_WINDOW_DAYS = 90`, `MAX_*_WINDOW_DAYS = 730`) y `MAX_EXPORT_ROWS =
5000` como invariantes de su contrato (una petición fuera de rango es un 422
> limpio, no un clamp silencioso).

## Decisiones

- **LLM-as-judge con un modelo de juez distinto al evaluado.** El juez puntúa
  cada criterio custom y agrega un veredicto ponderado; correr con
  `judge_model == subject_model` se rechaza (evita auto-sesgo). Juez/sujeto detrás
  de seams Protocol inyectables (scripted en tests). Registrado en **ADR 0038**.
- **Golden dataset por tenant promocionado desde tareas reales aprobadas.** Una
  tarea APROBADA se promociona a fila golden con procedencia, idempotente por
  UNIQUE parcial `(dataset_id, source_task_id)`. Registrado en **ADR 0038**.
- **Merge-gate eval-driven en CI, con umbral configurable.** Un cambio de prompt
  dispara un eval que diffea contra el baseline; una regresión más allá del umbral
  **bloquea el merge** (exit no-cero). El paso vivo está gateado a un secreto de
  proveedor; sin él, dry-run + skip-with-notice. Registrado en **ADR 0038**.
- **Shadow evals NO bloqueantes.** Una muestra aleatoria de tareas reales se
  replica en background solo para registrar señal de calidad; nunca escribe filas
  `tasks` / `executions` ni retrasa la ejecución real. Registrado en **ADR 0038**.
- **Estadísticas tenant-scoped (RLS) + única superficie cross-tenant para System
  Admin.** Todo dashboard/stats/export es tenant-scoped; solo
  `/admin/cross-tenant-stats` cruza tenants, gateado a System Admin sobre
  BYPASSRLS y estrictamente agregado (sin PII / secretos). Aislamiento por RLS
  desde ADR 0001; cross-tenant System Admin desde ADR 0010.
- **Costes en USD canónico.** El toggle de moneda del tenant del explorador
  depende del sistema FX (`exchange_rates`) **no construido** (gap del Plan 11 sin
  tarea numerada): no se fabrica conversión. Ver [Pendiente](#pendiente).

## Verificación

- `pre-commit run --files <cambiados>` (black/ruff/mypy/prettier/markdown/yaml) ✅
  por tarea. Endpoints de agregación tipados + mypy-strict-clean; `fastapi.Query`
  inline (whitelisted para ruff B008) o helpers `Query(...)` casteados.
- Suites pytest en verde por tarea: `tests/unit/test_eval_models.py`,
  `tests/integration/test_eval_endpoints.py`, `tests/integration/test_llm_judge.py`,
  `tests/unit/test_metrics.py`, `tests/unit/test_eval_diff.py`,
  `tests/unit/test_ci_eval_gate.py`, `tests/integration/test_regression_block.py`,
  `tests/integration/test_shadow_eval.py`, `tests/integration/test_drift_detection.py`,
  `tests/integration/test_eval_quality_dashboard.py`,
  `tests/integration/test_tenant_stats_dashboard.py`,
  `tests/integration/test_outlier_detection.py`, `tests/integration/test_stats_export.py`,
  `tests/integration/test_cross_tenant_stats.py`.
- **Aislamiento multi-tenant** marcado `@pytest.mark.cross_tenant`: las stats /
  dashboards / export de un tenant nunca ven filas de otro; solo
  `/admin/cross-tenant-stats` cruza tenants y un `tenant_admin`/`member` recibe
  **403**.
- `actionlint .github/workflows/eval-on-prompt-change.yml` ✅ (`task_14_07`).
- `admin-panel` typecheck / lint / build en verde para los dashboards
  (`task_14_11` / `task_14_12`).
- Single head de migraciones intacto en **`0061_outlier_alert_rules`**.

## Pendiente

### Gaps conocidos (reportados por las fases A–D)

1. **Toggle de moneda del tenant / conversión FX NO construido.** El explorador de
   runs (y todo coste del plan) menciona un toggle "ver en moneda del tenant" / "ver
   en USD" con el rate aplicado. Ese sistema FX (`exchange_rates` /
   `display_currency`) **no tiene tarea numerada y NO se construyó** — el Plan 11 lo
   marcó como gap de alcance en su changelog. Los costes se muestran en **USD
   canónico** únicamente; **no se fabrica conversión**. La columna "coste moneda
   tenant" del explorador / export queda fuera hasta que se construya el sistema FX.
2. **Eval-en-CI necesita secretos de proveedor LLM para correr vivo.** CI no trae
   claves por defecto; el paso vivo está gateado a un secreto de proveedor (cualquiera
   de los cuatro caminos de ADR 0021). Sin él, la CLI corre en `--dry-run` (valida
   config + sale 0) y el workflow hace **skip-with-notice** — el merge-gate real solo
   se ejercita con un proveedor configurado (verificación humana / CI-con-secreto).
3. **Specs Playwright e2e escritos-no-ejecutados.** `promote-to-dataset.spec.ts`
   (`task_14_02`), `eval-dashboard.spec.ts` (`task_14_11`) y `tenant-stats.spec.ts`
   (`task_14_12`) están **escritos pero PENDIENTES DE VERIFICACIÓN HUMANA**: el runtime
   node-playwright de este entorno no tiene navegador. El backend se verificó por
   pytest + el admin-panel por typecheck/lint/build.
4. **Exportación PDF degradada.** `format=pdf` devuelve un documento `text/html`
   listo para imprimir ("Guardar como PDF" del navegador), no un PDF binario (la imagen
   del api-server no incorpora un renderizador PDF nativo — mismo criterio que el
   docs-viewer). CSV y XLSX se entregan completos; `format=xlsx` devuelve un **501**
   limpio si falta el wheel `openpyxl`.
5. **Tokens cacheados sin contador por step.** El resumen de consumo reporta
   `total_tokens_cached = 0`: el snapshot de precio del Plan 11 congela el **precio**
   cacheado, no el **contador**. No se fabrica un valor.
6. **Tests humanos `human_14_*` pendientes.** `human_14_01` (eval CI bloquea
   regresión), `human_14_02` (shadow eval no afecta producción), `human_14_03`
   (estadísticas del tenant accionables), `human_14_04` (exportación funciona).
   Requieren un stack vivo + un proveedor LLM real + meses de uso simulado.

### Cierre del plan

El plan pasa a `pending_human_validation` (no `completed`): faltan los tests
humanos `human_14_*` con un stack vivo y el **PR a `main`**, ambos **human-owned**.
Las 16 tareas tienen su checkbox `[x]` y su test automático en verde (o, para los
checks live/e2e, marcado como verificación humana).

## PR

Pendiente de apertura/merge a `main` (lo gestiona el humano tras validar los tests
humanos del plan y cerrar — o aceptar explícitamente — los gaps de arriba).
