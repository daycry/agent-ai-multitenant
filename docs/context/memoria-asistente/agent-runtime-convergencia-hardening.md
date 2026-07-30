---
name: agent-runtime-convergencia-hardening
description: Hardening del agent-runtime (2026-06-27) para que los runs claude_sdk completen — 8 fixes en rama plan/runs-visor-trabajo.
metadata:
  type: project
  originSessionId: cc6008fc-23fa-4218-be2b-123a3f5cd8cc
---

Sesión larga (2026-06-26/27) arreglando por qué los runs de agente con **claude_sdk**
(Claude Code CLI, sonnet/opus) no completaban. Todo en la rama **`plan/runs-visor-trabajo`**
(apilada sobre `feat/provider-llm-selection`; aún sin PR). 8 fixes, depurados de forma
iterativa observando cada run real en el stack dev "manuals":

1. `498ade1` — HOME del CLI fuera del worktree (`/home/agent` tmpfs propio; antes HOME=/workspace
   → el CLI escribía `.claude.json` 25KB en el worktree y el agente se lo leía) + **timeout de
   contenedor por-provider** (claude_sdk más largo).
2. `1765f02` — prompt de decisión **dirigido por la tarea** (no "escribe siempre"; el rol entra por
   `skill_prompt_fragments`→system_preamble) + **acceptance_criteria de la tarea al prompt** +
   `list_files` oculta `.claude`/`.claude.json` + limpiar `_SDK_NATIVE_TOOLS` (quitar MultiEdit/LS/SlashCommand).
3. `24978ec` — **coerción de scopes en memory_recall** (el enum del schema es _advisory_: sonnet manda
   `["project","error"]`→422; el adaptador mapea project→project_shared, descarta inválidos).
4. `f252097` — el **wall-clock interno del loop** (`Budgets.max_wall_clock_s` default 600s) se alinea
   con el budget del contenedor (si no, abortaba a 600s anulando el timeout del contenedor).
5. `f3284eb` — budget claude_sdk → **7200s** (2h).
6. `f229975` — **nudge anti-sobre-investigación** en el nodo `reflect`: tras 5 research-calls seguidas
   (`_RESEARCH_STREAK_LIMIT=5`) o una repetición (2ª idéntica) inyecta guía en el contexto. El
   loop-detector ya es acumulativo pero solo ABORTA a la 4ª idéntica; el nudge avisa antes y sin matar.
7. `22a456d` — **max_iterations por-provider** (claude_sdk=50; default runtime 25 cortaba multi-fichero
   justo al acabar de escribir, sin llegar a FINISH).
8. `b73f4e9` — el nudge **empuja a FINISH tras producir** (`has_produced`): si ya escribió y reincide en
   verificar (re-list/re-read), cambia de "escribe" a "ya está, FINALIZA: resumen y SIN tool-call".
   Causa: el run escribía las 15 entregables pero se quedaba verificando hasta `repetitive_loop_detected`.

**Progresión observada** (misma tarea "Implementar migraciones y seeders", CI4 multi-tenant): run1 25 research/0 writes → run2 11 writes pero cortado a 25 iter → run3 15 writes pero loop-detector por sobre-verificar → **run4 (019f0644) `done` VERIFICADO**: research→write→FINISH, 18 tool-calls, FINISH-nudge disparó 2×, la tarea pasó a `done` automáticamente y cerró con resumen de entregables. Bloque cerrado. (Las tareas autocontenidas tipo "escribir un spec .md" ya cerraban en `done` desde antes.)

**Config relevante (operator-tunable, prefijo WORKERS\_):** `container_run_timeout_claude_sdk_s=7200`,
`agent_max_iterations_claude_sdk=50`; ambos inyectados en `spec["budgets"]` por `_agent_spec`
(setdefault → un valor del operador en request.budgets gana). El **`reasoning_effort=high`** del
agente (config por-agente, ADR 0070, en la UI) es la causa de las model_calls lentas (~1-2 min);
bajarlo a **medium** acelera y hace al modelo más decisivo. Para opus 4.8 xhigh el límite que muerde
es el wall-clock, no las iteraciones. El `kind` es el mismo (claude_sdk) para opus y sonnet → si se
quiere distinto tope por modelo haría falta un knob por-MODELO.

**Gotchas de deploy:** rebuild agent-runtime SIEMPRE con `--build-arg WITH_CLAUDE=1` (si no, ImportError).
Cambios en `apps/workers` → rebuild `workers:ci` (`--build-arg BASE_IMAGE=agentic-platform/api-server:manuals`)

- recrear el contenedor workers. Cambios solo en agent-runtime → basta rebuild de la imagen (se usa
  fresca por run; NO recrear workers). **Recrear el worker MIENTRAS un run corre lo deja huérfano**
  (contenedor agent-runtime sin supervisor, ejecución `running` fantasma que bloquea el re-dispatch);
  la reconciliación (marcar la ejecución failed + tarea a ready) destraba. `sweep_stale_executions`
  existe pero su umbral es 7h. Builds docker con --build-arg desde PowerShell (bug MSYS). Relacionado:
  [[memoria-tool-calling-fix]], [[adr-0082-provider-id-unificacion]], [[estado-trabajo-en-curso]].
