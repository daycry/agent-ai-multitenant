---
title: "Córtex F3 — Identidad evolutiva + reflexión periódica"
status: pending_human_validation
blocking_plan:
  - "docs/roadmap/cortex-system-owner.md (Fase 3)"
  - "F1 — Córtex conversacional con memoria persistente — IMPLEMENTADO"
  - "F2 — Modelo afectivo + Panel de Mente — IMPLEMENTADO"
  - "ADR 0074 (rol system_owner / tablas BYPASSRLS; accepted-f0, F3 proposed)"
  - "ADR 0078 (bucles de fondo; proposed — exige aprobación + kill-switch)"
  - "ADR 0077 (protección identity/owner_model en olvido; proposed)"
  - "ADR 0021 (catálogo LLM cerrado) / ADR 0070 (reasoning_effort)"
started_at: 2026-06-24
completed_at: null
phase: F3
gated: false
docs_language: es
---

# Córtex F3 — Identidad evolutiva + reflexión periódica (ADR 0074/0078)

> **Auditoría 2026-07-27 — las casillas de este plan se verificaron una a una
> contra el código.** Las marcadas `[x]` lo están con evidencia `file:line` y una
> segunda pasada adversarial; las que siguen sin marcar tienen su hueco concreto
> descrito en
> [`gaps-cortex-2026-07-27.md`](gaps-cortex-2026-07-27.md) (informe:
> [`auditoria-cortex-2026-07-27.md`](auditoria-cortex-2026-07-27.md)).
> Antes de implementar una casilla sin marcar, **abre el fichero**: la pasada
> adversarial dio al menos un falso positivo comprobado.

> **✅ IMPLEMENTADO Y DESPLEGADO** (verificado 2026-07-06 — auditoría de estado del roadmap). El
> banner "GATED — F1/F2 sin código" era cierto el día que se escribió el diseño (commit `cf8f7cd`)
> pero quedó congelado mientras F1-F3 se implementaban: `cortex/identity.py`, migración
> `0094_cortex_identity`, endpoints `GET/PUT /identity` + `POST /reflect` en `cortex_mind.py`,
> worker `cortex_reflection.py` (fail-open, clamp Δ≤0.05/ciclo), con `test_cortex_f3_identity_endpoints.py`/
> `test_cortex_f3_reflection.py`/`test_cortex_identity_dynamics.py` en verde. Ver
> [cortex-identidad-real.md](cortex-identidad-real.md) para el productor del `owner_model`
> (relationship_model) añadido encima el 2026-07-06, que esta fase deja como campo vacío sin
> escritor. Checkboxes de tareas NO re-verificados línea a línea; el status refleja el veredicto
> agregado, no un cierre formal con changelog propio.

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
- `PATCH /owner/cortex/identity` (implementado como **`PUT`**) → **override del owner** de la PROSA co-diseñada: `name`/`core_values`/**`narrative`**/`language`/`learning_goals`. El estado derivado NUMÉRICO —`traits`, `mood_baseline`, `relationship_model`, `affect_params`— NO se escribe a mano: **422** (`extra="forbid"`). Crea fila history (`updated_by='owner_override'`). La frontera la fija el [ADR 0157](../05-architecture-decisions/0157-quien-reescribe-la-narrativa-del-cortex.md): lo que la reflexión mueve **acotado** no se escribe a mano; la prosa se co-diseña y su autoría queda firmada por versión. Este documento decía antes «NUNCA `narrative`», que es lo que contradecía al código desde el 2026-06-24.
- `GET /owner/cortex/identity/history?limit=` → timeline de versiones con su `diff`.
- `POST /owner/cortex/identity/reflect-now` (implementado como **`POST /owner/cortex/reflect`**) → dispara una pasada de reflexión bajo demanda (respeta budget cap + kill-switch). **GATED**.

Todos filtran `owner_user_id == principal.user_id` en SQL sobre `get_admin_sessionmaker`.

