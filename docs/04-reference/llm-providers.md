---
title: Capa LLM — reintentos, streaming, capacidades por kind, costes y Vault
audience: backend-dev, devops, architect
phase: prod-07-fiabilidad-llm-costes
updated: 2026-07-30
---

# Capa LLM — Referencia

Contrato de `packages/shared-llm` y de sus tres consumidores (el sandbox
`agent-runtime`, el asistente/córtex del api-server y el worker que resuelve el
modelo antes de lanzar el contenedor). El catálogo de proveedores es **cerrado**
por el ADR 0021: `claude_sdk`, `copilot`, `azure_foundry`, `ollama`. Un quinto
exige ADR.

Esta página documenta **lo que hace el código hoy**. Donde una pieza del plan
prod-07 sigue pendiente se dice explícitamente, con el nombre de la tarea, en vez
de describir el diseño como si estuviera entregado.

---

## 1. Reintentos

`packages/shared-llm/src/shared_llm/retry.py` es la política ÚNICA. Los providers
no reintentan: traducen una llamada HTTP en errores tipados y nada más (ADR 0021),
y **el consumidor** decide si vuelve a pagar el mismo prompt.

|               |                                                                                   |
| ------------- | --------------------------------------------------------------------------------- |
| Intentos      | 3 en total (1 llamada + 2 reintentos)                                             |
| Espera        | exponencial desde 1 s, techo 30 s, con jitter                                     |
| `Retry-After` | se respeta si el proveedor lo manda (segundos o fecha HTTP), acotado por el techo |

Se reintenta:

- `RateLimitError` (429);
- fallos de transporte — reset de conexión, read timeout — que llegan como
  `ProviderError(transient=True)`;
- `5xx`, y además `408`, `409`, `425`, `529`.

**No** se reintenta, a propósito:

- `AuthError` y cualquier otro 4xx. Un token revocado no se vuelve válido
  preguntando otra vez; reintentar solo quema presupuesto y retrasa el error real.
- Un 200 con cuerpo malformado. Un gateway que rompe su propio contrato lo rompe
  igual en el reintento y se paga dos veces.

Consumidores que lo aplican:

- `apps/api-server/src/api_server/assistant/llm.py` → `with_retries(...)`.
- `docker/agent-runtimes/agent-runtime/agent_runtime/providers.py` →
  `_run_with_retry(...)`, que además envuelve cada intento en `asyncio.wait_for`
  (un proveedor colgado se convierte en `ProviderTimeout` tipado) y loguea cada
  reintento con provider / intento / causa / espera.

**El coste de un reintento es real**: un timeout DESPUÉS de que el proveedor haya
procesado el prompt factura dos veces. Por eso el presupuesto es corto y cada
reintento se reporta (`on_retry` / log), para que el gasto sea visible y no oculto.

## 2. Event loop del sandbox

El lazo del agente es síncrono (LangGraph) y los providers son async, así que el
runtime puentea con `asyncio.run` **por llamada** (`providers._run`). Cada
`asyncio.run` crea un loop y lo cierra, así que un `httpx.AsyncClient` guardado en
el constructor del provider quedaría atado al loop de la primera llamada y la
segunda estallaría con `RuntimeError: Event loop is closed`.

La solución adoptada es la alternativa que el plan dejaba abierta (no el
`asyncio.Runner` persistente): **los providers crean su cliente por llamada**
cuando no se les inyecta uno. `_ProviderModelClient.close()` invoca
`aclose()` del provider al terminar el run, también cruzando el puente.

Si alguien vuelve a inyectar un cliente compartido, el patrón roto reaparece:
`docker/agent-runtimes/agent-runtime/tests/test_event_loop_reuse.py` lo reproduce
contra un servidor keep-alive real y comprueba que rompe, de modo que las demás
aserciones del fichero no puedan pasar vacíamente.

## 3. Contrato de streaming

`stream()` emite `StreamChunk`s y **el último lleva `done=True`** con el `usage` y
los `tool_calls` acumulados. Para los tres providers OpenAI-compat eso exige
`stream_options: {"include_usage": true}` en el body, que ya se manda
(`ollama.py`, `azure_foundry.py`, `copilot.py`); el parseo del delta de
`tool_calls` y del chunk final de `usage` — donde `choices` viene VACÍO — vive una
sola vez en `_openai_compat.py`. `ClaudeAgentProvider.stream()` ya cumplía.

