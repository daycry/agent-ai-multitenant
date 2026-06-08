---
adr_id: "0055"
title: "Validación de model_config contra el catálogo cerrado (ADR 0021) + default seguro operator-configurable + saneo de spec legacy {}"
status: accepted
date: 2026-06-04
authors: [system_architect]
plan_referenced: 06.17-capacitacion-agentes
docs_language: es
---

# ADR 0055 — Validación de `model_config` contra el catálogo cerrado (ADR 0021)

> **Estado: `accepted`** (aprobado por el operador 2026-06-04, Fase 0 del Plan
> `06.17-capacitacion-agentes`). Lo consume `task_06_17_10` (validación + default
> seguro), `task_06_17_11` (selector de proveedor/modelo en la UI) y el dispatch.

## Contexto

El `model_config` de un agente (la pata **SER** del modelo de capacitación: qué
proveedor/modelo/temperatura usa) nace **sin validación y a menudo vacío**:

- **Ningún diálogo de la UI envía `model_config`** al crear/editar un agente, así
  que la fila nace con `{}`. En dispatch, ese `{}` se traduce a un **spec de
  modelo vacío** que puede hacer fallar el arranque del run (sin proveedor que
  resolver) — un fallo tardío y opaco para el operador.
- El `llm_config`/`model_config` que sí se llega a enviar **no se valida** contra
  el catálogo: nada impide guardar un `provider` inexistente, un `model` vacío o
  una `temperature` fuera de rango. El error, si llega, aparece en runtime.
- El **ADR 0021** fijó un **catálogo cerrado de cuatro proveedores** y dejó
  escrito que _"cualquier proveedor adicional pide un ADR explícito"_. La
  validación de `model_config` debe anclar exactamente en ese catálogo, no en una
  lista paralela que se desincronice.

Los cuatro proveedores del catálogo cerrado del **ADR 0021** (valores enum
exactos que la validación acepta) son:

| Proveedor (enum) | Clase en `shared-llm`      | Autenticación (resumen)                     |
| ---------------- | -------------------------- | ------------------------------------------- |
| `claude_sdk`     | `ClaudeAgentProvider`      | `ANTHROPIC_API_KEY` o suscripción Pro/Max   |
| `copilot`        | `CopilotProvider`          | OAuth Device Flow → JWT (~30 min)           |
| `azure_foundry`  | `AzureFoundryAPIMProvider` | `Ocp-Apim-Subscription-Key` o `Bearer`      |
| `ollama`         | `OllamaProvider`           | local: sin auth · cloud: `Bearer <api_key>` |

El reto: **validar estrictamente** lo nuevo (create/update por UI) **sin romper**
los agentes legacy que ya tienen `{}` en producción (no podemos rechazarlos en
arranque ni forzar al operador a re-editarlos uno a uno).

## Opciones consideradas

- **M-A. Sin validación + fallar tarde en dispatch (status quo).** Dejar
  `model_config` libre; el error aparece cuando el run arranca.
  - ✅ Cero trabajo. ❌ Fallo tardío y opaco; el operador no sabe que su agente
    es inválido hasta que lanza una tarea; permite proveedores fuera de catálogo.
    Rechazada.

- **M-B. Validación estricta en create/update + default seguro + saneo de legacy
  (ELEGIDA).** `create/update` valida `provider ∈ {claude_sdk, copilot,
azure_foundry, ollama}`, `model` no vacío y `temperature` en rango (`422` fuera
  de catálogo). La UI manda un **default explícito** (no `{}`) al crear. El
  **dispatch** aplica un **default seguro operator-configurable** a cualquier spec
  legacy `{}` (no fallo de arranque, **sin auto-retry**). Una **migración** sanea
  las filas `{}` existentes asignándoles el default.
  - ✅ Error temprano y claro (`422`) en la UI; ✅ ancla en el catálogo cerrado
    del ADR 0021; ✅ no rompe legacy (default seguro + saneo); ✅ default
    operator-configurable vía `platform_settings`. ❌ Una migración de datos +
    una clave nueva de settings que mantener.

- **M-C. Validación estricta + rechazar legacy `{}` en arranque.** Igual que M-B
  pero sin default seguro: un agente con `{}` falla el dispatch con un error
  claro y obliga a re-editarlo.
  - ✅ Fuerza datos limpios. ❌ Rompe agentes existentes en producción (el plan
    exige explícitamente _no fallo en arranque_); mala UX. Rechazada.