> **Dónde viven de verdad (verificado 2026-08-19).** No existe
> `routers/cortex_identity.py`: los cinco endpoints están en
> [`routers/cortex_mind.py`](../../apps/api-server/src/api_server/routers/cortex_mind.py)
> (`GET /identity`:494, `PUT /identity`:514, `POST /identity/onboarding`:558,
> `GET /identity/history`:699, `POST /reflect`:740), con el mismo prefijo `/owner/cortex`, el mismo
> `Depends(require_system_owner)` y los schemas en
> [`schemas/cortex_identity.py`](../../apps/api-server/src/api_server/schemas/cortex_identity.py).
> El aislamiento es doble desde el [ADR 0156](../05-architecture-decisions/0156-aislamiento-estructural-del-cortex.md)
> (migración 0140): filtro `owner_user_id` explícito **y** RLS de eje owner en las
> dos tablas.

---

## FASES → TAREAS

> TDD estricto en cada tarea: escribe el test → falla → implementa → pasa → commit. Migración reversible.
> Catálogo LLM cerrado (ADR 0021). Copy ES+EN honesto. Cross-owner test obligatorio en todo acceso a `cortex_*`.

### F3.0 — Precondiciones (verificación, NO código)

- [x] **Verificar que F1 y F2 están merged y verdes**
  - Confirmar existencia de: `apps/api-server/src/api_server/cortex/graph.py` (o el grafo del córtex de F1), `cortex_conversations`/`cortex_turns` (migración F1), `cortex_affect_snapshots` + motor PAD + `mood_baseline` (F2), y `app/admin/cortex/page.tsx`.
  - Confirmar el **número de migración base**: el HEAD actual es `0091_system_owner_f0` (`apps/api-server/migrations/versions/20260623_0091_system_owner_f0.py`). Si F1/F2 ya consumieron `0092..009N`, F3 encadena `down_revision` sobre la ÚLTIMA migración de F2 y renumera (no asumir 0092 a ciegas).
  - **Criterio de aceptación**: `alembic heads` devuelve una sola cabeza y los módulos F1/F2 importan sin error. Si falta cualquiera → **DETENER** (F3 no es implementable).

### F3.1 — Migración de tablas de identidad

- [x] **Migración `cortex_identity` + `cortex_identity_history`**
  - Crear: `apps/api-server/migrations/versions/20260624_0092_cortex_identity.py` (encadenar `down_revision` a la última migración de F2; ej. `0091_system_owner_f0` solo si F1/F2 no añadieron migraciones — ver F3.0).
  - TDD: test `tests/integration/test_cortex_f3_identity_migration.py::test_upgrade_creates_tables_and_unique_owner` → `alembic upgrade head`, inserta dos filas con el MISMO `owner_user_id` → espera `asyncpg.UniqueViolationError` (singleton por `uq_cortex_identity_owner`); `test_downgrade_drops_tables` → `downgrade -1` deja el esquema sin las dos tablas. Patrón de fixture: `tests/integration/test_cortex_f0_ownership.py` (`alembic_config`, `command.upgrade`, `migrations_pg_dsn`).
  - Implementar `upgrade()` con `op.create_table` (columnas arriba) + índices; `downgrade()` con `op.drop_table` en orden inverso. Estilo: `apps/api-server/migrations/versions/20260618_0084_memory_entities.py` (JSONB `server_default text("'{}'::jsonb")`, índices nombrados).
  - **Criterio**: ambos tests en verde; `upgrade`/`downgrade` simétricos.

- [x] **Modelos ORM `CortexIdentity` / `CortexIdentityHistory`**
  - Crear: `apps/api-server/src/api_server/db/cortex_identity.py` con `class CortexIdentity(Base, UUIDPrimaryKeyMixin, TimestampMixin)` y `class CortexIdentityHistory(...)` (importados de `api_server.db.base`). **SIN** `TenantScopedMixin` (tenant-less). `__table_args__` con los índices/uniques que reflejan la migración.
  - TDD: test `tests/unit/test_cortex_identity_model.py` → instanciar, comprobar `__tablename__`, columnas y que NO hay `tenant_id` (defensa: cualquier confusión RLS la detecta el cross-owner test después).
  - **Criterio**: modelos importan y mapean; mypy/ruff limpios.

### F3.2 — Capa pura de mutación de identidad (determinista, sin LLM)

