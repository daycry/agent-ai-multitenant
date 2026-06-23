---
title: "Córtex F3 — Identidad evolutiva + reflexión periódica"
status: pending_approval
blocking_plan:
  - "docs/roadmap/cortex-system-owner.md (Fase 3)"
  - "F1 — Córtex conversacional con memoria persistente (cortex_conversations/cortex_turns, grafo, recall híbrido, app/admin/cortex) — GATED, NO implementado"
  - "F2 — Modelo afectivo + Panel de Mente (cortex_affect_snapshots, motor PAD, drives Redis, mood_baseline) — GATED, NO implementado"
  - "ADR 0074 (rol system_owner / tablas BYPASSRLS; accepted-f0, F3 proposed)"
  - "ADR 0078 (bucles de fondo; proposed — exige aprobación + kill-switch)"
  - "ADR 0077 (protección identity/owner_model en olvido; proposed)"
  - "ADR 0021 (catálogo LLM cerrado) / ADR 0070 (reasoning_effort)"
started_at: null
phase: F3
gated: true
docs_language: es
---

# Córtex F3 — Identidad evolutiva + reflexión periódica (ADR 0074/0078)

> **🔒 GATED — NO IMPLEMENTAR sin aprobación.** F3 depende de F1 y F2 (ambas `proposed`/sin código:
> un `glob **/*cortex*` solo encuentra los docs y `tests/integration/test_cortex_f0_ownership.py`).
> F3 introduce además un **bucle Celery beat autónomo** (reflexión) que consume LLM/coste cuando
> nadie habla → regido por ADR 0078 (budget caps + kill-switch desde el MVP). Requiere: F1+F2 merged,
> luz verde explícita del operador para F3, y promover ADR 0074/0078 al alcance F3.

## Objetivo

Dotar al córtex de una **identidad propia que evoluciona**: un `identity_state` singleton (nombre
autoelegido, valores, rasgos Big-Five, narrativa autobiográfica, modelo del owner, baseline afectivo)
co-construido en onboarding y reescrito por un **bucle de reflexión periódica** que sintetiza episodios,
deriva `traits`/`mood_baseline` de forma **clampeada y versionada**, y que **nunca se auto-olvida**.

## Arquitectura

Dos tablas nuevas **tenant-less sobre BYPASSRLS** (`get_admin_sessionmaker`, aislamiento por
`owner_user_id` explícito — excepción consciente al Principio 1, ADR 0074): `cortex_identity`
(singleton, blob `identity_state` JSONB) y `cortex_identity_history` (versionado append-only con diff).
La **mutación de identidad** vive en una capa pura determinista (`cortex/identity.py`): clamp de
rangos, _bound_ de cambio por ciclo, derivación de diffs — fuera del LLM y testeable sin red. La
**reflexión** es un **Celery beat NUEVO** en `apps/workers` (sobre `settings.database_url`, que ya es
un rol BYPASSRLS, igual que `workers/maintenance.py`): recall de episodios de F1/F2 → síntesis de
narrativa con `claude_sdk run_agent(effort=...)` (el fix de F0 ya propaga `effort` a `_build_options`)
→ aplicación determinista del delta → snapshot versionado → saciado del drive `coherence`. **Gobierno
ADR 0078**: budget caps en Redis (`cortex:budget:reflection:{owner}`) + kill-switch + fail-open. La
**identidad nunca se auto-olvida** (ADR 0077: `metadata_.kind ∈ {identity, owner_model}` protegido).
La UI añade una **tarjeta de identidad** (radar Big-Five + narrativa Markdown + timeline) a la página
`app/admin/cortex` de F1, con **copy honesto** (modelo computacional, no consciencia).

## Tablas nuevas (migración 0092, reversible)

> Ambas SIN `TenantScopedMixin` (no RLS) — usan `Base, UUIDPrimaryKeyMixin, TimestampMixin` de
> `api_server.db.base`, patrón de `LlmProvider` (`api_server/db/llm_providers.py`, tabla global ADR 0028).

