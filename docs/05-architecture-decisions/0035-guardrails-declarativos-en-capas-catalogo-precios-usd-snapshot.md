---
adr: "0035"
title: Guardrails declarativos en capas con baseline de plataforma bloqueable, eventos tenant-scoped enmascarados, catálogo de precios USD-canónico con snapshot effective-dated, LiteLLM-JSON como fuente de datos y gate de confirmación >10%
status: accepted
date: 2026-05-30
deciders: System Architect, Security
phase: 11-guardrails-precios
---

# ADR 0035 — Guardrails declarativos en capas, eventos tenant-scoped enmascarados, catálogo de precios USD-canónico con snapshot, LiteLLM-JSON como fuente de datos y gate de confirmación >10%

> **Estado: `accepted`.** Recoge las decisiones arquitectónicas tomadas
> durante el Plan 11 que no estaban registradas en un ADR previo: el **motor de
> guardrails declarativo en tres capas con baseline de plataforma bloqueable**;
> la **persistencia de eventos de guardrail tenant-scoped con detalle siempre
> enmascarado**; el **catálogo de precios USD-canónico con snapshot
> effective-dated por llamada**; el uso del **JSON público de LiteLLM solo como
> fuente de datos** (reafirma ADR 0021); y el **gate de confirmación obligatoria
> ante una subida de precio >10%** en la sincronización. El **sistema de
> Budgets**, la tabla **`exchange_rates`** y **`Organization.display_currency`**
> que el Resumen del plan describe **no se implementaron** (no hay tarea
> numerada para ellos); este ADR no decide sobre ellos — quedan abiertos a un
> plan de seguimiento (ver el changelog del Plan 11, sección Pendiente).

## Contexto

El Plan 11 introduce dos capacidades transversales que tocan toda la
plataforma. Varias cuestiones de diseño no quedaban cerradas por ADRs previos:

1. **¿Cómo se declaran y componen los guardrails?** El sistema es multi-tenant
   con RLS desde el día uno (ADR 0001) y necesita controles de seguridad en
   varios puntos del ciclo LLM/tool, configurables a nivel de plataforma, tenant
   y proyecto, sin que un tenant o proyecto pueda debilitar los controles
   obligatorios de la plataforma.

2. **¿Cómo se observa un guardrail que dispara sin filtrar datos sensibles?** El
   detalle de un guardrail que detecta PII o un secreto contiene, por
   definición, el dato sensible. Persistirlo en crudo violaría el principio
   "ningún secreto en claro" y la regla de enmascarado de PII (CLAUDE.md).

3. **¿En qué moneda y con qué vigencia se modela el precio de un modelo, y cómo
   se calcula el coste histórico de una llamada** cuando el precio cambia
   después?

4. **¿De dónde se obtienen los precios** sin reintroducir LiteLLM como runtime
   (retirado del catálogo de proveedores en la ADR 0021)?

5. **¿Cómo se evita aplicar a ciegas una subida de precio anómala** procedente
   del feed comunitario?

## Decisión

### 1. Motor de guardrails declarativo en tres capas con baseline bloqueable

El motor (`packages/shared-guardrails`, paquete nuevo) es una
`GuardrailPipeline` **pura** (sin DB, sin I/O) que evalúa una config
declarativa (YAML/dict) en **cuatro hook points**: `pre_llm`, `post_llm`,
`pre_tool`, `post_tool`. Cada guardrail implementa el Protocol `Guardrail`
(`check(GuardrailContext) -> GuardrailResult`), resuelto por `type` en un
`GuardrailRegistry`. La config se compone en **tres capas** plataforma → tenant
→ proyecto (`layers.py`): una capa más específica reemplaza un guardrail por su
`key`, **excepto** cuando la plataforma lo marcó _locked_ — entonces el override
se ignora (modo default) o se rechaza con `LockedFieldOverrideError` (modo
`strict`), siempre con provenance. Un guardrail _locked_ es además
**obligatorio**: una capa inferior no puede removerlo. Los baselines `pii`,
`secret_leakage` y `prompt_injection` viven bloqueados en la capa de plataforma.

Al disparar, una de **seis acciones** decide el efecto (`block`, `redact`,
`warn`, `retry_with_feedback`, `escalate_to_human`, `transform`); cuando varios
guardrails disparan en un hook, la acción decisiva se elige por **precedencia**
`block > escalate > retry > transform > redact > warn`. Se implementaron los
**12 tipos built-in** del plan; las integraciones pesadas (Presidio para `pii`,
guard model para `content_safety`) son **extras opcionales y lazy** que degradan
a un fallback puro o a un resultado tipado _unavailable_ — **nunca** fingen un
veredicto.

### 2. Eventos de guardrail tenant-scoped con detalle SIEMPRE enmascarado

Cada guardrail que dispara se persiste como una fila en `guardrail_events`,
**tenant-owned** (`tenant_id` NOT NULL + política RLS `FOR ALL` de aislamiento
tenant, migración 0052), **append-only / inmutable** (solo `created_at`). Un
tenant ve EXCLUSIVAMENTE sus propios eventos / dashboard. El detalle —
`detail` más `detail_payload` — lleva **solo un resumen enmascarado**: el
recorder (`api_server.guardrails.events`) copia únicamente un **allowlist** de
claves no sensibles (familias, conteos, offsets, el error de schema resuelto, …)
y dropea cualquier clave que pueda llevar contenido crudo (`redacted_text`,
`matched_text`, `prompt`, `response`, `secret`, …), como defensa en profundidad
por encima del enmascarado que ya hacen los propios guardrails. El secreto/PII
en crudo nunca llega a la BD.

