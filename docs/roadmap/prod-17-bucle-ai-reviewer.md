---
plan_id: prod-17-bucle-ai-reviewer
title: Bucle del AI reviewer — in_review → veredicto → done/backlog (cierre del ciclo autónomo)
status: pending_human_validation
blocking_plan: [prod-06-ciclo-vida-ejecucion]
started_at: 2026-06-26
completed_at: null
estimated_duration_calendar: 2-3 semanas
estimated_effort_person_days: 10-13
estimated_cost_human_eur: 4.500 € – 6.500 €
estimated_cost_ai_eur: 30 € – 70 €
created_by: prod-06-dag_03-defer-2026-06
spec_sections_referenced: [12.5, 12.6]
docs_language: es
priority: P2
gate_override:
  approved_by: operador
  date: 2026-07-31
  adr: 0138
  unmet: prod-06-ciclo-vida-ejecucion
  reason: >-
    `prod-06` está entregado y en `pending_human_validation`. La única casilla que le
    queda a prod-17 es una e2e que exige runner Docker y lanzar runs reales, que la
    orden vigente del operador tiene vetado hasta dar el sistema por verificado.
---

# Plan prod-17 — Bucle del AI reviewer

## Cabecera

| Campo                            | Valor                                                        |
| -------------------------------- | ------------------------------------------------------------ |
| **ID del Plan**                  | `prod-17-bucle-ai-reviewer`                                  |
| **Prioridad**                    | P2                                                           |
| **Bloqueado por**                | prod-06 (usa su transición a `in_review` y la promoción DAG) |
| **Tiempo estimado (calendario)** | 2-3 semanas                                                  |
| **Rama git sugerida**            | `plan/prod-17-bucle-ai-reviewer`                             |
| **ADR semilla**                  | `0084` (accepted, Opción B) + `0027` (bucle)                 |

> **Estado**: la fuente de verdad es el frontmatter YAML de este fichero (`status:`). El campo duplicado que había en esta tabla se retiró en prod-15 (hallazgo docsroadmap-6): se había desincronizado en 22 de 51 planes.

---

> **Estado (2026-07-06, auditoría de roadmap)**: `status` corregido de `in_progress` (congelado
> desde 2026-06-26) a `pending_human_validation`. PR #56 fusionado a `master` (`0f4d505`); 6/7
> checkboxes hechos, el único pendiente (`task_prod17_e2e_01`) requiere runner Docker real. El
> bloqueo original de este plan (reviewer "ciego" al código, sin worktree) se resolvió **fuera**
> de este documento, en un track paralelo sin cruce de vuelta: ADR 0086/0087 (self-review
> autoritativo), ADR 0088 (reconciler), ADR 0095 D1 (worktree read-only del reviewer) —
> formalizado en [refactor-pipeline-ejecucion-review.md](refactor-pipeline-ejecucion-review.md).
> Antes de cerrar este plan de verdad, cruzarlo con ese documento.
>
> **Cruce hecho (2026-07-08)**: `refactor-pipeline-ejecucion-review.md` queda **`completed`** —
> el bucle del reviewer se validó humanamente en el QA e2e en vivo del plan CI4 (2026-07-07/08):
> ×3 ciclos in_review→veredicto por tool→done, vía de escalado a humano ejercitada, plan 14/14 a
> `pending_human_validation` con sesión de review auto-creada. A ESTE plan le queda únicamente
> `task_prod17_e2e_01` (e2e automatizado con Docker real — la misma deuda que el hallazgo #8 de
> `hallazgos-pendientes-2026-07-07.md`); al cerrarse aquélla, cerrar este plan.

## Resumen

El plan 06 (testing-revisión-git, `completed`) construyó la **biblioteca** del AI
reviewer — `reviewer_bridge.parse_reviewer_output` (parsea los tags
`<verdict>approve|reject</verdict>`) + `apply_reviewer_verdict` (aplica el veredicto a
la tarea) + el agente builtin `reviewer` sembrado + `reviewer_input.reviewer_input_block`
(envuelve un TestReport en `<test-report>`) — pero **nunca cableó el productor**:
`apply_reviewer_verdict` tiene **0 callers productivos** (workers-1). prod-06
(`task_prod06_dag_01`) cerró media historia: una tarea `done` con `reviewer_agent_id`
pasa a `in_review`, **pero nadie consume `in_review`** (el orchestrator solo reacciona a
`ready`/`done`), así que la tarea se queda parada ahí para siempre.

