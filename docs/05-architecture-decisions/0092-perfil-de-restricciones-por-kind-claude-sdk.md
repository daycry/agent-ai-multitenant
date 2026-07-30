---
adr: "0092"
title: Perfil de restricciones por-kind — el Claude Agent SDK, agéntico nativo, corre con menos límites de loop
status: accepted
date: 2026-06-29
deciders: operador, System Architect (claude-opus)
phase: remediacion-ciclo-vida-ejecucion
related: ["0012", "0019", "0021", "0040", "0045", "0090"]
docs_language: es
---

# ADR 0092 — Perfil de restricciones por-kind para `claude_sdk`

## Contexto

Los cuatro providers del catálogo (ADR 0021) NO son equivalentes: **Copilot/Azure/Ollama** son
_completions_ estilo HTTP (NUESTRO grafo LangGraph maneja la agencia turno-a-turno), mientras que el
**Claude Agent SDK** (`kind=claude_sdk`) es **agéntico nativo** (corre su propio sub-loop). Hoy lo
metemos en nuestro loop con restricciones pensadas para los providers finos, y eso lo amuralla: en
un run real (opus/claude_sdk sobre una tarea JWT) el agente chocó con `command not allowed: git` y
`command not allowed: rm` (el `allowed_commands` del proyecto estaba vacío — ADR 0045 lo deja
deny-by-default), no pudo reconciliar el worktree y derivó en read-churn hasta `max_iterations`.

La seguridad NO está en esos límites de loop, sino en el **aislamiento del contenedor** (ADR 0012/
0019/0040): cap-drop ALL, rootfs read-only salvo `/workspace`+`/tmp`, red `internal` (sin egress),
sin socket docker, non-root, mem/pids limits. Esa frontera es invariante; los límites de loop son
solo convergencia.

## Decisión

### D1 — Allowlist de shell base para `claude_sdk` (incremental, Opción A)

El worker, al armar el `AGENT_TASK_SPEC` (`_agent_spec`), para `kind==claude_sdk` **UNE** un set base
de comandos VCS/fichero seguros (`git`, `rm`, `mv`, `cp`, `mkdir`, `rmdir`, `ls`, `cat`, `find`,
`grep`, `touch`, `head`, `tail`, `wc`, `diff`) con el `allowed_commands` del proyecto, y **fuerza el
registro de `shell_exec`** aunque el proyecto no pinee nada. Así el SDK puede reconcilar el worktree
con sus propias operaciones (git/rm) en vez de chocar con muros. Los providers finos mantienen el
allowlist del proyecto **verbatim** (sin cambios). Todos los comandos quedan confinados al contenedor
y al worktree por el sandbox.

### D2 — Budgets ya per-kind (sin cambio)

`claude_sdk` ya recibe un techo de iteraciones y un wall-clock más altos vía
`settings.agent_max_iterations_for_kind` / `container_timeout_for_kind`. No se tocan: el muro del run
real fue el allowlist, no el budget.

### D3 — `delete_file` ya disponible (sin cambio)

La capacidad de borrado segura (`delete_file`, ADR 0090-R6) ya está en el catálogo y concedida a los
agentes del equipo CI4 (`_FILE_TOOLS`), así que el SDK la ve como cualquier tool asignada.

## Invariantes preservadas

- **Seguridad-dura del contenedor intacta** (ADR 0012/0019/0040): este ADR NO toca cap-drop, rootfs
  read-only, egress, socket docker ni los límites de recursos. Solo amplía el allowlist de comandos
  DENTRO del sandbox.
- **Providers finos sin cambios**: el perfil base solo aplica a `kind==claude_sdk`; un run
  ollama/azure/copilot conserva exactamente el `allowed_commands` del proyecto (ADR 0045).
- **`shell_exec` sigue sin shell**: ejecuta argv vía `shlex` (sin shell-injection); el allowlist
  empareja por basename del primer token (deny-by-default fuera del set).

## Alternativas rechazadas / diferidas

- **Opción B (loop nativo del SDK vía `run_agent()`)**: dejar que el SDK corra su agencia multi-turno
  nativa y que el grafo solo orqueste arranque + self-review. Mayor upside pero cambio arquitectural
  (state machine, captura de `written_files`, error handling). **Diferida** a follow-up.
- **Cablear el `max_turns` muerto** de `ClaudeSDKModelClient` (`providers.py`): hoy el SDK corre con
  el default 8 de `complete()`; pasar `self._max_turns` (=1) lo REDUCIRÍA a 1 turno por turno del
  grafo (menos agencia). Wiring correcto pide un default alto pensado — **diferido**, no se toca ahora.

## Trazabilidad

Plan de 5 tracks en `~/.claude/plans`; informe SDK en el scratchpad de la sesión. Implementación:
`apps/workers/.../execution.py` (`_SDK_BASE_SHELL_COMMANDS`, `_agent_spec`). Tests:
`tests/unit/test_agent_spec_sdk_shell_allowlist.py`.