Errores dentro del stream: con un status ≥ 400 se hace `await resp.aread()`
**antes** de clasificar, de modo que salga el `AuthError`/`RateLimitError` tipado
en vez del `httpx.ResponseNotRead` opaco que salía al tocar `.text` sobre una
respuesta en streaming sin leer.

La paridad `complete()` ↔ `stream()` (mismos tokens, mismos tool_calls) está
fijada en `packages/shared-llm/tests/test_stream_usage_parity.py`.

## 4. Capacidades por kind

| kind            | Endpoint                       | Credencial (campo en Vault)                          | tools                  | streaming | Coste reportado por el proveedor |
| --------------- | ------------------------------ | ---------------------------------------------------- | ---------------------- | --------- | -------------------------------- |
| `claude_sdk`    | — (CLI/suscripción)            | `api_key` o `oauth_token`                            | **no** en `complete()` | sí        | **sí**                           |
| `copilot`       | fijo (`api.githubcopilot.com`) | `oauth_token` (JWT corto minteado por el provider)   | sí                     | sí        | no                               |
| `azure_foundry` | APIM (`apim_base_url`)         | `api_key` → `subscription_key`, **o** `bearer_token` | sí                     | sí        | solo con policy en APIM          |
| `ollama`        | `base_url` (local o cloud)     | `bearer_token` → `api_key` (local: ninguna)          | sí                     | sí        | no                               |

Limitación de `claude_sdk` que hay que conocer: `ClaudeAgentProvider.complete()`
ignora `tools`/`max_tokens`/`temperature`, y en el runtime
`ClaudeSDKModelClient.decide()` devuelve FINISH. Un agente `claude_sdk` con
herramientas asignadas puede "terminar" la tarea sin actuar. El cableado de
`run_agent()` con tools es **decisión de producto** y sigue pendiente
(`task_prod07_09`); mientras no esté, no asignes herramientas a un agente cuyo
kind sea `claude_sdk`.

La tabla de credenciales es **dato en un solo sitio**:
`packages/shared-llm/src/shared_llm/credential_fields.py`
(`CREDENTIAL_FIELDS` + `overlay_credentials`). La consumen el runtime y el worker;
el factory del api-server mantiene sus `_build_*` y un test de paridad los ata a
la tabla (`tests/unit/test_credential_fields_parity.py`). El desajuste de nombres
es real y por eso la tabla guarda PARES: la `api_key` de Vault es la
`subscription_key` de Azure, y el `bearer_token` de una fila Ollama es la
`api_key` del provider.

## 5. Resolución del modelo y comportamiento ante Vault caído