### `cortex_identity` (singleton)

- `id` UUID PK (uuid7).
- `owner_user_id` UUID NOT NULL → `users.id` — **único enlace de aislamiento**; filtrado explícito en TODO SQL.
- `identity_state` JSONB NOT NULL `server_default '{}'::jsonb` — blob con: `name` (str|null), `core_values` (str[]), `traits` (Big-Five [0,1]: openness/conscientiousness/extraversion/agreeableness/neuroticism), `narrative` (str, 1ª persona), `relationship_model` (obj — lo que cree saber del owner), `learning_goals` (str[]), `language` (`es`|`en`), `mood_baseline` (PAD set-point {valence,arousal,dominance} — set-point que F2 lee), `affect_params` (obj).
- `version` INT NOT NULL `server_default '0'` — se incrementa en cada reescritura.
- `updated_by` TEXT NOT NULL — `onboarding` | `reflection` | `owner_override`.
- `onboarded_at` TIMESTAMPTZ NULL — NULL ⇒ onboarding pendiente.
- **Índices**: `uq_cortex_identity_owner` UNIQUE(`owner_user_id`) — invariante singleton por owner.

### `cortex_identity_history` (versionado append-only)

- `id` UUID PK; `owner_user_id` UUID NOT NULL (filtrado explícito).
- `version` INT NOT NULL — la versión que esta fila CAPTURA.
- `identity_state` JSONB NOT NULL — snapshot completo en esa versión.
- `diff` JSONB NOT NULL `server_default '{}'::jsonb` — `{campo: {before, after}}` (auditoría del cambio).
- `updated_by` TEXT NOT NULL; `reason` TEXT NULL — resumen 1-línea del ciclo de reflexión.
- **Índices**: `ix_cortex_identity_history_owner_version` (`owner_user_id`, `version` DESC); `uq_cortex_identity_history_owner_version` UNIQUE(`owner_user_id`,`version`).

## Endpoints / WS (gated `require_system_owner`, DB-authoritative)

Router `apps/api-server/src/api_server/routers/cortex_identity.py` (incluido en `main.py`):

- `GET /owner/cortex/identity` → `identity_state` actual + `version` + `onboarded_at`.
- `POST /owner/cortex/identity/onboarding` → arranca/avanza el onboarding co-diseñado (turno del córtex que propone nombre/valores; el owner confirma). Idempotente: no re-onboarda si `onboarded_at` ya está.
- `PATCH /owner/cortex/identity` → **override del owner** SOLO de `name`/`core_values`/`language`/`learning_goals` (NUNCA `narrative` ni `traits` — esos los deriva la reflexión). Crea fila history (`updated_by='owner_override'`).
- `GET /owner/cortex/identity/history?limit=` → timeline de versiones con su `diff`.
- `POST /owner/cortex/identity/reflect-now` → dispara una pasada de reflexión bajo demanda (respeta budget cap + kill-switch). **GATED**.

Todos filtran `owner_user_id == principal.user_id` en SQL sobre `get_admin_sessionmaker`.

---

## FASES → TAREAS

> TDD estricto en cada tarea: escribe el test → falla → implementa → pasa → commit. Migración reversible.
> Catálogo LLM cerrado (ADR 0021). Copy ES+EN honesto. Cross-owner test obligatorio en todo acceso a `cortex_*`.

### F3.0 — Precondiciones (verificación, NO código)

- [ ] **Verificar que F1 y F2 están merged y verdes**
  - Confirmar existencia de: `apps/api-server/src/api_server/cortex/graph.py` (o el grafo del córtex de F1), `cortex_conversations`/`cortex_turns` (migración F1), `cortex_affect_snapshots` + motor PAD + `mood_baseline` (F2), y `app/admin/cortex/page.tsx`.
  - Confirmar el **número de migración base**: el HEAD actual es `0091_system_owner_f0` (`apps/api-server/migrations/versions/20260623_0091_system_owner_f0.py`). Si F1/F2 ya consumieron `0092..009N`, F3 encadena `down_revision` sobre la ÚLTIMA migración de F2 y renumera (no asumir 0092 a ciegas).
  - **Criterio de aceptación**: `alembic heads` devuelve una sola cabeza y los módulos F1/F2 importan sin error. Si falta cualquiera → **DETENER** (F3 no es implementable).

