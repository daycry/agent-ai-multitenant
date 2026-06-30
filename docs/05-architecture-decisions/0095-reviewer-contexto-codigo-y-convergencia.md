---
adr: "0095"
title: Reviewer con contexto de código (worktree read-only) y convergencia/escalado del review
status: proposed
date: 2026-06-30
deciders: operador, System Architect (claude-opus)
phase: runs-convergencia
related: ["0045", "0085", "0086", "0087", "0088", "0089", "0092"]
docs_language: es
---

# ADR 0095 — Reviewer con contexto de código y convergencia/escalado del review

## Contexto

Un run del **AI Code Reviewer** (CI4, claude_sdk/opus) abortó con `max_iterations_exceeded` y dejó
la tarea atascada en `in_review` en bucle. La evidencia del run real (`019f181e`): `iterations=50`,
`tool_call_count=50`, **`submit_result=0`**, `read_file composer.json → "not a file"` repetido, y
"Reflection: tool failed — will reconsider" en las 50 iteraciones. La causa es **doble y
estructural**:

### Causa 1 — El reviewer está CIEGO

Un run de review **no monta worktree** (`apps/workers/src/workers/execution.py:1063`,
`if not request.review …`): por la decisión de **ADR 0085** ("el worktree RW es del implementador;
el reviewer lee `review_context`"), su `/workspace` es un **tmpfs vacío**. Solo recibe
`review_context` = criterios de aceptación + **la prosa del implementador** + test_report
(`apps/orchestrator/src/orchestrator/dispatch.py:619-624`). **Nunca ve el código ni el diff.**

Pero su prompt (`docker/agent-runtimes/.../__main__.py:291-323` + `seeds/ci4_team.py:548-565`) le
ordena _inspeccionar el código y correr las quality gates_ (PHPStan, `@ci`). Ante un workspace vacío,
el modelo lanza `read_file`/`list_files`/`search_code` una y otra vez buscando un código que no tiene
montado — de ahí el `"not a file: composer.json"`. Es el hallazgo "reviewer a ciegas" (C1/F51 de la
auditoría) en su forma residual: aunque se le pasa `review_context`, sigue sin ver el código.

### Causa 2 — Un run read-only estéril nunca converge

En claude_sdk, FINISH solo ocurre en un **turno de prosa sin tool-call** (`providers.py:546`); como
el modelo no para de hacer tool-calls (buscando el código), **nunca emite el `<verdict>`**. Y los
safeguards de convergencia están **gateados a `has_produced`**, que un reviewer (que no produce
fichero) nunca cumple:

- `_research_exhausted` (`graph.py:240-252`, ADR 0089-D4) exime explícitamente al run analysis-only
  estéril → **no corta al reviewer**.
- `_research_nudge` (`graph.py:199-237`) le dice "STOP researching … produce the deliverable now
  (e.g. write_file)" — justo lo que un reviewer tiene **prohibido**.

→ agota `max_iterations=50` (`config.py:162`) → `has_produced=False` → `STATUS_ABORTED`
(`graph.py:130-139`) → el worker `_apply_review_verdict` ve `status != "done"` sin `<verdict>` →
loguea `review_infra_error` y hace **`return None`** (`execution.py:736-753`) → la tarea queda
`in_review` → el reconciler `_reconcile_orphan_reviews` (`maintenance.py:957-1047`) **re-despacha la
misma review cada ~5 min sin contador de reintentos** → **bucle infinito**, sin escalar a humano.

**Síntesis:** abortar es el síntoma; la raíz es que el reviewer **no ve el código que debe revisar**,
y los safeguards no contemplan un run de solo-lectura.

## Decisión

**Root fix en tres frentes.**

### D1 — Desciegar: el reviewer monta el worktree del implementador en READ-ONLY

Se refina **ADR 0085**: un run de review deja de correr con `/workspace` vacío y monta el worktree
del implementador (task-keyed, persistente, `worktrees/{task_id}`, ya con el código + `vendor/`) en
**read-only**. El reviewer ve y lee el código real; `review_context` (prosa + test_report) se queda
como contexto suplementario. El worktree es **read-only** → el reviewer no puede mutar el trabajo del
implementador (sin conflicto RW; coherente con el espíritu de ADR 0085 de que el worktree RW es del
implementador). Si el worktree no existe en disco (implementador corrió en tmpfs) → fallback al
comportamiento actual (tmpfs vacío).

### D2 — Convergencia: safeguards conscientes-de-reviewer (refina ADR 0089-D4)

Se cablea un flag `is_review` del spec al grafo. Para un run de review:

- `_research_nudge` devuelve la redacción **FINISH/veredicto** ("tienes contexto suficiente —
  TERMINA con tu etiqueta `<verdict>`"), no la de "write_file".
- `_research_exhausted` **sí dispara** al cruzar `hard_limit` aunque `has_produced=False` (un run de
  review es estéril por diseño; tras N lecturas debe concluir). Cuando dispara como último recurso,
  se finaliza **escalando** (`STATUS_NEEDS_HUMAN_REVIEW`), no con abort crudo.

Con D1 el reviewer ya ve el código y emite su `<verdict>` de forma natural; D2 es la red de seguridad
para el caso raro de no-convergencia.

### D3 — Escalado: romper el bucle infinito (cierra el hueco de ADR 0088/0089)

`_apply_review_verdict`: el branch `unknown + status != done` (hoy `return None` → re-bucle) gana un
**cap de reintentos**. Tras **N=2** runs de review no-concluyentes para la misma tarea (executions de
review `aborted`/`needs_human_review` sin `<verdict>`), se **escala la tarea a `needs_human_review`**
con motivo claro, en vez de `return None`. Por debajo de N se mantiene el `return None` (deja que el
reconciler reintente — ahora con código visible, debería converger). El reconciler respeta el
escalado (no re-anuncia una tarea ya escalada).

## Alternativas rechazadas

- **Pasar solo el `git diff` en `review_context`** (sin worktree): más ligero, pero el reviewer no
  puede correr checks (PHPStan/`@ci`) ni navegar el código completo; diffs grandes (un scaffold CI4
  entero) saturan el contexto. Válida como punto intermedio; rechazada frente al worktree read-only,
  más capaz y natural para el modelo.
- **Solo la red de seguridad (sin desciegar)**: deja de abortar/buclear, pero el reviewer sigue
  juzgando a ciegas (prosa + test_report) → veredictos de baja calidad más rápido. Rechazada: no
  arregla la causa raíz.
- **Worktree RW para el reviewer**: regresión de ADR 0085 (el reviewer podría pisar el trabajo del
  implementador). Rechazada: read-only basta.
- **Defensive-reject en el abort de infra** (en vez de escalar): re-implementa una tarea quizá
  correcta y quema reintentos hasta `blocked` por una causa de infra. Rechazada (ya documentado en
  `execution.py:737-745`); el escalado a humano es la respuesta correcta a un fallo de infra.

## Consecuencias / notas

- El reviewer gana coste (monta worktree + lee código + posibles checks read-only) pero produce un
  veredicto **fundamentado**; antes gastaba 50 iteraciones para nada.
- PHPStan/`@ci` que necesiten escribir caché pueden chocar con el mount read-only; el reviewer puede
  apoyarse en el `test_report` (los tests ya corrieron en el run del implementador) y en la lectura
  del código. Afinar qué checks corre el reviewer queda como follow-up.
- Invariantes intactas: sandbox endurecido (cap-drop ALL, root read-only, seccomp, non-root, sin
  socket), egress (ADR 0094), reparto worker/agent (principios 2 y 3).

## Trazabilidad

Investigación multi-agente (esta sesión) + evidencia del run `019f181e` (steps_log). Implementación:
`execution.py`/`isolation.py`/`container.py` (worktree read-only + cap de escalado),
`agent_runtime/{graph.py,__main__.py,model.py}` (`is_review` + nudge/backstop conscientes-de-reviewer),
`maintenance.py` (reconciler respeta el escalado).
