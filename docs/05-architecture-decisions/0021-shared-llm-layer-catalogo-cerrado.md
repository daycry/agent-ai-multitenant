---
adr: "0021"
title: Capa shared-llm con catálogo cerrado de proveedores y retirada de LiteLLM
status: accepted
date: 2026-05-24
deciders: System Admin
phase: 03-chat-planning-aprobacion
supersedes:
  # ADR 0009 — supersede de la sub-decisión "LiteLLM como gateway
  # primario". El resto (LangGraph como motor) sigue vigente.
  - "0009"
  # ADR 0017 — supersede parcial: el cuadro de Fase G ya no enumera
  # LiteLLM como proveedor; el resto del cableado e2e sigue vigente.
  - "0017"
  # ADR 0018 — supersede parcial de las menciones a LiteLLM en el
  # contexto. El "Claude SDK como ModelClient de un turno" sigue
  # vigente.
  - "0018"
  # ADR 0019 — supersede parcial de la allowlist concreta de hosts
  # (LiteLLM fuera, APIM y Ollama dentro). La estructura del egress
  # (sandbox internal + tinyproxy FilterDefaultDeny) sigue vigente.
  - "0019"
---

# ADR 0021 — Capa `shared-llm` con catálogo cerrado de proveedores y retirada de LiteLLM

