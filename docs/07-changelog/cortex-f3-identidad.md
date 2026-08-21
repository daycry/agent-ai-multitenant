---
plan_id: cortex-f3-identidad
title: "Córtex F3 — identidad evolutiva + reflexión periódica"
completed_at: null
status: pending_human_validation
docs_language: es
---

# Córtex F3 — identidad evolutiva + reflexión periódica

> **Reescrita entera el 2026-08-19, contra el código, fichero por fichero.** La
> versión anterior daba por **ausentes cinco cosas que están implementadas** —el
> budget de la reflexión, el saciado del drive `coherence`, `list_history`,
> `GET /identity/history` y `identityDiffSummary`— y decía que los rasgos se pintan
> como barras cuando hay **radar**. Es el modo de fallo de
> [`verificar-antes-de-implementar.md`](../03-guides/verificar-antes-de-implementar.md)
> §1 aplicado a un changelog: un documento de estado que envejece y luego se cita
> como si fuese la verdad. Lo que cambió y por qué está en
> [«Lo que esta entrada afirmaba y era falso»](#lo-que-esta-entrada-afirmaba-y-era-falso).

## Resumen

Da al córtex una identidad singleton que **evoluciona de forma acotada, versionada
y auditable**: nombre, valores, rasgos Big-Five, narrativa autobiográfica, modelo
del owner y baseline afectivo; reescrita por un bucle de reflexión que sintetiza
los turnos nuevos y aplica el delta **clampeado**. Gobierno:
[ADR 0074](../05-architecture-decisions/0074-rol-system-owner-y-cortex-singleton.md)
(córtex singleton por `owner_user_id`),
[ADR 0078](../05-architecture-decisions/0078-bucles-cognitivos-fondo-cortex.md)
(bucles de fondo con kill-switch y budget) y
[ADR 0157](../05-architecture-decisions/0157-quien-reescribe-la-narrativa-del-cortex.md)
(quién puede reescribir la narrativa).

## Cambios

- **Dos tablas del córtex** (migración
  [`20260624_0094_cortex_identity.py`](../../apps/api-server/migrations/versions/20260624_0094_cortex_identity.py)
  — **0094, no la 0092 del diseño**: esa la ocupó `cortex_threads` de F1):
  `cortex_identity` (singleton por owner, blob `identity_state` JSONB) y
  `cortex_identity_history` (versionado append-only con `diff`). Modelos en
  `db/cortex_identity.py`, sin `TenantScopedMixin` — `tenant_id` no está entre sus
  columnas. **Aislamiento doble desde el 2026-08-19**: filtro `owner_user_id`
  explícito en todo SQL (ADR 0074) **y** RLS de eje owner (`ENABLE` + `FORCE` +
  policy `owner_user_id = app.user_id`) por el
  [ADR 0156](../05-architecture-decisions/0156-aislamiento-estructural-del-cortex.md)
  y la migración `0140_cortex_owner_rls`.
- **Capa determinista** en
  [`cortex/identity.py`](../../apps/api-server/src/api_server/cortex/identity.py):
  `clamp_traits` (rasgos a [0,1]; basura → 0.5), `clamp_baseline`
  (valence/dominance ∈ [-1,1], arousal ∈ [0,1]), `bounded_update` con la cota
  `BASELINE_MAX_DELTA_PER_REFLECTION = 0.05` por ciclo, `compute_diff`,
  `editable_owner_state`, `apply_reflection_delta`, `apply_owner_model_delta`,
  `identity_preamble`, `effective_mood_baseline`, `list_history` y
  `propose_identity`.
- **La separación que importa, y quién la fija.** El owner co-diseña la PROSA
  (`name`/`core_values`/`narrative`/`language`/`learning_goals`); el estado
  derivado NUMÉRICO (`traits`, `mood_baseline`, `relationship_model`,
  `affect_params`) lo mueve **sólo** la reflexión, clampeado y acotado, y un PUT
  que lo intente es **422** (`extra="forbid"`, `schemas/cortex_identity.py`), no un
  campo ignorado en silencio. Que la `narrative` caiga del lado del owner **no es
  un descuido**: es la decisión del
  [ADR 0157](../05-architecture-decisions/0157-quien-reescribe-la-narrativa-del-cortex.md)
  (2026-08-19), que resolvió la contradicción entre el plan —pedía 422— y el
  código. Razón corta: la cota del ADR 0074 es sobre NÚMEROS; la narrativa la
  reescribe la reflexión entera y sin cota, así que prohibírsela al owner no
  protegía ningún invariante y le quitaba el único correctivo a lo que un LLM
  escriba sobre él. La honestidad la sostiene la **procedencia** (`updated_by` +
  `diff` por versión), no la prohibición.
- **Bucle de reflexión gobernado**
  ([`workers/cortex_reflection.py`](../../apps/workers/src/workers/cortex_reflection.py)): kill-switch `cortex.autonomy_enabled` (OFF por defecto) **y** budget
  diario por owner (`REFLECTION_DAILY_CAP = 12`, ventana UTC, claves
  `cortex:budget:{owner}:reflection:{yyyymmdd}` compartidas con el gobierno de F4)
  comprobados en el **núcleo**, así que aplican a los DOS caminos —el beat y el
  botón «Reflexionar ahora»—; el gasto se contabiliza **por intento**, no por
  éxito. **Idempotente por marca** (`metadata_.reflected_through`): sólo sintetiza
  turnos posteriores a la última pasada. **Fail-open** (ADR 0064): LLM caído o JSON
  inválido ⇒ no-op sin nueva versión. Persiste memoria semántica
  `kind='reflection'` y hechos `kind='owner_model'` (protegidos del olvido, ADR 0077) y **sacia el drive `coherence`** del motor PAD de F2 (paso 8 del plan,
  `_satisfy_coherence`). Entrada de beat `sched["cortex-reflection"]` →
  `workers.cortex_reflect_scheduled`, cadencia `WORKERS_CORTEX_REFLECTION_CRON`.
- **Endpoints** (en `routers/cortex_mind.py`, gate `require_system_owner`
  DB-authoritative, sesión BYPASSRLS con filtro `owner_user_id`):
  `GET /owner/cortex/identity` (:479), `PUT /owner/cortex/identity` (:499),
  **`GET /owner/cortex/identity/history?limit=`** (:543 — el timeline de versiones
  CON su `diff`, que el `/journal` aplanaba y descartaba), `POST /owner/cortex/reflect`
  (:581) y `GET /owner/cortex/journal` (:230).
- **UI**: `app/admin/cortex/identity/page.tsx` — identidad editable, narrativa en
  Markdown, **radar Big-Five** (`components/cortex/trait-radar.tsx`, no barras) y
  **timeline de versiones** (`components/cortex/identity-timeline.tsx`) con el
  cambio resumido en lenguaje del owner por el helper puro
  `identityDiffSummary(diff, lang)` (`lib/cortex-identity.ts:285`, bilingüe ES/EN).
- **El consumo es real, no decorativo**: `identity_preamble` entra en el
  self-context de cada turno (`cortex/self_context.py:300`) y el baseline derivado
  gobierna la convergencia del motor afectivo (`cortex/affect_store.py:81-90`, vía
  `effective_mood_baseline`). El productor del `owner_model` (`relationship_model`)
  ya existe: lo escribe la propia reflexión (`apply_owner_model_delta` + memorias
  `kind='owner_model'`); llegó después de esta fase, en
  [cortex-identidad-real](../roadmap/cortex-identidad-real.md).

## Divergencias respecto al plan (verificadas 2026-08-19)

- **La pureza del módulo no se cumple.** El criterio decía «100 % determinista, SIN
  imports de red/LLM/DB». `cortex/identity.py` importa `select`, `IntegrityError` y
  `AsyncSession`, y aloja corrutinas de acceso a BD (`get_identity`,
  `ensure_identity`, `update_identity`, `list_history`). Las **funciones** puras lo
  son; el **módulo** no. El `db/cortex_identity_repo.py` que el plan pedía como capa
  separada **no existe**.
- **`merge_identity_state(...)` no existe con ese nombre**: su función está repartida
  entre `editable_owner_state` y `apply_reflection_delta`.
- **No hay `routers/cortex_identity.py`**: los endpoints viven dentro de
  `routers/cortex_mind.py`, con el mismo prefijo y el mismo gate. Los **schemas** sí
  están separados (`schemas/cortex_identity.py`).
- **Rutas distintas a las diseñadas**: `PUT /identity` en vez de `PATCH`;
  `POST /owner/cortex/reflect` en vez de `/identity/reflect-now`.
- **La reflexión no lee por donde el plan decía**: toma los turnos nuevos de
  `cortex_turns` con SQL directo en vez de `memorizer.recall(scopes=['private'])`,
  y sintetiza con `OllamaProvider.complete()` en vez de
  `claude_sdk run_agent(effort=…)` — desviación consciente y documentada en el
  propio módulo (bucle de fondo barato, sin egress).
- **La `narrative` editable por el owner ya no es una divergencia**: era la
  contradicción abierta de la fase y la resolvió el ADR 0157 a favor del código. Se
  cuenta arriba, en «Cambios», donde le toca.

## Lo que esta entrada afirmaba y era falso

Comprobado uno a uno el 2026-08-19 (el `grep` que sostenía la lista anterior es del
2026-07-30 y quedó viejo):

| Afirmaba                             | Realidad de hoy                                                                       |
| ------------------------------------ | ------------------------------------------------------------------------------------- |
| «La reflexión no tiene budget»       | `REFLECTION_DAILY_CAP = 12` + kill-switch en el **núcleo**, o sea también en el botón |
| «El drive `coherence` no se sacia»   | `_satisfy_coherence(...)`, paso (8) de `_reflect`                                     |
| «No existe `list_history`»           | `cortex/identity.py:222`                                                              |
| «No existe `GET /identity/history`»  | `routers/cortex_mind.py:543`, con `diff` por versión                                  |
| «No existe `identityDiffSummary`»    | `lib/cortex-identity.ts:285` + `identity-timeline.tsx`, bilingüe                      |
| «La reflexión no marca lo procesado» | Marca `reflected_through`: dos pasadas seguidas ya no re-sintetizan los mismos turnos |
| «Los rasgos se pintan como barras»   | Radar Big-Five (`components/cortex/trait-radar.tsx`)                                  |
| «No hay `propose_identity`»          | `cortex/identity.py:391`, con 14 tests unitarios                                      |

## Lo que sigue abierto (verificado 2026-08-19)

> **Aviso, para que esta sección no vuelva a fosilizarse:** hay trabajo en vuelo
> sobre esta fase mientras se escribe. **La autoridad sobre el estado de cada
> casilla son el plan de la fase y sus tests**, no esta lista.

- **El copy honesto de la UI es ES-only.** `HONESTY_NOTE` es un string fijo en
  castellano (`app/admin/cortex/identity/page.tsx:52`) y la página no pasa por el
  diccionario i18n, así que el «(ES+EN)» que exige la casilla F3.6 no se cumple.
  Además la tarjeta sigue siendo una **ruta hermana** (`/admin/cortex/identity`) en
  vez de la segunda columna de la página de F1, y no hay test de render de la
  tarjeta (sí de radar y de timeline).
- **Onboarding co-diseñado**: `cortex/onboarding.py` (`propose_onboarding` /
  `apply_onboarding`) y el `POST /identity/onboarding` aterrizaron en esta misma
  ola, el 2026-08-19; su cierre lo manda la casilla F3.3 del plan.
- **El ADR 0078 quedó `accepted`, no `accepted-f3`.** Benigno y deliberado: el
  corpus no usa estados por fase (el `accepted-f0` del ADR 0074 es el único, y por
  una razón histórica). Lo que sí se hizo es anotar en él el estado real de la
  reflexión.
- **PR sin abrir.** Regla dura de `CLAUDE.md`: sin PR mergeado ningún plan pasa a
  `completed`, así que la fase sigue en `pending_human_validation`.

## Tests

| Fichero                                                              | Qué fija                                                                                      |
| -------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| `tests/unit/test_cortex_identity_dynamics.py` (31)                   | clamps, cota por ciclo, `compute_diff`, convergencia sin oscilar                              |
| `tests/unit/test_cortex_identity_model.py` (18)                      | modelos ORM tenant-less (sin `tenant_id`)                                                     |
| `tests/unit/test_cortex_identity_preamble.py` (4)                    | la identidad entra en el system prompt                                                        |
| `tests/unit/test_cortex_identity_onboarding.py` (14)                 | `propose_identity`: caps, fail-open, no toca derivados                                        |
| `tests/integration/test_cortex_f3_identity_endpoints.py` (16)        | gate 403, cross-owner, versionado, history con `diff`, y el **ADR 0157** en sus dos sentidos  |
| `tests/integration/test_cortex_identity.py` (7)                      | migración 0094 reversible + singleton, `ensure/update_identity`, `list_history` y cross-owner |
| `tests/integration/test_cortex_f3_reflection.py` (16)                | delta acotado, fail-open, cross-owner, kill-switch, budget, `coherence`, idempotencia         |
| `tests/integration/test_cortex_owner_rls.py` (8)                     | RLS de eje owner en las seis tablas (ADR 0156)                                                |
| `apps/admin-panel/lib/cortex-identity.test.ts` (24)                  | cliente API + `identityDiffSummary` ES/EN                                                     |
| `apps/admin-panel/components/cortex/identity-timeline.test.tsx` (11) | radar Big-Five + timeline de versiones                                                        |

## Estado de cierre

**No cerrable todavía**, por dos cosas concretas y verificables: el **copy honesto
ES-only** de la tarjeta de identidad (casilla F3.6) y el **PR sin abrir**. La
contradicción de diseño que bloqueaba F3.5 —quién reescribe la narrativa— quedó
resuelta por el ADR 0157 y acreditada con tests.

## PR

- _pendiente_
