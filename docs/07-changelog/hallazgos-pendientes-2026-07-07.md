---
plan: hallazgos-pendientes-2026-07-07
title: "Hallazgos pendientes (QA e2e en vivo + refactor 2026-07-07/08)"
completed_at: null
status: pending_human_validation
docs_language: es
---

# Hallazgos pendientes — QA e2e en vivo + refactor

## Resumen

Backlog nacido del **QA end-to-end en vivo del plan CI4** (2026-07-07/08), del
refactor por partes y de la habilitación de mypy total. No es una fase del
roadmap: son fricciones reales vistas ejecutando el sistema, más deuda
estructural anotada. Método declarado y cumplido: TDD por hallazgo, commit
atómico, sin big-bang.

De los 11 hallazgos numerados, 9 están resueltos, 1 quedó como decisión de
producto (y se resolvió después por ADR) y los "menores" del #10 siguen
parcialmente abiertos con nombre.

## Cambios

### P1 — fricciones vistas en el QA en vivo

- **#1 — carrera del run-lock ↔ evento diferido** (opción (a), la preferida):
  el evento de fin se publica **solo después de soltar el lock**. Verificado en
  `workers/tasks/run_cycle.py`: `release_run_lock(...)` y, a continuación, el
  `pending_task_event` con el comentario que lo explica ("publish the deferred
  finish event ONLY once the lock is free"). Antes, el orchestrator del mismo
  host despachaba el review en <10 ms, el worker lo recibía con el lock vivo
  (`run_lock_held_skip`) y el reconciler lo recuperaba ~6 minutos después: se
  auto-curaba, pero pagaba esa latencia en **cada** ciclo de review que perdiera
  la carrera.
- **#2 — un plan `blocked` no se auto-revertía** cuando desaparecía su causa.
  Resuelto con `reactivate_plan_if_unstuck`, invocado desde las vías humanas y
  desde `routers/tasks.py` y `routers/plans.py`, más la pasada del reconciler
  `_reconcile_unblocked_plans` (espejo de `_reconcile_complete_plans`, sin
  ping-pong). Tres vías huérfanas (delete / deps-only / free-task) que no lo
  hacían, y una cuarta (`create_task(plan_id)`) descubierta en la auditoría
  posterior, quedaron cubiertas.
- **#3 — el botón "Desbloquear" era invisible** en la superficie natural: hoy
  está en el board, en la página de escaladas y en la barra de ciclo de vida del
  detalle de plan.
- **#4 — app-preview del review-runtime**: sin UI de configuración y con un
  placeholder engañoso. Hoy es configurable y, si el proyecto no la tiene, la
  respuesta lo dice explícitamente en vez de fingir
  (`routers/review.py:173-180` y `:418`, con la referencia al hallazgo escrita
  en el código).
- **#5 — `pcov`** horneado en `php-phpunit` **y** en `php-pest` (el hallazgo
  solo mencionaba el primero), imágenes reconstruidas.
- **#11 — el rechazo humano de un plan no disparaba rework**: resuelto por el
  [ADR 0107](../05-architecture-decisions/0107-rechazo-con-correcciones-mismo-plan.md)
  (correcciones en el MISMO plan).

### P2 — deuda estructural

- **#6 — estado tipado del runtime**: `agent_runtime/state.py` define
  `AgentState(TypedDict)` y `ReviewState(AgentState, total=False)`, y la clave
  inyectada `written_files` vive **tipada** ahí en vez de aparecer por sorpresa
  en un `state.get(...)`. Además los tests del agent-runtime **corren en CI**
  (paso nuevo + meta-test que lo verifica): antes existían y nadie los ejecutaba.
- **#7 — fusión de los dos canales de veredicto**: era decisión de producto y se
  redactó como [ADR 0108](../05-architecture-decisions/0108-fusion-canales-veredicto-review.md)
  con tres opciones. Resuelto el 2026-07-12 aceptando la **Opción C** (statu quo
  documentado): la divergencia entre el tag `<verdict>` del run reviewer y la
  tool `submit_verdict` de la self-review **no es un accidente**, responde a dos
  contextos de ejecución distintos. Consecuencias aplicadas: anclas cruzadas en
  ambos parsers, wire-format único en `review_contract.py` con su test de
  contrato cruzado, y semánticas de tolerancia documentadas
  (`unknown → reject` defensivo en el worker vs `inconclusive → humano` en el
  runtime).
- **#8 — e2e del ciclo autónomo**: `tests/integration/test_autonomous_cycle.py`
  recorre el ciclo completo sobre **Docker real** (implementador → `in_review` →
  reviewer approve → `done`, y la rama reject → backlog) con modelos scripted, y
  no está skippeado. Vive en `integration/` porque reutiliza el harness probado
  del smoke. Además el floor de cobertura subió 30 → 31 con ratchet y meta-test.
- **#9 — ronda de refactor del frontend**: los 4 hotspots modularizados. El peor
  (detalle de plan, 1703 líneas) quedó partido en `plan-spec-types.ts`,
  `plan-spec-sections.tsx` y `plan-interactive-sections.tsx`, con `page.tsx`
  reducido a composición; hoy el directorio del detalle de plan tiene una decena
  de ficheros `*-section.tsx` con sus tests. Verbatim, testids intactos.

### #10 — menores

Cerrados: la protección de truncado F32 alcanza también a `claude_sdk`
(`CompletionResponse.stop_reason` cosechado del SDK); el **schema-gap del
córtex** (`schema_fn` inyectable, el córtex pasa `cortex_tool_schemas` — antes
**todas** sus llamadas `complete()` iban con `tools=None` y las tools operaban a
ciegas); y `worktree_coordinates` como fuente única de coordenadas de worktree
en los 6 sitios que las calculaban por su cuenta, con golden test endurecido.

## Lo que sigue abierto, con nombre

- `_decide_messages` interpola título y descripción de la tarea **sin fencing**.
  Aceptado como decisión consciente (posición de menor privilegio; tocarlo
  arriesga una convergencia calibrada), no como olvido.
- Partir `plan-interactive-sections.tsx` (1248 líneas), heredado de la propia
  auditoría del refactor.
- **P8** (unificar `db/domain.py` y `db/models.py`): **no abordar** — 273
  ficheros importadores para un beneficio moderado.

## Estado de cierre

Lo que falta es humano y de despliegue: este backlog acumula fixes de tres
tandas (2026-07-08, 07-09 y la remediación del 07-10) cuyo QA en vivo lo hizo el
operador sobre el plan CI4, y su rebuild de imágenes (api-server, workers,
agent-runtime) estaba pendiente cuando se escribió el último banner. Cerrarlo
exige que el operador confirme que el ciclo que destapó estos hallazgos vuelve a
recorrerse limpio.

## PR

- _pendiente_
