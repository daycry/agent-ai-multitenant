---
plan_id: price-sync-active-providers
title: Sync de precios filtrado a las familias de proveedores LLM activos
status: in_progress
blocking_plan: []
started_at: 2026-06-01
completed_at: null
estimated_duration_calendar: 2-3 días
estimated_effort_person_days: 2
estimated_cost_human_eur: 800 € – 1.500 €
estimated_cost_ai_eur: 25 € – 60 €
created_by: system_architect
spec_sections_referenced: [28]
docs_language: es
---

# Plan price-sync-active-providers — Sync de precios filtrado a proveedores activos

> Plan correctivo (reportado por el operador). `plan_id` descriptivo.

## Cabecera

| Campo           | Valor                              |
| --------------- | ---------------------------------- |
| **ID del Plan** | `price-sync-active-providers`      |
| **Rama git**    | `plan/price-sync-active-providers` |

## Resumen

`/admin/model-prices` sincroniza los ~2000 modelos del feed LiteLLM **sin filtrar** (`pricing/litellm_sync.py`:
`provider = raw["litellm_provider"]`, solo descarta `sample_spec`). El operador pide que **solo sincronice las
familias de los `llm_providers` configurados Y activos** (`is_active=true`): si solo Ollama cloud está activo,
trae modelos `ollama`; si no hay ninguno activo, no trae nada. Sin fallback al catálogo cerrado.

## Alcance

**Entra**:

- **Mapa kind→familias LiteLLM**: `claude_sdk→{anthropic}`, `azure_foundry→{azure, azure_ai, openai}`,
  `copilot→{openai, anthropic}`, `ollama→{ollama}` (constante, ajustable).
- **Resolver de familias activas**: en cada sync, consulta `llm_providers` `WHERE is_active=true`, mapea kinds→
  familias y construye el `allowed_families` (frozenset). Override opcional en `platform_settings`
  (`price_sync.allowed_families`) — si está, manda; si no, se deriva de los activos.
- **Filtro en el sync**: `sync_prices_from_litellm` / `compute_sync_diff` / `apply_sync_from_litellm` aceptan
  `allowed_families`; las entradas del feed con `litellm_provider ∉ allowed_families` se omiten (contabilizadas
  como `skipped: family_not_active`). En re-sync, los modelos del catálogo de familias **fuera** del allowlist se
  tratan como discontinuados → **cierran periodo (no se borran, conservan histórico)**. `allowed_families` vacío
  ⇒ no se sincroniza nada.
- **UI**: `/admin/model-prices` muestra el ámbito activo ("Sincronizando solo: ollama, …") y avisa si no hay
  proveedores activos (no hay nada que sincronizar). Endpoint wiring.
- Tests + changelog.

**Queda fuera**:

- Cambiar el modelo de precios o el snapshot por ejecución (Plan 11).
- Borrar histórico (los periodos cerrados + snapshots se conservan).

## Decisiones clave

- **Derivado de proveedores activos, no lista manual** (con override opcional en settings). Cumple el principio
  de config operable + el requisito exacto del operador.
- **Sin fallback**: 0 proveedores activos ⇒ sync vacío (no el catálogo cerrado).
- **No destructivo**: las familias que salen del allowlist cierran periodo, no se borran (auditoría/facturas).

## Tareas

### Fase A — Filtro en el backend

#### `task_psa_01` — Resolver de familias activas + filtro en el sync + endpoint

- [ ] **Título**: Mapa kind→familias + resolver `active_litellm_families(session)` (query `llm_providers` activos +
      override `platform_settings`); añadir `allowed_families` a `sync_prices_from_litellm`/`compute_sync_diff`/
      `apply_sync_from_litellm` (omitir entradas fuera del allowlist; cerrar periodo de familias fuera en re-sync;
      vacío ⇒ nada); el endpoint `/admin/model-prices/sync` calcula el allowlist y lo pasa.
- **Tests**: `pytest tests/integration/test_price_sync_active_families.py tests/unit/test_litellm_sync.py -v`
  (solo familias activas; 0 activos ⇒ vacío; re-sync cierra familias fuera; override de settings; mapeo correcto)

### Fase B — UI + docs

#### `task_psa_02` — UI del ámbito + changelog

- [ ] **Título**: `/admin/model-prices` muestra "Sincronizando solo: <familias activas>" + aviso si no hay
      proveedores activos; el resultado del sync indica cuántos se omitieron por familia-no-activa. Changelog
      `docs/07-changelog/price-sync-active-providers.md`; nota en ADR 0028; fila en roadmap README.
- **Tests**: admin-panel `typecheck && lint && build` verde; `test -f` changelog

## Tests humanos del Plan

```yaml
- id: human_psa_01
  description: "El sync de precios respeta los proveedores activos"
  checklist:
    - "Con solo Ollama cloud activo, 'sincronizar precios' trae solo modelos ollama (no 2000)"
    - "La pantalla indica 'Sincronizando solo: ollama'"
    - "Activar Azure Foundry y re-sincronizar añade modelos azure/openai"
    - "Desactivar todos los proveedores → el sync no trae nada (aviso claro), sin borrar el histórico"
    - "Los precios ya sincronizados de una familia desactivada quedan como periodo cerrado (no desaparecen)"
```

## Criterios de cierre

1. Tareas `[x]`; `pytest tests/unit tests/integration -v` (nuevos) verde.
2. `pre-commit run --all-files` + admin-panel `typecheck && lint && build` verde.
3. 0 proveedores activos ⇒ sync vacío verificado; familias fuera del allowlist cierran periodo (no borran).
4. Test humano validado.
5. Changelog + fila en README.
6. PR de `plan/price-sync-active-providers` mergeado (lo hace el humano).

## Próximo Plan

Tras este: reorganización del menú (#32) y docs integrales (#24).