- [x] **`cortex/identity.py` — clamp + bound + diff**
  - Crear: `apps/api-server/src/api_server/cortex/identity.py` con funciones puras:
    - `clamp_traits(traits: dict) -> dict` — cada Big-Five a [0,1].
    - `clamp_baseline(pad: dict) -> dict` — valence∈[-1,1], arousal∈[0,1], dominance∈[-1,1] (piso/techo de mood, ADR 0075).
    - `bounded_update(current, proposed, *, max_delta_per_cycle) -> dict` — limita |Δ| por ciclo de reflexión (guardrail de auto-modificación, ADR 0074): un ciclo no puede mover un trait/baseline más de `max_delta_per_cycle` (ej. 0.05).
    - `compute_diff(before: dict, after: dict) -> dict` — `{campo:{before,after}}` solo de los campos que cambiaron.
    - `merge_identity_state(current, *, traits=None, baseline=None, narrative=None, ...) -> dict` — aplica los updates ya clampeados/bounded y devuelve el nuevo `identity_state`.
  - TDD: `tests/unit/test_cortex_identity_dynamics.py` — un `proposed` fuera de rango se clampa; un salto grande se acota a `max_delta_per_cycle`; `compute_diff` ignora campos sin cambio; reflexión repetida converge (no oscila).
  - **Criterio**: 100% determinista, sin imports de red/LLM/DB; tests en verde.

- [x] **`cortex/identity_repo.py` — acceso DB con aislamiento explícito**
  - Crear: `apps/api-server/src/api_server/db/cortex_identity_repo.py`:
    - `get_identity(session, owner_user_id) -> CortexIdentity | None` (SELECT con `where(owner_user_id == ...)`).
    - `upsert_identity(session, owner_user_id, new_state, *, updated_by, reason=None)` — bump `version`, escribe `cortex_identity` y **append** a `cortex_identity_history` con `diff` (en una transacción).
    - `list_history(session, owner_user_id, limit)`.
  - **Aislamiento (ADR 0074)**: TODA query filtra `owner_user_id` explícito; el `session` viene de `get_admin_sessionmaker` (BYPASSRLS).
  - TDD: `tests/integration/test_cortex_f3_identity_repo.py` con **test cross-owner OBLIGATORIO**: crear identidad para owner A; abrir sesión admin "como" owner B; `get_identity(B)` devuelve None y un upsert para B NUNCA toca la fila de A; `list_history(B)` vacío. Más: `upsert` incrementa `version` y crea exactamente una fila history con el `diff` correcto.
  - **Criterio**: cross-owner aislado; versionado correcto; tests en verde.

### F3.3 — Onboarding co-diseñado