El sandbox no tiene BD ni Vault (principio #2), así que el worker resuelve el
`model_config` a un spec ejecutable ANTES de lanzar el contenedor
(`apps/workers/src/workers/model_resolver.py`, ADR 0057 F1). Dos caminos: por
`provider_id` (la fila EXACTA) y, si no, por `provider` (el kind → fila activa más
nueva).

Nada degrada en silencio. La ejecución termina `failed` **sin lanzar el
contenedor**, con dos `abort_code` distintos:

| `abort_code`        | Cuándo                                                                                                     | Dónde mirar                        |
| ------------------- | ---------------------------------------------------------------------------------------------------------- | ---------------------------------- |
| `model_unresolved`  | `model_config` sin provider/model resoluble, o ningún `llm_providers` activo de ese kind                   | el catálogo / la config del agente |
| `vault_unavailable` | la fila TIENE `secret_vault_path` y el kind usa credencial, pero Vault no la sirve (tras **un reintento**) | Vault                              |

La distinción no es cosmética: antes ambos casos acababan en un 401 dentro del
sandbox que atribuía la culpa al proveedor. El log diferencia «Vault transport
error» de «sin credencial configurada».

Una fila **sin** `secret_vault_path` (ollama local) se resuelve sin credencial:
ahí no hay nada que leer, así que no hay nada que pueda fallar.

Ojo con la asimetría deliberada: `resolve_provider_config(strict_vault=...)`
degrada para el ASISTENTE (donde el fallback de env existe y el chat debe seguir)
y aborta para el DISPATCH (donde el fallback no existe). Es el mismo código con
dos contratos porque las dos premisas son ciertas.

## 6. Test de conexión («probar conexión»)

`POST /admin/llm-providers/{id}/test` → `api_server/llm_providers/liveness.py`.
Devuelve un estado clasificado (`ok`, `auth_error`, `connection_error`,
`config_error`, `upstream_error`) y un `detail` que por construcción **no**
contiene el secreto.

| kind            | Qué hace el probe                                                                                                                            |
| --------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `ollama`        | `GET {base_url}/models` (OpenAI-compat), bearer opcional                                                                                     |
| `azure_foundry` | `GET` a APIM con `Ocp-Apim-Subscription-Key` **o** `Authorization: Bearer` si la fila es bearer-only                                         |
| `copilot`       | **mintea el JWT** contra `api.github.com/copilot_internal/v2/token`, igual que el provider en cada run — es lo que detecta un token REVOCADO |
| `claude_sdk`    | comprueba que la credencial está en Vault y lo DICE en el detail (`no live call for this kind`): verde más débil, no connection verificada   |

Los dos primeros comportamientos son de `task_prod07_11`: copilot solo miraba que
el `oauth_token` existiera en Vault (un token revocado sigue ahí → verde falso), y
azure rechazaba las configs bearer-only que el factory construye sin problema
(rojo falso). Un probe que no puede decir NO no es un probe; uno que dice NO a lo
que funciona enseña a ignorarlo.

## 7. Contabilidad de costes — estado real

Lo que SÍ está:

- cada step `model_call` registra el **kind del proveedor**
  (`agent_runtime/steps.py::model_call_step`, `provider=`), sin el cual
  `snapshot_execution_prices` buscaba en el catálogo con `provider=""` y no casaba
  nunca;
- `snapshot_execution_prices` congela un `price_snapshot` por llamada con los
  precios unitarios vigentes y un `cost_usd` calculado, y un precio ausente se
  registra como _unknown_ tipado (`available=False`), **nunca** como un cero falso.

Lo que NO está, y por eso los costes de Ollama/Copilot/Azure pueden seguir
sumando 0 en `executions.total_cost_usd`:

- **`task_prod07_13`** — persistir la suma de los snapshots cuando el runtime
  reporta 0, y que `budgets/consumption` consuma esa fuente. Requiere decidir
  entre columna nueva `cost_estimated_usd` (recomendada: conserva la trazabilidad
  runtime-reportado vs estimado) u override de `total_cost_usd`, y una migración
  Alembic si se elige la columna.
- **`task_prod07_14`** — el test e2e que asserta coste > 0 consumido por budgets.

`claude_sdk` es hoy el único kind que reporta coste real, así que cualquier cambio
en `finalize_execution` necesita el test de no-regresión de ese camino.

## 8. Lo que queda de prod-07 (para no dar por hecho lo que no está)

| Tarea                   | Estado                                                                                                        |
| ----------------------- | ------------------------------------------------------------------------------------------------------------- |
| `task_prod07_05`        | `get_assistant_model` sigue devolviendo el provider sin `finally: aclose()` → cada chat fuga un `AsyncClient` |
| `task_prod07_09`        | claude_sdk + tools: falta el bloqueo/aviso en validación y el ADR de decisión                                 |
| `task_prod07_10`        | la credencial sigue viajando dentro de `AGENT_TASK_SPEC` (env del contenedor), no en un mount tmpfs read-only |
| `task_prod07_13` / `14` | ver §7                                                                                                        |

## Relacionado

- [pricing.md](./pricing.md) — catálogo de precios, sincronización y snapshot.
- ADR 0021 — catálogo cerrado de proveedores y capa `shared-llm`.
- ADR 0057 — resolución del modelo en el worker (F1) y sus fases pendientes.
- ADR 0028 — `llm_providers` platform-global sin RLS y su credencial en Vault.
- [../03-guides/gotchas/](../03-guides/gotchas/) — trampas del toolchain.
