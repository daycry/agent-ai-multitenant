---
plan_id: cortex-f3-identidad
title: "Córtex F3 — identidad evolutiva + reflexión periódica"
completed_at: null
status: pending_human_validation
docs_language: es
---

# Córtex F3 — identidad evolutiva + reflexión periódica

## Resumen

Da al córtex una identidad singleton que **evoluciona de forma acotada,
versionada y auditable**: nombre, valores, rasgos Big-Five, narrativa
autobiográfica, modelo del owner y baseline afectivo; reescrita por un bucle de
reflexión que sintetiza los turnos recientes y aplica el delta **clampeado**.
Gobierno: [ADR 0074](../05-architecture-decisions/0074-rol-system-owner-y-cortex-singleton.md)
(tablas tenant-less) y [ADR 0078](../05-architecture-decisions/0078-bucles-cognitivos-fondo-cortex.md)
(bucles de fondo con kill-switch).

## Cambios

- **Dos tablas tenant-less sobre BYPASSRLS** (migración
  `20260624_0094_cortex_identity.py`): `cortex_identity` (singleton, blob
  `identity_state` JSONB) y `cortex_identity_history` (versionado append-only
  con `diff`). Modelos en `db/cortex_identity.py`, sin `TenantScopedMixin` —
  verificado: `tenant_id` no está entre sus columnas.
- **Capa pura determinista** en
  [`cortex/identity.py`](../../apps/api-server/src/api_server/cortex/identity.py):
  `clamp_traits` (rasgos a [0,1]; basura → 0.5), `clamp_baseline`
  (valence/dominance ∈ [-1,1], arousal ∈ [0,1]), `bounded_update` con la cota
  `BASELINE_MAX_DELTA_PER_REFLECTION = 0.05` por ciclo, `compute_diff`,
  `editable_owner_state` y `apply_reflection_delta`.
- **La separación que importa**: el owner edita `name`/`core_values`/
  `narrative`/`language`/`learning_goals`; `traits`, `mood_baseline`,
  `relationship_model` y `affect_params` los **deriva la reflexión** y el PUT
  los preserva (422 si se intentan tocar). Nadie se sube los rasgos a mano.
- **Bucle de reflexión**: `workers/cortex_reflection.py` (fail-open, delta
  clampeado, narrativa versionada) con su entrada `sched["cortex-reflection"]`
  → `workers.cortex_reflect_scheduled`, cadencia configurable
  (`WORKERS_CORTEX_REFLECTION_CRON`) y **kill-switch
  `cortex.autonomy_enabled`, OFF por defecto**.
- **Endpoints** (`routers/cortex_mind.py`, gate `require_system_owner`):
  `GET /identity` (:381), `PUT /identity` (:401), `POST /reflect` (:445) y
  `GET /journal` (:226).
- **UI**: `app/admin/cortex/identity/page.tsx` — identidad editable, narrativa
  en Markdown, rasgos y copy honesto.
- **El consumo es real, no decorativo**: `identity_preamble` entra en el
  self-context de cada turno (`cortex/self_context.py`), y el baseline derivado
  gobierna el decay del motor afectivo. El productor del `owner_model`
  (`relationship_model`) llegó después, en
  [cortex-identidad-real](cortex-identidad-real.md) — esta fase dejaba el campo
  sin escritor.

## Divergencias respecto al plan (verificadas)

- **La pureza del módulo no se cumple.** El criterio decía literalmente "100 %
  determinista, SIN imports de red/LLM/DB". `cortex/identity.py` importa
  `select`, `IntegrityError` y `AsyncSession`, y aloja tres corrutinas de acceso
  a BD (`get_identity`, `ensure_identity`, `update_identity`). Las **funciones**
  puras lo son; el **módulo** no. El `db/cortex_identity_repo.py` que el plan
  pedía como capa separada no existe.
- **`merge_identity_state(...)` no existe con ese nombre**: su función está
  repartida entre `editable_owner_state` y `apply_reflection_delta`.
