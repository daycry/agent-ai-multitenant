---
title: Auditoría de la implementación de los hallazgos-pendientes (tanda 2026-07-09)
date: 2026-07-10
status: completed
owner: operador (jmano)
branch: plan/runs-visor-trabajo
scope: commits 3c1094c5..cd0ea48a (12 commits, ~3.200 líneas)
---

# Auditoría — implementación de los 9 hallazgos (2026-07-09)

Auditoría de la tanda que implementó los hallazgos de
`hallazgos-pendientes-2026-07-07.md`: **#2** (`c55597a2`), **#6** (`9f95a9fc`),
**#7** (`46655724`, ADR 0108), **#8** (`e3954baa` + `2a0d5496`), **#9**
(`415a2578` + `618e6844`), **#10a/c/e** (`0642211c`, `5fd17cc1`, `944085ae`).

**Método**: 5 revisores independientes en paralelo (uno por cluster, read-only,
con el requisito literal de cada hallazgo como contrato) + verificación local de
TODAS las suites y claims de build. Los hallazgos con severidad crítica/importante
se re-confirmaron leyendo el código directamente antes de reportarlos.

## 1. Verificación de claims (todo ejecutado en local, 2026-07-10)

| Claim de la tanda                                | Resultado                                                                                                                                                                                            |
| ------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Suite unit backend en verde                      | ✅ 2143 passed                                                                                                                                                                                       |
| Cobertura 31.6% ≥ ratchet 31                     | ✅ 31.59% (ratchet efectivo en `ci.yml:214` + meta-test)                                                                                                                                             |
| Tests agent-runtime + shared-llm                 | ✅ 371 passed (1 skip preexistente: SDK real no instalado)                                                                                                                                           |
| e2e ciclo autónomo «VERDE local (2 tests)»       | ✅ 2 passed (~27 s) — **requiere api-server del stack dev arriba** (la runtime llama a la API interna); con el stack parado falla con `InternalAPIUnreachableError`, igual que el smoke preexistente |
| admin-panel: tsc 0 + vitest 201/201 + next build | ✅ los tres en verde                                                                                                                                                                                 |
| ADR 0108 bien formado                            | ✅ 3 opciones, recomienda C, `proposed`; sus referencias a código existen                                                                                                                            |
| «detalle de plan 1703→161 líneas»                | ⚠️ es 1703→**134** (desviación a favor; el número reportado no corresponde)                                                                                                                          |
| «worktree_coordinates en 6 sitios»               | ❌ son **5**; el tercero nombrado por el hallazgo quedó fuera (ver crítico/importantes)                                                                                                              |

## 2. Hallazgos de la auditoría

### Crítico (1)

**C-1 · #2 — Ping-pong real entre `_reconcile_unblocked_plans` y la escalación
C8 F40 (review expirada).** Confirmado en código:

- `expire_review_runtimes` (cada 5 min) mueve un plan `pending_human_validation`
  cuya sesión de review expira (48 h) a `blocked` + notificación
  (`workers/maintenance/review_runtimes.py:38-61`).
- Ese plan tiene TODAS las tareas `done`, y `transition_from_blocked` revierte
  deliberadamente los snapshots all-done (`api_server/plan_progress.py:232-234`)
  → la red del reconciler lo devuelve a `in_progress` en ≤90 s
  (`workers/maintenance/reconciler.py:440-530`).
- En la misma pasada, `_reconcile_complete_plans` lo re-promociona a
  `pending_human_validation` **y re-lanza `_autostart_review_runtime`** (la sesión
  expirada es terminal, no cuenta como activa) → nueva sesión con nuevas 48 h.
- Resultado: bucle `blocked → in_progress → pending_human_validation → (48 h) →
blocked → …` sin fin mientras nadie emita veredicto — un review-runtime nuevo y
  una notificación de escalación por ciclo, y la señal «este plan necesita al
  operador» que C8 F40 quería fijar queda destruida cada vez.

