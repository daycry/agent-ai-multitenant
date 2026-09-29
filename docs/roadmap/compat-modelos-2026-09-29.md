---
plan_id: compat-modelos-2026-09-29
title: Compatibilidad del bucle del agente con modelos de 9B a frontera (Ollama local y cloud, Copilot, Azure, Claude)
status: approved
blocking_plan: []
started_at: null
completed_at: null
estimated_duration_calendar: 1-2 semanas
estimated_effort_person_days: 6-8
created_by: claude-opus-5-5-peticion-operador-2026-09-29
docs_language: es
priority: P0
source_audit: petición del operador del 2026-09-29 y verificación en código recogida en el ADR 0169
---

# Plan compat-modelos (2026-09-29)

## Cabecera

| Campo                 | Valor                                                                                 |
| --------------------- | ------------------------------------------------------------------------------------- |
| **ID del Plan**       | `compat-modelos-2026-09-29`                                                           |
| **Prioridad**         | P0                                                                                    |
| **Decisión**          | [ADR 0169](../05-architecture-decisions/0169-compatibilidad-de-modelos-del-agente.md) |
| **Rama git sugerida** | `plan/compat-modelos-2026-09-29`                                                      |

> **Estado.** `approved`: lo pidió el operador el 2026-09-29. No pasa por
> `in_progress` porque la regla de una sola fase activa la ocupa
> `ui-reestructuracion-2026-09-09`. Al cerrar la última casilla pasa a
> `pending_human_validation` con su changelog.

## Resumen

El operador quiere que el sistema funcione con modelos de unos 9B en adelante y hasta
Claude, Codex y Ollama cloud. Los cuatro caminos del catálogo cerrado bastan, pero el
bucle del agente falla hoy en seis puntos, descritos con su evidencia en el ADR 0169:
tope de salida de 1024 tokens, tool calls truncadas que se ejecutan con `{}`, tool
calls escritas como texto que acaban como FINISH, parámetros que los modelos de
razonamiento rechazan, modelos que sólo sirve la Responses API y el recorte
silencioso del contexto en Ollama.

## Criterios de cierre del plan

1. Las casillas de abajo en `[x]` con su test en verde.
2. La prueba viva de `task_cm_09` pasa contra un Ollama real con un modelo de la
   clase de 9B, y su resultado queda anotado aquí.
3. Changelog en `docs/07-changelog/compat-modelos-2026-09-29.md`.

## Tareas

### Ola 1 — El bucle del agente aguanta a un modelo pequeño

### `task_cm_01` — Tope de salida explícito en cada llamada del runtime

- [x] **Título**: los clientes HTTP del runtime pasan `max_tokens` en `decide`,
      `review` y `assess_progress`. Sale de `model.max_output_tokens` del spec si
      viene, y si no de 8192 para decidir; la reflexión y el review usan un tope
      menor porque sólo emiten un veredicto. `claude_sdk` no cambia: su SDK no
      expone el parámetro.
      **Test**: `docker/agent-runtimes/agent-runtime/tests/test_output_cap.py`.
      **Coste**: 0,5 d.
  - ✅ **Entregada el 2026-09-29.** Vista en rojo antes del código: 6 fallos
    (`KeyError: 'max_tokens'` y constructor sin el parámetro). Techos: 8192 al
    decidir, 4096 en el review y 2048 en la reflexión, y ninguno sube un tope
    configurado más bajo. La API valida la clave en `validate_model_config`
    (256 a 200 000; `tests/unit/test_model_config_max_output_tokens.py`, 11 casos).
    El worker ya copiaba el `model_config` entero al spec, así que no hizo falta
    tocar la resolución. Suite del runtime: 751 passed.

### `task_cm_02` — Una tool call de acción truncada o corrupta no se ejecuta

- [x] **Título**: `_decision_from` extiende la guarda F32 a la rama ACT. Si la
      respuesta salió truncada o los argumentos de la llamada elegida llegaron
      corruptos, la decisión es un `noop` con el motivo dirigido («reintenta con un
      contenido más corto o parte el fichero»), no la tool con `{}`.
      **Test**: `docker/agent-runtimes/agent-runtime/tests/test_truncated_action_guard.py`.
      **Coste**: 0,5 d.
  - ✅ **Entregada el 2026-09-29.** Vista en rojo: 4 de 6 casos (los dos limpios ya
    pasaban). La guarda es POR LLAMADA: `shared_llm` expone
    `corrupt_tool_call_indexes`, y el runtime descarta la llamada elegida si llegó
    corrupta o es la última de un turno truncado, y corta el lote de lecturas antes
    de una llamada inutilizable. El test antiguo
    `test_action_only_corrupt_call_still_acts` fijaba el defecto («exactly as
    before») y se reescribió a la decisión del ADR 0169. Runtime + shared-llm:
    948 passed, 1 skipped.

### `task_cm_03` — Rescate de tool calls escritas como texto

- [ ] **Título**: si una respuesta HTTP no trae tool calls estructuradas y su texto
      contiene una llamada en `<tool_call>{…}</tool_call>`, un objeto JSON con
      `name` y `arguments`, o ese objeto en un bloque de código, **y el nombre es una
      tool anunciada en ese turno**, se enruta como ACT (o como FINISH si es
      `submit_result`). Antes se retiran los bloques `<think>…</think>`. Nombre no
      anunciado, o camino `claude_sdk`: el texto sigue siendo lo que era.
      **Test**: `docker/agent-runtimes/agent-runtime/tests/test_text_tool_call_salvage.py`.
      **Coste**: 1 d.

