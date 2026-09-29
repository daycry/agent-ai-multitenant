---
plan_id: compat-modelos-2026-09-29
title: Compatibilidad del bucle del agente con modelos de 9B a frontera
started_at: 2026-09-29
completed_at: null
status: pending_human_validation
tasks_done: 12
tasks_total: 12
docs_language: es
---

> **Estado: entregado, pendiente de validación humana.** Las 12 tareas están cerradas
> con su test en verde. Los dos tests humanos del plan —un run real con un modelo de
> 9B en el Ollama del stack, y Codex por Copilot o Azure— siguen sin validar.

# Changelog — compat-modelos · Del modelo de 9B a Codex, sin quinto proveedor

El operador pidió el 2026-09-29 que el sistema funcione con modelos de unos 9B en
adelante y hasta Claude, Codex y Ollama cloud. El [ADR 0169](../05-architecture-decisions/0169-compatibilidad-de-modelos-del-agente.md)
lo resuelve dentro del catálogo cerrado del ADR 0021: Codex llega por Copilot y por
Azure, que ya lo exponen, y los modelos pequeños por Ollama.

## Qué cambió

**El bucle del agente aguanta a un modelo pequeño.**

- Cada llamada HTTP lleva un tope de salida explícito: 8192 al decidir, 4096 en el
  review y 2048 en la reflexión. Antes se aplicaba el default de 1024 de los
  proveedores y un `write_file` mediano no cabía. `model_config.max_output_tokens` lo
  sustituye, se valida en la API y se puede fijar desde la ficha del agente.
- Una acción con los argumentos truncados o corruptos ya no se ejecuta con `{}`. El
  `noop` dice cuál y cómo evitarlo. La guarda es por llamada.
- Una tool call escrita como texto se rescata si nombra una tool anunciada en ese
  turno. Antes el run terminaba con el JSON como entregable. Los bloques `<think>` se
  retiran también del entregable.

**GPT-5, la serie o y Codex funcionan por Copilot y Azure.**

- `temperature` sólo viaja si alguien la configuró.
- El proveedor lee el 400 del servidor y se adapta una vez por modelo: pasa a
  `max_completion_tokens`, quita la temperatura, baja el tope al máximo del modelo o
  cambia a la Responses API. Un 400 que no encaja sigue siendo un error.

**Ollama no recorta en silencio el prompt de sistema.**

- El Ollama del stack fija `OLLAMA_CONTEXT_LENGTH` (32768 por defecto). Con menos de
  23 GiB de VRAM usaba 4096, y el percentil 90 del prompt de un agente, medido en la
  BD viva, es de 9.294 tokens.
- Para un Ollama externo, el runtime pregunta a `/api/ps` con qué contexto cargó el
  modelo y detiene el run con `abort_code=context_truncated` si el prompt no cabe.
- El plazo HTTP de los proveedores ya no corta antes que el del runtime.

## Lo que la prueba viva encontró

Los tests unitarios usan dobles; la prueba viva usa el Ollama del stack, en CPU:

| Modelo       | Parámetros | Resultado                               |
| ------------ | ---------: | --------------------------------------- |
| `gemma4:e4b` |      8,0 B | `done`, fichero correcto, 2 iteraciones |
| `qwen3:8b`   |      8,2 B | `done`, fichero correcto, 2 iteraciones |

Y dos cosas que ningún test con dobles habría visto:

- **El contexto por defecto era 4096.** `ollama ps` lo mostró con el propio servidor
  del stack antes del cambio.
- **La primera pasada de `qwen3:8b` acabó en `provider_error`.** Los turnos en CPU
  duraban hasta 5 minutos y el cliente HTTP del proveedor cortaba a los 120 s: seis
  `500` en el log de Ollama a los 2m0s exactos. Es `task_cm_12`.

## Correcciones sobre la marcha

- **El detector de recorte no puede usar `usage.prompt_tokens`.** Ollama informa ahí
  de los tokens que evalúa, no de los que reutiliza de su caché de prefijos, y saltaría
  en falso desde el segundo turno. Se usa `/api/ps`. El ADR 0169 D6 lo recoge.
- **Un test fijaba el defecto.** `test_action_only_corrupt_call_still_acts` afirmaba
  que una acción corrupta se ejecutaba con `{}` «exactly as before». Se reescribió a la
  decisión del ADR.

## Lo que NO entra

- **Codex por la suscripción de ChatGPT.** Sería un quinto proveedor, con su propio
  OAuth: necesita su propio ADR.
- **Pruebas vivas de Codex, GPT-5.x y Ollama cloud.** No hay credenciales en esta
  máquina; ver `docs/04-reference/llm-providers.md` §9.4.

## Verificación (2026-09-29)

| Suite                                            | Resultado             |
| ------------------------------------------------ | --------------------- |
| `docker/agent-runtimes/agent-runtime/tests`      | 779 passed            |
| `packages/shared-llm/tests`                      | 212 passed, 1 skipped |
| Instalador, compose y guardas de entorno         | 779 passed, 1 skipped |
| vitest de persona y del editor de modelo         | 46 passed             |
| mypy del árbol entero                            | sin errores           |
| `tests/e2e/test_model_compat_live.py`, 2 modelos | 2 passed              |