**Fix mínimo**: en `_reconcile_unblocked_plans`, no revertir cuando el snapshot no
tiene NINGUNA tarea abierta (esa firma es la del bloqueo C8 F40; el caso legítimo
«borré la última tarea blocked y solo quedan done» ya lo cubre síncronamente el
router en `tasks.py:497-498`). **Fix estructural**: persistir la procedencia del
bloqueo (`blocked_reason`) y que la red solo revierta bloqueos de origen snapshot.
Test a añadir: plan `blocked` procedente de review expirada permanece `blocked`
tras la pasada.

### Importantes (7)

**I-1 · #2 — Cuarta vía huérfana**: `POST /projects/{id}/tasks` con `plan_id`
(`routers/tasks.py:271-313`) crea una tarea avanzable en un plan `blocked` sin
llamar a `reactivate_plan_if_unstuck` — gemelo exacto de la vía free-task que el
commit sí cubrió (`plans.py:1121-1127`). Replicar la llamada tras el `flush` + test espejo.

**I-2 · #10a — El commit NO unificó los 3 sitios que el hallazgo nombra**:
`execution._resolve_review_worktree` sigue construyendo `BareRepoLayout` a mano
(`execution.py:501-509`, confirmado) y alimenta un bind DooD (mount read-only del
reviewer, ADR 0095), mientras el comentario en `execution.py:445-446`, el mensaje
de commit («6 sitios»; son 5) y el doc de hallazgos afirman lo contrario. Extraer
una primitiva `worktree_layout(data_root, tenant_slug, project_slug)` que
`worktree_coordinates` use por dentro, llamarla ahí, y corregir comentario+doc.

**I-3 · #10a — El golden test no clava el path**: `test_worktree_coordinates.py`
compara el helper contra las MISMAS primitivas que el helper llama (tautológico
ante un cambio en `BareRepoLayout`/`make_plan_branch_name`), y su guard
anti-normalización no detecta un `resolve()` en el runner Linux de CI (path sin
`..` ni symlinks → no-op). Clavar el **string literal** esperado en POSIX y usar un
`data_root` con segmento `a/../` o symlink para cazar normalización en CI.

**I-4 · #10c — El verdict en PROSA sigue sin guard de truncado**
(`agent_runtime/providers.py:728-729`): cuando el reviewer claude_sdk degrada a
prosa, `_parse_verdict` corre sin consultar `_completion_signals` — una review
truncada por token-cap puede colar un PASS autoritativo, exactamente la clase de
bug que este fix cierra para el FINISH, en el consumidor de mayor riesgo. Fix: en
esa rama, si `signals.truncated` → inconcluso (escala a humano).

**I-5 · #10c — Reintento ciego**: el modelo cuyo FINISH se rechaza por truncado ve
`{"tool": "noop", "ok": true}` sin el motivo; un modelo persistentemente verboso
repite el mismo output hasta quemar `max_iterations`. Acotado y fail-closed, pero
un nudge sticky («tu respuesta se cortó en el tope; sé más conciso») lo haría
dirigido. Follow-up, no bloqueante.

**I-6 · #10e — El riesgo declarado quedó validado solo empíricamente**: no hay
test ni manejo en código del duplicado `web_search` (schema host + nativa del
modelo si alguien cablea `web_enabled` de ADR 0076 — hoy duplicación LATENTE), y
el córtex sobre claude_sdk cambió de transporte (`complete()` plano →
`_complete_with_tools` MCP) sin test. Además nada pinnea
`build_cortex_model(...).schema_fn is cortex_tool_schemas` — un revert accidental
del kwarg pasaría la suite en verde.

**I-7 · #8 e2e — El eslabón worker→orchestrator se fabrica**: el docstring dice
«el único seam es el proceso worker», pero los eventos `task.status_changed` se
construyen a mano en un stream de test, cuando el worker real SÍ publica el evento
real en el mismo Redis (`run_cycle.py:255-260` → `events:tasks`). Si
`publish_task_status_changed` se rompiera, el ciclo autónomo se pararía en
producción y este test seguiría verde. Consumir el entry real de `events:tasks` y
corregir el docstring. (Segundo matiz: el ciclo corre sin dimensión git — el
reviewer va en modo degradado sin worktree; declararlo en el docstring o añadir
variante con repo sembrado.)

### Menores (selección; detalle en los informes de los revisores)

