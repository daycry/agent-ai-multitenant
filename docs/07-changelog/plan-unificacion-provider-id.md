---
plan_id: plan-unificacion-provider-id
title: Unificación de la selección de modelo por provider_id (ADR 0082)
completed_at: null
docs_language: es
---

# Plan plan-unificacion-provider-id — Selección de modelo por `provider_id`

## Resumen

Antes de este plan, la UI elegía el modelo **por kind** (`anthropic`, `ollama`,
`azure_foundry`, `copilot`) y el backend resolvía cada kind al proveedor activo
**más nuevo**. Con dos filas del mismo kind —el caso real: Ollama local y Ollama
cloud— eso **escondía una de las dos** y no había forma de fijar la que se
quería. El ADR 0082 cambió la unidad de selección a la fila concreta
(`provider_id`), y este plan lo llevó a todas las superficies.

## Cierre (2026-07-26)

Las fases 1 a 3 estaban entregadas; quedaban tres cabos:

- **Convergencia de los cuatro selectores.** Se comparte la **regla**, no el
  widget. Los componentes no se fusionan por un motivo estructural: el del
  córtex vive tras `require_system_owner` y lee `/owner/cortex/model-options`,
  mientras que el compartido lee `/agents/provider-options`, que exige
  pertenencia a un tenant — y el córtex es **tenant-less por diseño** (ADR
  0074). Fusionarlos obligaría a parametrizar origen de datos, ámbito de
  autorización y forma del valor para servir a un llamante que difiere de
  verdad.

  Lo que sí estaba duplicado, y se ha extraído a `lib/model-selection.ts` (9
  tests), era una regla no obvia escrita **dos veces byte a byte**: conservar en
  el desplegable un `reasoning_effort` guardado que el proveedor ya no ofrece.
  Sin ella el `<select>` no casa con ningún `<option>` y el siguiente guardado
  **cambia la configuración en silencio**. Es el tipo de regla que diverge sin
  que nadie lo note, porque solo se manifiesta en una configuración concreta.

- **`GET /agents/model-options` retirado** con su esquema: 67 líneas. Cero
  llamantes en todo el repo. Se marcó primero como deprecado por prudencia —«no
  rompamos un SDK de ahí fuera»— y al comprobarlo resultó falso: los SDK se
  generan solo del OpenAPI **v1** (`build_v1_openapi()`) y esta ruta vive en la
  superficie de administración, fuera de `/api/v1`. Sin contrato que proteger,
  dejarlo en pie solo conservaba una forma de elegir el proveedor equivocado.

- **Referencia**: `domain-model.md` decía que el catálogo de precios lo alimenta
  «el sync LiteLLM», lo que invita a leer una violación del principio 9 donde no
  la hay. El sync lee el JSON de LiteLLM **como feed de datos**; el catálogo de
  proveedores sigue cerrado a los cuatro del ADR 0021. El código ya lo
  distinguía; la referencia, no.

## Verificación

`lib/model-selection.test.ts` (9) + `tests/unit/test_model_options_deprecation.py`
(4). El resto de suites, en verde tras el cambio.

## Lo que queda fuera

Tests humanos del plan y despliegue. Por eso queda en
`pending_human_validation` y no en `completed`.
