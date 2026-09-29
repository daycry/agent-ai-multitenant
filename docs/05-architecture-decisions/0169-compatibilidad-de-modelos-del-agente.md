---
adr_id: "0169"
title: "Compatibilidad del bucle del agente con modelos de 9B a frontera, dentro del catálogo cerrado"
status: accepted
date: 2026-09-29
decided_on: 2026-09-29
authors: [claude-opus-5-5, operador]
plan_referenced: compat-modelos-2026-09-29
related: ["0021", "0070", "0087", "0110", "0111", "0162"]
docs_language: es
---

# ADR 0169 — Compatibilidad del bucle del agente con modelos de 9B a frontera

> **Estado: `accepted` (2026-09-29).** El operador pidió el 2026-09-29 que el
> sistema funcione «para modelos más pequeños como los de 9B en adelante, hasta
> los modelos de Claude, Codex, Ollama cloud». Este ADR fija cómo se hace **sin
> añadir un quinto proveedor**, que es lo que el ADR 0021 exige decidir aparte.

## Contexto

El catálogo cerrado del ADR 0021 tiene cuatro caminos: Claude Agent SDK, GitHub
Copilot, Azure AI Foundry por APIM y Ollama, local y cloud. La petición del operador
cabe entera en esos cuatro, pero el código de hoy falla en cinco puntos, todos
verificados el 2026-09-29 contra la rama `plan/harness-programa-2026-09-17`:

1. **Tope de salida de 1024 tokens.** Ningún camino HTTP del runtime pasa
   `max_tokens` al decidir, así que se aplica el default de 1024 de los
   proveedores. Un `write_file` de un fichero mediano no cabe.
2. **Tool call truncada sin guarda.** Cuando el tope corta los argumentos, el JSON
   llega corrupto, se degrada a `{}` y la tool se ejecuta con argumentos vacíos.
   La guarda F32 sólo protegía `submit_result` y la prosa.
3. **Tool calls escritas como texto.** Un modelo pequeño, o un servidor cuya
   plantilla no reconoce la llamada, deja la tool call en `content`. El runtime la
   trata como FINISH y entrega el JSON como resultado.
4. **Parámetros que los modelos de razonamiento rechazan.** Los proveedores mandan
   siempre `temperature: 0.7` y `max_tokens`. GPT-5 y la serie o sólo aceptan la
   temperatura por defecto y exigen `max_completion_tokens`.
5. **Modelos que sólo sirve la Responses API.** Codex y los GPT-5.x recientes
   devuelven `unsupported_api_for_model` en Copilot y `OperationNotSupported` en
   Azure si se les llama por `/chat/completions`.

Y un sexto que no es de código sino de despliegue: **Ollama no deja fijar la
ventana de contexto por su endpoint compatible con OpenAI**, y con menos de 23 GiB
de VRAM usa 4096 tokens. Cuando el prompt no cabe, **recorta el principio de la
conversación en silencio**, que es el prompt de sistema. Medido en la BD viva el
2026-09-29 con `gpt-oss:120b`: mediana de 3.666 tokens de entrada por llamada,
percentil 90 de 9.294 y máximo de 22.109.

## Decisión

**D1 — Todo dentro del catálogo.** Codex se sirve por los dos proveedores que ya lo
exponen, Copilot y Azure Foundry. Los modelos pequeños y Ollama cloud, por el
proveedor Ollama. No se añade ningún proveedor.

**D2 — Los modelos dicen qué no soportan y el proveedor se adapta una vez.** En vez
de listas de nombres de modelo, que envejecen (el caso documentado: GPT-5.4 no lleva
«codex» en el nombre y también es sólo Responses), el proveedor reacciona al error
del propio servidor:

- `unsupported_api_for_model` u `OperationNotSupported` → reintenta por la Responses
  API;
- `unsupported_parameter` sobre `max_tokens` → reintenta con `max_completion_tokens`;
- `unsupported_value` o `unsupported_parameter` sobre `temperature` → reintenta sin
  ella;
- un tope de salida por encima del máximo del modelo («supports at most N completion
  tokens») → reintenta con ese N.

La adaptación se **memoriza por modelo** durante la vida del proceso: la segunda
llamada ya sale bien. El reintento es uno solo y sólo ante esos códigos: cualquier
otro 400 sigue siendo un error.

**D3 — La temperatura sólo viaja si alguien la configuró.** El default deja de ser
0.7 en el proveedor; sin valor explícito no se manda, y cada servidor aplica el suyo.

**D4 — El tope de salida es explícito y configurable.** El runtime pasa un tope en
cada llamada: `model.max_output_tokens` del spec si viene, y si no 8192. Las
llamadas de reflexión y review, que sólo emiten un veredicto, usan el suyo más bajo.

**D5 — Robustez del parseo, en el runtime y sólo para los caminos HTTP.**

- Una tool call de acción truncada o con argumentos corruptos no se ejecuta: se
  convierte en un `noop` con el motivo dirigido, igual que F32 ya hacía con
  `submit_result`.
- Si no hay tool calls estructuradas pero el texto contiene una llamada en un
  formato conocido —`<tool_call>{…}</tool_call>`, un objeto JSON con `name` y
  `arguments`, o ese objeto en un bloque de código— **y el nombre es una tool
  anunciada en ese turno**, se enruta como ACT. Si el nombre no está anunciado, el
  texto sigue siendo lo que era. `claude_sdk` queda fuera: su FINISH real es prosa.
- Los bloques `<think>…</think>` se retiran del contenido antes de interpretarlo.

**D6 — Contexto de Ollama.** El compose y el instalador fijan
`OLLAMA_CONTEXT_LENGTH` (default 32768, configurable). Y como un Ollama externo o
cloud no lo controlamos, el runtime **detecta el recorte**: si el servidor informa
de muchos menos tokens de entrada de los que el prompt enviado tiene como mínimo, el
run se detiene con `abort_code=context_truncated` y un mensaje que nombra la
variable. Seguir con el prompt de sistema recortado produce trabajo que parece
válido y no lo es.

## Opciones descartadas

- **Transporte nativo `/api/chat` de Ollama** para fijar `num_ctx` por petición.
  Resuelve el contexto en local, pero cambia el formato de las tool calls, obliga a
  mantener dos transportes para un proveedor y no aplica a Ollama cloud por la misma
  vía. La variable de entorno más el detector cubren los dos casos.
- **Listas de modelos por nombre.** Envejecen; ver D2.
- **Codex por la suscripción de ChatGPT**, análogo al Claude Agent SDK con la
  suscripción de Claude. Es un quinto proveedor, con su propio flujo OAuth, y el ADR
  0021 lo pone fuera del alcance de una decisión de plataforma. Si el operador lo
  quiere, necesita su propio ADR.

## Consecuencias

- Un modelo de 9B en un Ollama local con el compose de la plataforma recibe el prompt
  entero. En un Ollama que no controlamos, el recorte deja de ser silencioso.
- Codex y los GPT-5.x que sólo sirve Responses funcionan por Copilot y por Azure sin
  configuración nueva. El primer turno paga un 400 y un reintento; los siguientes no.
- La Responses API se usa sin estado (`store: false`) y con el mismo historial en
  texto que ya manda el runtime: no depende del `previous_response_id`.
- El plan `compat-modelos-2026-09-29` implementa las seis decisiones y añade una
  prueba viva, contra un Ollama real, que ejercita el bucle del agente por su camino
  productivo.