> **Estado: `accepted`.** El System Admin pidió simplificar el catálogo
> de proveedores LLM ("litellm tiene muchos proveedores y no me
> interesan todos") y aceptó la **Opción A** propuesta: adoptar una
> capa propia `shared-llm` con tres proveedores cerrados (Claude Agent
> SDK, GitHub Copilot, Azure AI Foundry vía APIM) y retirar LiteLLM.

## Contexto

ADR 0009 escogió **LiteLLM** como **gateway primario** del sistema con
el argumento de que abstrae **+100 backends** detrás de un endpoint
OpenAI-compatible. La realidad del proyecto es otra:

1. El operador sólo va a configurar **uno** de estos tres caminos:
   - Suscripción Anthropic Claude Pro/Max → **Claude Agent SDK**.
   - Cuenta GitHub Copilot Pro/Business/Enterprise → **GitHub Copilot**
     vía OAuth Device Flow.
   - Despliegue corporativo con **Azure AI Foundry** detrás de Azure API
     Management — el endpoint OpenAI-compatible que ya tiene una
     organización con governance/billing centralizado.
2. LiteLLM añade una capa de transformación adicional, un servicio
   extra en el docker-compose, una superficie de testing
   (configuración por modelo, fallbacks, cachés), y soporte para
   backends (Bedrock, Replicate, Together, Cohere, …) que el producto
   no usa ni piensa usar.
3. La forma actual del `ModelClient` del agent-runtime es **sync** y
   está acoplada al loop LangGraph (`decide` / `review`). El
   `Summariser` de la compresión jerárquica del chat (Plan 03
   `task_03_04`) y el sub-grafo de planning (`task_03_09`) viven en el
   api-server, son **async**, y necesitan una forma genérica
   `complete` / `stream`. No tenemos una capa común y se nota.
4. El Device Flow de Copilot quedó documentado como deuda explícita al
   cierre del Plan 02 ("no tiene aún UI en el admin-panel"). El
   `CopilotAuth` actual sólo _consume_ un OAuth token ya obtenido por
   fuera del sistema — falta el bootstrap interactivo.

## Decisión

### Una capa: `packages/shared-llm`

Forma async genérica que cualquier consumer del repo (api-server,
agent-runtime, futuros workers) puede usar sin saber de qué proveedor
está hablando:

```
packages/shared-llm/
├── pyproject.toml
└── src/shared_llm/
    ├── __init__.py
    ├── types.py           # Message, Usage, CompletionResponse,
    │                      # StreamChunk, ToolCall, AgentRunEvent
    ├── base.py            # LLMProvider Protocol async
    ├── exceptions.py      # LLMError / AuthError / RateLimitError /
    │                      # ProviderError
    └── providers/
        ├── __init__.py
        ├── claude_agent.py    # Wrapper sobre claude-agent-sdk
        ├── copilot.py         # OAuth Device Flow + chat + JWT mint
        ├── azure_foundry.py   # Azure AI Foundry vía APIM
        └── ollama.py          # Ollama local + cloud (mismo wrapper)
```

Forma del `Protocol`:

```python
class LLMProvider(Protocol):
    name: str

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        tools: list[ToolSpec] | None = None,
        **kwargs,
    ) -> CompletionResponse: ...

    async def stream(...) -> AsyncIterator[StreamChunk]: ...

    async def aclose(self) -> None: ...
```

### Catálogo cerrado de cuatro proveedores

| Proveedor                  | Autenticación                                   | Endpoint                                                |
| -------------------------- | ----------------------------------------------- | ------------------------------------------------------- |
| `ClaudeAgentProvider`      | `ANTHROPIC_API_KEY` o suscripción Pro/Max (SDK) | claude-agent-sdk `query()`                              |
| `CopilotProvider`          | OAuth Device Flow → JWT (TTL ~30 min)           | `api.githubcopilot.com/chat/completions`                |
| `AzureFoundryAPIMProvider` | `Ocp-Apim-Subscription-Key` o `Bearer`          | `<apim-base>/openai/deployments/<dep>/chat/completions` |
| `OllamaProvider`           | local: sin auth · cloud: `Bearer <api_key>`     | `<host>/v1/chat/completions`                            |

`OllamaProvider` cubre dos despliegues distintos con la misma clase:

```python
local  = OllamaProvider.local()                                  # http://localhost:11434
cloud  = OllamaProvider.cloud(api_key=os.environ["OLLAMA_API_KEY"])  # https://ollama.com
remote = OllamaProvider.local(host="http://gpu-server.local:11434")  # tu propio servidor
```

Cualquier proveedor adicional pide un ADR explícito. **No** se reabre
la puerta a un gateway multi-backend tipo LiteLLM.

### Sobre futuros proveedores OpenAI-compatibles

Tres de los cuatro providers (`AzureFoundryAPIMProvider`,
`OllamaProvider`, `CopilotProvider`) hablan el mismo dialecto
`/chat/completions` OpenAI-compatible — sólo cambia el `base_url`, la
auth y, en el caso de Copilot, los `Editor-*` headers. Si en el
futuro aparecen otros (Groq, Together, DeepInfra, Fireworks,
OpenRouter…) el patrón es **añadir una clase delgada** por proveedor.

**No extraemos un `OpenAICompatibleProvider` base todavía**. La regla
del proyecto: refactorizar a la base común sólo cuando haya al menos
**3 providers OpenAI-compat sin lógica custom** (Copilot no cuenta,
tiene JWT mint y headers de editor). Hoy son dos (Azure + Ollama) →
clase propia cada uno. Si un día hay un tercer Groq/Together/etc.
sin lógica custom, **se refactoriza en ese momento** y se documenta
en un ADR de refactor. Antes es over-engineering.

Los proveedores que **no** son OpenAI-compatibles (AWS Bedrock con
SigV4, Vertex AI con OAuth Google, endpoint nativo de Ollama
`/api/chat` con `keep_alive`/`num_ctx`/…) necesitan su propia clase
por definición y no caen bajo esa regla de extracción.

### Cuatro ajustes sobre el ejemplo original

El ejemplo en `docs/provider-example/` es la base; se incorpora con
estos cuatro cambios:

1. **`Usage.cost_usd: float = 0.0`** además de tokens. El `executions`
   captura del Plan 02 y el coste IA del Plan 03 lo necesitan en USD.
   Cada proveedor lo rellena como puede: Claude SDK lo trae en
   `total_cost_usd`; Copilot calcula con catálogo local; APIM lo
   expone si el operation policy lo configura.
2. **`CompletionResponse.tool_calls: list[ToolCall] | None`**. Sin
   esto `complete()` no puede devolver tool use y los providers que
   sí pueden (Copilot / APIM) quedarían tullidos.
3. **`run_agent()` de Claude envuelve los mensajes del SDK** en un
   tipo neutro `AgentRunEvent` (`kind`, `text`, `tool_use`, `usage`,
   `cost_usd`) para que el resto del sistema no dependa del shape
   exacto del paquete `claude-agent-sdk`.
4. **JWT de Copilot se re-minta con margen** (60 s antes de expirar)
   en lugar de esperar al 401. Mismo patrón que el `CopilotAuth`
   actual del agent-runtime, portado a la nueva capa.

### El `ModelClient` del agent loop pasa a ser adaptador

`agent_runtime/providers.py` ya no implementa los proveedores. Cada
`ModelClient` envuelve a un `LLMProvider` y traduce:

- `decide(state)` → `provider.complete(messages, tools=...)` → parsea
  `tool_calls` para una `ACT` o `content` para una `FINISH`.
- `review(state)` → `provider.complete(messages)` → parsea el JSON
  `{passed, feedback}` o cae al sniffing actual.

Se ejecuta `asyncio.run(...)` internamente — el loop de LangGraph del
agent-runtime sigue siendo sync. Cuatro adaptadores:

```python
ClaudeSDKModelClient    -> shared_llm.providers.ClaudeAgentProvider
CopilotModelClient      -> shared_llm.providers.CopilotProvider
AzureFoundryModelClient -> shared_llm.providers.AzureFoundryAPIMProvider
OllamaModelClient       -> shared_llm.providers.OllamaProvider
```

`LiteLLMModelClient` se elimina del código y del test
`test_model_clients.py`.

### Egress + infra

- `docker/egress-proxy/filter.txt` retira los patrones `litellm`,
  añade un patrón APIM por convención
  (`^[a-z0-9-]+\.azure-api\.net$`) y añade `^ollama\.com$` para el
  endpoint cloud de Ollama. Si el operador usa otro dominio para su
  APIM (custom domain) o un servidor Ollama propio fuera del compose
  (`gpu-server.local`, etc.), los añade aquí.

  El Ollama **local** que corre en el host (`localhost:11434`) o como
  contenedor adyacente NO sale por el proxy: el sandbox lo alcanza
  por la red interna del compose (FYI: hay que añadirlo al network
  `agentic-agents` si se quiere usar desde dentro del sandbox).

- No hay servicio `litellm` en el docker-compose (no estaba
  desplegado), así que no hay nada que retirar de la infra del
  compose.
- El `workers/config.py` gana variables de configuración para APIM
  (`WORKERS_APIM_BASE_URL`, `WORKERS_APIM_DEPLOYMENT`,
  `WORKERS_APIM_SUBSCRIPTION_KEY`) y Ollama
  (`WORKERS_OLLAMA_BASE_URL`, `WORKERS_OLLAMA_API_KEY`) y pierde las
  de LiteLLM.

### Device Flow en la admin-panel

Queda como **task adicional de cierre** del Plan 03 (`task_03_32`,
fuera de las 31 originales): pantalla "Conectar GitHub Copilot" que
llama a `CopilotProvider.start_device_flow()` desde un nuevo endpoint
`/admin/llm/copilot/device-flow/start` + polling.

## Alternativas consideradas

### Opción A — Adoptar `shared-llm` y retirar LiteLLM ✅

La que se aplica. Trade-offs: una migración de medio día / día
completo (los tests del provider, la documentación, la limpieza del
filter de egress). Beneficios: tres caminos en vez de un gateway
abstracto, una capa propia que controlas, sin servicio LiteLLM que
mantener.

### Opción B — Mantener LiteLLM como cuarto provider transitorio

Aceptar `shared-llm` pero conservar `LiteLLMModelClient` como cuarto
en `agent_runtime/providers.py`, marcado como `deprecated`, hasta que
se confirme que no hay despliegues productivos que dependan de él.
Descartada: el proyecto no tiene aún despliegues productivos y el
coste de mantener cuatro caminos durante una transición vaga supera
al beneficio. Si en el futuro aparece un nuevo proveedor que LiteLLM
cubre y los tres no, abrimos un ADR para añadirlo.

### Opción C — Status quo (sólo LiteLLM)

Descartada por las razones del Contexto: LiteLLM es un Swiss Army
knife para un caso de uso donde el usuario sólo necesita un
destornillador, dos llaves Allen y un martillo.

## Consecuencias

### Positivas

- **Catálogo cerrado** y trazable a tres ADRs (este, 0018 Claude SDK,
  y un futuro ADR Copilot Device Flow + APIM).
- **Una capa async genérica** que el chat del Plan 03 y el summariser
  del Plan 04 ya pueden consumir sin reinventar la rueda.
- **Menos superficie**: se borra un servicio externo del modelo
  mental (LiteLLM), se elimina código no usado y la documentación
  vuelve a coincidir con la realidad.

### Negativas

- **Romper compatibilidad** con cualquier despliegue (no hay aún)
  que apunte a LiteLLM. Mitigación: el `kind: "litellm"` del
  `model_from_spec` deja de existir; el factory rechaza ese valor con
  un `ValueError` claro.
- **Dependencia de Azure APIM** para el camino "gateway empresarial".
  Mitigación: APIM es estándar de facto en organizaciones con Azure;
  para operadores que prefieran apuntar directamente a Azure OpenAI
  sin APIM, el provider acepta `bearer_token` y un `apim_base_url`
  que de hecho puede ser el endpoint Azure OpenAI nativo (mismo
  contrato OpenAI-compatible).

### Neutras / migración

- ADR 0009 queda **superseded** por este ADR en el punto de
  "LiteLLM como gateway primario". El resto de ADR 0009 (LangGraph,
  motor de agentes, chat planning) sigue vigente.
- ADR 0017 queda **parcialmente superseded** en la sub-decisión
  `LiteLLMModelClient` → ahora `AzureFoundryModelClient`. El resto
  (cableado end-to-end, Fase G) sigue vigente.
- Las trampas / gotchas y el changelog del Plan 02 mantienen su
  redacción histórica pero se les añade una nota cabecera
  apuntando a este ADR.

## Implementación

Sale de este ADR como un commit grande que toca:

1. `packages/shared-llm/` nuevo, con tests unit propios.
2. `docker/agent-runtimes/agent-runtime/agent_runtime/providers.py`
   reescrito como adaptadores; `LiteLLMModelClient` eliminado;
   `AzureFoundryModelClient` añadido.
3. `docker/agent-runtimes/agent-runtime/pyproject.toml` añade
   `shared-llm` como dependencia.
4. `apps/workers/src/workers/config.py` añade variables APIM.
5. `docker/egress-proxy/filter.txt` retira `litellm`, añade APIM.
6. `tests/integration/test_model_clients.py` migra los tres tests de
   LiteLLM al provider Azure Foundry equivalente.
7. `scripts/demo_human_02_01.py` renombra `kind=litellm` →
   `kind=azure_foundry`.
8. Notas en `docs/07-changelog/02-ejecucion-agentes.md`,
   `docs/05-architecture-decisions/0009-*.md` y
   `docs/05-architecture-decisions/0017-*.md` apuntando a este ADR.