`task_prod06_dag_03` iba a cerrar este hueco, pero (a) su **parte B** (métrica de
profundidad de cola/estado) ya se entregó en prod-06, y (b) su **parte A** (cablear el
reviewer) se DIFIRIÓ aquí por decisión del operador (2026-06-26, **ADR 0084 Opción B**):
es un subsistema, no una tarea de 1,5 días, y la review automática añade una **segunda
ejecución de agente por tarea revisada** (coste) — una decisión de producto que merece
su propio plan.

**Corrección importante (ADR 0084):** este bucle **NO depende de ADR 0063**. Aquél es el
contenedor **review-runtime de preview HUMANO** (sirve la app construida para que un
humano la pruebe; bloqueado por B1 `main_image` / B2 worktree). El AI reviewer es una
**ejecución de agente normal** — el motor de ejecución ya es agnóstico.

Este plan cierra el **ciclo autónomo de revisión**: `in_review` → ejecuta el agente
reviewer → parsea el veredicto → `approve` avanza la tarea a `done`, `reject` la devuelve
a `backlog` con feedback estructurado y, al agotar reintentos, escala a validación humana.

## Alcance

**Entra**:

- Reconciliación de `apply_reviewer_verdict`: `approve` mueve `in_review → done` (hoy es
  no-op); `reject` → `backlog` + `retry_count++` (ya existe) **+ escalado a `blocked` al
  llegar a `max_retries`** (DB-legal desde `in_review`; ver decisión 2; evita bucle infinito
  reject↔retry).
- Trigger en el orchestrator: reacciona a `task.status_changed → in_review` y, si el
  `reviewer_agent_id` resuelve a un agente **AI**, encola una ejecución de review.
- Routing por `agent_type`: AI reviewer (este plan) vs peer-review **humano** (camino
  existente `human_agents/review.py`, sin cambios).
- Builder del spec de review: contexto = tarea + criterios de aceptación + salida/diff del
  implementador (la ejecución previa).
- Worker: marcar la ejecución como de review; al terminar, pasar su salida por
  `parse_reviewer_output` → `apply_reviewer_verdict`; publicar el evento de tarea.
- Inyección del `<test-report>`: cableo productivo de `run_test_runtime` (hoy también sin
  caller productivo) → `TestReport` → `reviewer_input_block` en el prompt del reviewer,
  completando el bucle de ADR 0027:106-118.
- El envelope de budget (prod-06 `budget_02`) aplica también a la ejecución de review (se
  verifica explícitamente, no se re-implementa).
- Tests: `tests/integration/test_reviewer_bridge_wiring.py` (el que `dag_03` pidió y nunca
  existió) + integración del trigger + e2e del bucle.

**Queda fuera**:

- El **review-runtime de preview humano** (contenedor que sirve la app) — ADR 0063 Parte B,
  bloqueado por B1/B2. Concepto distinto; no se toca aquí.
- La regla de alerta `CeleryQueueGrowing` y el dashboard de profundidad de cola —
  **prod-08** (la métrica ya se emite desde prod-06 `dag_03` parte B).
- Cambios al peer-review **humano** (`HumanTaskReviewMode`, `human_inbox`) — estable.

## Decisiones clave

1. **El reviewer corre como ejecución de agente normal** (no un runtime propio). El motor
   (`conduct_execution`) es agnóstico; solo cambia el spec (agent_id del reviewer + contexto
   de review) y la captura de salida. Alternativa descartada: un review-runtime dedicado
   (over-engineering; el AI reviewer no sirve una app, solo emite texto).
2. **Escalado en reject**: `reject` con `retry_count < max_retries` → `backlog` (reintento);
   al alcanzar `max_retries` → **`blocked`** (no `backlog` infinito). NOTA de reconciliación:
   el in-memory `TaskLifecycle` escala a `awaiting_human`, pero la **state machine de BD NO
   permite `in_review → awaiting_human_approval`** (ese estado es de la approval-engine de
   ADR 0020, alcanzable solo desde `in_progress`). La salida DB-legal desde `in_review` para
   "review agotado, interviene un humano" es `blocked` (`in_review → blocked` ✓), coherente
   con `dag_01` (failed → blocked). El audit `review_comment` registra el motivo.