### F3.1 — Migración de tablas de identidad

- [ ] **Migración `cortex_identity` + `cortex_identity_history`**
  - Crear: `apps/api-server/migrations/versions/20260624_0092_cortex_identity.py` (encadenar `down_revision` a la última migración de F2; ej. `0091_system_owner_f0` solo si F1/F2 no añadieron migraciones — ver F3.0).
  - TDD: test `tests/integration/test_cortex_f3_identity_migration.py::test_upgrade_creates_tables_and_unique_owner` → `alembic upgrade head`, inserta dos filas con el MISMO `owner_user_id` → espera `asyncpg.UniqueViolationError` (singleton por `uq_cortex_identity_owner`); `test_downgrade_drops_tables` → `downgrade -1` deja el esquema sin las dos tablas. Patrón de fixture: `tests/integration/test_cortex_f0_ownership.py` (`alembic_config`, `command.upgrade`, `migrations_pg_dsn`).
  - Implementar `upgrade()` con `op.create_table` (columnas arriba) + índices; `downgrade()` con `op.drop_table` en orden inverso. Estilo: `apps/api-server/migrations/versions/20260618_0084_memory_entities.py` (JSONB `server_default text("'{}'::jsonb")`, índices nombrados).
  - **Criterio**: ambos tests en verde; `upgrade`/`downgrade` simétricos.

- [ ] **Modelos ORM `CortexIdentity` / `CortexIdentityHistory`**
  - Crear: `apps/api-server/src/api_server/db/cortex_identity.py` con `class CortexIdentity(Base, UUIDPrimaryKeyMixin, TimestampMixin)` y `class CortexIdentityHistory(...)` (importados de `api_server.db.base`). **SIN** `TenantScopedMixin` (tenant-less). `__table_args__` con los índices/uniques que reflejan la migración.
  - TDD: test `tests/unit/test_cortex_identity_model.py` → instanciar, comprobar `__tablename__`, columnas y que NO hay `tenant_id` (defensa: cualquier confusión RLS la detecta el cross-owner test después).
  - **Criterio**: modelos importan y mapean; mypy/ruff limpios.

### F3.2 — Capa pura de mutación de identidad (determinista, sin LLM)

- [ ] **`cortex/identity.py` — clamp + bound + diff**
  - Crear: `apps/api-server/src/api_server/cortex/identity.py` con funciones puras:
    - `clamp_traits(traits: dict) -> dict` — cada Big-Five a [0,1].
    - `clamp_baseline(pad: dict) -> dict` — valence∈[-1,1], arousal∈[0,1], dominance∈[-1,1] (piso/techo de mood, ADR 0075).
    - `bounded_update(current, proposed, *, max_delta_per_cycle) -> dict` — limita |Δ| por ciclo de reflexión (guardrail de auto-modificación, ADR 0074): un ciclo no puede mover un trait/baseline más de `max_delta_per_cycle` (ej. 0.05).
    - `compute_diff(before: dict, after: dict) -> dict` — `{campo:{before,after}}` solo de los campos que cambiaron.
    - `merge_identity_state(current, *, traits=None, baseline=None, narrative=None, ...) -> dict` — aplica los updates ya clampeados/bounded y devuelve el nuevo `identity_state`.
  - TDD: `tests/unit/test_cortex_identity_dynamics.py` — un `proposed` fuera de rango se clampa; un salto grande se acota a `max_delta_per_cycle`; `compute_diff` ignora campos sin cambio; reflexión repetida converge (no oscila).
  - **Criterio**: 100% determinista, sin imports de red/LLM/DB; tests en verde.

