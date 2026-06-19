---
adr_id: "0070"
title: "Esfuerzo de razonamiento (reasoning_effort) por proveedor en model_config"
status: accepted
date: 2026-06-19
authors: [system_architect]
plan_referenced: personalizacion-equipos-built-in
docs_language: es
extends: ["0021", "0055", "0057", "0065"]
---

# ADR 0070 — Esfuerzo de razonamiento por proveedor en `model_config`

> **Estado: `accepted`** (operador, 2026-06-19; v1 **por proveedor**). Extiende el
> `model_config` (ADR 0055) y la cadena de herencia (ADR 0065); las opciones son
> **específicas de cada proveedor** del catálogo cerrado (ADR 0021).

## Contexto

Los modelos de razonamiento exponen un control de "thinking"/esfuerzo, pero **cada
proveedor lo expresa distinto** (verificado introspeccionando el SDK / por contrato
de API):

| Proveedor         | Parámetro nativo                            | Valores                                                                                            |
| ----------------- | ------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| **claude_sdk**    | `ClaudeAgentOptions.effort` (`EffortLevel`) | `low` · `medium` · `high` · `xhigh` · `max` (+ `thinking`/`max_thinking_tokens` para control fino) |
| **azure_foundry** | `reasoning_effort` (OpenAI o-series)        | `low` · `medium` · `high`                                                                          |
| **copilot**       | `reasoning_effort` (OpenAI-compat)          | `low` · `medium` · `high`                                                                          |
| **ollama**        | `think` (booleano)                          | on/off                                                                                             |

Además es **dependiente del modelo** (un modelo sin razonamiento ignora el
parámetro). No existe un set común → las opciones deben ser las reales de cada
proveedor.

## Decisión

Una clave nueva **opcional** `model_config.reasoning_effort: str` con **valores
específicos por proveedor**. `""`/ausente = sin razonamiento. Viaja por la cadena
de herencia (ADR 0065) junto al `provider` con el que se fija (quedan
consistentes por construcción; nunca un valor de un proveedor colgado en otro).

**Catálogo de opciones por kind** (fuente única en el backend,
`REASONING_OPTIONS_BY_KIND` junto a `LLM_PROVIDER_KINDS`):

| kind            | opciones                                            |
| --------------- | --------------------------------------------------- |
| `claude_sdk`    | `off` · `low` · `medium` · `high` · `xhigh` · `max` |
| `azure_foundry` | `off` · `low` · `medium` · `high`                   |
| `copilot`       | `off` · `low` · `medium` · `high`                   |
| `ollama`        | `off` · `think`                                     |

**Traducción al parámetro nativo** (cada adaptador del agent-runtime interpreta su
valor; `off`/ausente = no enviar nada):

- `claude_sdk` → `ClaudeAgentOptions(effort=<valor>)`.
- `azure_foundry` / `copilot` → kwarg `reasoning_effort=<valor>` (→ body
  `/chat/completions`; los providers ya hacen `{..., **kwargs}`).
- `ollama` → kwarg `think=true` (si valor ≠ `off`).

**Granularidad v1: por proveedor.** El selector ofrece el superset del proveedor;
si el modelo concreto no razona, el adaptador es **no-op** (el parámetro se ignora;
no rompe el run). Evolucionar a por-proveedor+modelo (mapa de capacidades por
modelo) queda para una v2 si hace falta.

**Capas:**

- **Validación** (`validate_model_config`, ADR 0055): si hay `reasoning_effort`
  no vacío, debe estar en `REASONING_OPTIONS_BY_KIND[provider]` (requiere
  `provider`); `off` siempre válido.
- **Herencia** (`resolve_model_config_chain`): **sin cambios** — la clave es parte
  del bloque de modelo y se hereda con él.
- **Spec/worker** (`model_resolver`): la clave viaja en `model_config` → spec; se
  añade a `safe_spec_summary` (observabilidad, sin secretos).
- **agent-runtime** (`build_provider_client`): lee `spec["reasoning_effort"]` y lo
  pasa al adaptador, que lo traduce al parámetro nativo del proveedor.
- **UI**: `GET /agents/model-options` gana `reasoning_by_kind`; `PersonaModelFields`
  añade un selector "Razonamiento" poblado por proveedor (oculto si vacío; reset al
  cambiar de proveedor; `off` por defecto).

## Alternativas

- **Abstracción universal** (off/low/.../max común mapeado por proveedor):
  rechazada — Ollama no tiene niveles (solo on/off) y los rangos no coinciden;
  exponer opciones irreales confunde. Por-proveedor es honesto.
- **Por proveedor + modelo en v1**: más preciso pero exige un mapa de capacidades
  por modelo a mantener; se difiere.

## Consecuencias

- **+** Razonamiento configurable por agente/equipo/proyecto, heredable, con las
  opciones reales de cada proveedor; el flagship `claude_sdk` usa su `effort`
  nativo (low…max).
- **+** Retro-compatible: clave opcional; ausente = comportamiento actual. Los
  providers OpenAI-compat ya aceptan `**kwargs`; solo Claude necesita cablear
  `effort` en `_build_options`.
- **−** Por-proveedor puede ofrecer una opción a un modelo que no razona (no-op).
  Mitigado documentándolo; v2 puede precisar por modelo.
- **Verificado, no asumido**: `ClaudeAgentOptions.effort = Literal['low','medium',
'high','xhigh','max']` (introspección del SDK ≥0.2.82); `**kwargs`→body en
  copilot/azure/ollama (lectura del código).

## Tests

`validate_model_config` (unit: acepta valores válidos por proveedor, rechaza
cruzados/desconocidos, `off` siempre); `GET /agents/model-options` expone
`reasoning_by_kind`; el adaptador del agent-runtime traduce por proveedor
(claude→effort, openai-compat→reasoning_effort, ollama→think).
