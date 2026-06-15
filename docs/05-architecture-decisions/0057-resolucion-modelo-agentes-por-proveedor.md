---
adr_id: "0057"
title: "Resolución de modelo de agentes por proveedor concreto (provider_id) + cableado del resolver en el worker (los agentes usan su modelo real)"
status: proposed
date: 2026-06-10
authors: [system_architect]
plan_referenced: 06.17-capacitacion-agentes
supersedes_partially: ["0055"]
docs_language: es
---

# ADR 0057 — Resolución de modelo de agentes por proveedor concreto + cableado del resolver

> **Estado: `proposed`** — pendiente de aprobación del operador. Extiende el ADR
> 0055 (validación de `model_config`) y replica el patrón del ADR 0053 (modelo
> del asistente personal por `provider_id`). Lo motiva un hallazgo **crítico**
> de la auditoría de resolución de modelo (2026-06-10).

## Contexto

Tres piezas previas fijan el terreno:

- **ADR 0021** — catálogo **cerrado** de 4 proveedores (`claude_sdk`, `copilot`,
  `azure_foundry`, `ollama`). Añadir un quinto pide ADR.
- **ADR 0055** — el `model_config` del agente (pata SER) se valida contra ese
  catálogo: `{provider, model, temperature}` donde **`provider` es el _kind_**.
  El dispatch rellena un default seguro cuando el agente no fija modelo.
- **ADR 0053** — el modelo del **asistente** se selecciona por **`provider_id`
  concreto** + `model_id`, con herencia (override del tenant → default de
  plataforma) y validación contra el catálogo. Ya distingue proveedores del
  mismo kind (p. ej. `ollama-cloud` vs `ollama-local`).

El sistema permite **varios proveedores del mismo kind** (se añadió `slug` único
para diferenciarlos). El asistente lo resuelve bien por `provider_id`; **los
agentes lo resuelven por kind**, y de ahí salen dos problemas.

### Problema 1 (CRÍTICO): los agentes con modelo real corren `scripted` en silencio

Trazada la ruta de ejecución de agentes (orchestrator → worker → runtime en
contenedor):

1. `orchestrator/dispatch.py` construye `model_spec = dict(agent.model_config)`
   (rellenando el default si procede) y lo reenvía **verbatim** como
   `request['model']`. Ese spec lleva la clave **`provider`** (por ADR 0055).
2. `workers/execution.py` lo pasa **tal cual** al contenedor en
   `AGENT_TASK_SPEC` (el worker **no** resuelve proveedor ni traduce
   `provider`→`kind`).
3. En el sandbox, `agent_runtime/model.py:model_from_spec` hace
   **`kind = spec.get("kind", "scripted")`**. Como el spec trae `provider` y no
   `kind` → **cae al default `"scripted"`** → construye un `ScriptedModelClient`
   (respuestas deterministas/offline). Si llegara a
   `agent_runtime/providers.py:build_provider_client` (`kind = spec.get("kind")`
   → `None`) reventaría con `unknown provider kind: None`; pero el corto-circuito
   a scripted gana antes.

**Efecto:** un agente con `model_config = {provider: ollama, model: qwen3-coder…}`
**no usa Ollama** — responde con el cliente scripted. Todo lo configurado
(default del modelo, herencia del ADR 0055, equipo CodeIgniter 4, etc.) **no
llega a runtime**. No se detectó porque en dev/test solo se han ejecutado specs
`scripted`; nunca se lanzó una tarea real con un agente.