- [x] **`cortex/onboarding.py` — flujo de autonombrado + valores** (entregado el 2026-08-19)
  - Crear: [`apps/api-server/src/api_server/cortex/onboarding.py`](../../apps/api-server/src/api_server/cortex/onboarding.py) con `build_onboarding_prompt(identity_state) -> str`, `propose_onboarding(model, *, current_state, tool_ctx) -> OnboardingProposal` y `apply_onboarding(session, owner_user_id, confirmed_state) -> (CortexIdentity, aplicado)` (escribe el `identity_state` inicial, `updated_by='onboarding'`, `onboarded_at=now`, versionado en `cortex_identity_history`).
  - Reutiliza el **grafo del córtex de F1** para generar el turno de propuesta (el córtex se autonombra y propone valores; el owner confirma vía endpoint). NO duplica el turn-loop.
  - TDD: `tests/integration/test_cortex_f3_onboarding.py` — primer `POST /owner/cortex/identity/onboarding` con script del modelo (patrón `ScriptedAssistantModel` de `assistant/graph.py`) propone nombre+valores; confirmación persiste `identity_state` con `onboarded_at`; segundo POST es idempotente (no re-onboarda). Non-owner → 403 (gate DB-authoritative, patrón `test_cortex_f0_ownership.py::test_require_system_owner_gate_checks_the_db`).
  - **Criterio**: onboarding crea la identidad una sola vez; idempotente; gated.
  - ✅ **Hecho (2026-08-19).** El endpoint es `POST /owner/cortex/identity/onboarding`
    ([`routers/cortex_mind.py`:558](../../apps/api-server/src/api_server/routers/cortex_mind.py)) y funciona en **dos pasos sobre la misma ruta**: sin `confirm` corre UN turno con el grafo de F1
    ([`cortex/onboarding.py::propose_onboarding`](../../apps/api-server/src/api_server/cortex/onboarding.py))
    y devuelve el `identity_state` candidato + el `diff` + el texto literal del turno **sin persistir nada**;
    con `confirm` persiste vía `apply_onboarding`. Tests: `tests/integration/test_cortex_f3_onboarding.py`
    (8 verdes) + `tests/unit/test_cortex_onboarding.py` (9 verdes).
  - 📝 **Tres divergencias del enunciado, resueltas así (no se marcó a la fuerza):**
    1. **`propose_identity` NO se duplica.** Ya vivía en
       [`cortex/identity.py`:391](../../apps/api-server/src/api_server/cortex/identity.py) con sus 20 tests;
       `cortex/onboarding.py` la **importa y reexporta**, que es lo que el enunciado quería (que estuviera
       disponible desde el módulo del flujo) sin partir en dos el guardrail del ADR 0074.
    2. **`cortex_identity_repo.upsert_identity` no existe.** Ese fichero nunca se creó: la casilla F3.2
       —ya `[x]`— se resolvió consolidando la persistencia en `cortex/identity.py`
       (`ensure_identity` / `update_identity` / `list_history`). `apply_onboarding` usa `update_identity`.
    3. **`apply_onboarding` devuelve `(identidad, aplicado)`**, no sólo la identidad. La idempotencia tiene
       que vivir DENTRO de la función (la casilla pide «crea la identidad una sola vez»), y el llamante
       necesita saber si escribió o no. El endpoint corta antes por lo barato —no gastar un turno de LLM—,
       así que la guarda de la función se ejercita **directamente** en
       `test_apply_onboarding_is_idempotent_on_its_own`: sin ese test la guarda interna estaría tapada por
       la del endpoint y nadie notaría si desaparece.
  - ✅ **El llamante ya existe (2026-08-19, mismo día).** Cuando se escribió la nota de arriba, el
    endpoint no tenía botón: el owner sólo veía el formulario manual de `PUT /identity`. Cada carril se
    lo dejó al otro —F3.3 decía «es de F3.6» y F3.6 decía «esta casilla es la TARJETA»— y así es como el
    patrón nº5 de `verificar-antes-de-implementar.md` sobrevive a dos personas que hacen bien su parte.
    Cerrado con el botón «que se proponga él» en el banner de identidad pendiente
    (`app/admin/cortex/identity/page.tsx`), su cliente
    (`lib/cortex-identity.ts::proposeCortexOnboarding` / `confirmCortexOnboarding`) y once claves de
    diccionario ES+EN. Cuatro tests en
    `app/admin/cortex/identity/onboarding-proposal.test.tsx` fijan las propiedades que hacen que la
    pantalla no mienta: el botón sólo sale si el córtex NO está onboardado; **proponer no persiste** (el
    primer POST va sin `confirm`); el turno literal se PINTA, para que el owner acepte lo que ha leído;
    y aceptar manda **lo que hay en el formulario**, no lo que propuso el modelo — si el owner cambia el
    nombre, se guarda el suyo. Rojo comprobado por mutación en las tres direcciones (retirar el botón:
    3 rojos; que aceptar ignore la edición: 1; que proponer mande `confirm: true`: 1).

### F3.4 — Bucle de reflexión periódica (Celery beat — GATED, ADR 0078)

- [x] **Budget cap + kill-switch en Redis (gobierno ADR 0078)**
  - Crear: `apps/api-server/src/api_server/cortex/budget.py` (o reutilizar el de F1/F2 si ya existe): `try_consume(redis, key, *, max_calls, max_cost_usd, cost) -> bool` y `is_killed(redis, owner) -> bool`. Claves: `cortex:budget:reflection:{owner}` (TTL diario) + `cortex:killswitch:{owner}`.
  - TDD: `tests/unit/test_cortex_budget.py` — el cap se respeta (N+1 devuelve False); kill-switch detiene el bucle; reset diario.
  - **Criterio**: el bucle NO puede superar el cap; kill-switch efectivo.