- **`narrative` es editable por el owner a propósito**, cuando el plan exigía
  422 al tocarla ("solo la reflexión la muta"). Divergencia deliberada de la
  implementación, no un olvido: está en `OWNER_EDITABLE_FIELDS`.
- **Rutas distintas a las diseñadas**: `PUT /identity` en vez de
  `POST /identity/onboarding` + `PATCH /identity`; `POST /owner/cortex/reflect`
  en vez de `/identity/reflect-now`.
- **Los rasgos se pintan como barras**, no como el radar Big-Five del plan, y
  la identidad vive en una ruta hermana (`/admin/cortex/identity`) en vez de la
  segunda columna de la página de F1.

## Lo que sigue abierto

> **Re-verificado con `grep` el 2026-07-30.** Los cinco puntos de abajo siguen abiertos a esa
> fecha: `propose_identity`, `cortex/onboarding.py`, `list_history`, `GET /identity/history`,
> `identityDiffSummary` y la palabra `coherence` en el worker de reflexión dan **cero
> coincidencias** en `apps/`.
>
> **Aviso de concurrencia, para que nadie lea esta lista como definitiva:** el 2026-07-30 había
> una remediación en curso sobre los ficheros de esta fase (`cortex/identity.py` entre ellos). La
> autoridad sobre el estado de cada casilla son **el plan de la fase y sus tests**, no esta
> sección.

- **No hay co-construcción de la identidad.** El núcleo de la tarea de
  onboarding era que el córtex se **autonombrara** y propusiera valores usando
  su propio grafo, y que el owner confirmara. No existe `cortex/onboarding.py`
  ni `propose_identity(...)`: hay un formulario que el owner rellena a mano.
- **La reflexión no tiene budget.** El plan exigía "el bucle NO puede superar el
  cap". Verificado en `workers/cortex_reflection.py`: no se consulta ningún
  budget, y el kill-switch solo se comprueba en el camino programado — el
  disparo manual desde `POST /owner/cortex/reflect` no mira ni una cosa ni la
  otra. El owner puede pulsar "Reflexionar ahora" sin tope y el gasto de LLM no
  se contabiliza en ninguna parte.
- **El drive `coherence` no se sacia.** El paso 8 del enunciado no existe:
  `coherence` no aparece en el fichero del worker, así que nada se escribe a la
  Redis de F2 tras reflexionar.
- **No hay historial expuesto.** No existe `list_history(...)` para
  `cortex_identity_history` ni el endpoint `GET /identity/history?limit=`; el
  `/journal` lee la tabla pero deduplica narrativas y **descarta el `diff`**.
  Sin ese endpoint el timeline de versiones de la UI es inconstruible — y en
  efecto no existe (`identity-timeline.tsx` y el helper `identityDiffSummary`
  tampoco).
- **La reflexión no usa lo que el plan decía**: lee los últimos 20 turnos de
  `cortex_turns` con SQL directo en vez de `memorizer.recall(scopes=['private'])`,
  sintetiza con `OllamaProvider.complete()` en vez de
  `claude_sdk run_agent(effort=…)` (desviación consciente, documentada en el
  propio módulo), y no marca lo procesado: dos pasadas seguidas re-sintetizan
  los mismos 20 turnos.

Detalle completo por casilla en
[gaps-cortex-2026-07-27.md](../roadmap/gaps-cortex-2026-07-27.md).

## Tests

`test_cortex_identity_dynamics.py`, `test_cortex_identity_model.py`,
`test_cortex_identity_preamble.py` (unidad); `test_cortex_identity.py`,
`test_cortex_f3_identity_endpoints.py`, `test_cortex_f3_reflection.py`
(integración, incluye cross-owner y `relrowsecurity=false` de las tablas).

## Estado de cierre

No cerrable: cinco casillas del plan siguen abiertas con hueco identificado
(onboarding co-construido, budget de la reflexión, saciado de `coherence`,
`/identity/history` + timeline, idempotencia de la síntesis).

## PR

- _pendiente_
