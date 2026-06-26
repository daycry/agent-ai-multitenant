---
adr_id: "0084"
title: "Cableado del bucle del AI reviewer (in_review → veredicto → done/backlog)"
status: proposed
date: 2026-06-26
authors: [claude-opus]
plan_referenced: prod-06-ciclo-vida-ejecucion
docs_language: es
related: ["0027", "0063", "0020"]
supersedes: []
---

# ADR 0084 — Cableado del bucle del AI reviewer

> **Estado: `proposed`** — decisión de PRODUCTO (la review automática añade una
> SEGUNDA ejecución de agente por tarea revisada = coste, y cambia la semántica
> de `reviewer_agent_id`). Es la **parte A** de `task_prod06_dag_03`; la parte B
> (métrica de cola/estado) ya está entregada.

## Contexto y corrección de una conflación

`task_prod06_dag_03` estaba rotulado **"⏸️ DIFERIDO (depende de ADR 0063 —
ejecución del reviewer)"**. Al mapear el subsistema se confirmó que eso mezcla
**dos "reviewers" distintos**:

1. **AI code reviewer** — un agente LLM que revisa el trabajo y termina su
   salida con `<verdict>approve|reject</verdict>`. `reviewer_bridge.parse_reviewer_output`
   lo parsea y `apply_reviewer_verdict` lo aplica. **Es lo que pide dag_03.** Es
   una ejecución de agente normal — **NO necesita el contenedor de ADR 0063.**
2. **review-runtime** (ADR 0063) — un **contenedor que sirve la app construida**
   para que un **humano** la pruebe durante `pending_human_validation`. _Eso_ sí
   está bloqueado por las decisiones abiertas B1 (`main_image`) y B2 (worktree del
   plan) de ADR 0063.

Conclusión: **dag_03 (parte A) NO depende de ADR 0063.** Depende de una decisión
propia — la que toma este ADR.

### Estado actual (el hueco workers-1)

- `reviewer_bridge.parse_reviewer_output` + `apply_reviewer_verdict` existen y están
  probados como biblioteca, pero tienen **0 callers productivos**.
- El agente builtin `reviewer` está sembrado (system prompt que emite los tags).
- `dag_01` (ya entregado) mueve una tarea `done` a `in_review` si tiene
  `reviewer_agent_id`. Pero **nadie consume `in_review`**: el orchestrator solo
  reacciona a `ready` y `done` (`dispatch.handle`), así que la tarea se queda en
  `in_review` para siempre. El bucle de ADR 0027:106-118 nunca se construyó.
- `reviewer_agent_id` hoy solo lo consume el peer-review **humano**
  (`human_agents/review.py`, exige `agent_type='human'`). No hay noción operativa
  de "reviewer AI".
- Limitaciones del bridge a reconciliar: `approve` es **no-op** (no mueve la tarea
  a `done`); `reject` va **siempre a `backlog`** + `retry_count++` **sin escalado**
  (a diferencia de `TaskLifecycle.reject_review`, que escala a `awaiting_human` al
  llegar a `max_retries`).

## Decisión (a tomar por el operador)

### Opción A — Construir el bucle del AI reviewer ahora (MVP)

- **Trigger**: el orchestrator reacciona a `task.status_changed → in_review`;
  si `reviewer_agent_id` resuelve a un agente **AI** (`agent_type` reviewer/ai con
  `review_capability`), construye una `ExecutionRequest` con `agent_id =
reviewer_agent_id` y un contexto de revisión (criterios de aceptación + salida/diff
  del implementador) y la despacha como **una ejecución de agente más** (el motor es
  agnóstico). Si resuelve a un **humano**, sigue el peer-review existente (sin cambio).
- **Aplicación del veredicto**: al terminar la ejecución de review, el worker pasa la
  salida por `parse_reviewer_output` y aplica:
  - `approve` → `in_review → done` (vía state machine; se cierra el no-op actual).
  - `reject` → `backlog` + `retry_count++` + audit `review_comment` (ya existe), y
    **escalado**: al llegar a `max_retries` → `awaiting_human_approval` (reconcilia
    con `TaskLifecycle`, evita bucle infinito reject↔retry).
  - `unknown` → re-prompt acotado una vez, luego trata como `reject` defensivo.
