---
plan_id: price-sync-active-providers
title: Sync de precios filtrado a las familias de proveedores LLM activos
completed_at: null
docs_language: es
---

# Plan price-sync-active-providers — Sync de precios filtrado a proveedores activos

## Resumen

Plan **correctivo** (reportado por el operador). `/admin/model-prices`
sincronizaba los ~2000 modelos del feed comunitario de LiteLLM **sin filtrar**
(`pricing/litellm_sync.py` mapeaba `provider = raw["litellm_provider"]` y solo
descartaba `sample_spec`). El operador pide que el sync **solo traiga las
familias de los `llm_providers` configurados Y activos** (`is_active=true`): si
solo Ollama cloud está activo, trae modelos `ollama`; si no hay ninguno activo,
no trae nada. **Sin fallback** al catálogo cerrado.

> **No destructivo (GUARDRAIL DURO).** Las familias que salen del allowlist
> **cierran su periodo abierto** (se tratan como descontinuadas en el re-sync),
> **nunca se borran**: el histórico de precios y los snapshots de las
> ejecuciones/facturas quedan intactos (ADR 0028 — snapshot at execution time).

## Cambios por tarea

### Fase A — Filtro en el backend

- ✅ **`task_psa_01`** — **Resolver de familias activas + filtro en el sync +
  endpoint.** En `api_server.pricing.litellm_sync`:
  - **Mapa `KIND_TO_LITELLM_FAMILIES`** (constante, ADR-tracked): `claude_sdk →
{anthropic}`, `azure_foundry → {azure, azure_ai, openai}`, `copilot →
{openai, anthropic}`, `ollama → {ollama}` (ADR 0021 catálogo cerrado → las
    familias `litellm_provider` del feed por ADR 0028).
  - **Resolver `active_litellm_families(session)`**: consulta `llm_providers`
    `WHERE is_active=true`, mapea kinds → familias y construye el
    `allowed_families` (frozenset). **Override opcional** en `platform_settings`
    (`price_sync.allowed_families`): si está, manda; si no, se deriva de los
    activos.
  - **Filtro en el sync**: `sync_prices_from_litellm` / `compute_sync_diff` /
    `apply_sync_from_litellm` aceptan `allowed_families`. Las entradas del feed
    con `litellm_provider ∉ allowed_families` se omiten, contabilizadas como un
    `SkippedEntry` con `reason = family_not_active` (constante
    `SKIP_FAMILY_NOT_ACTIVE`). En re-sync, los modelos del catálogo de familias
    **fuera** del allowlist se tratan como descontinuados → **cierran periodo**
    (no se borran). `allowed_families` **vacío ⇒ no se sincroniza nada**.
  - El endpoint `POST /admin/model-prices/sync[/diff|/apply]` calcula el
    allowlist (resolver) y lo pasa a la capa de sync.

### Fase B — UI + docs

- ✅ **`task_psa_02`** — **UI del ámbito + changelog** (esta entrada).
  `apps/admin-panel/app/admin/model-prices/page.tsx`:
  - Aviso del **ámbito activo** junto a la acción "Sincronizar precios":
    **"Sincronizando solo: `<familias activas>`"** (derivado de los proveedores
    activos vía el mapa `KIND_TO_LITELLM_FAMILIES` espejo del backend), tanto en
    la cabecera de la página (`data-testid="sync-scope-notice"`) como dentro del
    diálogo de sync (`data-testid="sync-dialog-scope"`).
  - **Aviso claro cuando NO hay proveedores activos**
    (`data-testid="sync-scope-empty"` / `sync-dialog-scope-empty`): "no hay
    proveedores LLM activos; nada que sincronizar", con enlace a
    `/admin/llm-providers`.
  - El resumen del diff muestra el **conteo de entradas omitidas por
    familia-no-activa** (`data-testid="sync-skipped-family"`), leído de
    `diff.skipped` filtrando `reason === "family_not_active"`
    (`SKIP_FAMILY_NOT_ACTIVE`).
  - Solo `system_admin` (la lista de proveedores y la acción de sync ya son
    System-Admin only); un lector tenant ni ve el aviso ni dispara el sync.
  - **`data-testid` y comportamiento preservados**; se reutilizan las
    primitivas existentes (`Badge`, `AlertTriangle`/`Info` de lucide, `Link`).
  - Nota añadida a **ADR 0028** (el sync de precios ahora se acota a las
    familias de los proveedores activos) y **fila en `docs/roadmap/README.md`**.

## Mapa kind → familias LiteLLM (ADR 0021 → ADR 0028)

| `llm_providers.kind` | Familias `litellm_provider`   |
| -------------------- | ----------------------------- |
| `claude_sdk`         | `anthropic`                   |
| `azure_foundry`      | `azure`, `azure_ai`, `openai` |
| `copilot`            | `openai`, `anthropic`         |
| `ollama`             | `ollama`                      |

La fuente de verdad es el backend (`KIND_TO_LITELLM_FAMILIES` en
`api_server.pricing.litellm_sync`); el mapa del frontend es solo una pista
visual del ámbito y debe mantenerse en sincronía. Un override
`price_sync.allowed_families` en `platform_settings` (System Admin) puede fijar
un conjunto distinto y, si existe, manda.

## Migraciones

**Ninguna.** Usa las tablas existentes (`llm_providers`, `model_prices`,
`platform_settings`); la cabeza única de Alembic sigue siendo `0075`.

## Verificación

- Backend: `pytest tests/integration/test_price_sync_active_families.py
tests/unit/test_litellm_sync.py -v` ✅ (solo familias activas; 0 activos ⇒
  vacío; re-sync cierra familias fuera del allowlist sin borrar; override de
  settings; mapeo kind → familias correcto).
- admin-panel: `npm run typecheck` ✅, `npm run lint` ✅, `npm run build` ✅.
- `pre-commit` (black/ruff/mypy/prettier/eslint) ✅ en cada commit (sin
  `--no-verify`).

## Pendiente

- **Tests humanos del plan** (`human_psa_01`) — pendientes de ejecutar por un
  humano: con solo Ollama cloud activo, "sincronizar precios" trae solo modelos
  `ollama` (no 2000) y la pantalla indica "Sincronizando solo: ollama"; activar
  Azure Foundry y re-sincronizar añade modelos `azure`/`openai`; desactivar
  todos los proveedores → el sync no trae nada (aviso claro) sin borrar el
  histórico; los precios ya sincronizados de una familia desactivada quedan como
  periodo cerrado (no desaparecen).
- **Merge del PR de `plan/price-sync-active-providers` a `main`** — lo gestiona
  el humano tras los tests humanos. El plan no se marca `completed` aquí.

## PR

Pendiente de apertura/merge a `main` (lo gestiona el humano tras validar los
tests humanos del plan).
