---
title: Catálogo de precios de modelos — Referencia del catálogo, sincronización, snapshot y RBAC
audience: backend-dev, devops, architect
phase: 11-guardrails-precios
updated: 2026-05-30
---

# Catálogo de precios de modelos — Referencia

Esta página documenta el catálogo global de precios del Plan 11: el modelo de
datos USD-canónico con vigencia, los endpoints CRUD y su RBAC, el snapshot de
precio por llamada, la sincronización desde el JSON comunitario de LiteLLM (con
diff, confirmación >10%, modelos nuevos/descontinuados, programación y audit) y
el soporte de prompt caching. Para el motor de guardrails ver
[`guardrails.md`](./guardrails.md); para la matriz de roles general ver
[`rbac.md`](./rbac.md); para los ADRs de fondo ver
[ADR 0035](../05-architecture-decisions/0035-guardrails-declarativos-en-capas-catalogo-precios-usd-snapshot.md)
(catálogo + sync) y
[ADR 0021](../05-architecture-decisions/0021-shared-llm-layer-catalogo-cerrado.md)
(catálogo cerrado de proveedores — el JSON de LiteLLM es solo fuente de datos).

## Modelo de datos (resumen)

| Tabla                          | Tenancy                                                                                                                 |
| ------------------------------ | ----------------------------------------------------------------------------------------------------------------------- |
| `model_prices`                 | **Platform-global** (sin `tenant_id`). RLS de **lectura global** (`FOR SELECT USING (true)`, sin política de escritura) |
| `price_sync_audit`             | **Platform-global**, **append-only** (misma RLS SELECT-only → inmutable)                                                |
| `executions.steps_log` (JSONB) | Tenant-owned (RLS de `executions`): cada step `model_call` lleva su `price_snapshot`                                    |

### `model_prices` — campos clave

| Campo                              | Notas                                                                              |
| ---------------------------------- | ---------------------------------------------------------------------------------- |
| `provider`, `model_id`, `modality` | clave del precio; `modality` enum (text/vision/audio/embedding/image/rerank)       |
| `input_price`, `output_price`      | `Numeric(18,10)`, **USD canónico**                                                 |
| `cached_input_price`               | **nullable** (prompt caching); helper `cached_input_price_or_default()` ~10% input |
| `unit`                             | enum, default `per_1m_tokens`                                                      |
| `currency`                         | CHECK `currency='USD'` (catálogo USD-only)                                         |
| `source`                           | enum litellm/manual/provider_api                                                   |
| `effective_from` / `effective_to`  | vigencia; `effective_to` NULL == periodo abierto/actual                            |

**Regla del precio actual**: índice parcial único `uq_model_prices_current`
sobre `(provider, model_id, modality)` donde `effective_to IS NULL` → un solo
periodo abierto por clave. Helper puro `select_current_price(...)`.

## USD canónico

Todo el catálogo está **siempre en USD** (CHECK en BD; ningún endpoint acepta
`currency`). USD es además la **moneda canónica del coste** de cada ejecución
(snapshot por llamada, abajo).

> **Conversión a la moneda del tenant — implementada en el Plan 11.1.** La
> conversión a la moneda de visualización del tenant
> (`Organization.display_currency`, default `EUR`), la tabla **global**
> `exchange_rates` (job diario ECB, RLS de lectura global) y el **sistema de
> Budgets** (umbrales platform-global, auto-pausa de nuevos arranques al 100%,
> override auditado) que el Plan 11 describió en su Alcance **se construyeron en
> el sub-plan de seguimiento `11.1-budgets-fx`**. El coste sigue siendo USD; la
> moneda del tenant es **solo de visualización**, convertida on-the-fly con el
> rate del **día de cada ejecución**. Ver el
> [changelog 11.1](../07-changelog/11.1-budgets-fx.md) y la
> [ADR 0043](../05-architecture-decisions/0043-coste-usd-canonico-fx-de-visualizacion-budgets-con-auto-pausa.md).

## RLS de lectura global

`model_prices` y `price_sync_audit` activan `ENABLE` + `FORCE ROW LEVEL
SECURITY` con una **única política `FOR SELECT USING (true)`** y **ninguna
política de escritura**:

- una sesión **tenant** (NOBYPASSRLS) **lee** todo el catálogo (lo necesita para
  estimar costes) pero tiene **denegado** todo INSERT/UPDATE/DELETE;
- la sesión **System-Admin / worker** (BYPASSRLS, `get_admin_session` /
  `WORKERS_DATABASE_URL`) escribe libremente.

Esto hace además `price_sync_audit` **append-only / inmutable** desde la app.
Espeja el patrón `marketplace_listings_*_read` (0041) y el endurecimiento de
`marketplace_audit_entries` (0043).

## Endpoints

| Endpoint                         | Método         | Rol mínimo                | Notas                                                                |
| -------------------------------- | -------------- | ------------------------- | -------------------------------------------------------------------- |
| `/model-prices`                  | GET            | autenticado (lectura RLS) | filtros `provider`/`model_id`/`modality`/`current_only` + paginación |
| `/model-prices/{id}`             | GET            | autenticado               | una fila                                                             |
| `/model-prices/current`          | GET            | autenticado               | el periodo abierto en vigor (404 tras supersede)                     |
| `/admin/model-prices`            | POST           | `system_admin`            | crea periodo abierto (409 si ya existe para la clave)                |
| `/admin/model-prices/{id}`       | PATCH          | `system_admin`            | campos mutables; la clave es inmutable; patch vacío → 422            |
| `/admin/model-prices/{id}`       | DELETE         | `system_admin`            | **supersede**: cierra `effective_to=now()` (sin hard-delete)         |
| `/admin/model-prices/sync`       | POST           | `system_admin`            | summary; difiere subidas >10% salvo confirmación                     |
| `/admin/model-prices/sync/diff`  | POST (dry-run) | `system_admin`            | diff por modelo, **no escribe**; feed roto → 502                     |
| `/admin/model-prices/sync/apply` | POST           | `system_admin`            | **409** con la lista de subidas si no confirmado                     |
| `/admin/model-prices/sync/audit` | GET            | `system_admin`            | histórico de sincronizaciones (filtro `trigger` + paginación)        |