- **Sin inyección de test-report en el MVP**: el reviewer revisa el diff/salida del
  implementador contra los criterios. La inyección del `<test-report>` (ADR 0027
  completo, vía `reviewer_input_block`) se añade cuando se cablee el test-runtime
  productivo (hoy `run_test_runtime` tampoco tiene caller productivo) — enhancement
  posterior, no bloquea el bucle.
- **Coste**: cada tarea AI-revisada lanza una SEGUNDA ejecución de agente. Acotado a
  tareas que opten (con `reviewer_agent_id` AI). El envelope de budget (budget_02) ya
  aplica también a la ejecución de review.
- **Pros**: cierra el ciclo autónomo (el objetivo del producto); da caller productivo
  a `apply_reviewer_verdict`; el `in_review` deja de ser un agujero negro.
- **Contras**: coste LLM x2 en tareas revisadas; superficie nueva en orchestrator
  (trigger + builder de spec de review) y worker (captura de salida + aplicación);
  semántica de `reviewer_agent_id` ampliada (AI vs humano por `agent_type`).

### Opción B — Diferir a un plan dedicado (Recomendada)

- dag_03 (parte A) resultó **mayor que su estimación** (1,5 días asumían "cablear un
  bridge a un flujo existente", pero ese flujo —reviewer-como-ejecución-de-agente—
  no existe) y **solapa** con el subsistema test-runtime y la visión completa de ADR
  0027 / el plan 06 (testing-revision-git).
- Dejar `apply_reviewer_verdict` sin cablear por ahora; rastrear el bucle completo
  (test-runtime → reviewer → veredicto + escalado + inyección de test-report) en un
  plan dedicado donde se diseñe junto a su contexto natural.
- **Pros**: no introduce un cambio de coste/comportamiento a medias; el bucle se
  diseña entero y coherente con el test-runtime. **Contras**: `in_review` sigue siendo
  un estado terminal de facto hasta entonces (mitigado: la métrica de la parte B ya lo
  hace VISIBLE — un `in_review` creciente se ve en el dashboard).

### Opción C — Solo un caller mínimo, sin auto-ejecución

- Exponer un camino que aplique un veredicto (endpoint o hook) para un reviewer
  externo/manual, dando caller a `apply_reviewer_verdict` (+ el fix approve→done y el
  escalado) **sin** la segunda ejecución automática de agente.
- **Pros**: coste/riesgo mínimo, cierra los gaps del bridge. **Contras**: NO cierra el
  bucle autónomo (sigue requiriendo un disparo externo).

## Recomendación

**Opción B (diferir a un plan dedicado)**, porque la parte A es un subsistema (no una
tarea) y se diseña mejor junto al test-runtime y al resto de ADR 0027. La parte B ya
entregada hace el problema **observable** mientras tanto. Si el operador prioriza la
review autónoma ya, **Opción A** como MVP (sin test-report), asumiendo el coste de la
segunda ejecución. La Opción C es el término medio si se quiere caller productivo sin
coste automático.

## Consecuencias

- **Si A**: trigger `in_review` en `dispatch` + builder de spec de review + captura de
  salida → `parse_reviewer_output` → `apply_reviewer_verdict` extendido (approve→done,
  escalado a awaiting_human en max_retries) + routing por `agent_type` + tests
  (`tests/integration/test_reviewer_bridge_wiring.py`).
- **Si B**: marcar `task_prod06_dag_03` parte A como diferida a su plan; `reviewer_bridge`
  queda como biblioteca lista sin caller; este ADR documenta el diseño objetivo.
- **Si C**: endpoint/servicio que aplica veredicto + fixes del bridge + test; sin trigger
  automático.

En los tres casos, la métrica de profundidad/estado (parte B) **ya está emitida** y es
independiente.
