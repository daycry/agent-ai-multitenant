---
plan_id: prod-07-fiabilidad-llm-costes
title: Capa LLM fiable y contabilidad de costes exacta
status: pending_approval
blocking_plan: null
started_at: null
completed_at: null
estimated_duration_calendar: 3-4 semanas
estimated_effort_person_days: 14
estimated_cost_human_eur: 6.300 € – 8.400 €
estimated_cost_ai_eur: 60 € – 120 €
created_by: auditoria-claude-2026-06
spec_sections_referenced: []
docs_language: es
priority: P1
---

# Plan prod-07 — Capa LLM fiable y contabilidad de costes exacta

## Cabecera

| Campo                              | Valor                                                     |
| ---------------------------------- | --------------------------------------------------------- |
| **ID del Plan**                    | `prod-07-fiabilidad-llm-costes`                           |
| **Prioridad**                      | P1                                                        |
| **Bloqueado por**                  | — (independiente; coordina con prod-06, prod-08, prod-10) |
| **Tiempo estimado (calendario)**   | 3-4 semanas                                               |
| **Tiempo estimado (persona-días)** | 14                                                        |
| **Rama git sugerida**              | `plan/prod-07-fiabilidad-llm-costes`                      |

> **Estado**: la fuente de verdad es el frontmatter YAML de este fichero (`status:`). El campo duplicado que había en esta tabla se retiró en prod-15 (hallazgo docsroadmap-6): se había desincronizado en 22 de 51 planes.

---

## Resumen

