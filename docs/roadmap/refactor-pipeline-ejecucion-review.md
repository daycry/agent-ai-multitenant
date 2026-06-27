---
title: "Refactor limpio del pipeline ejecución + self-review (sin parches)"
status: pending_approval
date: 2026-06-27
authors: [claude-opus, operador]
kind: design-spec
related_adr: ["0086", "0013", "0021", "0050"]
blocking_plan: null
docs_language: es
---

# Refactor limpio del pipeline ejecución + self-review

> **Estado: `pending_approval`.** Este es el **spec de diseño** (no el plan de
> implementación). Surge del análisis multi-agente del 2026-06-27 sobre la
> dispersión de parches acumulados en el agent-runtime, y de tres decisiones de
> producto del operador. El plan de implementación bite-sized se escribe aparte
> tras aprobar este spec.

## 1. Por qué (diagnóstico honesto)

El pipeline ejecución→self-review del `agent-runtime` acumuló, en una sola
sesión de estabilización, una pila de parches reactivos:

1. **Nudge anti-research** (`graph.py:60-107`): `_RESEARCH_TOOLS`/`_PRODUCING_TOOLS`/
   `_RESEARCH_STREAK_LIMIT=5` + `_research_nudge` — heurística de comportamiento
   del LLM en strings, acoplada a nombres de tools, umbral mágico.
2. **FINISH-nudge / `has_produced`** (`graph.py:93-97,186-188,362-363`): flag
   mutable en el objeto del loop (no en `AgentState`), latch que nunca se
   resetea, intenta forzar un FINISH que el modelo no decidió.
3. **`_parse_verdict` + `_REVIEW_FAIL_MARKERS`** (`providers.py:184-227`):
   _prose-sniffing_ del veredicto del review, bilingüe, mantenido a mano. Su
   propio docstring lo admite frágil. Historia: marcadores que **colisionaban
   con vocabulario de dominio** (auth/JWT: "el filtro **rechaza** tokens",
   "maneja el **fallo**") provocaron false-negatives que abortaron la tarea JWT
   por `max_review_retries_exceeded` mientras specs/migraciones pasaban.
4. **Triple parseo del veredicto co-igual**: tool-call `submit_verdict` (ADR 0086) **+** `_extract_json` **+** prose-markers, sin orden canónico, todos con
   **default a PASS** (`bool(args.get('passed', True))` en `:261`, `not
is_explicit_fail` en `:227`) → el gate de calidad es casi inerte.

**Hechos verificados en código que reencuadran el problema:**

- El review **ya es tool-based en los 4 providers HOY**: `providers.py:328` y
  `:482` ya pasan `tools=[_SUBMIT_VERDICT_TOOL]`; `_review_from:268` ya prefiere
  la tool-call. **No hace falta inventar contrato nuevo para arreglar el review.**
- `FINISH` se define por la **AUSENCIA** de tool-call (`_decision_from`,
  `providers.py:236`: `if resp.tool_calls → ACT else → FINISH`).
- `claude_sdk` devuelve `content=""` cuando dispara **cualquier** tool
  (`claude_agent.py:383-395`); no hay "args estructurados Y prosa en el mismo
  turno", y **no hay `tool_choice`** que compela una tool (`can_use_tool` solo
  intercepta una llamada que el modelo _elige_ hacer).
- El blob de texto `DESENLACE / === ficheros === / === tools ===` que el
  operador observa **no lo produce el código** (grep: 0 productores) — es prosa
  libre del modelo. **Ningún parser downstream lo consume**: la UI
  (`executions/[id]/page.tsx:55`) solo lo tipa y no lo pinta; el judge de evals y
  el human-inbox lo tratan como texto opaco.

`shared-llm` (ADR 0021) **no expone** `response_format`/`response_schema`/
`tool_choice`; los 3 providers HTTP los dejarían pasar vía `**kwargs` (sin
contrato), pero `claude_sdk` no tiene equivalente nativo.

