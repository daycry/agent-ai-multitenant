# Claude Agent SDK — ejemplo de integración (referencia para task_02_32)

> **Qué es esto.** Un ejemplo de integración con el Claude Agent SDK que el
> operador (System Admin) aportó como referencia para implementar
> `task_02_32` de la Fase G (ADR 0017). Procede de **otro proyecto**
> ("Agent AI") — sus rutas (`app/core/providers/...`), números de gotcha
> (#9, #13, #26…) y números de run NO son de este repo. Es una **idea**, no
> una receta a copiar literal.

## Claves a retener para nuestra implementación

- Usar el SDK oficial **`claude-agent-sdk`** (≥ 0.2.82). Internamente arranca
  el CLI de Claude Code como subproceso.
- **Auth sin API key**: OAuth con la suscripción Claude Max/Pro; credenciales
  en `~/.claude/.credentials.json`. En Windows hace falta Git Bash y
  `CLAUDE_CODE_GIT_BASH_PATH`.
- **Tools nativas del SDK**: registrar nuestras platform tools con
  `create_sdk_mcp_server` + el decorador `@tool` (in-process), NO un shim
  por subproceso. Llegan al LLM como `mcp__<server>__<tool>`.
- **Las 3 banderas de `ClaudeAgentOptions`**: `tools=[]` (desactiva las tools
  built-in del CLI — sin esto el filtro por agente no limita nada),
  `strict_mcp_config=True` (ignora el `~/.claude.json` del operador),
  `setting_sources=[]` (ignora settings.json + plugins). Más
  `permission_mode="bypassPermissions"`, `cwd`, `max_turns`.
- `query(prompt, options)` es un async iterator: `AssistantMessage`
  (TextBlock / ToolUseBlock), `UserMessage` (¡aquí llega el `ToolResultBlock`
  con `is_error`!), `ResultMessage` (usage + coste). El caching del system
  prompt es automático.
- Modelos con guion: `claude-sonnet-4-6`, `claude-opus-4-7`,
  `claude-haiku-4-5`.

## Tensión arquitectónica que la Fase G debe resolver

⚠️ El Claude Agent SDK **es en sí mismo un runtime agéntico** — corre su
propio loop, su propio `max_turns`, sus propias tool calls. Pero el Plan 02
construyó nuestro **agent loop LangGraph** (`agent_runtime`, ADR 0013) con su
protocolo `ModelClient` (`decide()` → una decisión por llamada).

"El SDK corre el loop entero" no encaja sin más en "un `ModelClient` devuelve
una decisión". Antes de implementar `task_02_32` hay que decidir cómo
componen — opciones a sopesar (probablemente merezca su propio ADR):

1. El provider Claude SDK **reemplaza** nuestro LangGraph loop cuando
   `provider=claude` (el SDK ES el loop; nuestro grafo se usa solo con los
   otros providers / el modelo scriptado).
2. Envolver el SDK detrás de `ModelClient` forzándolo a un solo turno por
   `decide()` (desaprovecha el loop nativo del SDK).
3. Un `ModelClient` que internamente delega el loop al SDK y traduce sus
   mensajes a nuestros `steps`.

No es decisión trivial — la Fase G la afronta de frente, no a mitad de
implementación.

---

## Documento original aportado por el operador

# Integración con Claude SDK

> Agent AI ejecuta agentes con `provider=claude` a través del **Claude Agent SDK**
> oficial (`claude-agent-sdk`), que internamente arranca el CLI de Claude Code
> como subproceso. Funciona con tu suscripción Claude Max/Pro sin necesidad de
> API keys — credenciales OAuth en `~/.claude/.credentials.json`.
>
> **Tools nativas del SDK, no shim custom**: las platform tools del repo
> (`write_file`, `read_file`, `git_*`, etc.) se registran como handlers
> in-process via `create_sdk_mcp_server` + `@tool` decorator del propio
> `claude-agent-sdk` (≥0.2.82). El "shim subprocess" historico fue eliminado
> en 2026-05-19 (gotcha #26). El built-in toolkit del CLI (Bash/Skill/Read
> nativas) queda **desactivado** mediante `tools=[]` para que el filtro
> `Agent.tools_config` realmente limite al agente — sin esto, un agente
> con `tools_config=["read_file"]` podría ejecutar Bash igualmente (gotcha #9).

## Requisitos

1. **Claude Code CLI** instalado (`npm install -g @anthropic-ai/claude-code`).
2. **Suscripción activa** a Claude Max o Claude Pro.
3. **Sesión iniciada** en el CLI (`claude auth login`).
4. **Windows**: Git Bash instalado — el SDK necesita un shell POSIX para arrancar el subproceso CLI.

## Instalación

### 1. Claude Code CLI

```bash
npm install -g @anthropic-ai/claude-code
claude --version   # verificar
```

### 2. SDK Python

```bash
pip install claude-agent-sdk
```

Versión mínima: `0.2.82+` — incluye `create_sdk_mcp_server` + `@tool` decorator que usamos para registrar las platform tools sin shim custom.

### 3. Variables de entorno

```bash
# Windows: obligatorio
CLAUDE_CODE_GIT_BASH_PATH=C:\Program Files\Git\bin\bash.exe

# Opcional — el SDK lo localiza automáticamente:
# CLAUDE_CODE_CLI_PATH=C:\Users\<you>\AppData\Roaming\npm\claude.cmd
```

## Autenticación

```bash
claude auth login
```

Abre el navegador, OAuth con tu cuenta Anthropic. Las credenciales quedan en `~/.claude/.credentials.json` y se renuevan solas.

## Cómo funciona internamente

`ClaudeSDKProvider.stream()` hace, por cada turno del agente:

```
1. Construir el system prompt con: Identity, Anti-loop directive, Global
   instructions, Project context, Recent plans, Recent deliverables,
   Prior conversations, RAG chunks, Subtask results, Prior attempts, etc.

2. Construir `ClaudeAgentOptions`:
   - system_prompt (inline o SystemPromptFile si > 8000 chars)
   - model (e.g. "claude-opus-4-7")
   - tools=[]                    ← built-in tools del CLI desactivados
   - mcp_servers={"platform": <SdkMcpServer>}  ← SDK-native server (create_sdk_mcp_server)
   - allowed_tools=["mcp__platform__read_file", ...]   ← auto-aprobación
   - max_turns=N                 ← scaling per task.complexity
   - strict_mcp_config=True       ← ignora ~/.claude.json del operador
   - setting_sources=[]           ← ignora settings.json + plugins/cache
   - permission_mode="bypassPermissions"
   - cwd=project.local_path
   - env=_build_sdk_env()

3. `async for message in query(prompt, options)`:
   - AssistantMessage → TextBlock (yield al runner) o ToolUseBlock (log + register)
   - UserMessage → ToolResultBlock (emite tool_result event, pair-by tool_use_id)
   - ResultMessage → final usage + cost
```

## Tools nativas del SDK (`create_sdk_mcp_server` + `@tool`)

El paquete `claude-agent-sdk` ≥ 0.2.82 expone dos primitivos clave — NO hay shim custom:

- **`create_sdk_mcp_server`**: crea un MCP server in-process.
- **`@tool` decorator**: registra una función async como tool de ese server.

```python
from claude_agent_sdk import create_sdk_mcp_server, tool

def build_platform_mcp_server(*, agent_id, project_id, task_id, run_id,
                              project_path, allowed_tools):
    tool_definitions = []
    for tool_name, schema in PLATFORM_TOOL_CATALOG.items():
        if tool_name not in allowed_tools:
            continue
        # _name=tool_name default-arg captura el nombre en closure
        # (evita el late-binding bug clásico de loops + closures).
        @tool(tool_name, schema["description"], schema["input_schema"])
        async def _handler(args, _name=tool_name):
            return await _dispatch_tool_call(
                tool_name=_name, args=args,
                agent_id=agent_id, project_id=project_id,
                task_id=task_id, run_id=run_id, project_path=project_path,
            )
        tool_definitions.append(_handler)
    if not tool_definitions:
        return None  # agente sin platform tools → no registramos server
    return create_sdk_mcp_server(name="platform", tools=tool_definitions)
```

`_dispatch_tool_call` es el mismo dispatcher que usan Copilot y LiteLLM — behavior, audit, sandbox, approval y execution gate idénticos cross-provider.

### MCP servers externos

Cuando hay MCP servers de proyecto (`{name: {command, args, env} | {url, transport}}`), se añaden al mismo dict `mcp_servers` de `ClaudeAgentOptions` con tipo `stdio` o `sse`. Las tools llegan con prefijo `mcp__<servername>__<toolname>`.

## Las 3 banderas críticas de `ClaudeAgentOptions`

Las tres juntas son **el contrato** entre la plataforma y el SDK.

### `tools=[]`

- `tools=None` (default del SDK) → TODO el toolkit nativo (Bash, Skill, …). PELIGRO.
- `tools=[]` → ningún built-in. El LLM solo ve las tools del SDK MCP server.
- `tools=["Read", "Bash"]` → solo esos built-in.

### `strict_mcp_config=True`

Sin esta flag, el CLI carga los MCP servers del `~/.claude.json` del operador — contamina el catálogo de tools, añade 10-30s de startup y hace el healthcheck flaky. Con la flag, solo cargan los servers que pasamos explícitamente.

### `setting_sources=[]`

Sin esta flag, el CLI lee `~/.claude/settings.json` y los plugins de `~/.claude/plugins/cache/` — inyecta ruido (p.ej. preambles de plugins) en cada respuesta. Con `setting_sources=[]`, el agente arranca limpio.

## Modelos disponibles

| Modelo            | ID                  | Notas                                       |
| ----------------- | ------------------- | ------------------------------------------- |
| Claude Sonnet 4.6 | `claude-sonnet-4-6` | Default balanced.                           |
| Claude Opus 4.7   | `claude-opus-4-7`   | Top model — más capaz, más caro, más lento. |
| Claude Haiku 4.5  | `claude-haiku-4-5`  | Ultra-fast para tasks triviales.            |

## Cache, max_turns, observabilidad

- **Prompt caching automático**: el SDK cachea el system prompt entre turnos. Métricas en `input_tokens` / `cache_read_input_tokens` / `cache_creation_input_tokens`.
- **`max_turns`** escala con la complejidad de la tarea; hit max_turns es **terminal** (no retry).
- **Observability**: `ToolUseBlock` viene en `AssistantMessage.content`; el `ToolResultBlock` correspondiente (con `is_error` y `result`) llega en el **siguiente** `UserMessage`. Hay que mantener un dict `tool_use_by_id` para emparejar.

## Docker setup

Node.js 20 + Claude CLI + `claude-agent-sdk` en la imagen. Credenciales del operador montadas read-only:

```yaml
services:
  agent-ai:
    volumes:
      - ~/.claude:/root/.claude:ro
    environment:
      CLAUDE_CODE_GIT_BASH_PATH: "" # Linux usa bash nativo
```

## Troubleshooting (resumen)

- **`CLIConnectionError: Failed to start Claude Code`** → `claude --version`, `CLAUDE_CODE_GIT_BASH_PATH`, `claude auth status`.
- **Healthcheck SDK lento (10-30s)** → `~/.claude.json` con MCP servers globales lentos; las 3 banderas lo arreglan.
- **Agente con `tools_config` igual ejecuta Bash** → el provider debe pasar `tools=[]`, no `None`.
- **Costes altos** → verificar complejidad de la tarea + ratio `cache_read` ≥ 80%.

## Gotchas relevantes (del proyecto de origen)

- **#9** — Restringir tools vía el campo `tools` (no `allowed_tools`), `SystemPromptFile` para prompts >8K, `strict_mcp_config=True`, `setting_sources=[]`.
- **#13** — `MaxTurnsError` terminal, no retry.
- **#14** — `ToolResultBlock` viene en `UserMessage`, no en `AssistantMessage`.
- **#16** — `max_turns` scaling per complexity.
- **#26** — Platform tools vía SDK-native `create_sdk_mcp_server` + `@tool` (no subprocess shim).

---

_Documento aportado por el operador el 2026-05-22 como referencia para la Fase G del Plan 02._