- **#6**: «tipada, visible a mypy» sobrevende — `ReviewState` no se usa como
  anotación en producción; quien protege es el scanner AST (que sí es
  bidireccional y sólido). Rebajar el docstring o anotar `review_state: ReviewState`
  en `graph.py:998` (2-3 firmas, no cascada).
- **#8a**: comentario stale en `pyproject.toml:409` («Current floor: 30%») — el
  sitio al que el workflow remite; actualizar a 31.
- **#10c**: comentario desactualizado en `providers.py:536-541` (contradice el
  fix); doc-drift en `types.py:83-84` (`stop_reason` dice «normalizado del
  provider» pero los providers HTTP nunca lo populan — o popularlo desde
  `finish_reason`, o corregir el docstring).
- **#2**: sin evento `plan_unblocked` (la UI se entera por polling y el operador
  puede actuar sobre una notificación `plan_blocked` obsoleta); router sin guard
  atómico (lost-update humano-vs-humano, baja probabilidad); PUT que mueve
  `plan_id` no re-evalúa el plan ORIGEN (la red lo cubre en ≤90 s); falta test
  negativo a nivel router.
- **#8 e2e**: `TEST_REDIS_URL` hardcodeado (`test_autonomous_cycle.py:55`);
  ~80 líneas duplicadas con el smoke (extraer `_pipeline_helpers.py`); asserts del
  camino reject sin verificar executions/feedback persistido; sin
  `pytest.mark.timeout`.
- **#9**: `plan-interactive-sections.tsx` (1248 líneas) queda como 4º hotspot del
  admin-panel — apuntar su partición como continuación del tramo; B2/B3/C1/C2/D1/T11
  NO son tests de esta página (la caracterización efectiva es el `page.test.tsx`
  de ADR 0107) — corregir la premisa en el doc de hallazgos; export muerto
  `PlanCorrectionEntry`.

## 3. Lo que está bien (verificado, no asumido)

- **#9 es una extracción verbatim al 100%, verificada mecánicamente**: las 33
  declaraciones top-level del monolito son idénticas línea a línea en los 3
  ficheros nuevos, los 85 `data-testid` sobreviven, el orden de render de las 13
  secciones es exacto, sin hooks reordenados, sin `any` nuevos, nomenclatura
  segura para el App Router y `'use client'` correcto.
- **#2**: las 3 vías nombradas (delete / deps-only / free-task) quedan cubiertas
  en la misma transacción, `transition_from_blocked` es la negación literal de
  `transition_to_blocked` (no reimplementa), la red del reconciler es tenant-safe
  con guard atómico, y los tests son de integración reales (app completa +
  Postgres migrado + asyncpg).
- **#6**: el step de CI es real y no puede pasar sin ejecutar (exit 5 si no
  colecta); el contrato de claves es bidireccional y se auto-verifica.
- **#8a**: los tests de `detect_outliers` son de la función de producción con
  aserciones exactas en Decimal (no teatro de cobertura); el ratchet está en la
  línea que la CI ejecuta.
- **#10c**: la cosecha de `stop_reason` se verificó contra el wheel REAL
  `claude-agent-sdk 0.2.82`; los 3 sitios que construyen `CompletionResponse` lo
  pasan; mapeo conservador (`max_tokens`/`length`), sin regresión en los
  providers HTTP; el guard replica el patrón F32 ya calibrado.
- **#10e**: seam `schema_fn` mínimo con default seguro (gotcha de binding evitado
  y documentado), catálogo del córtex completo en chat Y voz, gates web y cap de
  `cortex_remember` intactos, y el cierre FINISH_NUDGE sobrevive
  (`enabled_tools=()` → `tools=None` en el turno de cierre).
- **#8 e2e**: el pipeline ejercitado es real donde importa — Postgres desechable
  migrado, orchestrator/dispatcher de producción, cola Celery real, DOS
  contenedores `agent-runtime:v1` reales por test, `_apply_review_verdict` real
  parseando stdout real; scripted = SOLO el LLM (el seam correcto, preexistente y
  no alcanzable vía API); asserts de estados intermedios en DB; corre en cada CI
  con fail-loud si falta la imagen.

## 4. Veredicto