- [x] **Tarea de reflexión `workers.cortex_reflect`**
  - Crear: `apps/workers/src/workers/cortex_reflection.py` con `@app.task(name="workers.cortex_reflect")` (patrón exacto de `workers/maintenance.py`: `def task(): return asyncio.run(_async(...))`, engine `create_async_engine(settings.database_url)` — ya BYPASSRLS, line 38 de `workers/config.py`).
  - Núcleo async `_reflect(...)`: (1) resolver el owner (singleton: `SELECT id FROM users WHERE is_system_owner`); (2) chequear kill-switch + budget cap → si excedido, no-op log; (3) recall de episodios recientes vía `memorizer.recall(... user_id=owner, scopes=['private'])` (filtrado estricto al owner, defensa cross-owner); (4) síntesis de insights + narrativa con `claude_sdk run_agent(effort=...)` (ADR 0070; degradar a no-op fail-open si no hay SDK — ADR 0064); (5) aplicar delta con `cortex/identity.py` (clamp+bounded) sobre `mood_baseline`/`traits`; (6) `cortex_identity_repo.upsert_identity(updated_by='reflection', reason=...)`; (7) persistir insight como memoria `type='semantic'`, `metadata_.kind='reflection'` vía `persist_memory_candidates` **directo** (NO `workers/memorizer.py`, que enruta episodic→project*shared); (8) saciar drive `coherence` (escribir a Redis de F2). Idempotente: marca lo procesado en `metadata*`.
  - **Protección de identidad (ADR 0077)**: la reflexión NUNCA borra `metadata_.kind ∈ {identity, owner_model}`; solo reescribe `narrative`/`traits`/`baseline` (versionado).
  - TDD: `tests/integration/test_cortex_f3_reflection.py` — inyectando un modelo scripted + embedder determinista (patrón `HashEmbedder` de `maintenance.py` back-fill tests): una pasada deriva traits/baseline CLAMPEADOS+BOUNDED, crea fila history con diff, persiste memoria `kind='reflection'`, y NO toca filas de otro owner (cross-owner). Con budget excedido → no-op. Sin SDK → fail-open no-op (no crash).
  - **Criterio**: reflexión determinista en su aplicación, gated por budget/kill-switch, cross-owner aislada, fail-open.

- [x] **Entrada en `build_beat_schedule` (cadencia configurable, GATED)**
  - Modificar: `apps/workers/src/workers/beat_schedule.py` — añadir `CORTEX_REFLECT_BEAT_ENTRY = "cortex-reflect"` y, en `build_beat_schedule`, `sched[CORTEX_REFLECT_BEAT_ENTRY] = {"task":"workers.cortex_reflect","schedule":_parse_cron(cfg.cortex_reflect_cron),"options":{"queue":"default"}}`. Añadir `cortex_reflect_cron` (default ej. cada 6h) + `cortex_reflect_enabled` a `workers/config.py` Settings (patrón `price_sync_cron`/`price_sync_enabled`). El beat NO se enciende salvo `cortex_reflect_enabled` (palanca de platform_settings que el owner posee) — kill-switch operativo.
  - TDD: `tests/integration/test_cortex_f3_beat_schedule.py` — `build_beat_schedule` incluye la entrada con la cadencia de Settings; deshabilitado por defecto → el body es no-op.
  - **Criterio**: la entrada existe, cadencia configurable, deshabilitada por defecto (opt-in del owner).

### F3.5 — Endpoints de identidad

