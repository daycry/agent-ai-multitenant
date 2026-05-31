---
adr: "0038"
title: Evals continuos — LLM-as-judge con modelo de juez distinto, golden dataset promocionado desde tareas reales, merge-gate eval-driven en CI y shadow evals no bloqueantes
status: accepted
date: 2026-05-31
deciders: System Architect, AI Engineer, QA
phase: 14-evals-estadisticas
---

# ADR 0038 — Evals continuos: LLM-as-judge (modelo de juez distinto), golden dataset promocionado desde tareas reales, merge-gate eval-driven en CI y shadow evals no bloqueantes

> **Estado: `accepted`.** Recoge cuatro decisiones arquitectónicas tomadas
> durante el Plan 14 que no estaban registradas en un ADR previo: el
> **LLM-as-judge con un modelo de juez DISTINTO al evaluado**; el **golden
> dataset por tenant promocionado (idempotentemente) desde tareas reales
> APROBADAS**; el **merge-gate eval-driven en CI** que bloquea un merge ante una
> regresión más allá de un umbral configurable; y los **shadow evals NO
> bloqueantes**. El aislamiento por RLS arranca de la **ADR 0001**; la
> comparativa cross-tenant para System Admin reusa el patrón BYPASSRLS de la
> **ADR 0010**; el catálogo cerrado de proveedores LLM (del que sale tanto el
> sujeto como el juez) es la **ADR 0021**; las alertas de drift/outlier reusan el
> notificador del Plan 10 (**ADR 0034**) y el patrón de debounce de las alertas
> de guardrail del Plan 11 (**ADR 0035**).

## Contexto

Hasta el Plan 14 el sistema no tenía forma de saber si los agentes **mejoran o
empeoran** con cada cambio de prompt, ni de medir su **coste/calidad** de forma
agregada. El plan introduce los **evals continuos** y las **estadísticas**.
Varias cuestiones de diseño no quedaban cerradas por ADRs previos:

1. **¿Quién juzga la calidad de la salida de un agente, y cómo se evita que el
   juez tenga el mismo sesgo que el evaluado?**
2. **¿De dónde salen los casos golden contra los que se mide**, y cómo se evita
   que la curación a mano se desincronice de la realidad del producto?
3. **¿Cómo se impide que un cambio de prompt degrade la calidad sin que nadie se
   entere** —cómo se conecta el eval al flujo de merge?
4. **¿Cómo se mide la calidad en producción real sin arriesgar la ejecución del
   usuario** (sin retrasos, sin alterar resultados)?

## Decisión

### 1. LLM-as-judge con un modelo de juez DISTINTO al evaluado

La calidad de la salida de un agente la puntúa un **LLM-as-judge** contra
**criterios custom** del tenant (`eval_criteria`: rúbrica `judge_instruction` +
`weight` + `pass_threshold`). El juez **DEBE ser un modelo distinto al
evaluado** (Decisiones Clave del plan: "usa un modelo distinto al que evalúa")
para evitar el **auto-sesgo** de un modelo que se evalúa a sí mismo: correr con
`judge_model == subject_model` se rechaza con `SameModelJudgeError`. Por item
golden, el juez puntúa cada criterio (`{score, passed, rationale}`), se agrega un
**score global ponderado** por `weight` y un **veredicto** (un único criterio que
falla fuerza `fail`), y se persiste un `EvalResult`.

Tanto el **juez** como el **sujeto** viven detrás de **seams Protocol**
inyectables (`JudgeModel` / `SubjectModel`), de modo que los tests dirigen el
motor con un juez/sujeto **scripted** sin tocar un LLM real (mismo precedente que
el `ScriptedPlanningModel` del agent-runtime); la wiring de producción adapta un
`shared_llm.LLMProvider` (ADR 0021) detrás de la misma superficie. El motor
recibe una `AsyncSession` ya tenant-bound (RLS del caller), así que toda
lectura/escritura se queda dentro del tenant.

### 2. Golden dataset por tenant promocionado desde tareas reales APROBADAS

