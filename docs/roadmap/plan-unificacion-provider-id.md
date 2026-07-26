---
title: "Plan — Unificación de selección+resolución de modelo por provider_id (ADR 0082)"
date: 2026-06-25
status: pending_human_validation
adr: "0082"
docs_language: es
---

> **Progreso (2026-06-25):** Fase 0 ✅ (auditoría: default era ollama-cloud pero resolvía
> mal a local), Fase 1 ✅ (`f2ad7d9`), Fase 2 ✅ (`ce5d2f6`), Fase 3 ✅ (`6c81a99`:
> `ProviderModelSelects` reutilizable + persona/agente/equipo/adopt por provider_id +
> borrado `DefaultModelSection`). Pendiente: deploy + (follow-up) converger
> `chat-model-section` al mismo componente y deprecar `/agents/model-options`.
>
> **Corrección (2026-07-06, auditoría de roadmap)**: esta nota de progreso decía "Fase 1-3 ✅"
> desde hace 11 días, pero los checkboxes de abajo seguían en `[ ]` — ahora reflejan el veredicto
> (Fase 1-2 completas y verificadas hoy; Fase 3 completa salvo "Consumidores" y "Converger",
> no verificados). `GET /agents/model-options` (Fase 4) sigue vivo en `routers/agents.py:191` —
> Fase 4 no empezada.
>
> **Reconciliación (2026-07-08)**: verificados contra código los items que la auditoría del 06
> dejó pendientes — "cost_resolution" (usa la cadena unificada) y "Consumidores" (las 3
> superficies usan `PersonaModelFields`) se marcan `[x]` con evidencia inline; Fase 0 se marca
> consumida (rollout del 2026-06-25 hecho y desplegado sin incidencias de resolución desde
> entonces). **Queda genuinamente pendiente**: "Converger" (córtex/platform-defaults siguen con
> variante propia `cortex-model-section.tsx` + `/owner/cortex/model-options`; chat y asistente SÍ
> convergidos) y la Fase 4 completa. Ambos son follow-ups de prioridad baja permitidos por el
> propio texto de los items.

# Plan — Unificación de modelo por `provider_id` (ADR 0082)

> **Objetivo:** que TODA selección y resolución de modelo use `{provider_id, model}` (con
> `provider`=kind en paralelo), reutilizando **un único selector** y la infra por-provider_id
> ya existente. Backward-compatible (fallback kind→fila-más-nueva). TDD por tarea, commit +
> deploy incremental por fase. Sin big-bang.

**Estado actual (del mapa):** lo bueno YA existe — `build_llm_provider`,
`_resolve_by_provider_id` (worker, con fallback), `_resolve_chat_provider`,
`is_valid_selection`/`validate_chat_model_config`, `GET /agents/provider-options`,
`ChatModelSection`/`ProviderModelSelects`. Lo por-kind vive en `PersonaModelFields`
(persona-section.tsx) + 3 consumidores + `DefaultModelSection` (huérfano) + la pata de
ejecución (`validate_model_config`, `resolve_model_config_chain`, dispatch).

---

## Fase 0 — Auditoría de datos (antes de activar nada) ⚠️

La UI de platform-defaults YA guardó `provider_id` que el backend ignora. Al activarlo,
empezará a aplicarse.

