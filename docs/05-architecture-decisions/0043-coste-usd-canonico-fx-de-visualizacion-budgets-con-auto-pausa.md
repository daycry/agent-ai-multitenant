---
adr: "0043"
title: Coste USD canónico + FX solo de visualización con rate por-fecha-de-run + budgets tenant/proyecto con umbrales platform-global y auto-pausa de nuevos arranques
status: accepted
date: 2026-05-31
deciders: System Architect, Finance/Ops
phase: 11.1-budgets-fx
---

# ADR 0043 — Coste USD canónico, FX de visualización por-fecha-de-run y budgets con auto-pausa

> **Estado: `accepted`.** Cierra el hueco de alcance que el Plan 11 dejó
> flageado (ver el changelog del Plan 11, sección Pendiente, y la ADR 0035,
> Consecuencias): el **sistema de Budgets** de tenant/proyecto, la tabla
> **`exchange_rates`** y **`Organization.display_currency`**. La ADR 0035 fijó
> el catálogo de precios USD-canónico con snapshot por llamada; esta ADR decide
> cómo se **convierte** ese coste para mostrarlo en la moneda del tenant y cómo
> se construye el **control de presupuesto** encima.

## Contexto

El Plan 11 dejó dos piezas montadas: un catálogo de precios `model_prices`
**platform-global y USD-canónico** (ADR 0035) y un **snapshot de coste USD por
ejecución** (`Execution.total_cost_usd` + el `price_snapshot` por step en
`executions.steps_log`, task_11_13). El coste, por tanto, ya existe y es estable
frente a cambios de catálogo. Quedaban abiertas tres cuestiones que el Plan 11
describió en su Resumen pero no desglosó en tareas:

1. **¿En qué moneda se MUESTRA el coste a un tenant** que no piensa en USD, sin
   perder la fuente de verdad única ni falsear el histórico cuando el tipo de
   cambio se mueve?
2. **¿Dónde viven los tipos de cambio** y quién los actualiza, en un sistema
   multi-tenant con RLS desde el día uno (ADR 0001)?
3. **¿Cómo se limita el gasto** por tenant y por proyecto, se avisa al cruzar
   umbrales y se detiene el consumo, sin matar trabajo en curso ni dejar que un
   tenant relaje los controles de la plataforma?

## Decisión

### 1. USD canónico; la moneda del tenant es SOLO de visualización

El coste se **almacena y agrega siempre en USD** (se reutiliza tal cual el
snapshot del Plan 11; ningún recálculo). La moneda del tenant
(`Organization.display_currency`, NOT NULL, default `EUR`) es **solo de
presentación**: se convierte **on-the-fly** al renderizar, **nunca** se persiste
un coste convertido. La conversión usa el rate del **día de cada ejecución** (la
fecha del propio run), no el rate de hoy, de modo que el importe mostrado de un
run histórico es estable y reproducible aunque el tipo de cambio cambie después.
Cuando no hay rate exacto para esa fecha se usa el **más reciente anterior**
(fallback); USD→USD es la identidad. El endpoint del runs-explorer / stats
(Plan 14) acepta un override `?display_currency=` y, cuando la moneda efectiva
no es USD, devuelve por cada run su coste USD **más** el convertido + el rate
aplicado y su fecha (trazabilidad / tooltip).

### 2. `exchange_rates` es una tabla platform-global con RLS de lectura global

Un tipo de cambio es una propiedad del **mercado en una fecha**, idéntica para
todos los tenants. Por eso `exchange_rates` **NO** lleva `tenant_id`: es
**platform-global**, exactamente como `model_prices` (ADR 0035). Registra, por
`(currency, as_of_date)`, cuántas unidades de esa moneda compra 1 USD
(`rate_vs_usd`) + la `source`. La RLS es de **lectura global**: `ENABLE` +
`FORCE ROW LEVEL SECURITY` con una **única política `FOR SELECT USING (true)`**
y **ninguna política de escritura** → una sesión tenant (NOBYPASSRLS) lee todo
el catálogo de rates pero tiene denegado INSERT/UPDATE/DELETE; solo la sesión
BYPASSRLS (System Admin / migraciones / el worker del fetcher) escribe.
Restricciones: UNIQUE `(currency, as_of_date)`, CHECK `rate > 0`, CHECK
`currency != 'USD'` (USD es identidad, nunca se almacena como fila).

Un **job diario de Celery Beat** (`fetch-exchange-rates`, 06:00 UTC, cadencia
`WORKERS_FX_FETCH_CRON` configurable) descarga los rates de **ECB** (fuente por
defecto; `fx_source` configurable por System Admin, hoy solo ECB cableado) y
hace UPSERT con el rol BYPASSRLS del worker. Palanca live `fx_fetch_enabled`.
El fetch es **best-effort**: un fallo de red no rompe beat — emite el evento de
ops `fx_fetch_failed` y la conversión cae al rate previo. La red está
**MOCKEADA** en los tests (sin fetch real).