**Acoplado:** aunque se arregle la clave, el runtime construye el proveedor
**solo con campos del spec** y `resolver=None` (el sandbox **no** tiene BD/Vault,
principio #2 del CLAUDE.md). El dispatch **nunca inyecta** `base_url`/credencial.
El _seam_ resolver (`factory_resolver.make_async_resolver`,
`providers.build_provider_client(resolver=…)`, `_overlay_resolved`; Plan 11.2 /
ADR 0028) **existe pero no está cableado** en la ruta dispatch→worker. Para
`ollama` usaría `localhost:11434` _dentro del contenedor_ (incorrecto) y sin
token para cloud.

### Problema 2: selección por kind → no distingue proveedores del mismo kind

`model_config.provider` es un **kind**. Con `ollama-cloud` + `ollama-local`
activos, un agente "ollama" no puede decir _cuál_; la resolución por kind elige
el **más nuevo activo** (`list_active_llm_providers_by_kind(kind)[0]`). El
asistente ya no tiene este problema (usa `provider_id`).

## Decisión

Alinear la selección de modelo de **agentes** (y del **default de plataforma de
agentes**) al modelo del asistente — **por `provider_id` concreto** — y **cablear
el resolver en el worker** para que los agentes usen de verdad su modelo. En
concreto:

1. **Selección por proveedor concreto.** `model_config` gana **`provider_id`**
   (UUID de la fila `llm_providers`) como referencia autoritativa, junto a
   `model` y `temperature`. `provider` (kind) se mantiene como campo
   **derivado/compat** (se rellena desde la fila) y como **fallback** para
   configs antiguos. Misma forma de selección que el asistente
   (`AssistantModelSelection{provider_id, model_id}`).

2. **Herencia override → default.** Cadena de resolución del modelo efectivo de
   un agente: **override del agente → (default del proyecto) → default de
   plataforma → ninguno**, replicando `resolve_assistant_model`. "Override" =
   `model_config` con `provider_id`+`model` (o `provider`+`model` legacy). Si no,
   hereda el default. Un tier solo se usa si su proveedor sigue **activo**.

3. **La resolución concreta vive en el WORKER, no en el sandbox.** Antes de
   lanzar el contenedor, el worker (que sí tiene BD/Vault) resuelve el
   `model_config` a un spec **concreto** e **inyecta** en `AGENT_TASK_SPEC`:
   `kind`, `model` (nombre nativo vía `to_provider_model_name`), `base_url` y los
   campos de credencial que cada kind consume (api_key/bearer_token/
   github_token/apim_base_url…). El sandbox sigue **sin** BD/Vault (principio #2)
   y construye desde el spec ya resuelto. Esto **cablea** el resolver existente
   (`build_llm_provider` por `provider_id`; `resolve_provider_config` por kind
   para legacy) en la ruta dispatch→worker.

4. **Fin del `scripted` silencioso.** El spec reenviado lleva `kind` (+
   endpoint/credencial), de modo que `model_from_spec`/`build_provider_client`
   construyen el proveedor real. Además se endurece `model_from_spec`: **no**
   caer a `scripted` cuando hay intención de proveedor real (leer `provider`
   como fallback de `kind`; reservar `scripted` para specs explícitos de test).

5. **Validación DB-aware del `provider_id`.** Extender la validación de
   `model_config` (crear/editar agente y `model.default_config`) para validar,
   cuando hay `provider_id`, que el proveedor está **activo** y el modelo es
   **seleccionable** (reusando `is_valid_selection` /
   `list_available_models_for_provider` del asistente). Los configs solo-kind
   (legacy) siguen siendo válidos (fallback por kind).

6. **Compatibilidad hacia atrás.** Configs antiguos con solo `provider` (kind)
   **siguen funcionando**: el worker resuelve por kind (más nuevo activo) cuando
   no hay `provider_id`. **Sin migración de datos obligatoria** (cambio aditivo);
   una migración opcional posterior puede convertir kind→provider_id.

7. **UI por proveedor concreto.** `PersonaModelFields` (alta/edición de agente) y
   el control de `model.default_config` en "Valores por defecto" pasan de
   _kind + texto libre_ a **selector de proveedor (instancia) + desplegable de
   modelos**, reutilizando `ProviderModelSelects`/`_build_model_options` del
   asistente.

## Alternativas consideradas

- **A — Resolver dentro del runtime (pasar un resolver al sandbox).** Rechazada:
  viola el principio #2 (el sandbox no debe tener acceso a BD/Vault).
- **B — Seguir por kind + desactivar los proveedores no-default.** Rechazada: no
  permite cloud+local activos a la vez y **no arregla el bug del scripted**.
- **C — Solo arreglar la clave `provider`/`kind` (scripted), sin resolver ni
  provider_id.** Parcial: haría que los agentes intenten el proveedor real, pero
  sin `base_url`/credencial fallarían (o usarían `localhost` erróneo); no
  distingue cloud/local. Útil como **Fase 1 urgente**, no como solución final.
- **D (elegida) — provider_id + resolución en el worker + arreglo de clave.**
  Coherente con el asistente, mantiene el sandbox aislado, soluciona ambos
  problemas.

## Consecuencias

### Positivas

- Los agentes **usan de verdad** su modelo configurado (fin del scripted
  silencioso).
- Cloud vs local se distingue en **toda** la app (agentes, default, asistente).
- El sandbox sigue sin BD/Vault; la credencial se resuelve en el worker.
- Reutiliza piezas ya probadas del asistente (validación, build por fila,
  endpoints de opciones).

### Costes / riesgos

- Toca: esquema `model_config` + validadores (`validate_model_config`,
  `config_needs_default_model`, schemas de agente), `model.default_config`,
  **dispatch/worker** (resolución + inyección), el **contrato `AGENT_TASK_SPEC`**
  del agent-runtime, y **2 UIs**.
- El worker hace lecturas de Vault por dispatch (aceptable; credencial efímera en
  memoria/in-env del contenedor — **nunca loguear** el spec con secretos).
- Requiere tests nuevos, incluido uno **end-to-end** que pruebe que un agente con
  proveedor real **no** cae a scripted.

## Plan de implementación (por fases)

1. **Fase 1 (URGENTE, P0): los agentes ejecutan su modelo real.** Worker resuelve
   `model_config`→spec concreto (kind + nombre nativo + base*url + credencial vía
   `build_llm_provider`/`resolve_provider_config`) e inyecta en `AGENT_TASK_SPEC`;
   `model_from_spec` deja de caer a `scripted` con specs de proveedor real. Test
   e2e (agente real ≠ scripted). \_Esta fase ya quita el bug crítico aunque siga
   siendo por kind.*
2. **Fase 2: `provider_id` en `model_config`** + validación DB-aware + predicados
   de herencia (`config_needs_default_model`) conscientes de `provider_id` +
   cadena de resolución override→(proyecto)→default.
3. **Fase 3: UI** — `PersonaModelFields` y "Valores por defecto" a selector de
   proveedor concreto + desplegable de modelos.
4. **Fase 4: tests + migración opcional** kind→provider_id.

## Referencias de código (ancladas en el mapeo 2026-06-10)

- Bug scripted: `docker/agent-runtimes/agent-runtime/agent_runtime/model.py`
  (`model_from_spec`, `kind = spec.get("kind", "scripted")`);
  `…/agent_runtime/providers.py:build_provider_client` (`kind = spec.get("kind")`
  → `unknown provider kind`).
- Reenvío verbatim: `apps/orchestrator/src/orchestrator/dispatch.py` (`model_spec`
  ~L394-416); `apps/workers/src/workers/execution.py` (`_agent_spec` ~L209,
  `_build_runtime_env` ~L260-290).
- Resolver existente (no cableado al agente):
  `apps/api-server/src/api_server/llm_providers/factory_resolver.py`
  (`resolve_provider_config`, `make_async_resolver`);
  `…/llm_providers/factory.py:build_llm_provider` (por `provider_id`, post-#46).
- Patrón a replicar (asistente):
  `apps/api-server/src/api_server/assistant/model_config.py`
  (`AssistantModelSelection`, `resolve_assistant_model`, `is_valid_selection`,
  `list_available_models_for_provider`, `to_provider_model_name`);
  `routers/assistant.py` (`_build_model_options`, `/model/options`,
  `/default-model/options`).
- Validación/consumidores: `apps/api-server/src/api_server/db/platform_settings.py`
  (`validate_model_config`, `config_needs_default_model`, `get_default_model_config`,
  `DEFAULT_MODEL_CONFIG`); `apps/api-server/src/api_server/schemas/agents.py`
  (`_validate_model_config`); `routers/agents.py` (default stamping).
- UI: `apps/admin-panel/components/capability/persona-section.tsx`
  (`PersonaModelFields`); `apps/admin-panel/lib/persona/persona.ts`
  (`ModelConfigDraft`, `buildModelConfig`, `PROVIDER_KINDS`);
  `apps/admin-panel/app/admin/settings/platform-defaults/page.tsx`;
  `apps/admin-panel/app/admin/assistant/settings/model-cards.tsx`
  (`ProviderModelSelects`, `modelsFor`).