## Decisión

**Opción M-B.**

1. **Validación estricta en create/update (`422`).** El schema de agente valida:
   - `provider` ∈ **`{claude_sdk, copilot, azure_foundry, ollama}`** — los cuatro
     proveedores del catálogo cerrado del **ADR 0021**; cualquier otro valor → `422`;
   - `model` presente y **no vacío** → `422` si falta o es cadena vacía;
   - `temperature` dentro de un **rango válido** (p. ej. `0.0 ≤ t ≤ 2.0`) → `422`
     fuera de rango.
     Un proveedor fuera de catálogo es un `422`, no un guardado silencioso.

2. **Default explícito desde la UI.** Los diálogos de alta de agente envían un
   `model_config` completo (proveedor/modelo/temperatura elegidos del selector que
   **solo ofrece los cuatro proveedores**), de modo que ningún agente nuevo nace
   con `{}`.

3. **Default seguro operator-configurable en dispatch.** El dispatch resuelve el
   spec de modelo así: si `model_config` está vacío o incompleto (legacy `{}`),
   aplica un **default seguro** leído de `platform_settings` (clave operator-
   configurable de proveedor/modelo/temperatura por defecto para agentes). **No
   falla el arranque** y **no hace auto-retry** — solo rellena el spec con el
   default. El default mismo se valida contra el catálogo cerrado.

4. **Saneo por migración.** Una migración (reversible, encadenada al head vigente)
   recorre las filas `agents` con `model_config = {}` y les asigna el default
   seguro vigente, de modo que el estado en BD deja de tener specs vacíos.
   Backward-compat: si el default aún no está configurado, el dispatch usa un
   default de fallback del propio código (también del catálogo), nunca un fallo.

## Consecuencias

**Mejora:** un agente inválido se rechaza en la UI con `422` claro en vez de
fallar tarde en el run; el `model_config` queda anclado al catálogo cerrado del
ADR 0021 (no a una lista paralela); los agentes legacy `{}` siguen ejecutando
gracias al default seguro + saneo; el default es operator-configurable.

**Complejidad añadida:** una migración de datos, una clave de `platform_settings`
nueva y la lógica de resolución de default en dispatch. El selector de la UI debe
mantenerse en lockstep con los cuatro proveedores (el mismo riesgo que ya gestiona
el ADR 0021).

**Trade-offs:** se elige no romper legacy (default seguro + saneo) a cambio de
arrastrar una migración y un default. Se acepta porque rechazar agentes `{}` en
arranque rompería despliegues existentes — el plan lo prohíbe expresamente.

## Riesgos

| Riesgo                                                               | Prob. | Impacto | Mitigación                                                                          |
| -------------------------------------------------------------------- | ----- | ------- | ----------------------------------------------------------------------------------- |
| El selector de la UI se desincroniza de los 4 proveedores del 0021   | Media | Medio   | Fuente única del enum de proveedores; test de validación cubre los cuatro + uno KO  |
| La migración de saneo deja una fila sin default (default sin config) | Baja  | Medio   | Fallback de código (también del catálogo) en dispatch; migración reversible         |
| Validar de más rechaza un agente legacy válido pero raro             | Baja  | Bajo    | La validación estricta solo aplica a create/update; legacy `{}` pasa por el default |

## Alternativas rechazadas

M-A (sin validación) por fallo tardío/opaco y permitir proveedores fuera de
catálogo; M-C (rechazar legacy en arranque) por romper agentes existentes (el plan
exige _no fallo en arranque_).

## Trazabilidad

- Roadmap: `docs/roadmap/06.17-capacitacion-agentes.md` (`task_06_17_10`,
  `task_06_17_11`).
- Schema/endpoints: `apps/api-server/src/api_server/schemas/agents.py`,
  `apps/api-server/src/api_server/routers/agents.py`.
- Default + flag: `apps/api-server/src/api_server/db/platform_settings.py`.
- Dispatch: `apps/orchestrator/src/orchestrator/dispatch.py`.
- Migración: `apps/api-server/migrations/versions/` (saneo de `model_config = {}`).
- ADRs relacionados: **0021** (catálogo cerrado de los cuatro proveedores
  `claude_sdk`/`copilot`/`azure_foundry`/`ollama`), 0018 (Claude SDK como
  ModelClient), 0028 (platform global providers).
