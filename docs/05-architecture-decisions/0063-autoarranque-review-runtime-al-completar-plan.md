---
adr_id: "0063"
title: "Autoarranque del review-runtime al completar un plan"
status: proposed
date: 2026-06-18
decided_at: null
decided_by: null
authors: [claude-code-2026-06]
plan_referenced: 06-testing-revision-git
docs_language: es
---

# ADR 0063 — Autoarranque del review-runtime al completar un plan

> **Estado: `proposed`.** La **parte A** (transición live del plan a
> `pending_human_validation` cuando termina su última tarea) está
> **implementada y testeada** en esta sesión. La **parte B** (arrancar de
> verdad el contenedor review-runtime) depende de **dos decisiones de producto
> abiertas** que este ADR plantea para que las ratifique un humano — no se
> implementan "a ojo" (CLAUDE.md: no inventar features/arquitectura).

## Contexto

El flujo de validación humana (ADR 0062) asume que, cuando un plan termina, el
plan pasa a `pending_human_validation` y se **levanta un review-runtime** que
sirve la app construida para que un humano la pruebe.

La auditoría de esta sesión encontró que esa cadena estaba **rota en el path
live**:

- `plan_progress.transition_to_pending_human_validation` (función pura) solo se
  invocaba desde `orchestrator/plan_runner.py`, que es un runner **in-memory de
  demos** (usa `task_store._tasks.values()`, no la BD). En producción, un plan
  cuyas tareas se completaban **nunca** transicionaba solo a validación humana.
- `workers.compose_review_runtime` (la tarea Celery que arranca el contenedor)
  **no se encolaba desde ningún sitio** fuera de los tests.

El único consumidor live del stream de eventos de tareas es
`orchestrator.dispatch.TaskDispatcher.handle`, que solo reaccionaba a eventos
`ready`. Un evento `done` se descartaba.

## Decisión (Parte A — IMPLEMENTADA)

`TaskDispatcher.handle` reacciona ahora también a `task.status_changed` con
`new_status == "done"` (`_on_task_done`):

1. Carga la tarea (predicado `tenant_id` explícito; el orchestrator corre
   BYPASSRLS) → su `plan_id` (si no tiene plan, no-op).
2. Lee todas las tareas del plan desde la **BD** y reusa la función pura
   `transition_to_pending_human_validation(plan.status, snapshots)`.
3. Si procede, aplica una transición **atómica e idempotente**:
   `UPDATE plans SET status='pending_human_validation' WHERE id=... AND
tenant_id=... AND status='in_progress'`. El predicado `status='in_progress'`
   garantiza que, ante la entrega _at-least-once_ del stream (o varias tareas
   que terminan casi a la vez), la transición ocurra **exactamente una vez**.
4. Al ganar la transición, emite el log estructurado
   `orchestrator.plan_ready_for_review` — el **punto de enganche** de la Parte B.

El orchestrator es el hogar correcto: único consumidor live del stream, ya
multi-tenant, y ya dueño del `Celery` para encolar el spawn.

## Decisiones ABIERTAS (Parte B — el spawn real)

Para encolar un `compose_review_runtime` que **sirva** la app hace falta resolver
dos cosas que el código actual no decide y que NO deben inventarse:

### B1 — Procedencia de `main_image`

`review_runtime.py` referencia `main_image` **por tag** y dice explícitamente
que "la plataforma no lo construye". No existe pipeline que produzca una imagen
servible a partir del worktree del plan. Opciones a decidir:

- **(a) Imagen genérica por runtime-template**: mapear
  `projects.default_runtime_template` (p.ej. `php-apache`, `node`) a una imagen
  que **monta el worktree en `/workspace` y lo sirve** sin build. Simple; cubre
  apps interpretadas (PHP, estáticos, dev-servers). No cubre apps que requieren
  build/compilación.
- **(b) Build por plan**: una tarea previa construye `app:plan-{id}` desde el
  worktree (Dockerfile del repo) y el review-runtime la sirve. Potente; añade un
  paso de build (tiempo, caché, seguridad de la imagen).
- **(c) Operador-configurable**: un campo `review_runtime_image` (ajuste de
  plataforma/proyecto). Mínimo; traslada la decisión al operador.

### B2 — Worktree a nivel de plan

`compose_review_runtime` espera `worktree_host_path` (un path **absoluto,
idéntico en el worker y en el host del daemon** — ver gotcha
[agent-runtime DooD](../03-guides/gotchas/). Hoy los worktrees son **por tarea**
(`{data_root}/projects/{t}/{p}/worktrees/{task_id}`). Para el review **del
plan** hay que decidir qué árbol se sirve: ¿el worktree consolidado del plan
(tras `push_review_to_bare`)? ¿un worktree nuevo del HEAD de la rama del plan?
Hay que materializar ese path antes de encolar.

> Restricción transversal verificada: el bind del worktree es **DooD**; el
> `source` lo resuelve el daemon Docker, así que el path debe existir idéntico
> en el host. El instalador usa bind `{data_root}:{data_root}` (correcto); un
> **volumen nombrado** (como tenía el overlay de manuales) deja `/workspace`
> vacío. Ver gotcha `agent-runtime-egress-blocks-in-stack-llm` y el fix del
> overlay de manuales de esta sesión.

### Implicación con el modelo LLM

Aunque se resuelvan B1+B2, para que haya **algo que servir** el agente debe
**escribir y commitear** código real en el worktree. Con modelos pequeños
(p.ej. `llama3.2:1b`) el agente a menudo solo _responde_ el código en su mensaje
sin escribir el fichero. El autoarranque luce de verdad con un modelo capaz
(**Claude SDK**), que es el siguiente paso del operador.

## Consecuencias

- **Positivo (ya):** los planes vuelven a transicionar solos a validación humana
  en producción (bug de orquestación cerrado), idempotente y multi-tenant.
- **Pendiente:** el spawn del contenedor queda enganchado en
  `plan_ready_for_review` a la espera de B1/B2. Cuando se ratifiquen, se encola
  `compose_review_runtime` ahí con `{tenant_id, plan_id, repo_name,
worktree_host_path, main_image, main_port, expires_in_seconds,
human_checklist}` (best-effort, con revert/relog como el path de run AI).

## Alternativas descartadas

- **Barrer planes con Celery beat**: un sweep periódico que detecte planes
  completos. Descartado: el orchestrator event-driven ya recibe el evento `done`;
  un sweep añade latencia y otra fuente de verdad.
- **Transicionar desde el worker** (al terminar la última ejecución): el worker
  no tiene visión del plan completo sin recomputar; mantenerlo en el orchestrator
  centraliza la lógica de plan y reusa su sesión BYPASSRLS multi-tenant.