- [x] **Endpoints de identidad, gated** (enunciado reescrito el 2026-08-19; antes decía «Router `cortex_identity.py`»)
  - **Dónde están de verdad**: NO hay `routers/cortex_identity.py`. Los cuatro endpoints viven en `apps/api-server/src/api_server/routers/cortex_mind.py` — `GET /identity`:479, `PUT /identity`:499, `GET /identity/history`:543, `POST /reflect`:581 — con el mismo prefijo `/owner/cortex`, el mismo `Depends(require_system_owner)` (DB-authoritative, `auth/deps.py`) que el router entero declara, y sesión `get_admin_sessionmaker` con filtro `owner_user_id` explícito. Los **schemas sí** están separados: `apps/api-server/src/api_server/schemas/cortex_identity.py`.
  - **La contradicción del enunciado, resuelta por el [ADR 0157](../05-architecture-decisions/0157-quien-reescribe-la-narrativa-del-cortex.md)** (2026-08-19). Esta casilla pedía «422 al tocar `narrative`»; la implementación la hace editable a propósito. **Gana la implementación**, y la frontera se redibuja donde hay un invariante que defender: el owner co-diseña la PROSA (`name`/`core_values`/`narrative`/`language`/`learning_goals`) y NO escribe a mano el estado derivado NUMÉRICO (`traits`, `mood_baseline`, `relationship_model`, `affect_params`) → **422** por el `extra="forbid"` del schema. Razón corta: la cota |Δ| ≤ 0.05 por ciclo del ADR 0074 es sobre NÚMEROS; la narrativa la reescribe la reflexión entera y sin cota, así que prohibírsela al owner no protegía ningún invariante y le quitaba el único correctivo a lo que un LLM escriba sobre él. La honestidad la sostiene la **procedencia** (`updated_by` + `diff` por versión, legibles en `GET /identity/history`), no la prohibición. Alternativas descartadas y lo que se pierde: en el ADR.
  - TDD: `tests/integration/test_cortex_f3_identity_endpoints.py` (16 tests) — owner: GET identidad/history OK; PUT de `core_values` crea versión `owner_override`; non-owner → 403 en TODOS; cross-owner: el owner sólo ve su fila. Acreditan el ADR 0157 en sus **dos** sentidos: `test_put_identity_owner_can_rewrite_narrative_versioned` (la narrativa se persiste y queda firmada con su `diff`, y el override NO mueve los derivados) y `test_put_identity_rejects_non_editable_fields` (los CUATRO derivados → 422, no ignorados en silencio).
  - **Criterio**: endpoints gated, override acotado **a la prosa**, cross-owner aislado, tests en verde. ✅
  - Aislamiento: **doble** desde el [ADR 0156](../05-architecture-decisions/0156-aislamiento-estructural-del-cortex.md) + migración 0140 — filtro `owner_user_id` explícito **y** RLS de eje owner sobre `cortex_identity`/`cortex_identity_history`.

### F3.6 — UI: tarjeta de identidad + timeline (en `app/admin/cortex` de F1)

