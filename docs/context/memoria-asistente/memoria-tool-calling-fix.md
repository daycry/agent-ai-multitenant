---
name: memoria-tool-calling-fix
description: Auditoría de memoria (asistente+agentes) + fix provider-agnóstico de tool-calling; qué queda.
metadata:
  node_type: memory
  type: project
  originSessionId: cc6008fc-23fa-4218-be2b-123a3f5cd8cc
---

Auditoría (workflow, 2026-06-20) de los dos subsistemas de memoria. Raíz común:
**`claude_sdk.complete()` ignoraba `tools` y devolvía `tool_calls=None`**, así que
el tool-calling dependía del provider (ollama/azure/copilot sí, claude_sdk no) —
lo contrario de lo que el operador exige: **debe funcionar IGUAL sea cual sea el
provider, y un provider nuevo (p.ej. OpenAI) debe funcionar solo**.

**HECHO (commit en `feat/builtin-customization`):** `ClaudeAgentProvider.complete()`
ahora honra `tools` y emite `tool_calls` (host-executed tool-calling: advierte las
tools como MCP in-process + intercepta con `can_use_tool` deny+interrupt + cosecha
los `ToolUseBlock`). Arregla el bug reportado del **asistente** ("no recuerda mi
nombre"): ya puede invocar `remember_about_me`. Unit-tested con el `query_fn` fake;
**pendiente smoke test contra el SDK real** (rebuild api-server:manuals WITH_CLAUDE

- probar en UI "dime mi nombre").

**#2 (agentes) — HECHO + VERIFICADO EN VIVO (2026-06-26, PR #65 a master).** El
agent-runtime SÍ pasa `tools` al modelo (dispatch arma `build_model_tool_schemas`
→ `spec["model"]["tools"]`; `ClaudeSDKModelClient.decide` los pasa). El bug REAL
que quedaba: el Claude Agent SDK exponía AL MISMO TIEMPO su toolset NATIVO
(Bash/Read/Write/Edit/ToolSearch/Task/AskUserQuestion/Workflow) y el modelo lo
prefería; esas llamadas se cosechan con el nombre nativo, que el `ToolRegistry`
del host (nombres canónicos: shell_exec/read_file/…) rechaza ("not allowed in
this mode") → bucle → timeout (medido: 109 errores / 2 ok en un run). Fix:
`claude_agent._build_tool_options` fija `ClaudeAgentOptions.disallowed_tools` con
el set nativo (constante `_SDK_NATIVE_TOOLS`), restando lo que el caller permita
vía `allowed_tools` (WebSearch/WebFetch del córtex, ADR 0076). Verificado: el
agente llama `read_file`/`list_files`/`rag_search`/`memory_recall` → **ok**.
GOTCHA del deploy: el agent-runtime `[claude]` es opcional — rebuild con
`docker build -f docker/agent-runtimes/agent-runtime/Dockerfile --build-arg WITH_CLAUDE=1 -t agent-runtime:v1 .`
(omitirlo → `ImportError: claude-agent-sdk is not installed`). Y el Claude Code
CLI (Node) de esa imagen NO conoce aún `claude-opus-4-8` → lo mapea a un snapshot
viejo sin acceso (`claude-opus-4-6-20260205`, ProviderError); usar **sonnet-4-6**
para agentes (o actualizar el CLI) hasta que el catálogo del CLI traiga opus-4-8.

**PENDIENTE:**

- **#3 (barato, ~1 línea):** `trigger_memorize` filtra `if status=="done"`
  (`workers/memorizer.py:703`) ANTES del setting operator-configurable
  `memory.memorizable_statuses` → aprender de errores (aborted/failed) es
  inalcanzable. Cambiar el gate para leer `get_memorizable_statuses()`.
- El nodo `recall` del grafo del agente es placeholder (`_no_recall→[]`), decisión
  deliberada del Plan 04.5/ADR 0024 (vía reactiva). Confirmar/cerrar por ADR.

Informe completo: task output del workflow `audit-memory-subsystems` (run
wf_d19c4cac-ac9). Relacionado: [[provider-resolution-two-paths]],
[[cola-tarea-asistente-voz]].
