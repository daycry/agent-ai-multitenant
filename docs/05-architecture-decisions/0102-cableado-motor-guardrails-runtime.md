---
adr: "0102"
title: Cableado del motor de guardrails en la ejecución de agentes (4 hooks) con slice mínimo post_tool en modo LOG, transporte por task spec/result envelope y política de fallo por check
status: proposed
date: 2026-07-05
deciders: operador (pendiente)
phase: auditoria-plataforma-2026-07-03
related: ["0035", "0016", "0020"]
docs_language: es
---

# ADR 0102 — Cableado del motor de guardrails en el bucle del agente

> **Estado: `proposed`.** Cierra la OTRA mitad del P0 de guardrails (hallazgo
> g1): el motor `GuardrailPipeline` (ADR 0035) existe, está testeado y sólo se
> instancia en `api_server/guardrails/planning.py`; el bucle del agente
> sandboxed nunca lo invoca, así que las salidas de MCP/HTTP/RAG reentran
> **crudas** al contexto (inyección indirecta sin defensa). El gate humano (g6)
> ya está desplegado. Este ADR decide **cómo** se cablea el motor en el runtime,
> coordinándose con —sin duplicar— el Plan `prod-03` (tareas
> `task_prod03_09/10/11/12/13`).

## Contexto

1. **El motor es puro y ya existe.** `GuardrailPipeline`
   (`packages/shared-guardrails/src/shared_guardrails/pipeline.py:40`) se
   construye desde una `PipelineConfig` declarativa
   (`config.py:79`) y ejecuta, en orden, los guardrails de un hook contra un
   `GuardrailContext` (`types.py:71`), devolviendo un `PipelineDecision`
   (`types.py:157`) con la acción decisiva por precedencia
   `block > escalate > retry > transform > redact > warn` (`types.py:191`). Es
   **síncrono** y **sin I/O**: sólo reporta la decisión, no aplica efectos
   (`pipeline.py:10-11`). Los 4 hook points son `pre_llm / post_llm / pre_tool /
post_tool` (`types.py:28-30`). El check `prompt_injection`
   (`checks/prompt_injection.py:328`) ya escanea `pre_tool` mirando además los
   `tool_args` (`checks/prompt_injection.py:377`) y sugiere `block` (o `warn` en
   `learning_mode`, `:363`). Importar el paquete `shared_guardrails` auto-registra
   todos los tipos en `default_registry` (`__init__.py:19-25`,
   `checks/__init__.py:19-31`).

2. **Nadie lo cablea en el runtime.** El bucle
   (`docker/agent-runtimes/agent-runtime/agent_runtime/graph.py`) llama al LLM en
   el nodo `plan` (`graph.py:782` `self.deps.model.decide(...)`) y a las tools en
   el nodo `act` (`graph.py:892` `self.deps.tools.call(tool, args)`), y **funde la
   observación cruda** en el contexto en `observe` (`graph.py:915`
   `context = {"role": "observation", **observation}`) sin pasar por ningún
   guardrail. `apps/workers` no tiene ni una referencia a `guardrail`.

3. **El runtime es sandboxed (sin DB, sin Vault).** Todo lo que necesita viaja en
   el task spec (`AGENT_TASK_SPEC`), mismo canal por el que ya llegan
   `approval_policy`, `budgets`, `allowed_commands`, etc.
   (`__main__.py:457-462`, `execution.py:_agent_spec:322-452`). El resultado
   vuelve al worker en el envelope `execution.finished`
   (`__main__.py:615`, consumido en `execution.py:1390-1391` y ensamblado en
   `_assemble_result:597`). La persistencia tenant-scoped (`guardrail_events`,
   migración 0052) sólo puede hacerla el worker, que sí tiene sesión RLS
   (`record_guardrail_event` / `record_pipeline_decision` en
   `api_server/guardrails/events.py:155,232`).

4. **La imagen `agent-runtime` NO lleva `shared-guardrails`.** El Dockerfile
   copia e instala `shared-llm`, `shared-mcp` y `shared-domain`
   (`Dockerfile:44-64`) pero no `shared-guardrails`, y `pyproject.toml` no lo
   declara como dependencia (`pyproject.toml:6-37`). Es prerequisito.