### 3. Catálogo de precios USD-canónico con snapshot effective-dated por llamada

El catálogo `model_prices` es **platform-global** (sin `tenant_id`), **siempre
en USD canónico** (CHECK `currency='USD'`), con **vigencia**
`effective_from`/`effective_to` (NULL == periodo abierto/actual) y un **índice
parcial único** que garantiza un solo periodo abierto por
`(provider, model_id, modality)`. La RLS es de **lectura global** (`FOR SELECT
USING (true)` + ninguna política de escritura): una sesión tenant lee todo el
catálogo; solo la sesión System-Admin BYPASSRLS escribe (espeja
`marketplace_listings_*_read`). Soporta **prompt caching** (`cached_input_price`
nullable, fallback ~10% del input). Cada **model_call** (un step en
`executions.steps_log` JSONB) congela un **snapshot** del precio vigente
(input/output/cached + `price_snapshot_at` + coste USD), de modo que un cambio
posterior del catálogo NO altera el coste histórico. Un superseder cierra el
periodo (`effective_to = now()`), nunca hace hard-delete — el histórico y los
snapshots siguen válidos.

### 4. El JSON de LiteLLM como fuente de datos (reafirma ADR 0021)

El botón "Sincronizar precios" lee el `model_prices_and_context_window.json`
que la comunidad de LiteLLM publica, **solo como fuente de datos**: se parsea y
se normaliza a USD per-1M, pero **NO** se añade `litellm` como dependencia ni
como runtime de proveedor. El catálogo cerrado de proveedores de la ADR 0021
(Claude SDK + Copilot + Azure Foundry APIM + Ollama) sigue intacto. El fetch va
tras un Protocol `PriceFeedFetcher` inyectable, así que los tests mockean la red
(sin red real). Una fila `source=manual` no se pisa salvo `overwrite_manual`.

### 5. Gate de confirmación obligatoria ante una subida >10%

Una subida por encima de `LARGE_INCREASE_THRESHOLD` (+10%, constante nombrada,
nunca un número mágico inline) **no se aplica a ciegas**: en el flujo summary se
**difiere** (se reporta en `large_increases`) y en el flujo two-step
(diff → apply) el apply completo se **rechaza** (`LargeIncreaseNotConfirmedError`
→ HTTP 409) salvo confirmación explícita (`confirm=true`). El gate aplica
también al **sync programado** (Celery Beat), que difiere las subidas grandes
para confirmación manual desde el panel. Cada corrida (manual o programada)
escribe una fila en la bitácora append-only `price_sync_audit`.

## Alternativas consideradas

- **Guardrails como código (no declarativos).** Un guardrail por clase/función
  fijada en código impediría editarlos desde el panel y configurarlos por
  tenant/proyecto. Descartado a favor del pipeline declarativo YAML.
- **Config de guardrails sin lockable.** Una composición de capas sin campos
  bloqueables dejaría que un tenant/proyecto debilitara o removiera los
  baselines de seguridad (PII/secret/injection). Descartado: la plataforma debe
  poder imponer controles inviolables.
- **Persistir el detalle del guardrail en crudo.** Más informativo, pero
  volcaría PII/secretos en la BD. Descartado: el evento guarda solo el resumen
  enmascarado (allowlist + denylist).
- **Catálogo de precios en moneda del tenant.** Obligaría a conversiones y
  perdería una fuente de verdad única. Descartado: USD canónico con conversión
  on-the-fly (la conversión y `display_currency` quedan para un plan de
  seguimiento; ver Pendiente del changelog).
- **Recalcular el coste de una llamada con el precio actual.** Falsearía el
  histórico cuando el precio cambia. Descartado a favor del snapshot
  effective-dated por llamada.
- **Usar LiteLLM como runtime / dependencia para los precios.** Reintroduciría
  lo que la ADR 0021 retiró. Descartado: el JSON se usa solo como _data feed_.
- **Aplicar todas las subidas automáticamente.** Una subida anómala del feed
  comunitario distorsionaría estimaciones y facturación. Descartado: gate de
  confirmación >10%, también en el sync programado.

## Consecuencias

- Un guardrail nuevo es un `type` más registrado en el `GuardrailRegistry` +
  (si aplica) un extra opcional para su backend pesado — sin tocar el pipeline
  ni el esquema. El motor permanece importable en CI aunque los extras
  (Presidio / guard model) no estén instalados.
- La observabilidad de guardrails es tenant-scoped por construcción (RLS) y a
  prueba de filtraciones (enmascarado en el recorder). El dashboard de un tenant
  nunca ve otro tenant.
- El coste histórico de cada llamada es auditable y estable frente a cambios de
  catálogo. Ampliar el catálogo a una nueva modalidad/proveedor es una fila más.
- La sincronización de precios es segura ante un feed anómalo (gate >10%) y
  trazable (audit append-only), y no acopla la plataforma a LiteLLM.
- **El sistema de Budgets, `exchange_rates` y `display_currency` quedan SIN
  implementar** (no hay tarea numerada). Este ADR no los decide; requieren un
  plan/tareas de seguimiento. Mientras tanto, `tenant_budget_status` del
  asistente personal (Plan 10) sigue siendo un stub tipado "no disponible".
- **`task_11_21` (alertas configurables) queda pendiente**: los
  `guardrail_events` están listos como substrato, pero el evaluador de umbral
  por-ventana + el despacho vía el `notification-dispatcher` (Plan 10) no se
  construyeron.