- [x] **Componente `IdentityCard` (radar Big-Five + narrativa Markdown + copy honesto)**
  - ✅ **Hecho (2026-08-19).** Las tres cosas que la nota del 2026-07-30 daba por abiertas eran ciertas el 2026-08-19 y están cerradas:
    - **Copy honesto ES+EN**: `HONESTY_NOTE` era un `const` en castellano (`app/admin/cortex/identity/page.tsx:52`). Ahora es `cortexIdentity.honestyNote` en `lib/i18n/dictionary.ts`, **una sola clave** que usan la tarjeta y la pantalla — dos copias del mismo aviso es como una de las dos se queda atrás. De paso, la pantalla entera y el timeline pasan por el diccionario (~45 claves nuevas): `identity/page.tsx` queda a CERO infractores de `check-i18n` y sale de su `ATTR_ALLOWLIST`. Y el radar también: `traitRadarAxes` rotula con `TRAIT_LABELS_ES`, castellano fijo, y con el panel en inglés dejaba cinco palabras sin traducir DENTRO del gráfico; `TraitRadar` toma ahora los rótulos del diccionario (la geometría pura no se toca).
    - **La tarjeta, en la segunda columna de la página de F1**: `apps/admin-panel/components/cortex/identity-card.tsx` (nuevo) montado en `app/admin/cortex/page.tsx` junto al `MindPanel`.
    - **Test de render de la tarjeta**: `components/cortex/identity-card.test.tsx` (11) — aviso honesto en ES y EN y también cuando la carga falla, nombre/valores/objetivos/versión, radar con su valor por rasgo, narrativa renderizada como Markdown, el endpoint al que pega, y los tres estados que NO se pueden confundir (onboarding pendiente ≠ error de carga ≠ identidad vacía). Más `app/admin/cortex/second-column.test.tsx` para el montaje.
  - **Desviación consciente**: la ruta hermana `/admin/cortex/identity` **NO se retira**. La enlazan el NAV (`components/layout/admin-shell.tsx:264`), su test de shell (`admin-shell-cortex.test.ts:41`) y la e2e `e2e/cortex-identity.spec.ts`, y ahí vive el formulario de edición, que no cabe en una columna de 22 rem. El reparto queda: la tarjeta es la vista mientras conversas (solo lectura, con enlace «Editar identidad»), la ruta es donde se edita. El enunciado original —«integrar en la página de F1» dando por hecho que la tarjeta ERA la pantalla— se cumple en lo que perseguía (verla sin cambiar de pantalla) sin romper lo que ya dependía de la ruta.
  - Crear: `apps/admin-panel/app/admin/cortex/identity-card.tsx` (radar Big-Five, narrativa con preview Markdown — reutilizar el render Markdown del chat de F1, lista de `core_values`/`learning_goals`, nombre). **Copy honesto** fijo: "Modelo computacional de identidad/afecto — no es consciencia ni sentimientos reales" (ES+EN). _(Entregado en `components/cortex/identity-card.tsx`: los componentes compartidos del córtex viven en `components/cortex/`, como `trait-radar` e `identity-timeline`, no sueltos en `app/`.)_
  - Crear: `apps/admin-panel/app/admin/cortex/identity-timeline.tsx` (timeline de versiones desde `GET /identity/history`, mostrando el `diff` por versión). _(Entregado en `components/cortex/identity-timeline.tsx`, misma razón que la tarjeta. Su copy también pasó al diccionario el 2026-08-19: llevaba dentro un aviso honesto —«modelo computacional, no memoria de un yo»— en castellano a secas.)_
  - Integrar en la página de F1 `apps/admin-panel/app/admin/cortex/page.tsx` (segunda columna / Panel de Mente). Gated `isSystemOwner` (hook `use-current-user`). _(Hecho: `data-testid="cortex-second-column"`, dentro de la rama owner-only de la página — un no-owner ve `cortex-no-access` y ni la tarjeta ni los diales se montan, con test.)_
  - Cliente API: `apps/admin-panel/lib/cortex-identity.ts` (fetch a los endpoints) + helper puro testeado `identityDiffSummary(diff)` (resumen legible de un cambio).
  - TDD: `apps/admin-panel/lib/cortex-identity.test.ts` (vitest, patrón `lib/conversation-history.ts`/`conversation-history.test.ts` de Feature 2): `identityDiffSummary` resume un diff multi-campo; etiqueta de versión correcta.
  - **Criterio**: la tarjeta renderiza identidad+narrativa con copy honesto; timeline muestra versiones; gated `isSystemOwner`; tests vitest en verde. **Cumplido**: 11 vitest de la tarjeta + 5 del montaje + los 11 del timeline y los 24 de `lib/cortex-identity.test.ts` que ya había, todos verdes; `tsc --noEmit`, `next lint --max-warnings=0`, `check-i18n` y `check-component-size` en verde.
  - ✅ **Lo que quedaba abierto y NO era de esta casilla, cerrado el mismo día:** el botón «que se
    proponga él» (co-construcción con `propose_identity`, que la nota de **F3.3** atribuía a F3.6 y que
    también reclamaba `gaps-cortex-2026-07-27.md`). La observación de esta casilla era correcta —es la
    TARJETA, y marcarla no cerraba aquello—, y por eso se entregó aparte: botón en el banner de
    identidad pendiente + cliente + cuatro tests con su rojo comprobado. Ver la nota de F3.3.

### F3.7 — Documentación + cierre de ADRs