Las escrituras van en el `admin_router` (`require_system_admin` sobre
`get_admin_session` BYPASSRLS); un `tenant_admin`/`member` recibe **403**. Las
lecturas van en el `router` abierto a cualquier autenticado (la RLS de lectura
global deja a una sesión tenant leer todo el catálogo).

## Snapshot de precio por llamada (task_11_13)

El "model_call" es un step `model_call` dentro de `executions.steps_log` (no hay
tabla `model_calls` aparte). `compute_price_snapshot` congela los precios
unitarios vigentes (input/output/cached_input, **USD**) + `price_snapshot_at` +
un `cost_usd` calculado de los tokens registrados (los `cached_input_tokens` se
tarifan al rate cacheado, con fallback ~10% del input). Columnas roll-up
nullable/backfill-safe en `executions`. Un cambio posterior del catálogo **NO**
altera el snapshot histórico. Precio ausente → snapshot tipado `unknown`
(`available=False`, coste NULL) — nunca un cero/fake.

## Sincronización desde el JSON de LiteLLM

> **ADR 0021 / ADR 0035.** El sync lee el `model_prices_and_context_window.json`
> que mantiene la comunidad de LiteLLM **solo como fuente de datos**: NO añade
> `litellm` como dependencia ni como runtime. El catálogo cerrado de proveedores
> (Claude SDK + Copilot + Azure Foundry APIM + Ollama) sigue intacto.

- **Parseo + normalización** (`pricing/litellm_sync.py`): `parse_feed` /
  `map_entry` mapean cada entrada a un `MappedPrice` normalizando el coste
  per-token a **USD per-1M** (× 1.000.000); `cached_input_price` ←
  `cache_read_input_token_cost`. Entradas malformadas se saltan tipadas
  (`SkippedEntry`), nunca crash. El fetch va tras un Protocol
  `PriceFeedFetcher` inyectable → la red se mockea en tests.
- **UPSERT con effective-dating**: clave nueva → INSERT periodo abierto
  (`source=litellm`); precio cambiado → cierra el periodo actual + abre uno
  nuevo; precio igual → no-op. Una fila `source=manual` no se pisa salvo
  `overwrite_manual`.
- **Diff + confirmación >10%** (task_11_16): flujo dos pasos. **Dry-run**
  `compute_sync_diff(...)` devuelve un diff por modelo
  (added/updated/unchanged/increased/removed + %) sin escribir. **Apply**
  rechaza el apply completo (`LargeIncreaseNotConfirmedError` → 409) si algún
  precio sube **>10%** (`LARGE_INCREASE_THRESHOLD`) salvo `confirm=true`.
- **Nuevos / descontinuados** (task_11_17): `classify_models(...)` puro etiqueta
  `new`/`discontinued`/`changed`/`unchanged`. Descontinuado = **marcado, nunca
  borrado** (cierra el periodo; histórico + snapshots siguen válidos).
- **Programación** (task_11_18): job de Celery Beat `workers.sync_model_prices`.
  Cadencia configurable (`Settings.price_sync_cron`, env
  `WORKERS_PRICE_SYNC_CRON`, default `0 4 * * *`; malformado → default). Palanca
  live `price_sync_enabled` (`platform_settings`, solo System Admin) leída al
  inicio (OFF → no-op), en **Valores por defecto de plataforma →
  Mantenimiento** del panel. El guard >10% aplica también programado (difiere
  para confirmación manual).
- **Audit** (task_11_19): `price_sync_audit` escribe una fila por corrida
  (`actor` `user:<uuid>` o `scheduler`, `trigger`, `source`, `feed_url`,
  contadores, `held_large_increases`, `confirmed`, `diff` JSONB) en la **misma
  transacción** que las escrituras del catálogo. Un apply rechazado por spike no
  confirmado no escribe ni catálogo ni audit.

## Prompt caching

El catálogo modela el precio del input cacheado en `cached_input_price`
(nullable). Cuando es NULL, `cached_input_price_or_default()` asume ~10% del
precio de input. El snapshot por llamada tarifa los `cached_input_tokens` a ese
rate, de modo que el coste histórico refleja el ahorro del caching.

## Pantalla 'Modelos & Precios' (System Admin)

`apps/admin-panel/app/admin/model-prices/page.tsx`: listado + filtros + toggle
`current_only`, crear/editar/superseder en diálogo (clave inmutable), histórico
por modelo + gráfica SVG de precio-en-el-tiempo (sparkline). Escrituras tras
`<RoleGuard min="system_admin">`. Botón "Sincronizar precios" con diálogo de
diff y gate de confirmación (checkbox) que solo aparece con `has_large_increase`.
Los e2e Playwright (`admin-models-prices.spec.ts`, `prices-diff.spec.ts`) están
**escritos pero pendientes de verificación humana** (sin navegador en CI).

## Migraciones

| Revisión | Contenido                                                                          |
| -------- | ---------------------------------------------------------------------------------- |
| **0049** | `model_prices` (USD-canónico, vigencia, `cached_input_price`) + RLS lectura global |
| **0050** | columnas roll-up de snapshot en `executions` (el snapshot por step va en JSONB)    |
| **0051** | `price_sync_audit` (append-only)                                                   |

Todas reversibles; downgrade del plan a `0040_sso_email_domains`.
