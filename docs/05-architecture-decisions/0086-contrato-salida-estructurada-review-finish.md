---
adr_id: "0086"
title: "Contrato de salida estructurada para el self-review (y el finish del agente)"
status: accepted
date: 2026-06-27
decided_at: 2026-06-27
authors: [claude-opus]
plan_referenced: null
docs_language: es
related: ["0021", "0013", "0024", "0050"]
supersedes: []
---

# ADR 0086 — Salida estructurada del review (y opcionalmente del finish del agente)

> **Estado: `accepted`** (2026-06-27) — Opción 4 vía tool-path, alcance review+finish,
> por fases (ver "Decisión" al final). Las secciones de opciones se conservan como
> registro del razonamiento. Surge de la cadena de convergencia del agent-runtime: un run con
> `claude_sdk` escribió todo el deliverable y finalizó, pero el **self-review** lo
> abortó por `max_review_retries_exceeded` — el modelo respondía el veredicto en
> **prosa** y el parser best-effort lo contaba como `fail`. Parche interino aplicado
> (commit `c8b78c2`: la prosa pasa salvo rechazo explícito); este ADR decide la
> solución de fondo.

## Contexto

Estado real del contrato de entrada/salida con los modelos:

| Contrato                                             | ¿Estructurado?                                                                                                                                          |
| ---------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Tool calls** (input/output)                        | ✅ Sí — `Tool.parameters` + `Tool.output_schema` (JSON Schema); el agente las advierte vía `build_model_tool_schemas` (ADR 0021).                       |
| **Salida final de la tarea** (`DecisionKind.FINISH`) | ❌ No — texto libre (`output = resp.content`). Sin schema de deliverable.                                                                               |
| **Veredicto del self-review**                        | ❌ No de verdad — `_REVIEW_SYSTEM` _pide_ `{passed, feedback}` JSON, pero **no se fuerza**; `_parse_verdict` cae a sniffing de palabras si llega prosa. |

`packages/shared-llm` (la capa común de los 4 providers, ADR 0021) **no expone hoy**
`tool_choice` / `response_format` / structured-output: solo _advierte_ tools (el
modelo PUEDE llamarlas, no se le **obliga** a un formato de salida). Y el provider
más rebelde a formatos estrictos es el **`claude_sdk` (Claude Code CLI)**: aun
forzando una tool, el CLI a veces responde en prosa. Los providers HTTP
(`ollama`/`azure_foundry`/`copilot`) sí soportan `response_format` JSON-schema de
forma fiable.

## Fuerzas en tensión

- **Provider-agnóstico (ADR 0021)**: lo que se decida debe funcionar IGUAL en los 4
  caminos; un quinto provider futuro no debería romperlo.
- **Robustez**: el contrato no puede depender de que el modelo "se acuerde" del
  formato (lo que falla hoy).
- **Realidad del CLI**: `claude_sdk` no garantiza salida estructurada estricta →
  cualquier diseño necesita un **fallback tolerante** para ese camino.
- **Coste de cambio**: toca `shared-llm` (los 4 providers) + el motor de review (y,
  según alcance, el finish) en `agent_runtime` — transversal.

## Opciones

1. **Status quo + parser tolerante** (lo de `c8b78c2`). Cero estructura; el review
   sigue siendo adivinación. Rechazada como solución (es el parche, no el contrato).
2. **Forced tool-call para el veredicto** (`submit_verdict(passed, feedback)`):
   reusar la maquinaria de host-tools (advertir UNA tool y exigir su uso). Fiable en
   HTTP; en `claude_sdk` **no 100%** (el CLI puede ignorar la obligación).
3. **`response_format` JSON-schema** en `complete()`: el provider devuelve JSON
   validado contra schema. Fiable en HTTP; el **CLI de claude_sdk no lo soporta**.
4. **Híbrido (recomendado)**: añadir a `LLMProvider` una capacidad de **salida
   estructurada** (`response_schema`/forced-tool) que cada provider implementa como
   pueda (HTTP → `response_format`/`tool_choice`; `claude_sdk` → host-tool
   `submit_*` + instrucción), y **mantener el parser tolerante** (`c8b78c2`) como red
   de seguridad para el camino que degrade. Cinturón y tirantes.

## Alcance (a decidir)

- **Fase 1 — review-only**: el veredicto del self-review pasa a estructurado. Es lo
  que duele hoy; bajo riesgo; arregla el `max_review_retries_exceeded`.
- **Fase 2 — finish del agente**: sustituir el `FINISH` de texto libre por una tool
  `submit_result(summary, files_changed, criteria_met[])` con schema → salida de
  tarea **uniforme** y, de paso, **entrada estructurada** para el review (cierra el
  círculo: el review ya no lee prosa, lee campos). Más trabajo y más superficie.

## Recomendación

**Opción 4 (híbrido), por fases: Fase 1 ahora, Fase 2 después.**

1. Añadir a la capa `shared-llm` un parámetro de salida estructurada en `complete()`
   (p.ej. `response_schema: dict | None`), implementado por provider: HTTP usa
   `response_format`/`tool_choice`; `claude_sdk` advierte un host-tool `submit_*` y lo
   instruye. Devuelve el objeto validado cuando lo consigue.
2. **Review (Fase 1)**: `model.review()` pide el veredicto con `response_schema`
   `{passed: bool, feedback: str}`. Si el provider devuelve el objeto → directo; si
   degrada a prosa (CLI) → `_parse_verdict` tolerante (`c8b78c2`) como fallback. Adiós
   a los `fail` espurios.
3. **Finish (Fase 2)**: tool `submit_result` con schema; el review consume sus campos.
   Mantener compat: si el agente termina en prosa (CLI), envolver esa prosa en
   `{summary: <texto>}`.

Mantener SIEMPRE el fallback tolerante (no es deuda, es el contrato realista del CLI).

## Consecuencias

- ✅ El review deja de ser frágil; la salida de tarea (Fase 2) se vuelve uniforme y
  consumible. El parche `c8b78c2` pasa de "solución" a "red de seguridad documentada".
- ✅ Provider-agnóstico: HTTP fiable; `claude_sdk` mejor + fallback.
- ⚠️ Cambio transversal en `shared-llm` (los 4 providers) + `agent_runtime`
  (model clients, review, y en Fase 2 el grafo/finish). Tests por provider.
- ⚠️ No elimina el fallback: el CLI seguirá pudiendo ignorar el formato; por eso el
  parser tolerante se queda.

## Decisión (aprobada 2026-06-27)

- **Enfoque: Opción 4 (híbrido) vía TOOL-PATH.** El veredicto y el resultado se
  enrutan como **tool** (`submit_verdict`, `submit_result`), no como texto formateado.
  Dato decisivo que corrige el pesimismo inicial sobre `claude_sdk`: el CLI **falla
  al producir prosa/JSON con formato, pero llama host-tools de forma fiable** (se
  arregló y verificó: write_file/rag_search/…). Enrutar la salida como tool usa el
  camino que el CLI ya domina → fiable en los 4 providers (HTTP: `tool_choice`/
  `response_format`; claude_sdk: host-tool). El `_parse_verdict` tolerante (`c8b78c2`)
  se queda como **red de seguridad**, no como solución.
- **Alcance: Fase 1 (review) + Fase 2 (finish)**, por fases y con TDD: Fase 1 aislada
  (bajo riesgo) verde antes de tocar la Fase 2 (cambia la semántica terminal del loop
  —`FINISH`=texto→`submit_result`— recién estabilizada; mayor riesgo de regresión).
- **Invariante:** mantener SIEMPRE el fallback tolerante; un provider/modelo que
  degrade no debe romper el run.
