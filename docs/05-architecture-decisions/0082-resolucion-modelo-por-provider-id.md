---
adr_id: "0082"
title: "Resolución de modelo por provider_id en toda la plataforma (unificación de selección+resolución)"
status: accepted
date: 2026-06-25
authors: [claude-opus]
plan_referenced: plan-unificacion-provider-id
docs_language: es
related: ["0021", "0055", "0057", "0065", "0070"]
supersedes: []
---

# ADR 0082 — Resolución de modelo por `provider_id` en toda la plataforma

> **Estado: `accepted` (2026-06-25)** — aprobado por el operador ("adelante"), con el
> requisito explícito de **un único selector reutilizable** en todos los sitios de selección
> de provider+model. Implementación en `docs/roadmap/plan-unificacion-provider-id.md` (por
> fases, TDD, backward-compatible).

## Contexto

El catálogo LLM es **cerrado** (ADR 0021): 4 _kinds_ — `claude_sdk`, `copilot`,
`azure_foundry`, `ollama`. Un mismo kind puede tener **varias filas `llm_providers`
activas** distinguidas por `slug` (p.ej. `ollama-local` + `ollama-cloud`).

Hoy conviven **dos semánticas de resolución** de un `model_config` (provider+model) a un
cliente LLM concreto (base_url + credencial):

- **(A) por kind → fila activa más nueva** (`rows[0]`, `ORDER BY id DESC`):
  `factory_resolver.resolve_provider_config` sobre `list_active_llm_providers_by_kind`.
  La usa la **ejecución de agentes** (su `model_config` guarda `provider`=kind).
- **(B) por `provider_id` → fila EXACTA**: `factory.build_llm_provider(admin_session, *,
provider_id, model, vault)`. Ya la usan **chat del asistente/equipo/proyecto**
  (`chat_model_config`), el **asistente personal** y el **córtex** (sus selecciones
  guardan `{provider_id, model}`).

**Problema observado:** con dos providers `ollama` activos, la vía (A) siempre coge el más
nuevo, así que **no se puede elegir `ollama-cloud` para los agentes** — ni en el selector
(`GET /agents/model-options` agrega por kind usando solo `rows[0]`), ni en runtime. Además,
la UI de _platform-defaults_ y `chat-model-section` **ya guardan `provider_id`**, pero
`validate_model_config` y la cadena de herencia (ADR 0065) lo **ignoran** → es **dato
muerto** en la pata de ejecución.

El operador exige **consistencia**: _"todos los selectores deben funcionar igual"_ y _"la
parte que ya está bien hecha debe ser reutilizable en todos los sitios donde haya selección
de provider y model"_.

**La infraestructura por `provider_id` YA existe y está probada:**
`build_llm_provider` (api-server), `_resolve_by_provider_id` (worker, ya con fallback a
kind), `_resolve_chat_provider` (chat), `is_valid_selection`/`validate_chat_model_config`
(validación por provider_id), `GET /agents/provider-options` (lista filas concretas), y el
componente UI `ChatModelSection`/`ProviderModelSelects`. Lo que falta es **generalizarla** a
la pata de ejecución (agentes/equipos/proyectos/platform-default) y **reutilizar un único
selector** en el frontend.

## Decisión

1. **`{provider_id, model}` es la forma canónica** del `model_config` en TODA selección y
   resolución de modelo (ejecución de agentes incluida), igual que ya hacen
   chat/asistente/córtex. Se **conserva `provider` (kind) en paralelo** (para herencia,
   validación, display y back-compat), como ya hace `chat-model-section`.

2. **Resolución preferente por `provider_id`**, con **fallback a kind→fila-más-nueva**
   cuando no hay `provider_id` (configs legacy). Esto ya está implementado en el worker
   (`_resolve_by_provider_id` → None → camino por kind) y en el chat; se reutiliza, no se
   reescribe.

3. **El catálogo cerrado (ADR 0021) se mantiene**: una fila `llm_providers` ya garantiza
   `kind ∈ catálogo` (CHECK de DB). Cuando hay `provider_id`, la validación comprueba
   **fila activa + `model` ∈ modelos de ESA fila** (`is_valid_selection`), no el kind
   contra el enum. `reasoning_effort` sigue siendo **por kind** (`REASONING_OPTIONS_BY_KIND`
   derivado del `row.kind`) — no cambia.

4. **Un único selector reutilizable** en el frontend (mensaje del operador): se extrae el
   patrón por-provider (`ProviderModelSelects`, hoy privado en `model-cards.tsx` / la lógica
   de `chat-model-section`) a un **componente compartido** que consume `GET
/agents/provider-options` y emite `{provider_id, provider(kind), model, temperature?,
reasoning_effort?}`. Lo usan **todos** los sitios de selección (persona/agente/equipo/
   proyecto/adopt/platform-default; asistente/córtex pueden converger a él o seguir con su
   variante equivalente).

5. **`GET /agents/model-options`** (agrega por kind, `rows[0]`) queda **deprecado** para la
   selección de agentes; el único selector vivo que lo usa (`PersonaModelFields`) migra a
   `provider-options`.

## Consecuencias

- `ollama-cloud` (y cualquier 2ª fila de un kind) se vuelve **seleccionable y usable** por
  los agentes — cierra el problema reportado.
- **Consistencia y DRY**: un solo componente de selección en toda la plataforma.
- **⚠️ Migración**: el `provider_id` que la UI de platform-defaults YA guarda pasará a
  **surtir efecto** → podría cambiar el modelo efectivo de agentes que usan el default. Se
  audita el valor antes del rollout (ver plan, fase de migración).
- **Backward-compat**: configs legacy `{provider:kind, model}` siguen resolviendo por
  kind→más-nuevo. No se rompe `validate_model_config` para specs sin `provider_id`.
- El spec `kind` (scripted de tests) sigue pasando intacto.

## Alternativas descartadas

- **Solo arreglar el selector (`model-options` union) + resolución kind model-aware**:
  no unifica ni reutiliza; mantiene dos patrones e inconsistencia de UX. El operador pidió
  explícitamente consistencia/reutilización.
- **`ollama_cloud` como kind separado**: viola el catálogo cerrado (ADR 0021).
- **Resolver el cliente dentro del contenedor del agent-runtime**: el sandbox no tiene
  BD/Vault (ADR 0012); la resolución a dict-spec sigue en el worker.

## Invariantes a preservar (del mapa de código)

- Sesión **admin BYPASSRLS** para resolver `provider_id` (`llm_providers` no tiene RLS).
- `to_provider_model_name(row.kind, model)` se aplica con el **kind de la fila** (autoritativo).
- El overlay por kind está **duplicado** worker↔agent-runtime a propósito — tocar ambos si cambia.
- `config_needs_default_model` debe reconocer `{provider_id, model}` como **pineado** (hoy lo trataría como vacío → heredaría, perdiendo la elección).