## 2. Decisiones del operador (2026-06-27)

| #   | Decisión                                                                       | Consecuencia de diseño                                                                                             |
| --- | ------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------ |
| D1  | **Self-review AUTORITATIVO** (puede certificar/bloquear la tarea por sí mismo) | Polaridad **fail-closed**; el veredicto debe ser fiable; rúbrica más rica que el "pasa/no pasa simple" de ADR 0013 |
| D2  | **Finish estructurado (C+D)**: `submit_result` + render en UI                  | Hay **consumidor** (UI) → desaparece el bloqueo YAGNI de ADR 0086 Fase 2                                           |
| D3  | Veredicto **inconcluso → escalar a humano**                                    | Ni pasa en silencio ni aborta en seco: deriva a validación humana (infra `human-inbox` ya existe)                  |

D3 es la pieza que hace que D1 (autoritativo) **no reabra** los
`max_review_retries_exceeded`: la autoridad del self-review termina en el humano,
no en un abort ciego — coherente con CLAUDE.md ppio 7.

## 3. Diseño objetivo

### 3.1 Contrato de FINISH (estructurado, provider-aware)

Tool nueva, advertida en `decide()`:

```
submit_result(
  status:  enum["success", "failed", "partial"]   # REQUIRED — input/pista, NO auto-veredicto
  summary: str                                     # REQUIRED — resumen del desenlace
)
```

> **Sin `criteria_met[]`** (YAGNI: el reviewer ya tiene `task.acceptance_criteria`;
> ningún consumidor lo leería). `status` es vocabulario cerrado; `blocked`/
> `needs_human` NO son status del agente — el escalado lo decide el review (D3).

**Comportamiento por provider (clave del "sin parches"):**

- **HTTP (azure / copilot / ollama)**: `submit_result` se advierte y, opcional,
  se fuerza vía `tool_choice`. Llega pre-parseada (`parse_chat_completion`), cero
  string-matching. `status+summary` estructurados.
- **`claude_sdk`**: **NO se fuerza** `submit_result` (su `content=""` al disparar
  tool **tiraría el output rico** que el operador observa y consume). Finaliza en
  **prosa** (lo que el CLI hace bien); el _wrap_ sintetiza
  `FinishResult(status="success", summary=<prosa>)`. El reviewer juzga el output
  contra los criterios igual — el `status` es pista, no veredicto.

**Routing (`_decision_from` reescrito, por NOMBRE de tool):**

```
if tool_call.name == "submit_result":  → FINISH (output = summary, status capturado)
elif tool_call:                        → ACT
else:                                  → FINISH (wrap de prosa: status="success", summary=content)
```

Esto **es** un cambio en la rama más load-bearing del loop converso — por eso va
**gated, secuenciado, y con tests que lo pinen** (no es "aditivo", como creía el
diseño inicial; el código lo desmiente).

### 3.2 Contrato de REVIEW (autoritativo, escalera de confianza)

`review()` sigue siendo `complete()` con `tools=[_SUBMIT_VERDICT_TOOL]`. Veredicto:
`submit_verdict(passed: bool REQUIRED, feedback: str)`.

`_review_from` colapsa a **un orden canónico explícito**:

```
1) _verdict_from_tool_calls   # tool-call estructurada (preferente)
2) _extract_json              # JSON embebido en prosa
3) _parse_verdict + markers   # ÚNICO prose-sniffing, último recurso ETIQUETADO ADR 0086
```

**Polaridad fail-CLOSED (D1)** — distinta del default-a-PASS actual:

- `passed == true` (fiable) → **DONE**.
- `passed == false` (rechazo explícito) → feedback → **retry** hasta
  `max_review_retries` (ADR 0013, límite duro plataforma).
- retries agotados → **escalar a humano** (D3), NO abort ciego.
- **veredicto inconcluso** (sin tool-call, sin JSON, prosa sin señal clara, o
  args inválidos contra el schema) → **escalar a humano** (D3). NO pasar en
  silencio (sería fail-open, rechazado), NO abortar (regresión).