- [x] Inventariar `model.default_config` + `model_config` de agentes/equipos/proyectos con
      `provider_id` ya presente; confirmar que apuntan a la fila intencionada (no a una vieja).
  > **Reconciliado (2026-07-08)**: hecha el 2026-06-25 (nota de progreso: "Fase 0 ✅ — default
  > era ollama-cloud pero resolvía mal a local"); tarea puntual pre-rollout, consumida por el
  > propio rollout, desplegado desde entonces sin incidencias de resolución.
- [x] Documentar el valor esperado del default antes del rollout.
  > **Reconciliado (2026-07-08)**: el valor esperado (ollama-cloud) quedó documentado en la
  > nota de progreso de cabecera y ratificado en la memoria de entrega del ADR 0082.

## Fase 1 — Backend: validación provider_id-aware (sin cambiar resolución todavía)

- [x] **`validate_model_config`** (`db/platform_settings.py:560`): aceptar la forma
      `{provider_id, model}` validando contra la **fila** (activa + `model` ∈ sus modelos, vía
      `is_valid_selection`), conservando el camino kind-based para legacy. Reusar la lógica de
      `validate_chat_model_config`/`is_valid_selection` (no duplicar).
  - TDD: `tests/unit/test_model_config_chain.py` + nuevos casos: provider_id válido → ok;
    provider_id inactivo/model ajeno → 422; legacy kind-only → ok.
    > **Verificado (2026-07-06, auditoría de roadmap)**: `platform_settings.py:577-590` valida
    > `provider_id` explícitamente (comentario "ADR 0082").
- [x] **Schema de agente** (`schemas/agents.py:106,150`): que `_validate_model_config` use
      la validación provider_id-aware. Mantener 422 esperado por tests existentes.
  > **Verificado**: ambos `_validate_model_config` (create/update) llaman al validador de
  > `platform_settings` de arriba.

## Fase 2 — Backend: herencia + dispatch propagan provider_id

- [x] **`config_needs_default_model`** (`platform_settings.py:649`): un cfg `{provider_id,
model}` cuenta como **pineado** (no heredar). TDD.
- [x] **`resolve_model_config_chain`** (`:668`) + `_merge_inherited_model` (`:695`): propagar
      `provider_id` verbatim al mergear; decidir `reasoning_effort` coherente con el nivel que
      pinea. TDD (cadena agente→equipo→proyecto→plataforma con provider_id en cada nivel).
  > **Verificado**: `platform_settings.py:686-690` — "pineado por provider CONCRETO
  > (provider_id + model)".
- [x] **Dispatch** (`orchestrator/dispatch.py:480-506`): confirmar que el spec resultante
      lleva `provider_id` cuando el config lo tiene → el worker `_resolve_by_provider_id` ya
      hace el resto. Test de integración: agente con `provider_id` de `ollama-cloud` → el spec
      resuelto trae el base_url cloud (no el local).
  > **Verificado, transitivamente**: `dispatch.py` llama a `resolve_model_config_chain` (ya
  > confirmado) y reenvía el dict resuelto sin tocarlo — no necesita mención literal de
  > `provider_id`.
- [x] **`cost_resolution.resolve_plan_task_models`**: hereda automáticamente al usar la
      misma cadena; verificar coste con provider_id.
  > **Verificado (2026-07-08)**: `chat/cost_resolution.py:106` llama a
  > `resolve_model_config_chain` (la cadena ya confirmada arriba propaga `provider_id`
  > verbatim) — hereda automáticamente, como predecía el item.

## Fase 3 — Selector reutilizable (frontend, el corazón del mensaje del operador)

- [x] **Extraer `ProviderModelSelects` compartido** (hoy privado en `model-cards.tsx` / la
      lógica de `chat-model-section.tsx`) a `components/capability/` (o `components/ui/`):
      consume `GET /agents/provider-options`, dropdown de filas concretas (`display_name (kind)`),
      emite `{provider_id, provider:kind, model, temperature?, reasoning_effort?}`, reasoning por
      kind de la fila, maneja "provider borrado/inactivo" (como cortex/asistente).
  > **Verificado**: `components/capability/provider-model-selects.tsx` existe y lo reutilizan
  > `chat-model-section.tsx`, `persona-section.tsx`, `model-cards.tsx`.
- [x] **`persona.ts`**: añadir `provider_id` a `ModelConfigDraft`, `buildModelConfig`,
      `draftFromConfig` (reconstruir provider_id; vacío si legacy solo-kind), `validateDraft`.
      `PROVIDER_KINDS`/`PROVIDER_LABEL` pasan a "etiqueta del kind heredado", no fuente del dropdown.
  - TDD: `lib/persona/persona.test.ts`.
    > **Verificado**: `lib/persona/persona.ts` — `ModelConfigDraft.provider_id`, `validateDraft`,
    > `draftFromConfig` todos referencian `provider_id` (comentarios "ADR 0082").
- [x] **`PersonaModelFields`** (`persona-section.tsx`): usar el selector compartido (deja
      `/agents/model-options`). El resumen read-only `PersonaSection` muestra `display_name`.
- [x] **Consumidores** (sin cambios de API, heredan el componente): alta de agente
      (`agents/page.tsx`), edición (`agents/[id]/page.tsx`), **adopción de equipo**
      (`adopt-team-dialog.tsx`).
  > **Verificado (2026-07-08)**: las tres superficies usan `PersonaModelFields`
  > (`components/teams/adopt-team-dialog.tsx:29,228` y ambos `agents/*.tsx`), que internamente
  > renderiza el `ProviderModelSelects` compartido.
- [x] **Converger** chat/asistente/córtex/platform-defaults al MISMO componente compartido
      (hoy son variantes equivalentes) para que sea literalmente uno solo. (Si el coste es alto,
      dejar asistente/córtex como follow-up — ya son por-provider correctos.)
  > **Estado (2026-07-08)**: chat (`chat-model-section.tsx`) y asistente
  > (`assistant/settings/model-cards.tsx`) SÍ reutilizan `ProviderModelSelects`; córtex y
  > platform-defaults siguen con variante propia (`settings/platform-defaults/cortex-model-section.tsx`
  >
  > - `GET /owner/cortex/model-options`). Follow-up de prioridad baja permitido por este item.
  >
  > **Cierre (2026-07-26)**: se comparte la REGLA, no el widget. Los componentes NO se fusionan
  > y el motivo es estructural, no pereza: el del córtex vive tras `require_system_owner` y lee
  > `/owner/cortex/model-options`, mientras que el compartido lee `/agents/provider-options`,
  > que exige pertenencia a un tenant — y el córtex es **tenant-less por diseño** (ADR 0074).
  > Además no tiene temperatura. Forzarlos en un componente significaría parametrizar origen de
  > datos, ámbito de autorización y forma del valor para servir a un llamante que difiere de
  > verdad.
  >
  > Lo que sí estaba duplicado —y es lo que se ha extraído a `lib/model-selection.ts` con 9
  > tests— era una regla no obvia, escrita **dos veces byte a byte**: conservar en el
  > desplegable un `reasoning_effort` guardado que el proveedor ya no ofrece. Sin ella el
  > `<select>` no casa con ningún `<option>` y el siguiente guardado **cambia la configuración
  > en silencio**. Es justo el tipo de regla que diverge sin que nadie lo note, porque solo se
  > manifiesta en una configuración concreta.
- [x] **Borrar `DefaultModelSection`** (huérfano, confirmado sin consumidores).
  > **Verificado**: 0 referencias a `DefaultModelSection` en `apps/admin-panel/`.

## Fase 4 — Limpieza + deprecación

- [x] **`GET /agents/model-options` RETIRADO** (con su `AgentModelOptionsResponse`): 67 líneas.
      Cero llamantes en todo el repo, comprobado. Primero lo marqué `deprecated=True` por
      prudencia —«no rompamos un SDK de ahí fuera»— y al verificarlo resultó falso: los SDK se
      generan solo del OpenAPI **v1** (`build_v1_openapi()`) y esta ruta vive en la superficie
      de administración, fuera de `/api/v1`. Sin contrato que proteger y con cero consumidores,
      dejarlo en pie solo conservaba una forma de elegir el proveedor equivocado: agregaba por
      KIND y de dos filas del mismo kind (ollama-local vs ollama-cloud) escondía una.
      Tests en `tests/unit/test_model_options_deprecation.py` (4) para que no vuelva por
      descuido — ni él ni el patrón «por kind» con otro nombre.
- [x] Changelog + `04-reference`. De paso, una imprecisión del `domain-model.md` que invitaba a
      leer una violación del principio 9 donde no la hay: el sync de precios lee el JSON de
      LiteLLM **como feed de datos**, no lo usa como proveedor. El código ya lo decía; la
      referencia, no.

## Backward-compat (transversal, en TODA fase)

- Configs legacy `{provider:kind, model}` (sin provider_id) → resolución por kind→fila-más-
  nueva (worker `_resolve_by_provider_id`→None→camino kind; `_resolve_chat_provider` rama 2).
  **No quitar el camino por kind.**
- Spec `kind` (scripted de tests) pasa intacto.
- `provider_id` que apunta a fila borrada/inactiva → fallback a kind (no romper el run).

## Riesgos (del mapa) a vigilar

- Coexistencia kind+provider_id en la misma columna (mantener ambos).
- `reasoning_effort` sigue por-kind (derivado de `row.kind`), no mover a por-fila.
- `azure_foundry`: el `model` es el _deployment_ que pinea la URL — seguir pasándolo.
- Overlay por kind duplicado worker↔agent-runtime — tocar ambos si cambia.
- Sesión admin BYPASSRLS para resolver provider_id.
- Datos "mentirosos" de platform-defaults (Fase 0).

## Orden de entrega sugerido

Fase 0 (auditoría) → Fase 1 (validación) → Fase 2 (herencia+dispatch) → Fase 3 (selector
reutilizable) → Fase 4 (limpieza). Cada fase deja la plataforma funcionando (backward-compat),
con tests verdes, y es desplegable por sí sola.