- [ ] **`cortex/identity_repo.py` — acceso DB con aislamiento explícito**
  - Crear: `apps/api-server/src/api_server/db/cortex_identity_repo.py`:
    - `get_identity(session, owner_user_id) -> CortexIdentity | None` (SELECT con `where(owner_user_id == ...)`).
    - `upsert_identity(session, owner_user_id, new_state, *, updated_by, reason=None)` — bump `version`, escribe `cortex_identity` y **append** a `cortex_identity_history` con `diff` (en una transacción).
    - `list_history(session, owner_user_id, limit)`.
  - **Aislamiento (ADR 0074)**: TODA query filtra `owner_user_id` explícito; el `session` viene de `get_admin_sessionmaker` (BYPASSRLS).
  - TDD: `tests/integration/test_cortex_f3_identity_repo.py` con **test cross-owner OBLIGATORIO**: crear identidad para owner A; abrir sesión admin "como" owner B; `get_identity(B)` devuelve None y un upsert para B NUNCA toca la fila de A; `list_history(B)` vacío. Más: `upsert` incrementa `version` y crea exactamente una fila history con el `diff` correcto.
  - **Criterio**: cross-owner aislado; versionado correcto; tests en verde.

### F3.3 — Onboarding co-diseñado

- [ ] **`cortex/onboarding.py` — flujo de autonombrado + valores**
  - Crear: `apps/api-server/src/api_server/cortex/onboarding.py`: `propose_identity(turn_result, current_state) -> dict` (pura: extrae nombre/valores propuestos del turno del córtex) y `apply_onboarding(session, owner_user_id, confirmed_state)` (escribe `identity_state` inicial, `updated_by='onboarding'`, `onboarded_at=now`, vía `cortex_identity_repo.upsert_identity`).
  - Reutiliza el **grafo del córtex de F1** para generar el turno de propuesta (el córtex se autonombra y propone valores; el owner confirma vía endpoint). NO duplica el turn-loop.
  - TDD: `tests/integration/test_cortex_f3_onboarding.py` — primer `POST /owner/cortex/identity/onboarding` con script del modelo (patrón `ScriptedAssistantModel` de `assistant/graph.py`) propone nombre+valores; confirmación persiste `identity_state` con `onboarded_at`; segundo POST es idempotente (no re-onboarda). Non-owner → 403 (gate DB-authoritative, patrón `test_cortex_f0_ownership.py::test_require_system_owner_gate_checks_the_db`).
  - **Criterio**: onboarding crea la identidad una sola vez; idempotente; gated.

### F3.4 — Bucle de reflexión periódica (Celery beat — GATED, ADR 0078)

- [ ] **Budget cap + kill-switch en Redis (gobierno ADR 0078)**
  - Crear: `apps/api-server/src/api_server/cortex/budget.py` (o reutilizar el de F1/F2 si ya existe): `try_consume(redis, key, *, max_calls, max_cost_usd, cost) -> bool` y `is_killed(redis, owner) -> bool`. Claves: `cortex:budget:reflection:{owner}` (TTL diario) + `cortex:killswitch:{owner}`.
  - TDD: `tests/unit/test_cortex_budget.py` — el cap se respeta (N+1 devuelve False); kill-switch detiene el bucle; reset diario.
  - **Criterio**: el bucle NO puede superar el cap; kill-switch efectivo.