**Validación de args** (cubre el riesgo de ollama-modelo-pequeño): si
`submit_verdict`/`submit_result` llega con args que no validan contra el schema
(`status` fuera de enum, `passed` ausente, `summary` vacío) → tratar como
**inconcluso** → escalar. Un `FinishResult`/veredicto "confiadamente erróneo" no
debe colarse.

### 3.3 Escalado a humano (D3) — RESUELTO

Decisión (operador: "reusar `pending_human_validation`"; ajuste honesto):
**`pending_human_validation` es un estado de PLAN, no de tarea** (CLAUDE.md ppio 7;
`plan_state_machine`). A nivel de TAREA no existe — los estados humanos de tarea son
`awaiting_human_approval` (engine de aprobación), `assigned_to_human` (Plan 16) y
`blocked`. Por tanto el escalado **reusa la vía existente `blocked` + bandeja humana**:

- El runtime emite `status = needs_human_review` (`agent_runtime.state`, junto a
  `awaiting_human_approval`) con `abort_code` = `review_inconclusive` o
  `max_review_retries_exhausted` (el motivo, informativo). El `output` (prosa rica
  del deliverable) ya está puesto por `finalize` → se preserva.
- El worker (`transition_task_after_run`) mapea `needs_human_review` → tarea
  `blocked` (transición legal desde `in_progress`). El **execution row**
  (status + abort_code) distingue "escalado para validar" de un fallo duro, para que
  la bandeja humana (`human-inbox`, evento `task_blocked`) y el visor lo surfaceen.
- El `pending_human_validation` de **plan** queda intacto (lo dispara el orchestrator
  cuando las tareas llegan a in_review/done). Un estado de tarea LITERAL
  `pending_human_validation` queda fuera de alcance (sería migración de enum + edges +
  UI); se puede añadir como fase aparte si se decide.

Esto mantiene el blast-radius mínimo y respeta ppio 7. El espejo arquitectónico es el
patrón `awaiting_human_approval` (runtime emite status → worker lo mapea a estado
humano de tarea), ya probado.

## 4. Qué se elimina y qué se conserva

**Parches eliminados:**

- El triple parseo del veredicto como ramas **co-iguales** sin orden → orden
  canónico documentado.
- `_REVIEW_FAIL_MARKERS` como estrategia de parseo **co-igual** → degradado a
  único safety-net de último recurso, etiquetado, alcanzable solo si faltan (1) y (2).
- El **default-a-PASS** en ambos caminos (`:227`, `:261`) → fail-closed + inconcluso→escalar.
- La narrativa "el verdict depende de que el modelo recuerde la tool" → el review
  ya es tool-based; se documenta.
- El **FINISH-nudge contradictorio**: el nudge "termina con prosa y SIN tool-call"
  (`graph.py:96-107`) se **neutraliza** ("produce el resultado y finaliza") para
  no chocar con `submit_result` (C0, antes de C1).

**Redes de seguridad conservadas (invariantes, NO deuda):**

- **Fallback tolerante de prosa** para el verdict: KEPT como último recurso,
  documentado ADR 0086. El CLI puede degradar; nunca se borra como dead-code.
- El **fix de marcadores conservadores** ya desplegado (`providers.py:184-227`):
  se mantiene tal cual; los tests del postmortem (`test_auth_domain_prose_passes`)
  son la guardia anti-regresión.
- `_extract_json` dentro de la única rama de fallback.
- **NO se tocan** en este esfuerzo: la topología de 8 nodos, la duplicación de
  las dos clases cliente, la autoridad doble de timeouts, la aritmética de
  `SafeguardTracker`/`recursion_limit`, el perfil de seguridad del contenedor
  (cap-drop ALL, rootfs read-only). Blast-radius mínimo.

## 5. Plan por fases (resumen — el detalle bite-sized va en el plan)