El benchmark es un **golden dataset por tenant** (Decisiones Clave: "sus datos,
sus criterios"): cada tenant cura su propio dataset con sus criterios. La fila
golden se **promociona desde una tarea real APROBADA** con un click
(`POST /tasks/{id}/promote-to-dataset`), copiando input + output de referencia +
la **procedencia** (`source_task_id` / `source_execution_id`) a la tarea/ejecución
origen. La promoción es **idempotente** por un **UNIQUE parcial**
`(dataset_id, source_task_id)`: una segunda promoción de la misma tarea al mismo
dataset **colisiona** en vez de duplicar. El UNIQUE es _parcial_ (solo cuando
`source_task_id IS NOT NULL AND deleted_at IS NULL`) para que un item golden
escrito a mano (sin tarea origen) nunca colisione.

Promocionar desde tareas reales aprobadas (en vez de inventar casos sintéticos)
mantiene el dataset **anclado a lo que el producto realmente hace** y permite
refrescarlo con tareas nuevas, mitigando el riesgo "datasets desactualizados →
métricas engañosas" del plan.

### 3. Merge-gate eval-driven en CI con umbral configurable

Un cambio en una **definición de prompt de agente** debe demostrar que no
empeora la calidad antes de mergearse. Un workflow nuevo
(`.github/workflows/eval-on-prompt-change.yml`) **aditivo** (no toca `ci.yml`) se
dispara cuando un PR/push toca `seeds/builtin_agents.py`,
`seeds/qa_e2e_automator.py` o el propio harness (`evals/**`): corre el dataset
golden contra la versión **nueva** del prompt, la **diffea contra el baseline**
(`diff_runs`, task_14_06) y aplica el **merge-gate**. La decisión del gate
(`gate_decision`) es una **función pura** sobre el `RunDiff`: `REGRESSED` más allá
del **umbral configurable** (`EVAL_REGRESSION_THRESHOLD`, default `0` = "cualquier
caída de pass rate regresa", el más estricto) → **exit no-cero que bloquea el
merge** (task_14_08); `IMPROVED` / `UNCHANGED` → exit 0. El umbral es un **named
constant** operator-overridable, nunca un número mágico.

CI **no trae claves de proveedor LLM por defecto**, así que el _workflow_ gatea el
paso vivo a que exista un secreto de proveedor (cualquiera de los cuatro caminos
de ADR 0021). En su ausencia corre la CLI en `--dry-run` (valida el arg-parse +
la resolución de umbral/dataset/baseline y sale 0) y hace **skip-with-notice** —
de modo que el lint/test gate del repo nunca depende de un secreto, y el
merge-gate real se ejercita solo donde hay proveedor.

### 4. Shadow evals NO bloqueantes

Para medir calidad en **producción real** sin arriesgar la ejecución del usuario,
una **muestra aleatoria** (5% default, `EVAL_SHADOW_SAMPLE_RATE`) de tareas reales
**completadas** se replica en background a través del revisor/juez para
**registrar** una señal de calidad. La decisión vinculante (Decisiones Clave):
los shadow evals **NUNCA bloquean ni alteran la ejecución real**. Nada en el
camino shadow escribe una fila `tasks` / `executions`; produce su **propio**
`EvalRun` (contra un dataset de tipo `shadow`) + un `EvalShadowRecord` que enlaza
la tarea muestreada con su run + veredicto. El muestreo es un **hash determinista
semillado** (`(seed, task_id)`), así que re-correr elige el MISMO set y un test lo
predice exactamente.

Sobre esa señal, la **detección de drift** alerta solo ante una caída
**SOSTENIDA** (las últimas `EVAL_DRIFT_WINDOW`=3 ventanas caen cada una ≥
`EVAL_DRIFT_DROP_THRESHOLD`=0.1), no un bache puntual, con **debounce** por
`(tenant, dataset)` y **una** alerta al Tenant Admin vía el notificador del Plan
10 (ADR 0034) — mismo patrón que las alertas de outlier (floor de tasa de éxito o
desviación `stddev_k` sobre la media del tenant) y el debounce de guardrail-alert
del Plan 11 (ADR 0035).

## Alternativas consideradas

- **Que el mismo modelo se juzgue a sí mismo (sin restricción de juez distinto).**
  Un modelo evaluándose tiende a sobrepuntuarse (auto-sesgo). Descartado: el juez
  debe ser un modelo distinto; mismo-modelo se rechaza con error.
- **Reglas/heurísticas fijas en vez de LLM-as-judge** (regex, asserts). No capturan
  criterios cualitativos ("sigue el tono de marca", "PEP 8"). Descartado a favor de
  criterios custom puntuados por un juez LLM (con revisión humana periódica de
  samples como mitigación del sesgo, por el riesgo del plan).
- **Datasets sintéticos curados a mano.** Se desincronizan de lo que el producto
  realmente hace. Descartado a favor de promocionar tareas reales aprobadas
  (refrescables), con procedencia.
- **Promoción no idempotente (insertar siempre).** Promocionar dos veces la misma
  tarea duplicaría la fila golden y sesgaría las métricas. Descartado: UNIQUE
  parcial `(dataset_id, source_task_id)`.
- **Merge-gate bloqueante incondicional en CI** (fallar si no hay proveedor). CI no
  tiene claves LLM por defecto; bloquear todo merge sería inviable. Descartado a
  favor de gatear el paso vivo a un secreto + dry-run/skip-with-notice sin él.
- **Replicar el 100% de las tareas en eval (no una muestra).** Dobla el coste LLM y
  la carga. Descartado: muestra configurable (5% default).
- **Shadow evals que reusan/parchean la ejecución real.** Cualquier escritura sobre
  `tasks`/`executions` arriesga la corrida del usuario. Descartado: el shadow path
  produce filas propias y nunca toca la ejecución real.

## Consecuencias

- Un cambio de prompt que empeora la calidad **se bloquea en CI** (donde hay
  proveedor), con un diff detallado de qué items del dataset regresaron.
- La calidad se mide en **producción real** sin riesgo (shadow no bloqueante) y un
  declive **sostenido** alerta al Tenant Admin (drift), igual que un agente outlier.
- El golden dataset queda **anclado a tareas reales aprobadas** y es refrescable,
  por tenant.
- Las estadísticas / dashboards / export son **tenant-scoped (RLS)**; la única
  superficie cross-tenant (`/admin/cross-tenant-stats`) es **System-Admin-only**
  sobre BYPASSRLS y estrictamente agregada (sin PII / secretos).
- **Pendiente (no decidido aquí):** el **toggle de moneda del tenant** del
  explorador de runs depende del sistema FX (`exchange_rates`/`display_currency`)
  **no construido** (gap del Plan 11 sin tarea numerada): los costes se exponen en
  **USD canónico** y no se fabrica conversión. El **merge-gate vivo** y los tests
  humanos `human_14_*` requieren un **stack vivo + un proveedor LLM real**; los
  specs Playwright de los dashboards están escritos-no-ejecutados; la exportación
  **PDF está degradada** a HTML imprimible. Ver el changelog del Plan 14, sección
  Pendiente.