- [ ] **Tarea de reflexión `workers.cortex_reflect`**
  - Crear: `apps/workers/src/workers/cortex_reflection.py` con `@app.task(name="workers.cortex_reflect")` (patrón exacto de `workers/maintenance.py`: `def task(): return asyncio.run(_async(...))`, engine `create_async_engine(settings.database_url)` — ya BYPASSRLS, line 38 de `workers/config.py`).
  - Núcleo async `_reflect(...)`: (1) resolver el owner (singleton: `SELECT id FROM users WHERE is_system_owner`); (2) chequear kill-switch + budget cap → si excedido, no-op log; (3) recall de episodios recientes vía `memorizer.recall(... user_id=owner, scopes=['private'])` (filtrado estricto al owner, defensa cross-owner); (4) síntesis de insights + narrativa con `claude_sdk run_agent(effort=...)` (ADR 0070; degradar a no-op fail-open si no hay SDK — ADR 0064); (5) aplicar delta con `cortex/identity.py` (clamp+bounded) sobre `mood_baseline`/`traits`; (6) `cortex_identity_repo.upsert_identity(updated_by='reflection', reason=...)`; (7) persistir insight como memoria `type='semantic'`, `metadata_.kind='reflection'` vía `persist_memory_candidates` **directo** (NO `workers/memorizer.py`, que enruta episodic→project*shared); (8) saciar drive `coherence` (escribir a Redis de F2). Idempotente: marca lo procesado en `metadata*`.
  - **Protección de identidad (ADR 0077)**: la reflexión NUNCA borra `metadata_.kind ∈ {identity, owner_model}`; solo reescribe `narrative`/`traits`/`baseline` (versionado).
  - TDD: `tests/integration/test_cortex_f3_reflection.py` — inyectando un modelo scripted + embedder determinista (patrón `HashEmbedder` de `maintenance.py` back-fill tests): una pasada deriva traits/baseline CLAMPEADOS+BOUNDED, crea fila history con diff, persiste memoria `kind='reflection'`, y NO toca filas de otro owner (cross-owner). Con budget excedido → no-op. Sin SDK → fail-open no-op (no crash).
  - **Criterio**: reflexión determinista en su aplicación, gated por budget/kill-switch, cross-owner aislada, fail-open.

- [ ] **Entrada en `build_beat_schedule` (cadencia configurable, GATED)**
  - Modificar: `apps/workers/src/workers/beat_schedule.py` — añadir `CORTEX_REFLECT_BEAT_ENTRY = "cortex-reflect"` y, en `build_beat_schedule`, `sched[CORTEX_REFLECT_BEAT_ENTRY] = {"task":"workers.cortex_reflect","schedule":_parse_cron(cfg.cortex_reflect_cron),"options":{"queue":"default"}}`. Añadir `cortex_reflect_cron` (default ej. cada 6h) + `cortex_reflect_enabled` a `workers/config.py` Settings (patrón `price_sync_cron`/`price_sync_enabled`). El beat NO se enciende salvo `cortex_reflect_enabled` (palanca de platform_settings que el owner posee) — kill-switch operativo.
  - TDD: `tests/integration/test_cortex_f3_beat_schedule.py` — `build_beat_schedule` incluye la entrada con la cadencia de Settings; deshabilitado por defecto → el body es no-op.
  - **Criterio**: la entrada existe, cadencia configurable, deshabilitada por defecto (opt-in del owner).

### F3.5 — Endpoints de identidad

- [ ] **Router `cortex_identity.py` (gated)**
  - Crear: `apps/api-server/src/api_server/routers/cortex_identity.py` con los endpoints listados arriba; incluirlo en `apps/api-server/src/api_server/main.py`. Todos `Depends(require_system_owner)` (DB-authoritative, `auth/deps.py`) + sesión `get_admin_sessionmaker` con filtro `owner_user_id` explícito.
  - `PATCH` rechaza con 422 cualquier intento de tocar `narrative`/`traits` (solo la reflexión los muta).
  - Schemas: `apps/api-server/src/api_server/schemas/cortex_identity.py` (Pydantic, ES+EN labels donde aplique).
  - TDD: `tests/integration/test_cortex_f3_identity_endpoints.py` — owner: GET identidad/history OK; PATCH de `core_values` crea versión `owner_override`; PATCH de `narrative` → 422; non-owner → 403 en TODOS; cross-owner: el owner solo ve su fila.
  - **Criterio**: endpoints gated, override acotado, cross-owner aislado, tests en verde.

### F3.6 — UI: tarjeta de identidad + timeline (en `app/admin/cortex` de F1)