**La implementación es sustancialmente correcta y de calidad alta**: todos los
claims verificables se verificaron en verde, no hay teatro de tests, y las piezas
delicadas (DooD, convergencia, multi-tenancy, App Router) se trataron con el
cuidado que el repo exige. Queda **1 crítico** (C-1, el ping-pong C8 F40 — es la
misma clase de bug que el hallazgo #2 quería erradicar, trasladado al otro
productor de plan-`blocked`) que conviene arreglar antes de dar #2 por cerrado, y
un remate de #10a (I-2/I-3: la unificación quedó a un sitio de ser completa y su
red de test no sujeta). El resto son endurecimientos incrementales que no
bloquean.

**Orden sugerido**: C-1 (+test) → I-2/I-3 (remate #10a) → I-4 (verdict prosa
truncado) → I-1 (cuarta vía #2) → I-7 (evento real en el e2e) → I-6 (pin de
`schema_fn` + exclusión mutua web) → menores oportunistas.

## 5. Remediación (2026-07-10, misma fecha) — CRÍTICO + IMPORTANTES resueltos

Implementados con TDD + commit atómico en `plan/runs-visor-trabajo`:

| Hallazgo | Commit     | Qué se hizo                                                                                                                                                                                                                                                                     |
| -------- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **C-1**  | `0456c09c` | La red del reconciler salta snapshots todo-terminal (firma C8 F40) — `has_open_tasks` en `plan_progress` + test anti-ping-pong. También destapó y arregló que `c55597a2` había roto `test_reconcile_pipeline_state` (clave `unblocked_plans` sin actualizar en la expectativa). |
| I-1      | `0527bf3c` | `POST /projects/{id}/tasks` con `plan_id` llama `reactivate_plan_if_unstuck` (vía D) + test espejo.                                                                                                                                                                             |
| I-2/I-3  | `f203d279` | Primitiva `worktree_layout()`; `_resolve_review_worktree` unificado; golden test con strings LITERALES + guarda anti-`resolve()` efectiva (`x/..`) + contrato de fuente única sobre `execution.py`; doc corregido (5→6 sitios).                                                 |
| I-4      | `dcdf3e0f` | `_review_from` fuerza INCONCLUSO el verdict en prosa truncado (`_TRUNCATED_PROSE_VERDICT_FEEDBACK`); el boolean estructurado íntegro se sigue honrando. Nota de módulo de la señal actualizada.                                                                                 |
| I-5      | `ffe4ebbc` | Los guards F32 pasan `reason` y `noop` lo devuelve como output — reintento dirigido, no ciego.                                                                                                                                                                                  |
| I-6      | `56c0a6b4` | Pins: `schema_fn is cortex_tool_schemas` + preservación en `apply_effort_decision`; exclusión mutua web nativa/host (`cortex_tool_schemas_without_host_web` cuando `web_enabled` + claude_sdk).                                                                                 |
| I-7      | `0df2796b` | El dispatch del review consume el evento REAL de `events:tasks` (grupo fresco id=0, limpieza en seed); docstring declara los seams y el límite sin-git; `TEST_REDIS_URL` overridable.                                                                                           |

Verificación tras la remediación: unit 2149 en verde con ratchet 31, agent-runtime

- shared-llm + meta-tests 506 en verde, integración afectada (smoke + reconcilers +
  router-gaps) 13 en verde, e2e ciclo autónomo 2/2 (~37 s) consumiendo el evento real.
  Los MENORES del §2 siguen abiertos salvo los absorbidos de paso (nota de módulo
  F32, comentario del floor en `pyproject.toml`, `TEST_REDIS_URL`).

**Deploy pendiente**: los cambios de runtime (I-4/I-5) viven en la imagen
`agent-runtime:v1` y los de api-server/workers (C-1, I-1, I-6) en sus imágenes —
requieren rebuild+redeploy del stack dev para estar vivos.

## Referencias

- Requisitos: `docs/roadmap/hallazgos-pendientes-2026-07-07.md`.
- Nota de entorno: el e2e local exige Docker Desktop + `agent-runtime:v1` +
  Postgres dev (15432) + Redis (6379) + **api-server healthy** (API interna).
