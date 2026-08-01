---
title: Capa LLM — reintentos, streaming, capacidades por kind, costes y Vault
audience: backend-dev, devops, architect
phase: prod-07-fiabilidad-llm-costes
updated: 2026-08-01
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

| kind            | Endpoint                       | Credencial (campo en Vault)                          | tools             | streaming | Coste reportado por el proveedor |
| --------------- | ------------------------------ | ---------------------------------------------------- | ----------------- | --------- | -------------------------------- |
| `claude_sdk`    | — (CLI/suscripción)            | `api_key` o `oauth_token`                            | sí (no forzables) | sí        | **sí**                           |
| `copilot`       | fijo (`api.githubcopilot.com`) | `oauth_token` (JWT corto minteado por el provider)   | sí                | sí        | no                               |
| `azure_foundry` | APIM (`apim_base_url`)         | `api_key` → `subscription_key`, **o** `bearer_token` | sí                | sí        | solo con policy en APIM          |
| `ollama`        | `base_url` (local o cloud)     | `bearer_token` → `api_key` (local: ninguna)          | sí                | sí        | no                               |

> **Corregido el 2026-08-01.** Este párrafo decía que `claude_sdk` **no** soporta
> herramientas («`complete()` ignora `tools`», «`decide()` devuelve FINISH», «no
> asignes herramientas a un agente `claude_sdk`»). Era cierto cuando se escribió
> el hallazgo llm-4, en junio de 2026, y llevaba meses sin serlo.

Limitación de `claude_sdk` que hay que conocer, la de verdad: sus herramientas
**se median, no se compelen**. `ClaudeAgentProvider.complete()` anuncia los
schemas del host como un servidor **MCP in-process** e intercepta cada llamada
con `can_use_tool` (deny + interrupt), devolviéndola en
`CompletionResponse.tool_calls` para que la ejecute el host; el runtime
(`ClaudeSDKModelClient`) hereda `decide()` de la base y alcanza ACT igual que los
providers OpenAI-compat. Lo que **no** existe en el SDK es un `tool_choice`
forzado: no se puede obligar al modelo a llamar una tool concreta, y por eso
`_advertises_submit_result` y `_forces_verdict_choice` están a `False` y el
contrato de salida del review viaja como prosa + tag (ADR 0086/0087).

El ADR **0150** está `accepted` (firmado el 2026-08-01) con la **opción A**: se
mantiene el cableado. Con eso, las mitades (a) y (b) de `task_prod07_09` —
bloquear en validación la combinación `tools + claude_sdk` y anunciar la
limitación en la UI— quedan **retiradas por obsoletas**: implementarlas hoy
destruiría una capacidad entregada y en producción.

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

- y desde el **2026-08-01** (`task_prod07_13`), `executions.total_cost_usd`
  **deja de ser el 0 del runtime**. La precedencia la fija
  `_billable_cost_usd` (`api_server/db/execution_repo.py`), y tiene tres
  escalones, en este orden y no en otro:

  | #   | Fuente                                           | Cuándo se usa                                                        |
  | --- | ------------------------------------------------ | -------------------------------------------------------------------- |
  | 1   | lo que **reportó el runtime** (`usage.cost_usd`) | siempre que sea > 0 — es lo que el proveedor facturó de verdad       |
  | 2   | la **suma de los `price_snapshot` preciados**    | el runtime reportó 0 y el catálogo supo preciar al menos una llamada |
  | 3   | **0**                                            | el catálogo no supo preciar ninguna llamada                          |

  Se aplica en las **dos** vías de escritura, `finalize_execution` (la del
  worker) y `record_execution` (la de un paso); arreglar sólo una dejaba la otra
  facturando $0.

`claude_sdk` es hoy el único kind que reporta coste real, así que el escalón 1
existe sobre todo por él: pisarlo con una estimación sería una regresión, y hay
un test de no-regresión explícito
(`test_execution_cost_finalize.py::test_a_cost_reported_by_the_runtime_is_never_overwritten`).

**Dónde se lee la procedencia de la cifra.** No hay columna
`cost_estimated_usd` — se descartó a propósito (ver `task_prod07_13`): la
distinción «reportado» vs «estimado» ya vive **por llamada** en `steps_log`,
donde el `cost_usd` crudo del runtime sigue visible al lado de un
`price_snapshot` que nombra su `source` y su `price_id`. Una columna lo
duplicaría y obligaría a cada lector de la cifra facturable a aprender a hacer
coalesce. Por lo mismo, `budgets/consumption.py` no cambió: sigue sumando
`total_cost_usd`, que ahora es exacto.

**Lo que un `available=False` significa, y lo que no.** Un precio ausente NO se
convierte en 0 facturable por la puerta de atrás: si ninguna llamada del run se
pudo preciar, el coste se queda en 0 y el run queda marcado como no preciado en
sus snapshots. Es el mismo criterio de integridad del catálogo — «no lo sé»
nunca se transforma en una factura.

## 8. Lo que queda de prod-07 (para no dar por hecho lo que no está)

Actualizado el **2026-08-01**. Lo cerrado desde la revisión anterior:
`task_prod07_05` (la dependencia del asistente es async generator y cierra el
provider en su `finally`; el WS de voz cierra el suyo), `task_prod07_12` (el
casado kind→familia del catálogo tiene por fin test:
`test_execution_capture.py -k snapshot_provider`), **`task_prod07_09`** (el ADR
0150 se firmó `accepted` con la opción A) y **`task_prod07_13` + `14`** (la
contabilidad de costes de §7, con su e2e hasta budgets).

| Tarea            | Estado                                                                                                                                                                |
| ---------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `task_prod07_10` | la credencial sigue viajando dentro de `AGENT_TASK_SPEC` (env del contenedor), no en un mount tmpfs read-only                                                         |
| `task_prod07_15` | el Memorizer resuelve por `provider_id` (ADR 0082) pero `_default_llm_factory` sigue cayendo a `OllamaProvider` desde env, y no hay contador de fallos de destilación |

## Relacionado

- [pricing.md](./pricing.md) — catálogo de precios, sincronización y snapshot.
- ADR 0021 — catálogo cerrado de proveedores y capa `shared-llm`.
- ADR 0057 — resolución del modelo en el worker (F1) y sus fases pendientes.
- ADR 0028 — `llm_providers` platform-global sin RLS y su credencial en Vault.
- [../03-guides/gotchas/](../03-guides/gotchas/) — trampas del toolchain.