- [ ] **Componente `IdentityCard` (radar Big-Five + narrativa Markdown + copy honesto)**
  - Crear: `apps/admin-panel/app/admin/cortex/identity-card.tsx` (radar Big-Five, narrativa con preview Markdown — reutilizar el render Markdown del chat de F1, lista de `core_values`/`learning_goals`, nombre). **Copy honesto** fijo: "Modelo computacional de identidad/afecto — no es consciencia ni sentimientos reales" (ES+EN).
  - Crear: `apps/admin-panel/app/admin/cortex/identity-timeline.tsx` (timeline de versiones desde `GET /identity/history`, mostrando el `diff` por versión).
  - Integrar en la página de F1 `apps/admin-panel/app/admin/cortex/page.tsx` (segunda columna / Panel de Mente). Gated `isSystemOwner` (hook `use-current-user`).
  - Cliente API: `apps/admin-panel/lib/cortex-identity.ts` (fetch a los endpoints) + helper puro testeado `identityDiffSummary(diff)` (resumen legible de un cambio).
  - TDD: `apps/admin-panel/lib/cortex-identity.test.ts` (vitest, patrón `lib/conversation-history.ts`/`conversation-history.test.ts` de Feature 2): `identityDiffSummary` resume un diff multi-campo; etiqueta de versión correcta.
  - **Criterio**: la tarjeta renderiza identidad+narrativa con copy honesto; timeline muestra versiones; gated `isSystemOwner`; tests vitest en verde.

### F3.7 — Documentación + cierre de ADRs

- [ ] **Promover ADRs y registrar cambios**
  - Modificar: `docs/05-architecture-decisions/0074-rol-system-owner-y-cortex-singleton.md` (anotar F3 implementada) y `docs/05-architecture-decisions/0078-bucles-cognitivos-fondo-cortex.md` (de `proposed` → `accepted-f3` para la reflexión; curiosidad sigue gated para F4).
  - Modificar: `docs/roadmap/cortex-system-owner.md` (marcar Fase 3 hecha) y `docs/roadmap/mejoras-2026-06-chat-coste-cortex.md` (Feature 1: F3 ✅).
  - Crear changelog: `docs/07-changelog/` entrada de F3.
  - **Criterio**: estado de los docs coherente con el código; sin placeholders.

---

## Riesgos y mitigaciones (ADR-driven)

- **Excepción al Principio 1 (RLS)** — tablas tenant-less. Mitigación: filtro `owner_user_id` explícito en TODO SQL + **test cross-owner obligatorio** en F3.2 y F3.5 (es el punto de mayor escrutinio).
- **Coste autónomo** (ADR 0078) — el beat consume LLM sin owner. Mitigación: budget cap Redis + kill-switch + `enabled` opt-in + fail-open, parte del MVP (F3.4), no fast-follow.
- **Auto-modificación descontrolada** (ADR 0074) — la reflexión podría derivar la identidad. Mitigación: `bounded_update` (|Δ| por ciclo) + clamps + diff versionado en `cortex_identity_history`.
- **Olvido destructivo** (ADR 0077) — la identidad debe sobrevivir. Mitigación: `kind ∈ {identity, owner_model}` NUNCA se auto-olvida; el override del owner NO toca narrativa.
- **Honestidad de producto** — copy honesto fijo en la tarjeta (F3.6); la narrativa no afirma consciencia.

## Ficheros críticos para la implementación

- `apps/api-server/migrations/versions/20260624_0092_cortex_identity.py` (nueva migración)
- `apps/api-server/src/api_server/cortex/identity.py` (capa pura: clamp/bound/diff)
- `apps/api-server/src/api_server/db/cortex_identity_repo.py` (acceso DB con aislamiento `owner_user_id`)
- `apps/workers/src/workers/cortex_reflection.py` (bucle de reflexión Celery beat, GATED)
- `apps/api-server/src/api_server/routers/cortex_identity.py` (endpoints gated `require_system_owner`)