### Ola 2 — Modelos de razonamiento y Codex

### `task_cm_04` — La temperatura sólo viaja si se configuró

- [ ] **Título**: los cuatro proveedores HTTP de `shared-llm` dejan de mandar
      `temperature: 0.7` por defecto. Sin valor explícito, la clave no está en el
      cuerpo.
      **Test**: `packages/shared-llm/tests/test_temperature_only_when_set.py`.
      **Coste**: 0,25 d.

### `task_cm_05` — Adaptación reactiva a los parámetros que el modelo rechaza

- [ ] **Título**: helper común en `_openai_compat` que, ante un 400 cuyo cuerpo dice
      `unsupported_parameter` sobre `max_tokens`, `temperature` no soportada o un
      tope por encima del máximo del modelo, devuelve el cuerpo corregido. Azure,
      Copilot y Ollama reintentan **una vez** con él y memorizan la corrección por
      modelo durante la vida del proveedor. Cualquier otro 400 sigue siendo un error.
      **Test**: `packages/shared-llm/tests/test_reactive_param_adaptation.py`.
      **Coste**: 1 d.

### `task_cm_06` — Transporte Responses para Codex y los GPT-5.x que lo exigen

- [ ] **Título**: módulo `_openai_responses.py` en `shared-llm` que construye la
      petición (`input`, `instructions`, tools en formato plano,
      `max_output_tokens`, `reasoning.effort`, `store: false`) y parsea la respuesta
      (`output_text`, `function_call`, `usage`, `status: incomplete`). Azure y Copilot
      lo usan cuando el servidor contesta `unsupported_api_for_model` u
      `OperationNotSupported`, y lo memorizan por modelo. `completion_signals`
      reconoce el payload de Responses para que F32 y `task_cm_02` sigan valiendo.
      **Test**: `packages/shared-llm/tests/test_responses_transport.py`.
      **Coste**: 1,5 d.

### Ola 3 — Contexto de Ollama

### `task_cm_07` — `OLLAMA_CONTEXT_LENGTH` en el compose y en el instalador

- [ ] **Título**: el servicio `ollama` de `docker/docker-compose.yml` y el que genera
      el instalador llevan `OLLAMA_CONTEXT_LENGTH`, default 32768 y configurable, con
      la cifra medida en el ADR 0169 como justificación.
      **Test**: `tests/unit/installer/test_ollama_context_length.py`.
      **Coste**: 0,25 d.

### `task_cm_08` — El recorte silencioso del contexto se detecta

- [ ] **Título**: tras cada llamada de Ollama, el runtime compara los tokens de
      entrada que informa el servidor con una cota inferior del prompt enviado. Si el
      servidor informa de menos de la mitad, el run se detiene con
      `abort_code=context_truncated` y un mensaje que nombra `OLLAMA_CONTEXT_LENGTH`.
      **Test**: `docker/agent-runtimes/agent-runtime/tests/test_context_truncation_guard.py`.
      **Coste**: 0,75 d.

### Ola 4 — Verificación

### `task_cm_09` — Prueba viva contra un Ollama real

- [ ] **Título**: `tests/e2e/test_model_compat_live.py`, gated por
      `MODEL_COMPAT_LIVE=1`, ejecuta el bucle del agente por su camino productivo
      (`run_agent` con el cliente del proveedor y las tools de fichero sobre un
      directorio temporal) con una tarea pequeña: escribir un fichero con un contenido
      concreto y terminar con `submit_result`. Se parametriza por modelo con
      `MODEL_COMPAT_OLLAMA_MODELS` y, si hay clave, contra Ollama cloud. No lanza runs
      de la plataforma.
      **Test**: el propio fichero, con su resultado anotado en este plan.
      **Coste**: 1 d.

### `task_cm_10` — Referencia y changelog

- [ ] **Título**: sección «Compatibilidad de modelos» en
      `docs/04-reference/llm-providers.md` con la matriz de lo que se probó y cómo, y
      entrada en `docs/07-changelog/`.
      **Test**: `tests/unit/test_docs_governance.py`.
      **Coste**: 0,25 d.

## Tests humanos

```yaml
- id: human_cm_01
  title: Un run real con un modelo de 9B en el Ollama del stack
  steps:
    - Tirar de un modelo de la clase de 9B con tool-calling en el Ollama del stack.
    - Asignarlo a un agente y lanzar una tarea pequeña de un plan de prueba.
  expected: La tarea termina con su entregable escrito, sin FINISH con JSON dentro y sin context_truncated.

- id: human_cm_02
  title: Codex por Copilot o por Azure
  steps:
    - Configurar un modelo Codex en un proveedor Copilot o Azure Foundry.
    - Lanzar una tarea pequeña.
  expected: El primer turno se adapta a la Responses API y la tarea termina.
```

## Lo que este plan NO hace

- **Codex por la suscripción de ChatGPT.** Sería un quinto proveedor; ADR 0169,
  opciones descartadas.
- **Recortar el catálogo de tools para modelos pequeños.** Puede mejorar la tasa de
  acierto, pero es una decisión sobre qué ve cada agente, no un fallo de
  compatibilidad. Se mide primero con la prueba viva.