5. **Coordinación con prod-03 (no duplicar).** `prod-03` ya especifica la
   política de fallo (`task_prod03_09`), el seam async + límites de tamaño
   (`task_prod03_10`), el transporte de la config efectiva
   (`task_prod03_11`), el cableado en los 4 hooks (`task_prod03_12`) y la
   persistencia desde el worker (`task_prod03_13`). Este ADR fija las decisiones
   arquitectónicas transversales de esas tareas y define un **slice mínimo
   entregable antes** que el resto, para tener defensa de inyección indirecta en
   producción cuanto antes con riesgo cero de bloquear runs legítimos.

## Decisión propuesta

### D1 — Slice MÍNIMO viable primero: `post_tool` en modo LOG con el baseline `prompt_injection`

Se cablea **primero y solo** el hook `post_tool` sobre la salida de cada tool,
en **modo LOG (no bloqueante)**, con un baseline de plataforma que ejecuta
`prompt_injection` con `action: warn` / `learning_mode: true`. Este slice:

- **Cierra la inyección indirecta** (la mitad crítica de g1): las salidas de
  MCP/HTTP/RAG se escanean antes de reentrar al contexto en `observe`.
- **Es independiente del transporte de config** (`task_prod03_11`): el runtime
  construye el baseline en código (`build_pipeline` con fallback), así que se
  puede desplegar sin esperar a la tabla `guardrail_configs` ni al resolver.