3. **`approve` cierra la tarea**: `in_review → done` vía `transition_task_status` (state
   machine), no mutación directa. Hoy `approve` es no-op y deja la tarea colgada.
4. **Routing por `agent_type`**: `reviewer_agent_id` con `agent_type` AI/reviewer → bucle de
   este plan; `agent_type='human'` → peer-review existente. Sin solapamiento.
5. **Coste reconocido**: cada tarea AI-revisada lanza una 2ª ejecución. Acotado (opt-in por
   `reviewer_agent_id`) y sujeto al budget del proyecto. Se documenta y se hace observable
   (métrica `agentic_tasks_by_status{status="in_review"}` ya emitida).
6. **`unknown` (sin tag de veredicto)**: re-prompt acotado UNA vez; si sigue `unknown`, se
   trata como `reject` defensivo (no se cierra una tarea sin veredicto explícito).

## Tareas

### Fase A — Reconciliación del bridge (sin productor nuevo aún)

#### `task_prod17_bridge_01` — `approve → done` + escalado en reject

- [x] **Título**: Extender `apply_reviewer_verdict` (reviewer_bridge.py:95): en `approve`
      mover `in_review → done` vía `transition_task_status` (+ `completed_at`); en `reject`,
      tras `retry_count++`, si `retry_count >= max_retries` transicionar a **`blocked`** (DB-legal
      desde `in_review`; ver decisión 2) en lugar de `backlog`. Mantener el audit
      `review_comment` (en el escalado, anotar el motivo `max_retries`). Cargar la Task con
      predicado `tenant_id` explícito (defensa en profundidad, como `human_agents/review.py`).
      `unknown` sigue siendo no-op (lo gestiona el caller, Fase B). El `approve`/escalado solo
      aplican si la tarea está en `in_review` (idempotencia; guard de estado).
- **Tiempo**: 1,5 días · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod17_bridge_01_a
    runtime: python-pytest
    command: "pytest tests/integration/test_reviewer_bridge_wiring.py -v"
  ```

### Fase B — Bucle de ejecución del AI reviewer

#### `task_prod17_loop_01` — Trigger `in_review` + dispatch de la ejecución de review

- [x] **Título**: En `orchestrator/dispatch.py`, añadir `_is_in_review_trigger` + un manejador
      que, ante `task.status_changed → in_review`, resuelva `reviewer_agent_id`: si es AI
      (agent_type reviewer/ai), construir una `ExecutionRequest` con `agent_id =
