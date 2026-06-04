---
title: Persona y system prompt (SER) — modelo, prompt efectivo y edición es/en
audience: tenant admin, project owner, operator
phase: 06.17-capacitacion-agentes
updated: 2026-06-04
docs_language: es
---

# Persona y system prompt (SER)

La **persona** es la vía **SER** del modelo de capacitación: **quién es** el agente
y **cómo se comporta**. Se compone de cuatro piezas:

1. **Modelo** (`model_config`): proveedor, modelo y temperatura del **catálogo
   cerrado** ([ADR 0021](../05-architecture-decisions/0021-llm-provider-catalog.md)).
2. **System prompt** bilingüe (es/en) del rol del agente.
3. **Modo de chat** (planning / discussion / execution), que aporta su propio prompt.
4. **Skills** asignadas, cuyo `prompt_fragment` se suma ([skills-de-agentes.md](./skills-de-agentes.md)).

> Modelo mental completo en [`../04-reference/training-model.md`](../04-reference/training-model.md).
> Guía paraguas: [`como-capacitar-agentes.md`](./como-capacitar-agentes.md).
> La validación del modelo está en el
> [ADR 0055](../05-architecture-decisions/0055-validacion-model-config.md).

## El modelo: catálogo cerrado (ADR 0055)

Al crear o editar un agente, el selector de proveedor ofrece **solo los cuatro** del
catálogo cerrado del ADR 0021:

| `provider`      | Proveedor                   |
| --------------- | --------------------------- |
| `claude_sdk`    | Claude Agent SDK (Pro/Max)  |
| `copilot`       | GitHub Copilot              |
| `azure_foundry` | Azure AI Foundry (vía APIM) |
| `ollama`        | Ollama (local + cloud)      |

La validación (ADR 0055) exige:

- `provider` ∈ {`claude_sdk`, `copilot`, `azure_foundry`, `ollama`}; cualquier otro
  ⇒ **422**.
- `model` no vacío.
- `temperature` dentro de rango.

Un agente creado por UI nace con un **`model_config` poblado** (nunca `{}`). Para
los agentes **legacy** con spec vacío, el dispatch aplica un **default
operator-configurable** (no falla en arranque), y una **migración** sanea los
existentes. Así se acaba el "spec vacío" silencioso que rompía el dispatch.

## El prompt efectivo

El system prompt que realmente recibe el LLM **no** es solo el del rol: es la
**combinación** de varias fuentes. La vista **"prompt efectivo"** de la ficha del
agente lo muestra tal cual se ensambla:

```
prompt del MODO de chat (planning/discussion/execution)
        +
prompt del ROL del agente (model_config.system_prompts.{es,en})
        +
prompt_fragment de cada SKILL asignada
```

Al cambiar el modo de chat en la vista, el prompt efectivo se recompone en vivo, así
que ves exactamente con qué frame arranca el agente en cada modo.

## Edición bilingüe es/en (fuente única)

El system prompt vive en `model_config.system_prompts.{es, en}`. Esa es la **fuente
única**: la tarjeta de la lista de agentes y el editor de la ficha leen y escriben
el **mismo** campo. Se acabó la colisión histórica entre la lista (que leía
`model_config.system_prompts`) y el detalle (que leía un campo plano).

- Edita el prompt **es** y **en** por separado, sobre la misma fuente.
- Lo que guardas es lo que muestra la tarjeta de la lista: sin divergencias.

## El modo `custom`: "No disponible aún"

Los modos built-in (planning / discussion / execution) están disponibles. El modo
**`custom`** (modos definidos por el tenant end-to-end) está **diferido**: la UI lo
muestra **"No disponible aún"** y deshabilitado, con honestidad de estado. No se
ofrece como si funcionara.

## Cómo lo ves en el Hub

La sección **SER** del Hub muestra el estado real:

- Modelo válido ⇒ **"`provider · model`"** (verde).
- `model_config` sin provider/model ⇒ **"Modelo no configurado"** (aviso).
- En proyecto/equipo (sin persona propia) ⇒ **"No aplica"**.

## Resumen (EN)

The **persona** is the **BE** path: provider/model/temperature from the **closed
catalog** (the only four — `claude_sdk`, `copilot`, `azure_foundry`, `ollama`;
[ADR 0021](../05-architecture-decisions/0021-llm-provider-catalog.md)), the
bilingual (es/en) role system prompt, the chat mode and the assigned skills'
`prompt_fragment`. `model_config` is validated ([ADR 0055](../05-architecture-decisions/0055-validacion-model-config.md)):
an out-of-catalog provider, empty model or out-of-range temperature returns **422**;
UI-created agents never start as `{}`, and legacy empty specs get an
operator-configurable default at dispatch (no startup failure). The **effective
prompt** view shows the assembled prompt (chat mode + role + skill fragments) and
recomposes live when you switch modes. The system prompt lives in
`model_config.system_prompts.{es,en}` as a **single source** read/written by both
the list card and the detail editor. The **`custom`** chat mode is deferred and
shown **"Not available yet"**.

## Véase también

- [como-capacitar-agentes.md](./como-capacitar-agentes.md) — guía paraguas.
- [skills-de-agentes.md](./skills-de-agentes.md) — fragmentos de persona.
- [configurar-proveedores-llm.md](./configurar-proveedores-llm.md) — alta de los 4 proveedores (System Admin).
- [ADR 0055](../05-architecture-decisions/0055-validacion-model-config.md) — validación de `model_config`.