- [x] **Promover ADRs y registrar cambios** (hecho el 2026-08-19)
  - [ADR 0074](../05-architecture-decisions/0074-rol-system-owner-y-cortex-singleton.md): sección «F3 — identidad evolutiva» al final — qué concreta F3 de este ADR (guardrail determinista, aislamiento por owner con su test cross-owner) y qué lo **corrige**: la mitad «tablas sin RLS» del punto 5 dejó de describir el sistema con el ADR 0156 + migración 0140.
  - [ADR 0078](../05-architecture-decisions/0078-bucles-cognitivos-fondo-cortex.md): sección «Estado de implementación (2026-08-19 — la reflexión de F3)» con el gobierno cumplido (budget diario, kill-switch, idempotencia, saciado de `coherence`) y evidencia `fichero:línea`; además se marca **VENCIDO** el matiz del 2026-07-30, que daba por incumplidas cosas que hoy sí están (también las de la curiosidad: `check_and_reserve`/`record_spend`/`approval_gate` y las cuatro métricas). **No** se inventa un `accepted-f3`: el corpus no usa estados por fase (el `accepted-f0` del 0074 es el único, y por una razón histórica), así que el ADR se queda `accepted` y la trazabilidad por fase la dan el plan y el changelog. Decisión razonada en la propia sección.
  - `docs/roadmap/cortex-system-owner.md`: párrafo de la Fase 3 reescrito — las cuatro cosas que daba por faltantes (autonombrado co-construido, timeline, budget, `coherence`) ya no faltan; lo que queda es el copy ES-only y el PR.
  - `docs/roadmap/mejoras-2026-06-chat-coste-cortex.md`: **ya estaba correcto** (verificado). Su Feature 1 declara F0 como alcance propio y enlaza F1-F5 a sus changelogs, F3 entre ellos. No se toca: reescribirlo sería añadir ruido, no verdad.
  - Changelog `docs/07-changelog/cortex-f3-identidad.md`: **reescrito entero contra el código**. Contradecía al sistema en ocho puntos —daba por ausentes budget, saciado de `coherence`, `list_history`, `GET /identity/history`, `identityDiffSummary` y `propose_identity`, decía que los rasgos son barras (hay radar) y que la reflexión no marca lo procesado (marca `reflected_through`)—; los ocho están tabulados en su sección «Lo que esta entrada afirmaba y era falso», para que se vea qué cambió y por qué.
  - **Criterio**: estado de los docs coherente con el código; sin placeholders. ✅

---

## Riesgos y mitigaciones (ADR-driven)

- **Excepción al Principio 1 (RLS)** — tablas tenant-less. Mitigación: filtro `owner_user_id` explícito en TODO SQL + **test cross-owner obligatorio** en F3.2 y F3.5 (es el punto de mayor escrutinio).
- **Coste autónomo** (ADR 0078) — el beat consume LLM sin owner. Mitigación: budget cap Redis + kill-switch + `enabled` opt-in + fail-open, parte del MVP (F3.4), no fast-follow.
- **Auto-modificación descontrolada** (ADR 0074) — la reflexión podría derivar la identidad. Mitigación: `bounded_update` (|Δ| por ciclo) + clamps + diff versionado en `cortex_identity_history`.
- **Olvido destructivo** (ADR 0077) — la identidad debe sobrevivir. Mitigación: `kind ∈ {identity, owner_model}` NUNCA se auto-olvida. (Este riesgo añadía «el override del owner NO toca narrativa»: retirado el 2026-08-19 por el [ADR 0157](../05-architecture-decisions/0157-quien-reescribe-la-narrativa-del-cortex.md) — la narrativa sí es co-diseñable, y lo que la protege de perderse no es la prohibición sino el versionado: cada escritura deja fila en `cortex_identity_history` con su autor y su `diff`, así que ninguna versión se destruye.)
- **Honestidad de producto** — copy honesto fijo en la tarjeta (F3.6); la narrativa no afirma consciencia.

## Ficheros críticos para la implementación

- `apps/api-server/migrations/versions/20260624_0092_cortex_identity.py` (nueva migración)
- `apps/api-server/src/api_server/cortex/identity.py` (capa pura: clamp/bound/diff)
- `apps/api-server/src/api_server/db/cortex_identity_repo.py` (acceso DB con aislamiento `owner_user_id`)
- `apps/workers/src/workers/cortex_reflection.py` (bucle de reflexión Celery beat, GATED)
- `apps/api-server/src/api_server/routers/cortex_identity.py` (endpoints gated `require_system_owner`)

> **Dos de esas rutas nunca llegaron a existir (verificado 2026-08-19)**, y esta
> lista es de diseño, no un inventario: los endpoints salieron dentro de
> `routers/cortex_mind.py` (ver F3.5) y el acceso a BD dentro de
> `cortex/identity.py`, no en un `db/cortex_identity_repo.py` aparte (divergencia
> declarada en el [changelog](../07-changelog/cortex-f3-identidad.md)). La migración
> tampoco es la `0092` sino la `20260624_0094_cortex_identity.py` — la `0092` la
> ocupó `cortex_threads` (F1), justo el caso que F3.0 avisaba de no dar por
> supuesto.
