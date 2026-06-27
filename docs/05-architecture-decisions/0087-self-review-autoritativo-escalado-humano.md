---
adr: "0087"
title: Self-review autoritativo de 3 estados con escalado a humano
status: accepted
date: 2026-06-27
deciders: operador, System Architect (claude-opus)
phase: refactor-pipeline-ejecucion-review
related: ["0086", "0013", "0021", "0050"]
supersedes_partial: ["0013"]
docs_language: es
---

# ADR 0087 — Self-review autoritativo de 3 estados con escalado a humano

## Contexto

El ADR 0013 dejó el self-review como un gate **simple "pasa / no pasa"** y anotó
explícitamente que "una rúbrica de revisión más rica queda para una iteración
posterior". Esta es esa iteración.

El ADR 0086 (Fase 1) hizo que el veredicto del self-review viajara como tool
(`submit_verdict`), con un fallback de prosa tolerante. Pero ese fallback era
**fail-OPEN**: la prosa ambigua pasaba por defecto (`_parse_verdict` devolvía
`passed=True` salvo marcador explícito de rechazo, y la rama de tool defaulteaba
`passed` a `True`). Resultado: el gate de calidad era casi inerte — podía
**aprobar output malo** — y, al revés, una mala elección de marcadores ya había
provocado el incendio auth/JWT (palabras de dominio leídas como rechazo).

El operador decidió tres cosas (2026-06-27):

- **D1** — el self-review debe ser **AUTORITATIVO** (puede certificar/bloquear la
  tarea por sí mismo), no meramente orientativo.
- **D2** — la salida de tarea pasa a ser **estructurada** (`submit_result`) con
  consumidor en la UI (ver ADR 0086, actualización PM).
- **D3** — cuando el veredicto **no es de fiar**, ni pasar en silencio ni abortar
  en seco: **escalar a un humano**.

## Decisión

### Veredicto de 3 estados, fail-CLOSED

`ReviewResponse` gana un campo `inconclusive`. El veredicto se resuelve en
**orden canónico** (`_review_from`):

1. tool-call `submit_verdict` (preferente);
2. JSON embebido (`_extract_json`);
3. prosa con marcadores **conservadores** (`_REVIEW_FAIL_MARKERS` /
   `_REVIEW_PASS_MARKERS`) — ÚNICO prose-sniffing, último recurso etiquetado.

Polaridad **fail-closed**: ni la prosa ambigua ni un `submit_verdict` con `passed`
ausente/malformado pasan en silencio. Tres salidas:

- `passed=True` → **DONE**.
- `passed=False` (rechazo explícito) → feedback → **retry** hasta
  `max_review_retries` (ADR 0013, límite duro de plataforma).
- `inconclusive` (sin tool-call, sin JSON, prosa sin señal clara, o args
  inválidos) → **escalar a humano** (NO se gasta retry: re-promptear un veredicto
  ambiguo solo quema presupuesto).

La lección del postmortem auth/JWT se **preserva**: las palabras de dominio
(rechaza/falla) NO se leen como rechazo; bajo el gate autoritativo son
**inconcluso**, no fail.

### Escalado a humano (D3) — reusa `blocked` + bandeja, NO un estado de tarea nuevo

El runtime emite un status terminal nuevo, `needs_human_review` (junto a
`awaiting_human_approval`), con el motivo en `abort_code` (`review_inconclusive`
o `max_review_retries_exhausted`). El `output` (deliverable) se preserva. El
worker (`transition_task_after_run`) mapea la TAREA a **`blocked`**, y el
`execution.status=needs_human_review` la distingue de un fallo duro para que la
**bandeja humana existente** (`human-inbox`, evento `task_blocked`) la surface.

> **Por qué no `pending_human_validation`**: ese es un estado de **PLAN**, no de
> tarea (CLAUDE.md ppio 7: validación humana a nivel de plan). A nivel de tarea no
> existe; inventarlo sería migración de enum + edges + UI y entraría en tensión con
> ppio 7. Reusar `blocked` + bandeja es el mínimo coherente. El patrón es el espejo
> de `awaiting_human_approval` (runtime emite status → worker lo mapea), ya probado.

Así el gate es autoritativo (D1) **sin reabrir** los `max_review_retries_exceeded`:
la autoridad termina en el humano (ppio 7), no en un abort ciego.

### `finish_status` es un HINT, no el veredicto

El `submit_result(status)` del agente (ADR 0086) es una **pista** que se le pasa al
reviewer y se muestra en la UI; el reviewer juzga el output contra los
`acceptance_criteria` (que ahora SÍ se incluyen en el prompt de review) por su
cuenta. El status del agente nunca es auto-veredicto.

## Alternativas descartadas

1. **Mantener fail-open** (statu quo ADR 0086). Rechazada: el operador pidió un gate
   autoritativo; fail-open puede aprobar output malo.
2. **Inconcluso → tratar como fallo y reintentar / abortar.** Rechazada: reintroduce
   el riesgo de tumbar trabajo legítimo (la regresión que la pila de parches
   combatía); abortar da falsos bloqueos. Escalar a humano es el punto medio.
3. **Estado de tarea `pending_human_validation` literal.** Rechazada (de momento):
   choca con ppio 7 (es de plan) y cuesta migración+edges+UI; `blocked`+bandeja
   cubre el caso. Reabrible si el operador quiere el estado explícito.
4. **`response_schema` genérico en `shared-llm` para forzar el veredicto.** Rechazada
   como over-engineering: el review ya viaja como tool en los 4 providers; forzar no
   es implementable en `claude_sdk` (`can_use_tool` intercepta, no compele).

## Consecuencias

- ✅ El self-review deja de ser inerte: un veredicto débil ya no aprueba por
  defecto; se escala. Los gates últimos siguen siendo test-runtime + validación
  humana de plan (ppio 7).
- ✅ El deliverable se **preserva** en el escalado (no se descarta como fallo duro).
- ⚠️ **Más escalados a humano** posibles en `claude_sdk` cuando degrada a prosa
  ambigua — es el coste consciente de un gate autoritativo; la red de seguridad de
  prosa (marcadores conservadores) reduce el ruido, y `submit_verdict` ya se advierte
  en los 4 providers.
- ⚠️ Supera parcialmente a ADR 0013 (el "pasa/no pasa simple"); `max_review_retries`
  sigue siendo el límite duro de plataforma de 0013, ahora con escalado en vez de
  abort al agotarse.

## Referencias

- Spec del refactor: `docs/roadmap/refactor-pipeline-ejecucion-review.md`.
- ADR 0086 (salida estructurada review/finish) — `submit_verdict`/`submit_result`.
- ADR 0013 (agent loop) — `max_review_retries`, gate simple superado aquí.
- Código: `agent_runtime/{providers,model,graph,state}.py`;
  `workers/execution.py`; `api_server/db/{domain,execution_repo}.py`;
  migración `0100_execution_finish_status`.
- Tests: `test_review_verdict.py`, `test_finish_contract.py`,
  `test_claude_sdk_review.py`, `tests/unit/test_agent_graph.py`,
  `tests/integration/test_execution_task_transition.py`.