- **No puede romper un run**: modo LOG ⇒ ninguna acción `block` se aplica; y el
  seam es best-effort (ver D6). Acumula los eventos disparados en el **result
  envelope** para que el worker los persista (D4). Esto materializa la
  mitigación de riesgo nº1 de prod-03 ("arrancar los checks no-locked en warn,
  observar una semana, subir a block con datos").

### D2 — Alcance total: 4 hooks + acciones + persistencia (fases siguientes)

El objetivo completo cablea los 4 hooks con enforcement real:
`pre_llm` antes de `model.decide` (`graph.py:782`), `post_llm` sobre la respuesta
del modelo (tras `graph.py:806`), `pre_tool` antes de `tools.call`
(`graph.py:892`) y `post_tool` sobre `result` antes de `observe` (`graph.py:915`).
Acción `block`/`escalate_to_human` ⇒ aborta el paso / parkea (reutilizando el
vocabulario de estados `STATUS_ABORTED` / `STATUS_AWAITING_APPROVAL` /
`STATUS_NEEDS_HUMAN_REVIEW`); `warn`/`redact`/`transform` ⇒ anota y continúa.
Config efectiva por (tenant, proyecto) resuelta por el worker y transportada
(D3), persistencia por proyecto vía `record_pipeline_decision` (D4).

### D3 — Transporte de la config efectiva por el task spec

El worker resuelve la config efectiva con `resolve_config(platform, tenant,
project)` (`layers.py:239`) y la serializa como el **dict declarativo**
(`{"guardrails": {hook: [spec,...]}}`) en un nuevo campo del spec
`spec["guardrails"]`, emitido en `_agent_spec` (`execution.py:322-452`) igual que
`approval_policy` (`execution.py:366-367`). Se añade `spec["guardrails_version"]`
para invalidación de caché. **Límite de tamaño: 64 KB** para el bloque
`guardrails` (poda/rechazo con log si se excede — riesgo nº5 de prod-03). El
runtime hace `GuardrailPipeline.from_dict(spec["guardrails"])`
(`pipeline.py:71`); si la clave falta (bare run / slice mínimo), cae al baseline
en código. **Requiere** un serializador `PipelineConfig → dict` en
`shared-guardrails` (hoy no existe `to_dict`), que este ADR asigna a
`task_prod03_11`.

### D4 — Transporte de eventos por el result envelope + persistencia en el worker

El runtime **acumula** las decisiones disparadas en un campo acumulador de
`AgentState` (`guardrail_events: Annotated[list[dict], operator.add]`, mismo
patrón que `steps`, `state.py:91`), que fluye a `ExecutionResult`
(`graph.py:558`) y a su `as_dict()` (`graph.py:578`). Cada evento se serializa
plano y **seguro para JSON**: `{hook_point, guardrail_type, severity, action,
detail, detail_payload}` (offsets/categorías/conteos; nunca el span crudo). El
worker lee `final_result["guardrail_events"]` (envelope `execution.finished`,
`execution.py:1390`), lo pasa por `_assemble_result` y, **tras
`finalize_execution`** (`execution.py:1542`), persiste cada evento con la sesión
RLS ya abierta y las refs reales (tenant_id, `project.id` de
`execution.py:1222`, `request.agent_id`, `execution_id`) vía
`record_guardrail_event` (`events.py:155`), que **enmascara** el payload con su
allowlist/denylist (`events.py:137-152`). El detalle sensible nunca llega a la BD
(invariante ADR 0035 §2). En el alcance total se reconstruye un `PipelineDecision`
por hook y se usa `record_pipeline_decision` (`events.py:232`) para además
evaluar alertas (`maybe_alert_after_events`) una sola vez por run.

### D5 — Política de fallo del motor: opción (c), `on_error: block|warn` por check

Se recomienda la **opción (c)** de la decisión clave 1 de prod-03: `on_error`
declarativo por check en la config (`config.py`), con **default `block`
(fail-closed) para los guardrails `locked` de plataforma**
(`pii`/`secret_leakage`/`prompt_injection`) y **`warn` (fail-open) para el
resto**. Se implementa envolviendo cada check en try/except dentro de
`GuardrailPipeline.run` (`pipeline.py:98-99`), convirtiendo la excepción en un
`GuardrailOutcome` triggered con esa acción; `available: False` (p. ej.
`content_safety` sin clasificador) se trata con la misma política. Justificación:
un fail-closed global bloquearía runs legítimos ante cualquier bug de un check no
crítico; un fail-open global dejaría caer silenciosamente los baselines de
seguridad obligatorios. La opción (c) preserva la garantía de que un control
**inviolable** (locked) nunca falla en abierto, sin castigar a los checks
opcionales. Este comportamiento es de `task_prod03_09`; en el **slice mínimo D1**
todo corre en LOG, así que la política de fallo no altera el flujo todavía (los
`block` no se aplican) pero los eventos de error del motor sí se registran.

### D6 — Seam de concurrencia y resiliencia; límites de tamaño

- **El runtime es síncrono.** El bucle LangGraph (`graph.stream`,
  `model.decide`, `tools.call`) es sync; **no hay event loop que bloquear**, así
  que `pipeline.run()` se llama **directamente** en los nodos, **sin
  `asyncio.to_thread`**. El seam async (`asyncio.to_thread` / `anyio`) de
  `task_prod03_10` aplica **sólo** a los hosts async del api-server
  (`planning.py:273,341`, ambos `async def`). Esta asimetría es deliberada y se
  documenta para no introducir un thread pool inútil en el sandbox.
- **Resiliencia (best-effort).** Un fallo del motor **nunca** rompe un run: el
  helper del runtime envuelve construcción y `run()` en try/except (como el
  recall de memoria, `__main__.py:142`); ante excepción registra un evento de
  observabilidad y continúa. En D1, incluso una decisión `block` se degrada a
  log (modo calibración).
- **Límites de tamaño de input.** Cada hook trunca el texto escaneado a un tope
  configurable (p. ej. 50 000 chars) antes de correr los checks, acotando el
  coste lineal-cuadrático del detector genérico de `secret_leakage`
  (`checks/secret_leakage.py`) y el regex de `prompt_injection`; se marca
  `truncated: true` en el payload. Cierra el riesgo nº2 (latencia) de prod-03 en
  el lado runtime.

### D7 — El pipeline se inyecta por `AgentDeps`, no por nodo

El pipeline construido se pasa al bucle vía `AgentDeps.guardrails`
(`graph.py:542`), igual que `approval` y `recall`, en lugar de reconstruirlo en
cada nodo. `run_task` lo construye una vez (`__main__.py:538`) y lo inyecta. Los
nodos leen `self.deps.guardrails`. Ventaja: una sola instancia por run
(los guardrails se materializan en el constructor del pipeline, `pipeline.py:54-59`),
testeable offline con un pipeline scripted, y `None` = sin guardrails
(backward-compat / bare run).

## Opciones evaluadas

**Política de fallo del motor (decisión clave 1 de prod-03):**

- (a) _fail-closed global_ — cualquier excepción de un check bloquea. Descartada:
  un bug en un check opcional (`factuality`, `cost_ceiling`) tumbaría runs
  legítimos; frágil ante los backends lazy `available: False`.
- (b) _fail-open global_ — cualquier excepción se ignora. Descartada: deja caer
  en silencio los baselines de seguridad obligatorios (viola el principio de
  controles inviolables de ADR 0035).
- (c) **`on_error: block|warn` por check, default block para locked, warn resto**
  — **recomendada**. Preserva la inviolabilidad de los locked sin castigar a los
  opcionales; homogéneo con `available: False`.

**Dónde acumular/transportar los eventos:**

- En `steps_log` (piggyback en los steps). Descartada como canal primario:
  mezcla observabilidad de timeline con datos que exigen enmascarado y
  attribution tenant-scoped; los steps se streamean sueltos y no son el sitio de
  la persistencia RLS. (Se emite además un step-resumen para el timeline, pero
  no es load-bearing.)
- En el **result envelope** (`ExecutionResult.guardrail_events`) — **elegida**:
  un único punto de recolección que el worker persiste transaccionalmente junto a
  `finalize_execution`, con las refs reales y el enmascarado del recorder.
- Un canal de stream nuevo. Descartada: duplica plumbing; el envelope ya existe.

**Cómo alimentar la config en el slice mínimo:**

- Esperar al transporte completo (`task_prod03_11`) antes de cablear nada.
  Descartada: retrasa la defensa de inyección indirecta (P0) tras toda la Fase C
  de prod-03.
- **Baseline en código con fallback** — elegida: el slice mínimo no depende de la
  tabla ni del resolver; cuando `spec["guardrails"]` llegue, gana sobre el
  baseline.

**Dónde instanciar el pipeline:** por-nodo vs. `AgentDeps` — elegido `AgentDeps`
(D7).

## Consecuencias

- La imagen `agent-runtime` pasa a depender de `shared-guardrails` (una path-dep
  más, pura Python; única transitiva nueva: PyYAML — `jsonschema` ya está). Sin
  paquetes de sistema nuevos, sin extras pesados (Presidio/guard-model siguen
  siendo opcionales y lazy, `shared-guardrails/pyproject.toml:18-44`).
- Tras D1, cada run de agente escanea las salidas de tool y **registra**
  intentos de inyección indirecta en `guardrail_events`; el dashboard de
  guardrails deja de mostrar sólo datos de tests. Sin cambio de comportamiento
  del run (LOG).
- El alcance total (D2) introduce enforcement: un `block`/`escalate` en un hook
  cambia el estado del run; esto exige la calibración (una semana en warn) antes
  de subir los checks a `block`, por eso el slice mínimo va primero.
- El runtime NO gana un event loop (D6): el seam es sync-nativo; el thread pool
  vive sólo en el api-server. Menos superficie de bug de concurrencia en el
  sandbox.
- Coordinación cerrada con prod-03: este ADR es el "ADR nuevo propuesto" de la
  decisión clave 1 (`task_prod03_09`) y fija el diseño de
  `task_prod03_10/11/12/13`. No reabre la reparación de validación humana (Fase A
  de prod-03), ya cubierta por g6.
- Actualiza el estado de ADR 0035 (§ "el motor no se invoca en flujos reales")
  y complementa ADR 0016/0020 (validación humana) sin modificarlos.

## Criterio de aceptación

1. **ADR aprobado por el operador** antes de mergear el enforcement (D2/D5).
2. **Imagen**: `import shared_guardrails` y `default_registry.is_registered("prompt_injection")`
   `True` dentro del contenedor `agent-runtime` reconstruido.
3. **Slice mínimo (D1)**: un test de integración con una tool stub que devuelve
   un payload con `"ignore previous instructions…"` produce ≥1 evento
   `prompt_injection` en `ExecutionResult.guardrail_events` con `hook_point =
post_tool`, el run termina `done` (no se bloquea), y el worker escribe la fila
   tenant-scoped en `guardrail_events` con `detail_payload` enmascarado (sin el
   span crudo).
4. **Resiliencia**: un pipeline que lanza excepción en `run()` no rompe el run
   (status inalterado, run completa) y registra el fallo del motor.
5. **Alcance total (D2)**: test que cablea los 4 hooks; un `block` en `post_tool`
   sobre una salida inyectada aborta/parkea el paso y queda registrado; la
   config efectiva viaja por `spec["guardrails"]` respetando el límite de 64 KB.
6. **Política de fallo (D5)**: test unitario de `on_error` — un check `locked`
   que lanza produce acción `block`; uno no-locked produce `warn`;
   `available: False` sigue la misma política.
7. Todos los tests con el `.venv` del repo en verde; entrada de changelog y
   `docs/04-reference/guardrails.md` actualizada (hooks cableados + política de
   fallo) al cierre de prod-03.