Cada fase es independiente, testeable y ordenada para que **nunca coexistan dos
protocolos de finish**.

| Fase   | Alcance                                                                                                                                                  | Toca                                                                     | Riesgo               |
| ------ | -------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ | -------------------- |
| **A**  | `_review_from` orden canónico + polaridad fail-closed + caso inconcluso. Cero `shared-llm`.                                                              | `providers.py` (camino `review()`, que **nunca** pasa por decide/FINISH) | **Bajo**             |
| **A2** | Semántica autoritativa: inconcluso/retries-agotados → **escalar a humano** (no abort).                                                                   | `graph.py self_review`, wiring a `task_state_machine`/human-inbox        | Medio                |
| **B**  | Forzar `submit_verdict` solo en el adaptador `claude_sdk` (autoritativo exige veredicto fiable).                                                         | `ClaudeSDKModelClient.review()`                                          | Bajo-medio           |
| **C0** | Unificar/neutralizar el nudge de finish **antes** de tocar el routing.                                                                                   | `graph.py` nudge                                                         | Bajo                 |
| **C1** | `_decision_from` enruta por nombre (`submit_result→FINISH`) + tests pin.                                                                                 | `providers.py:236`                                                       | **Medio-alto**       |
| **C2** | Tool `submit_result(status,summary)` advertida en `decide()` **solo HTTP**; `claude_sdk` finaliza en prosa + wrap. `FinishResult` capturado en finalize. | `providers.py`, `model.py`, `graph.py finalize`                          | Medio-alto           |
| **C3** | El reviewer consume `status+summary` como input/pista.                                                                                                   | `providers.py _review_messages`                                          | Bajo                 |
| **D**  | UI: pintar `Execution.output` (ya se fetchea) + badge de `status`.                                                                                       | `admin-panel executions/[id]/page.tsx`                                   | Bajo (solo frontend) |

**Validación de cada fase = test automático verde**, según protocolo CLAUDE.md.
La suite `test_review_verdict.py` (incl. postmortem auth/JWT) es guardia
anti-regresión transversal y debe seguir verde tras A.

## 6. Impacto en ADRs

- **ADR 0086**: actualizar — registrar que la sección "Resultado final" en prosa
  se **rechaza como contrato primario** (recrea la colisión `_REVIEW_FAIL_MARKERS`)
  y se reposiciona como **forma del fallback**; declarar el review ya tool-based en
  los 4 providers; des-diferir la Fase 2 (`submit_result`) ahora que hay consumidor
  (D2); documentar el comportamiento por-provider de `submit_result`.
- **ADR nuevo (¿0087?) o ampliación de 0013**: **self-review autoritativo +
  escalado a humano**. Supera explícitamente el "pasa/no pasa simple" que 0013
  dejó para "una iteración posterior" (0013 §Consecuencias). Debe respetar
  `max_review_retries` como límite duro de plataforma (0013) y CLAUDE.md ppio 7
  (el humano es el gate último).
- **ADR 0021**: sin cambios. NO se añade `response_schema` genérico al Protocol
  (over-engineering: el review ya viaja por `tools`; forzar es local al adaptador).

## 7. Preguntas abiertas (a cerrar en el plan, no bloquean el spec)

1. ~~Estado/transición exacto del escalado humano.~~ **RESUELTO** (§3.3): tarea →
   `blocked` + `execution.status=needs_human_review`, reusando la bandeja humana;
   `pending_human_validation` es de plan, no de tarea.
2. ¿Forzar `submit_verdict`/`submit_result` por `tool_choice` en HTTP, o solo
   advertir? → medir en deploy; por defecto advertir (ya funciona) + forzar solo
   donde los datos lo justifiquen.
3. `status` del agente: ¿`success|failed|partial` basta, o se quiere `blocked`?
   → propuesta: bastan los 3; `blocked` es decisión del review (escalado), no del agente.