### 3. Budgets tenant/proyecto-scoped, umbrales platform-global, alertas debounced

`Organization` gana un budget tenant-wide
(`tenant_budget_amount/_currency/_period/_period_start_day/_period_length_days`)
y `Project` el peer por proyecto (todos nullable = sin budget). Estos campos, el
**consumo** y los flags de pausa son **tenant/proyecto-scoped (RLS)**. Los
**umbrales de alerta** son en cambio **platform-global** y configurables por el
System Admin (`budget_alert_thresholds`, default `[80, 90, 100]`): los mismos
para todos los tenants, y un tenant **no puede relajarlos**. Un evaluador suma
el coste USD del periodo (por tenant y por proyecto), lo compara con el budget
**convertido a USD**, y al cruzar un umbral dispara **una** alerta vía el
notifier del Plan 10 a los Tenant Admins. El **debounce** es por tabla
`budget_alert_states` (tenant-owned + RLS `FOR ALL`): un brazo por
`(scope, project_id, period_start, threshold)` → una alerta por umbral por
periodo por scope, de modo que una brecha sostenida no re-dispara. El stub
`tenant_budget_status` del asistente personal (Plan 10) pasa a devolver datos
reales.

### 4. Auto-pausa bloquea SOLO el arranque de nuevas ejecuciones; override auditado

Al alcanzar el **100%** del budget (tenant o proyecto) se marca el flag
`tenant_paused_by_budget` / `projects.paused_by_budget`. El orchestrator
consulta este flag en el **arranque** de cada ejecución y **rehúsa enqueue de
una NUEVA ejecución** (la tarea queda `ready` y se re-despacha cuando se
levanta la pausa o entra un nuevo periodo). Las **ejecuciones activas NUNCA se
matan** — el límite es sobre el gasto futuro, no sobre el trabajo en curso. El
**override manual** (System Admin o Tenant Admin, `POST /budgets/pause`)
reanuda y escribe una entrada en `audit_log`
(`action=budget_pause_override`), con una fecha de allowance temporal opcional.

## Alternativas consideradas

- **Almacenar el coste en la moneda del tenant.** Obligaría a conversiones en la
  escritura y perdería la fuente de verdad única + la comparabilidad cross-tenant.
  Descartado: USD canónico, conversión solo de visualización.
- **Convertir con el rate de HOY en vez del de la fecha del run.** El importe
  mostrado de un run histórico cambiaría cada día con el mercado. Descartado: se
  usa el rate de la fecha de cada ejecución (estable y reproducible).
- **`exchange_rates` tenant-scoped.** Duplicaría datos idénticos por tenant y
  abriría la puerta a rates divergentes. Descartado: platform-global con lectura
  global (espeja `model_prices`).
- **Umbrales de alerta por tenant.** Dejaría que un tenant se desactive sus
  propios avisos. Descartado: umbrales platform-global configurables solo por
  System Admin (un tenant no puede relajarlos).
- **Matar las ejecuciones activas al llegar al 100%.** Destruiría trabajo en
  curso y resultados parciales ya pagados. Descartado: la auto-pausa bloquea
  solo el arranque de nuevas, las activas terminan.
- **Override sin auditoría.** Un override de un control de gasto debe ser
  trazable. Descartado: el override escribe `audit_log`.
- **Re-alertar en cada evaluación mientras se sigue sobre el umbral.** Generaría
  ruido. Descartado: debounce por `budget_alert_states` (uno por
  umbral/periodo/scope).

## Consecuencias

- El coste histórico de cada run sigue siendo USD auditable (ADR 0035) y se
  puede **mostrar** en cualquier moneda con el rate de su fecha, sin tocar el
  dato almacenado.
- Añadir una moneda de visualización nueva no requiere migración: basta con que
  el fetcher la pueble en `exchange_rates`.
- La observabilidad y los controles de budget son tenant/proyecto-scoped por
  construcción (RLS), mientras los umbrales quedan bajo control de la
  plataforma. El asistente personal del Plan 10 deja de tener un stub.
- La plataforma puede frenar el gasto descontrolado de un tenant/proyecto sin
  interrumpir trabajo en curso, y todo override queda auditado.
- El sistema queda acoplado a la disponibilidad del feed ECB para rates
  frescos; ante un fallo el fetcher degrada al rate previo y emite una señal de
  ops — no bloquea ni la conversión ni el resto del sistema.
  </content>