La auditoría de producción (2026-06-10) confirmó que la capa `shared-llm` está bien
diseñada estructuralmente (Protocol único, errores tipados, secretos solo en Vault,
dos vías de resolución separadas tras el PR #46) pero tiene dos clases de deuda que
impiden declararla lista para producción:

1. **Fiabilidad**: no existe ningún retry/backoff ante 429 o errores transitorios —
   un solo blip de red mata una ejecución de agente de 30 iteraciones (llm-2). El
   agent-runtime reproduce el bug "Event loop is closed" ya documentado y arreglado
   solo en el asistente (llm-3). Un status no-2xx en `stream()` lanza un
   `ResponseNotRead` opaco que los tests enmascaran con MockTransport (llm-5). Una
   caída de Vault degrada en silencio a "sin credencial" y el run muere con un 401
   que misatribuye la causa raíz (llm-9, workers-8). Hay fugas de `AsyncClient`
   sin `aclose()` (llm-8) y mutación process-global de `os.environ` (llm-7).

2. **Exactitud de costes**: tres de los cuatro proveedores reportan
   `cost_usd=0` y el snapshot paliativo de precios nunca casa con el catálogo
   porque los pasos `model_call` no registran `provider` y usan el nombre de
   modelo despojado del prefijo de familia — los budgets suman $0 (llm-1). El
   streaming OpenAI-compat pierde `usage` y `tool_calls` (llm-6). Un agente
   `claude_sdk` nunca puede usar herramientas y "termina" en silencio (llm-4).
   El Memorizer resuelve su LLM solo por env, fuera del catálogo, y con
   ollama-local desactivado la memoria muere en silencio (llm-10).

Este plan cierra los 14 hallazgos (llm-1…llm-13 + workers-8) en cuatro fases:
fiabilidad de la capa (A), resolución de credenciales y capacidades honestas (B),
contabilidad de costes exacta (C) y Memorizer + documentación (D).

## Alcance

**Entra**:

- Retry con backoff exponencial + jitter en los consumidores de los 4 providers
  (agent-runtime y asistente), respetando `Retry-After`.
- Event loop único por ejecución en el agent-runtime + `aclose()` de providers.
- Errores de streaming no-2xx legibles; `usage` y `tool_calls` en streaming
  OpenAI-compat (`stream_options.include_usage`).
- Fallo de Vault → `model_unresolved`/`vault_unavailable` explícito en el dispatch
  (fail-fast, no 401 opaco dentro del sandbox).
- Capacidades `claude_sdk` honestas: bloqueo/aviso en validación cuando un agente
  con tools selecciona `claude_sdk` + timeout del SDK + ADR propuesto para el
  cableado futuro de `run_agent()`.
- Registro de `provider` + clave de catálogo en los pasos `model_call`;
  `total_cost_usd` real desde snapshots para Ollama/Copilot/Azure; test e2e de
  coste > 0 que los budgets consumen.
- Unificación del mapeo kind→credencial triplicado en una tabla única en
  `shared-llm` (incluye el `bearer_token` de Azure que hoy falta en el worker).
- Test de conexión que detecta tokens revocados (mint de Copilot) y acepta
  Azure bearer-only.
- Credencial del proveedor fuera del env del contenedor (mount tmpfs read-only).
- Memorizer resolviendo su LLM vía el catálogo `llm_providers` con env fallback.
- No mutar `os.environ` por construcción de `ClaudeAgentProvider`; cierre de
  providers del asistente por request.

**Queda fuera** (con coordinación anotada):

- Cableado del chat SSE de producción sobre `stream()` — aquí solo se deja el
  contrato de streaming correcto; el wiring de UI es de una fase funcional.
- Operabilidad general de Vault (HA, unseal, AppRole) — es **prod-10**; aquí
  solo el comportamiento fail-fast del consumidor ante un Vault caído.
- Alertas sobre las métricas nuevas (retries, fallos de Memorizer) — este plan
  emite logs/métricas; el cableado de alertas es **prod-08**.
- Enforcement de budgets sobre el coste corregido (pause/stop por presupuesto)
  — es **prod-06**; este plan garantiza que la cifra que consumen es exacta.
- F2-F4 del ADR 0057 (resolución por `provider_id` en dispatch en vez de "fila
  activa más nueva") — pendiente del ADR; este plan no lo adelanta.
- Sincronización/refresco del catálogo de precios LiteLLM — ya existe
  (`sync_prices`); aquí solo se corrige el casado de claves.

## Decisiones clave

- **Dónde vive el retry**: helper único `with_retries()` en `shared-llm`
  (políticas por tipo de error) aplicado en los **adaptadores** (runtime y
  asistente), no dentro de cada provider — los providers siguen lanzando errores
  tipados y los consumidores deciden la política. 2-3 intentos, solo para
  `RateLimitError` y `ProviderError` transitorios (timeout/5xx); `AuthError` y
  4xx≠429 fallan rápido.
- **Event loop del runtime**: `asyncio.Runner` persistente por ejecución en
  `_ProviderModelClient` (opción recomendada) frente a crear el `AsyncClient`
  dentro de cada llamada (más simple pero pierde keep-alive). Si el Runner
  complica el grafo síncrono de LangGraph, la alternativa es aceptable.
- **Coste desde snapshots**: columna nueva `cost_estimated_usd` que los budgets
  prefieren cuando `total_cost_usd=0` (opción recomendada: conserva la
  trazabilidad runtime-reportado vs estimado-por-catálogo) frente a sobrescribir
  `total_cost_usd` (más simple, pierde la distinción). Decidir en task_prod07_13
  y documentarlo.
- **claude_sdk + tools**: decisión de producto → **ADR propuesto** (no se toma
  aquí): (A) cablear `run_agent()` del SDK con sus herramientas en el runtime,
  (B) mantener `complete()` y bloquear la combinación en validación. Este plan
  implementa el bloqueo/aviso explícito (B) como salvaguarda inmediata y deja el
  ADR para que un humano decida si se invierte en (A).
- **Credencial al contenedor**: mount tmpfs read-only vía el seam ya existente
  (`ContainerSpec.extra_mounts`) con puntero en `AGENT_TASK_SPEC`, manteniendo
  compatibilidad con el formato anterior durante una versión de imagen
  (el runtime acepta ambos) para no romper despliegues en vuelo.

## Tareas

### Fase A — Fiabilidad: retry, event loop y streaming

#### `task_prod07_01` — Retry con backoff en los consumidores de los 4 providers

- [x] **Título**: Helper `with_retries()` en shared-llm + aplicación en runtime y asistente
- **Descripción**: crear `packages/shared-llm/src/shared_llm/retry.py` con backoff
  exponencial + jitter (2-3 intentos) que reintente `RateLimitError` (respetando
  `Retry-After` — `check_status` en `_openai_compat.py:61` debe adjuntar el header
  a la excepción) y `ProviderError` transitorios (timeout, 5xx); `AuthError` y 4xx
  no-429 fallan rápido. Aplicarlo en `_ProviderModelClient.decide()/review()`
  (`docker/agent-runtimes/agent-runtime/agent_runtime/providers.py:214`) y en
  `LLMAssistantModel` (`apps/api-server/src/api_server/assistant/llm.py`). Loguear
  cada reintento con provider/intento/causa.
- **Tiempo**: 10 h · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod07_01_a
    runtime: python-pytest
    command: "pytest packages/shared-llm/tests/test_retry_policy.py -v"
  - id: auto_prod07_01_b
    runtime: python-pytest
    command: "pytest docker/agent-runtimes/agent-runtime/tests/test_provider_retries.py -v"
  ```

#### `task_prod07_02` — Event loop único por ejecución en el agent-runtime + aclose

- [x] **Título**: Eliminar `asyncio.run` por llamada (bug "Event loop is closed")
- **Descripción**: sustituir `_run()` (`agent_runtime/providers.py:186-194`) por un
  `asyncio.Runner` persistente por ejecución (o hilo dedicado con loop propio) de
  modo que el `httpx.AsyncClient` que el provider crea una vez en su constructor
  (`ollama.py:51`, `azure_foundry.py:65`, `copilot.py:146`) viva siempre en el
  mismo loop. Añadir `await provider.aclose()` al terminar el run en
  `__main__.py`. Es el mismo fallo documentado y corregido en el asistente
  (`assistant/llm.py:33-38`); el runtime conserva el patrón roto.
- **Tiempo**: 8 h · **Complejidad**: m
- **Dependencias**: ninguna (paralelizable con task_prod07_01)
- **Tests automáticos**:
  ```yaml
  - id: auto_prod07_02_a
    runtime: python-pytest
    command: "pytest docker/agent-runtimes/agent-runtime/tests/test_event_loop_reuse.py -v"
  ```
  El test ejecuta dos `decide()` consecutivos sobre el mismo cliente con conexión
  keep-alive real (sin MockTransport precargado) y asserta que no se lanza
  `RuntimeError: Event loop is closed` y que `aclose()` se invoca al finalizar.

#### `task_prod07_03` — Errores de stream no-2xx legibles

- [x] **Título**: `await resp.aread()` antes de `check_status` en streaming
- **Descripción**: en `ollama.py:156`, `azure_foundry.py:134` y
  `copilot.py:368/380`, cuando `resp.status_code >= 400` dentro del bloque
  `client.stream(...)`, leer el cuerpo (`await resp.aread()`) antes de llamar a
  `check_status` para que se construya el `AuthError`/`RateLimitError` tipado en
  vez del `httpx.ResponseNotRead` opaco. Los tests actuales enmascaran el camino
  real con `MockTransport` (`test_ollama_provider.py:150-159`).
- **Tiempo**: 4 h · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod07_03_a
    runtime: python-pytest
    command: "pytest packages/shared-llm/tests/test_stream_error_status.py -v"
  ```
  Usa un transporte que NO precarga el cuerpo (AsyncByteStream) para cubrir el
  comportamiento real de httpcore.

#### `task_prod07_04` — `usage` y `tool_calls` en el streaming OpenAI-compat

- [x] **Título**: `stream_options.include_usage` + parseo de tool_calls en SSE
- **Descripción**: añadir `stream_options: {"include_usage": true}` al body de
  `stream()` en los tres providers OpenAI-compat; extender
  `parse_sse_delta`/`iter_sse_chunks` (`_openai_compat.py:111-152`) para parsear
  los deltas de `tool_calls` y el chunk final de `usage`, emitiéndolos en el
  `StreamChunk(done=True)` final, conforme al contrato (`base.py:23-24`).
  `ClaudeAgentProvider.stream()` ya cumple (`claude_agent.py:199-223`).
- **Tiempo**: 8 h · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod07_04_a
    runtime: python-pytest
    command: "pytest packages/shared-llm/tests/test_stream_usage_parity.py -v"
  ```
  Test de paridad `complete()` vs `stream()` por provider: mismos tokens y
  mismos tool_calls.

#### `task_prod07_05` — Cierre de providers en `/assistant/chat`

- [ ] **Título**: `get_assistant_model` como dependencia async-generator con `aclose()`
  - ⏳ **Pendiente (2026-07-31):** sin empezar — `routers/assistant.py::get_assistant_model` sigue haciendo `return` del provider (sin `yield`/`finally: await provider.aclose()`) y falta `tests/integration/test_assistant_provider_teardown.py`.
- **Descripción**: convertir `get_assistant_model`
  (`apps/api-server/src/api_server/routers/assistant.py:150`) en async generator
  de FastAPI (`yield` + `finally: await provider.aclose()`), siguiendo el patrón
  correcto ya presente en `factory.py:213-218` y en el memorizer. Hoy cada chat
  fuga un `AsyncClient` con keep-alives a merced del GC.
- **Tiempo**: 3 h · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod07_05_a
    runtime: python-pytest
    command: "pytest tests/integration/test_assistant_provider_teardown.py -v"
  ```

#### `task_prod07_06` — `ClaudeAgentProvider` sin mutar `os.environ`

- [x] **Título**: Credencial por env del subproceso del query SDK, no process-global
- **Descripción**: eliminar `os.environ["ANTHROPIC_API_KEY"] = api_key`
  (`claude_agent.py:51-52`); pasar la credencial por las opciones/env por
  invocación que acepta el claude-agent-sdk. Si no es viable en todos los caminos,
  restaurar/limpiar la variable en `aclose()` y documentar la restricción de una
  sola credencial Claude por proceso. Hoy dos filas `claude_sdk` con tokens
  distintos se pisan entre sí (carrera process-wide, `factory.py:62-63`) y el
  token persiste tras borrar el proveedor.
- **Tiempo**: 4 h · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod07_06_a
    runtime: python-pytest
    command: "pytest packages/shared-llm/tests/test_claude_agent_provider.py -v -k environ"
  ```

### Fase B — Resolución de credenciales y capacidades honestas

#### `task_prod07_07` — Vault fail-fast en el dispatch (`vault_unavailable`)

- [x] **Título**: Fallo de Vault → `ModelResolutionError`, no 401 opaco en el sandbox
- **Descripción**: en `apps/workers/src/workers/model_resolver.py` (resolve del
  spec, líneas 94-118), cuando la fila tiene `secret_vault_path` y el kind
  requiere credencial (azure_foundry, copilot, ollama-cloud), tratar
  `LLMProviderVaultError` como `ModelResolutionError` con abort_code
  `vault_unavailable` en vez de heredar el `secret = {}` silencioso de
  `factory_resolver.py:104-111` (cuyo "env fallback" no existe en el sandbox).
  Reintentar la lectura de Vault una vez antes de abortar; log diferenciado
  "Vault transport error" vs "sin credencial configurada". Mantener la
  degradación solo para kinds sin credencial. Cierra llm-9 y workers-8.
  **Coordinación**: prod-10 cubre la operabilidad de Vault; aquí solo el
  comportamiento del consumidor.
- **Tiempo**: 6 h · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod07_07_a
    runtime: python-pytest
    command: "pytest tests/unit/test_model_resolver_vault.py -v"
  ```
  Vault caído + fila con secret_vault_path → la ejecución finaliza
  `failed/vault_unavailable` SIN lanzar el contenedor.

#### `task_prod07_08` — Unificar el mapeo kind→credencial triplicado

- [x] **Título**: Tabla única en shared-llm consumida por worker, runtime y factory
- **Descripción**: extraer el mapeo kind→campos (azure*foundry→apim_base_url/
  subscription_key/bearer_token, copilot→github_token, ollama→base_url/api_key) a
  `packages/shared-llm/src/shared_llm/credential_fields.py` y hacer que lo
  consuman las tres copias actuales: `workers/model_resolver._overlay_provider_fields`
  (`model_resolver.py:49-74`), `agent_runtime/providers._overlay_resolved`
  (`providers.py:403-435`) y los `\_build*\*` del api-server (`factory.py:50-116`).
Corregir la divergencia ya existente: el worker no mapea `bearer_token` de
  Azure mientras el factory sí lo acepta — un proveedor azure bearer-only es hoy
  construible vía asistente pero irresoluble vía dispatch.
- **Tiempo**: 8 h · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod07_08_a
    runtime: python-pytest
    command: "pytest tests/unit/test_credential_fields_parity.py -v"
  ```
  Test de paridad: para cada kind, los campos generados por las tres rutas son
  idénticos.

#### `task_prod07_09` — Capacidades `claude_sdk` honestas

- [ ] **Título**: Bloqueo/aviso explícito de tools+claude_sdk, timeout del SDK y ADR
  - ⏳ **Pendiente (2026-07-31):** solo está (c), el `asyncio.wait_for` del SDK (7 tests verdes); faltan (a) la validación que rechaza tools+claude_sdk — `pytest tests/integration/test_model_config_validation.py -k claude_sdk` no selecciona NINGÚN test —, (b) la nota de limitación en el catálogo y (d) el ADR con las opciones A/B.
- **Descripción**: hoy `ClaudeAgentProvider.complete()` ignora tools/max_tokens/
  temperature (`claude_agent.py:152-154`) y `ClaudeSDKModelClient.decide()` siempre
  devuelve FINISH (`agent_runtime/providers.py:343-347`): un agente claude_sdk con
  herramientas "completa" la tarea sin actuar y sin warning. Implementar: (a)
  validación de model_config que rechaza (HTTP 422 con mensaje claro) o marca con
  warning bloqueante el dispatch de un agente con tools asignadas + kind
  claude_sdk; (b) nota de limitación en el catálogo de la UI; (c)
  `asyncio.wait_for` con timeout alrededor del query del SDK (hoy un cuelgue del
  CLI colgaría la request del asistente indefinidamente); (d) ADR propuesto en
  `docs/05-architecture-decisions/` con las opciones A (cablear `run_agent()` con
  tools) y B (mantener el bloqueo) para decisión humana.
- **Tiempo**: 10 h · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod07_09_a
    runtime: python-pytest
    command: "pytest tests/integration/test_model_config_validation.py -v -k claude_sdk"
  - id: auto_prod07_09_b
    runtime: python-pytest
    command: "pytest packages/shared-llm/tests/test_claude_agent_provider.py -v -k timeout"
  ```

#### `task_prod07_10` — Credencial fuera del env del contenedor

- [ ] **Título**: Mount tmpfs read-only para el secreto; env solo con puntero
  - ⏳ **Pendiente (2026-07-31):** sin empezar — la credencial sigue viajando dentro de `AGENT_TASK_SPEC` (`workers/execution.py`), no hay mount tmpfs por `extra_mounts` y falta `docker/agent-runtimes/agent-runtime/tests/test_spec_secret_file.py`.
- **Descripción**: mover api_key/subscription_key/github_token de
  `AGENT_TASK_SPEC` (`apps/workers/src/workers/execution.py:303-305`) a un
  fichero en tmpfs montado read-only vía el seam ya existente
  (`ContainerSpec.extra_mounts`, `container.py:49-50`); `AGENT_TASK_SPEC` lleva
  solo la ruta. El runtime lee el fichero al construir el provider
  (`_overlay_resolved`). Excluir además las claves sensibles de `config_env` en
  la captura (`container.py:260`). Versionar el formato del spec: el runtime
  acepta ambos formatos durante una versión de imagen (requiere reconstruir
  `agent-runtime`, igual que avisó la nota del commit de ADR 0057 F1).
- **Tiempo**: 10 h · **Complejidad**: l
- **Dependencias**: task_prod07_08 (el formato del overlay queda unificado antes
  de moverlo de sitio)
- **Tests automáticos**:
  ```yaml
  - id: auto_prod07_10_a
    runtime: python-pytest
    command: "pytest tests/integration/test_worker_launches_container.py -v -k secret_mount"
  - id: auto_prod07_10_b
    runtime: python-pytest
    command: "pytest docker/agent-runtimes/agent-runtime/tests/test_spec_secret_file.py -v"
  ```
  Asserta que el `Env` del contenedor no contiene api_key/token y que el runtime
  resuelve la credencial desde el mount.

#### `task_prod07_11` — Test de conexión que detecta tokens revocados

- [x] **Título**: Probe real para copilot y soporte bearer-only en azure_foundry
- **Descripción**: en `apps/api-server/src/api_server/llm_providers/liveness.py`:
  para copilot, ejecutar el mint del JWT (`copilot_internal/v2/token`, ya
  implementado en `copilot.py:264-273`) y distinguir 401 — hoy un token revocado
  luce verde porque solo se comprueba que EXISTE en Vault (`liveness.py:154-171`).
  Para azure_foundry, enviar `Authorization: Bearer` cuando no haya
  subscription_key (`liveness.py:140-146` rechaza hoy configs bearer-only que el
  factory acepta, `factory.py:86-99`). Para claude_sdk, documentar el límite del
  probe en la UI si no hay llamada barata disponible.
- **Tiempo**: 5 h · **Complejidad**: s
- **Dependencias**: task_prod07_08 (alineación probe↔factory sobre la tabla única)
- **Tests automáticos**:
  ```yaml
  - id: auto_prod07_11_a
    runtime: python-pytest
    command: "pytest tests/unit/test_provider_liveness.py -v"
  ```

### Fase C — Contabilidad de costes exacta

#### `task_prod07_12` — `provider` y clave de catálogo en los pasos `model_call`

- [ ] **Título**: El runtime registra provider/kind y model id casable con el catálogo
  - ⏳ **Pendiente (2026-07-31):** el `provider` del step `model_call` ya se registra (venía de AUD16-15, no de este plan) y `execution_repo` lo lee, pero el test que el plan exige no existe: `pytest tests/integration/test_execution_capture.py -k snapshot_provider` no selecciona NINGÚN test, así que la clave de catálogo del model id sigue sin acreditar.
- **Descripción**: `model_call_step`
  (`docker/agent-runtimes/agent-runtime/agent_runtime/steps.py:52-72`) no registra
  `provider`, así que `snapshot_execution_prices` busca con `provider=""`
  (`execution_repo.py:91`) y nunca casa con el catálogo (familias LiteLLM como
  `ollama`/`anthropic`); además registra el nombre de modelo despojado del prefijo
  (`to_provider_model_name`). El worker conoce ambos en el spec resuelto:
  propagarlos al runtime y registrarlos en cada step `model_call` (kind + model id
  en clave de catálogo). Requiere reconstruir la imagen agent-runtime.
- **Tiempo**: 8 h · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod07_12_a
    runtime: python-pytest
    command: "pytest tests/integration/test_execution_capture.py -v -k snapshot_provider"
  ```
  Asserta que el snapshot encuentra precio de catálogo para ollama/copilot/azure.

#### `task_prod07_13` — `total_cost_usd` real desde los snapshots de precio

- [ ] **Título**: Cuando el runtime reporta 0, persistir la suma de snapshots y que budgets la consuma
  - ⏳ **Pendiente (2026-07-31):** sin empezar — no existe `tests/integration/test_execution_cost_finalize.py` ni la columna/override de coste estimado que la decisión clave pedía elegir aquí.
- **Descripción**: `_openai_compat.py:99` solo rellena `usage.cost_usd` si el
  endpoint añade `cost` (Ollama/Copilot nunca; APIM solo con policy), y ese 0 se
  persiste en `finalize_execution` (`execution_repo.py:254`) y lo suman los
  budgets (`budgets/consumption.py:218`). Implementar: en `finalize_execution`,
  si el coste reportado es 0 y existen snapshots con `cost_usd`, persistir la
  suma (columna `cost_estimated_usd` preferida por budgets, u override — según la
  decisión clave); migración Alembic reversible si hay columna nueva; ajustar
  `consumption.py` a la fuente correcta. Test de no-regresión del camino
  claude_agent (único que hoy reporta coste real, `claude_agent.py:139/266`).
- **Tiempo**: 8 h · **Complejidad**: m
- **Dependencias**: task_prod07_12
- **Tests automáticos**:
  ```yaml
  - id: auto_prod07_13_a
    runtime: python-pytest
    command: "pytest tests/integration/test_execution_cost_finalize.py -v"
  ```

#### `task_prod07_14` — Test de integración e2e: coste > 0 y budgets consumiendo

- [ ] **Título**: Pipeline completo con modelo del catálogo asserta coste real
  - ⏳ **Pendiente (2026-07-31):** sin empezar — no existe `tests/integration/test_execution_cost_e2e.py`, y depende de task_prod07_12 y task_prod07_13, ambas abiertas.
- **Descripción**: test de integración que ejecuta el pipeline con un fake
  provider OpenAI-compat (sin campo `cost` en usage, como Ollama/Copilot reales)
  y un modelo presente en el catálogo de precios, y asserta:
  `executions.total_cost_usd > 0` (o `cost_estimated_usd`), los steps
  `model_call` llevan provider + clave de catálogo, y
  `budgets/consumption` suma ese coste en el proyecto. **Coordinación**: prod-06
  añade el enforcement de budgets sobre esta cifra; este plan garantiza que la
  cifra es exacta.
- **Tiempo**: 8 h · **Complejidad**: m
- **Dependencias**: task_prod07_12, task_prod07_13
- **Tests automáticos**:
  ```yaml
  - id: auto_prod07_14_a
    runtime: python-pytest
    command: "pytest tests/integration/test_execution_cost_e2e.py -v"
  ```

### Fase D — Memorizer y documentación

#### `task_prod07_15` — Memorizer dentro del catálogo `llm_providers`

- [ ] **Título**: Resolver el LLM del Memorizer vía catálogo (DB row > env)
  - ⏳ **Pendiente (2026-07-31):** parcial y por otra vía: el camino primario es el provider del agente por `provider_id` (ADR 0082), pero `_default_llm_factory` sigue construyendo `OllamaProvider` desde env, no hay contador de fallos consecutivos de destilación y `pytest tests/integration/test_memorizer.py -k provider_resolution` no selecciona NINGÚN test.
- **Descripción**: `_default_llm_factory`
  (`apps/workers/src/workers/memorizer.py:87-97`) construye `OllamaProvider`
  directamente con `WORKERS_MEMORIZER_LLM_BASE_URL` (default
  `http://localhost:11434/v1`), incumpliendo la precedencia "DB row > env" del
  propio `factory_resolver.py:13`. Con ollama-local desactivado (estado operativo
  actual, ADR 0056), la memorización falla en silencio. Resolver vía
  `resolve_provider_config("ollama")` (fila activa más nueva, igual que dispatch)
  con el env como fallback; ídem el embedder de back-fill
  (`config.py:179-183`). Añadir contador/métrica de fallos consecutivos de
  destilación con log de error visible (la alerta se cablea en **prod-08**).
- **Tiempo**: 8 h · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod07_15_a
    runtime: python-pytest
    command: "pytest tests/integration/test_memorizer.py -v -k provider_resolution"
  ```
  Con fila ollama activa el memorizer usa su base_url; sin fila, cae al env.

#### `task_prod07_16` — Documentación de referencia de la capa LLM

- [ ] **Título**: Referencia: retry, streaming, capacidades por kind, costes, Vault
  - ⏳ **Pendiente (2026-07-31):** `docs/04-reference/llm-providers.md` existe pero la tarea documenta lo implementado y depende del resto de fases, con 8 de 16 tareas aún abiertas (costes enteros incluidos); falta además la entrada de changelog del plan.
- **Descripción**: crear/ampliar `docs/04-reference/llm-providers.md` con: la
  política de retry (qué se reintenta y qué no), el contrato de streaming
  (chunk final con usage/tool_calls), la matriz de capacidades por kind (incluida
  la limitación claude_sdk sin tools hasta el ADR), la contabilidad de costes
  (fuentes, precedencia runtime-reportado vs snapshot de catálogo) y el
  comportamiento ante Vault caído (`vault_unavailable`). Preparar la entrada de
  changelog `docs/07-changelog/prod-07-fiabilidad-llm-costes.md` para el cierre.
- **Tiempo**: 4 h · **Complejidad**: s
- **Dependencias**: resto de fases (documenta lo implementado)

## Hallazgos de auditoría cubiertos

| fid       | Severidad | Tarea(s) que lo cierran                        |
| --------- | --------- | ---------------------------------------------- |
| llm-1     | high      | task_prod07_12, task_prod07_13, task_prod07_14 |
| llm-2     | high      | task_prod07_01                                 |
| llm-3     | high      | task_prod07_02                                 |
| llm-4     | high      | task_prod07_09                                 |
| llm-5     | medium    | task_prod07_03                                 |
| llm-6     | medium    | task_prod07_04                                 |
| llm-7     | medium    | task_prod07_06                                 |
| llm-8     | medium    | task_prod07_05                                 |
| llm-9     | medium    | task_prod07_07                                 |
| llm-10    | medium    | task_prod07_15                                 |
| llm-11    | low       | task_prod07_10                                 |
| llm-12    | low       | task_prod07_11                                 |
| llm-13    | low       | task_prod07_08                                 |
| workers-8 | medium    | task_prod07_07                                 |

## Riesgos

1. **Retries que duplican coste**: un timeout tras procesar la petición en el
   proveedor + retry = doble consumo de tokens. Mitigación: máximo 2-3 intentos,
   solo errores claramente transitorios, y el coste de reintentos queda
   contabilizado (fase C) — visible, no oculto.
2. **Cambio del formato de `AGENT_TASK_SPEC`** (task_prod07_10 y 12): exige
   reconstruir la imagen `agent-runtime` y coordinar versiones worker↔runtime;
   un despliegue con imagen vieja rompería runs. Mitigación: el runtime acepta
   ambos formatos durante una versión y se verifica el rebuild en el deploy
   (gancho con prod-01/prod-02).
3. **Regresión en la contabilidad de Claude**: claude_agent es el único provider
   que hoy reporta coste real; tocar `finalize_execution` puede pisarlo.
   Mitigación: test explícito de no-regresión en task_prod07_13.
4. **Refactor del event loop del runtime**: introducir `asyncio.Runner` en el
   grafo síncrono de LangGraph puede causar deadlocks sutiles. Mitigación: test
   con conexión keep-alive real (no MockTransport) y la alternativa
   cliente-por-llamada documentada como plan B.
5. **Bloqueo de tools+claude_sdk rompe agentes existentes**: tenants con esa
   combinación configurada dejarían de despachar. Mitigación: empezar con
   warning bloqueante solo en dispatch nuevo + mensaje accionable en la UI; el
   ADR decide el destino final.
6. **Claves de catálogo de precios heredadas de LiteLLM**: la normalización
   kind→familia puede no casar para todos los modelos (p.ej. apodos de Azure).
   Mitigación: el test e2e (task_prod07_14) usa modelos reales del catálogo y el
   snapshot loguea los misses para detectarlos en staging.

## Tests humanos del Plan

```yaml
- id: human_prod07_01
  description: "Un 429 transitorio no mata la ejecución del agente"
  hint: "Forzar rate-limit (proxy o límite del proveedor) durante un run real"
  checklist:
    - "El log del runtime muestra el reintento con provider/intento/causa"
    - "La ejecución termina done a pesar del 429 puntual"
    - "Un AuthError (token inválido) sigue fallando rápido, sin reintentos"

- id: human_prod07_02
  description: "Los costes dejan de ser $0 para Ollama/Copilot/Azure"
  hint: "Ejecutar un plan con un agente sobre ollama cloud (modelo del catálogo)"
  checklist:
    - "executions.total_cost_usd (o cost_estimated_usd) > 0 al finalizar"
    - "Los pasos model_call muestran provider y modelo en clave de catálogo"
    - "La vista de budgets del proyecto refleja el consumo del run"
    - "Un run con claude_sdk sigue reportando su coste real (no-regresión)"

- id: human_prod07_03
  description: "Vault caído produce un fallo explícito, no un 401 opaco"
  hint: "Parar el contenedor de Vault y despachar una tarea con azure/copilot"
  checklist:
    - "La ejecución finaliza failed con abort_code vault_unavailable"
    - "NO se ha llegado a lanzar el contenedor agent-runtime"
    - "El log distingue 'Vault transport error' de 'credencial no configurada'"

- id: human_prod07_04
  description: "La credencial no es visible en el contenedor ni en inspect"
  hint: "Durante un run vivo, docker inspect del contenedor agent-runtime"
  checklist:
    - "El Env del inspect no contiene api_key/github_token/subscription_key"
    - "AGENT_TASK_SPEC solo lleva el puntero al fichero del secreto"
    - "El run funciona igual (la credencial llega por el mount tmpfs)"

- id: human_prod07_05
  description: "El test de conexión detecta tokens revocados y acepta azure bearer-only"
  hint: "Revocar un token de Copilot en GitHub y pulsar 'probar conexión'"
  checklist:
    - "Copilot con token revocado → resultado rojo con causa clara"
    - "Azure Foundry configurado solo con bearer_token → resultado verde"
    - "Un agente con tools que selecciona claude_sdk recibe el aviso/bloqueo"

- id: human_prod07_06
  description: "El Memorizer usa el catálogo y su muerte ya no es silenciosa"
  hint: "Con ollama-local desactivado y fila ollama cloud activa, cerrar un run"
  checklist:
    - "La memorización post-run funciona contra la fila del catálogo"
    - "Desactivando todas las filas ollama, el log muestra el error visible y el contador de fallos"
```

## Criterios de cierre

1. Todas las tareas con `[x]` y sus tests automáticos en verde.
2. Suite completa verde: `pytest packages/shared-llm/tests tests/unit tests/integration -v`
   y los tests del agent-runtime (`docker/agent-runtimes/agent-runtime/tests`).
3. Los 6 tests humanos validados por un humano.
4. ADR de claude_sdk+tools creado y en estado `proposed` (la decisión la toma un humano,
   no bloquea el cierre del plan).
5. Imagen `agent-runtime` reconstruida y desplegada con el nuevo formato de spec.
6. Migración Alembic (si hay columna de coste nueva) verificada reversible.
7. Entrada de changelog en `docs/07-changelog/prod-07-fiabilidad-llm-costes.md`.
8. PR del plan mergeado a `master`.

## Próximo Plan

El siguiente de la serie correctiva por prioridad es
**prod-08-observabilidad-alertas** (P1) — observabilidad de aplicación y cadena
de alertas funcional: las métricas que este plan emite (reintentos LLM, fallos
de destilación del Memorizer, misses del snapshot de precios, abortos
`vault_unavailable`) se cablean allí a alertas reales. También se coordinan:
**prod-06** (enforcement de budgets sobre el coste ya exacto) y **prod-10**
(operabilidad de Vault, cuyo modo de fallo este plan convierte en explícito).