reviewer_agent_id` y un contexto de review (Fase B task_loop_02) y encolarla; si es
      humano o no hay reviewer, no hacer nada (lo cubre el peer-review existente). BYPASSRLS
      con predicado `tenant_id`.
- **Tiempo**: 2 días · **Complejidad**: m
- **Depende de**: task_prod17_bridge_01
- **Tests automáticos**:
  ```yaml
  - id: auto_prod17_loop_01_a
    runtime: python-pytest
    command: "pytest tests/integration/test_in_review_dispatch.py -v"
  ```

#### `task_prod17_loop_02` — Builder del spec de review

- [x] **Título**: Función que arma el contexto de review: tarea (título/descr/criterios de
      aceptación) + salida/diff del implementador (de la ejecución previa, `executions`/audit).
      Marca la ejecución como de review (p.ej. un flag/labels en el spec) para que el worker
      sepa aplicar el veredicto al terminar.
- **Tiempo**: 1,5 días · **Complejidad**: m
- **Depende de**: task_prod17_loop_01
- ✅ **Comando corregido (2026-08-20)**: `tests/unit/test_review_spec_builder.py` nunca existió,
  y el builder tampoco es una función suelta llamada así — es `_build_review_request` en
  `orchestrator/dispatch.py`. Las **dos** mitades del título están probadas, en dos ficheros
  porque son dos cosas distintas y se rompen por separado:
  - **el contexto que se arma** (criterios de aceptación reales, salida del implementador,
    `<test-report>` si lo hay, envoltorio de presupuesto) →
    `tests/integration/test_in_review_dispatch.py`, cuyo docstring dice «prod-17
    task_prod17_loop_01 + loop_02». El `-k review_request` separa lo de esta casilla de lo de
    `loop_01`, que declara el fichero entero. Verde 3/3;
  - **la marca de «esto es una review»** que el worker necesita para aplicar el veredicto al
    terminar → `tests/unit/test_agent_spec_review_context.py`, que nació de un hallazgo de
    auditoría (C1/F51): el orquestador construía `review_context` y **el worker no lo
    reenviaba al contenedor**, así que el reviewer trabajaba a ciegas y rechazaba por defecto
    todas las tareas. Verde 3/3.
- **Tests automáticos**:
  ```yaml
  - id: auto_prod17_loop_02_a
    runtime: python-pytest
    command: "pytest tests/integration/test_in_review_dispatch.py -v -k review_request"
  - id: auto_prod17_loop_02_b
    runtime: python-pytest
    command: "pytest tests/unit/test_agent_spec_review_context.py -v"
  ```

#### `task_prod17_loop_03` — Aplicación del veredicto al terminar la ejecución de review

- [x] **Título**: En el flujo post-ejecución del worker (`conduct_execution`), cuando la
      ejecución es de review: pasar su salida por `parse_reviewer_output` y llamar a
      `apply_reviewer_verdict` (Fase A). `unknown` → re-prompt acotado una vez (re-encola la
      review), luego `reject` defensivo. Publicar el evento de tarea resultante (done/backlog/
      awaiting_human) para que el board y el dispatcher reaccionen.
- **Tiempo**: 2 días · **Complejidad**: m
- **Depende de**: task_prod17_loop_02
- **Tests automáticos**:
  ```yaml
  - id: auto_prod17_loop_03_a
    runtime: python-pytest
    command: "pytest tests/integration/test_review_execution_applies_verdict.py -v"
  ```

### Fase C — Inyección del TestReport (bucle completo ADR 0027)

#### `task_prod17_test_01` — Cableo productivo de `run_test_runtime` → TestReport

- [x] **Título** ✅ **DESBLOQUEADO por prod-18 Fase D** (`task_prod18_test_01`): el productor
      (`run_test_runtime` encadenado tras el commit del worktree, antes de `in_review`) ya
      existe; el consumidor (`test_02`) ya leía el report. Falta solo el e2e Docker (prod-18
      Fase E). Dar caller productivo a `run_test_runtime` tras la ejecución del implementador:
      ejecutar los tests del proyecto en el test-runtime, persistir el `TestReport` en
      `task_audit_events`. Coordinar con prod-06 (la tarea ya pasa a `in_review`); el
      TestReport debe estar disponible antes de la review. > **BLOQUEO descubierto (2026-06-26):** `conduct_execution` NO fija > `ContainerSpec.workspace_host_path` — el agente implementador corre **sin el repo > del proyecto montado** ("la pool con reuso de worktree llega en Plan 06", ver > `container.py`). Sin un worktree con el código del implementador, el test-runtime no > tiene QUÉ testear. Es el subsistema de **git-worktrees / ejecución en worktree** > (CLAUDE.md principios 4/5), separado y mayor que esta tarea. test_01 espera ese > cableado; mientras tanto no se produce TestReport y test_02 (consumidor) degrada > elegantemente (revisa el diff). Candidato a un plan dedicado de worktree-execution.
- **Tiempo**: 2 días · **Complejidad**: m
- ✅ **Comando corregido (2026-08-20)**: `tests/integration/test_test_runtime_wiring.py` nunca
  existió. El cableado que desbloqueó esta casilla lo entregó **prod-18 Fase D**, y sus tests
  se escribieron allí, con el nombre del fichero de aquel plan: `_run_task_tests` recibe el
  `worktree_host_path` y **sólo** los criterios de aceptación automáticos (los `human` se
  descartan) → `tests/integration/test_conduct_execution_worktree.py -k run_task_tests`, cuyo
  docstring dice «prod-18 test_01». El segundo comando cubre el cambio de sitio que vino
  después (`task_wf_22`): la fase de tests **sale del worker `default`** y se despacha a la
  cola `test`, siguiendo esperando el resultado a propósito, porque el reviewer se despacha
  detrás y necesita encontrar un `<test-report>` real. Verde 2/2 + 11/11.
- **Tests automáticos**:
  ```yaml
  - id: auto_prod17_test_01_a
    runtime: python-pytest
    command: "pytest tests/integration/test_conduct_execution_worktree.py -v -k run_task_tests"
  - id: auto_prod17_test_01_b
    runtime: python-pytest
    command: "pytest tests/unit/test_test_phase_queue.py -v"
  ```

#### `task_prod17_test_02` — Inyección de `<test-report>` en el prompt del reviewer

- [x] **Título**: En el builder del spec de review (task_loop_02), si hay TestReport para la
      tarea, envolverlo con `reviewer_input.reviewer_input_block` e inyectarlo en el prompt del
      reviewer, completando el bucle de ADR 0027:106-118. Sin TestReport (proyecto sin tests) el
      reviewer revisa solo el diff (degradación elegante). > Hecho (consumidor): `_build_review_request` lee los `test_run_completed` persistidos > y `_format_test_report_block` arma el `<test-report>` que va en `review_context`. > Independiente del productor (test_01, bloqueado): cuando el test-runtime persista esos > eventos, el reviewer los usa automáticamente. Sin eventos → bloque vacío (degrada). > Test en `test_in_review_dispatch.py::test_review_request_includes_test_report_when_present`.
- **Tiempo**: 1 día · **Complejidad**: s
- **Depende de**: task_prod17_loop_02, task_prod17_test_01
- **Tests automáticos**:
  ```yaml
  - id: auto_prod17_test_02_a
    runtime: python-pytest
    command: "pytest tests/integration/test_in_review_dispatch.py -v -k test_report"
  ```

### Fase D — e2e, cierre y ratificación

#### `task_prod17_e2e_01` — e2e del bucle + presupuesto + ratificación ADR

- [ ] **Título** ⏸️ **BLOQUEADO (Docker real + test_01)**: Test e2e del ciclo completo
      (implementador → test-runtime → reviewer → approve=done / reject=backlog → reintento →
      escalado a `blocked`). Verificar que la ejecución de review cuenta contra el budget del
      proyecto (reusa prod-06 budget_02). Ratificar ADR 0084 + entrada de changelog +
      actualizar el diagrama de ADR 0027 si procede. > **BLOQUEADO:** el e2e corre contenedores reales (la ejecución del reviewer de punta a > punta) → gateado por Docker como el e2e de instalación; y el tramo con test-runtime > depende de `task_prod17_test_01` (a su vez bloqueado por el worktree-en-ejecución). El > bucle SIN test-report (Fases A–B + test_02 consumidor) está cubierto por los tests de > integración (`test_reviewer_bridge_wiring`, `test_in_review_dispatch`, > `test_review_execution_applies_verdict`). Changelog de progreso emitido en > `docs/07-changelog/prod-17-bucle-ai-reviewer.md`.
- **Tiempo**: 1,5 días · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod17_e2e_01_a
    runtime: python-pytest
    command: "pytest tests/e2e/test_autonomous_review_loop.py -v"
  ```

## Hallazgos de auditoría cubiertos

| fid       | Severidad | Tarea(s) que lo cierran                                                              |
| --------- | --------- | ------------------------------------------------------------------------------------ |
| workers-1 | high      | task_prod17_bridge_01, task_prod17_loop_01..03 (la parte que prod-06 dag_03 difirió) |

## Coordinación con otros planes

- **prod-06**: aporta la transición a `in_review` (`dag_01`), la promoción DAG (`dag_02`) y la
  métrica que hace `in_review` observable (`dag_03` parte B). Este plan consume esas piezas.
- **prod-08**: la regla `CeleryQueueGrowing` + dashboard consumen la métrica ya emitida; un
  `in_review` que no decrece sería la señal de que este bucle falla.
- **ADR 0063 (review-runtime de preview humano)**: NO relacionado funcionalmente; no confundir.

## Criterios de cierre

1. Todos los checkboxes `[x]` con su test automático en verde.
2. El bucle e2e (implementador → tests → reviewer → veredicto → escalado) verificado.
3. Tests humanos del plan validados.
4. Entrada en `docs/07-changelog/prod-17-bucle-ai-reviewer.md`.
5. ADR 0084 ratificado a IMPLEMENTADO; PR del plan mergeado a master.