## 8. Honestidad sobre alcance

- **Aporta valor real**: Fase A (colapso+etiquetado del verdict, casi ya en
  código), la polaridad autoritativa con escalado humano (D1+D3), y el finish
  estructurado **ahora que hay consumidor** (D2+D).
- **Habría sido over-engineering** (y se descarta): `response_schema` genérico en
  `shared-llm`, `criteria_met[]`, y `submit_result` sin la UI comprometida. El
  operador comprometió la UI (D2), así que C+D dejan de ser YAGNI.
- **El riesgo real** está en C1/C2 (routing del FINISH). Por eso van gated,
  secuenciados tras C0, y con tests que pinen `submit_result→FINISH`,
  `prosa→FINISH (wrap)`, y `act() nunca recibe submit_result`.

## 9. Estado de implementación

- **Fase A — HECHA** (commit `f6b1a94`): `_review_from` orden canónico, verdict
  3-estados, `_REVIEW_PASS_MARKERS` conservadores, fail-closed. Tests verdes.
- **Fase A2 — HECHA** (`f6b1a94`): `STATUS_NEEDS_HUMAN_REVIEW`; `self_review`
  escala (inconcluso → directo; retries-agotados → escalar, no abort); worker
  mapea `needs_human_review` → `blocked`. Tests grafo + integración.
- **Fase B — HECHA (ajustada)**: forzar vía `tool_choice` NO es implementable en
  el SDK (`can_use_tool` intercepta, no compele). `submit_verdict` ya se advierte
  en los 4 providers y queda **verificada end-to-end** por `test_claude_sdk_review`
  (harvest deny+interrupt + fallback 3-estados a través del provider real). La red
  A2 cubre la degradación a prosa.
- **Fase C0 — HECHA** (`bcdd9cb`): nudge de finish provider-neutral (sin "NO tool call").
- **Fase C1 — HECHA** (`bcdd9cb`): `_decision_from` enruta por nombre (`submit_result→FINISH`).
- **Fase C2 — HECHA** (`bcdd9cb` runtime + `77f37fe` persistencia): tool
  `submit_result(status,summary)` en HTTP, prosa+wrap en claude_sdk;
  `finish_status` propagado a `ExecutionResult` → columna `executions.finish_status`
  (migración 0100, reversible) + `ExecutionStatus.NEEDS_HUMAN_REVIEW`; API expone el campo.
- **Fase C3 — HECHA** (`bcdd9cb`): `_review_messages` incluye `acceptance_criteria` + el
  `finish_status` como pista.
- **Fase D — HECHA** (`77f37fe`): la página de detalle del run pinta el `output` +
  badge "Resultado del agente" + variante `needs_human_review`.
- **ADRs — HECHO** (`9e5501b`): ADR 0087 (nuevo) + ADR 0086 (actualización) + changelog.

### Verificación

- agent-runtime: 54 tests verdes. Repo unit: 1670 passed (el único rojo,
  `test_user_role_values`, es **preexistente** y ajeno — verificado en el commit base).
- admin-panel: `tsc --noEmit` limpio, vitest verde, prettier OK.
- Migración 0100: SQL offline válido (`ALTER TABLE executions ADD COLUMN finish_status
VARCHAR(16)` ↔ `DROP COLUMN`), reversible.

### Deploy (paso del operador — flujo de tags propio `:manuals`, sin auto-migrate)

1. Rebuild `agent-runtime` **WITH_CLAUDE=1** (desde PowerShell, gotcha de `--build-arg`),
   `api-server`, `workers`, `admin-panel`.
2. Aplicar migración: `docker compose run --rm api-server alembic upgrade head` (o el
   flujo equivalente que aplica las migraciones en este stack).
3. Recreate de los servicios. Smoke: re-lanzar la tarea JWT y confirmar que el run
   llega a `done` (review por tool) o, si el veredicto es inconcluso, a
   `needs_human_review` → tarea `blocked` en la bandeja.
