---
plan_id: remediacion-instalador-runs-de-serie-2026-09-09
title: Remediación del instalador — una instalación limpia ejecuta runs de serie
status: pending_human_validation
blocking_plan: []
started_at: 2026-09-09
completed_at: null
estimated_duration_calendar: 1 día
estimated_effort_person_days: 1
created_by: claude-fable-5-1-depuracion-2026-09-09
docs_language: es
priority: P0
source_audit: depuración en vivo del 2026-09-09 (primer run real de la plataforma en una máquina nueva; ver CONTINUE_HERE §0)
---

# Remediación del instalador — runs de serie (2026-09-09)

## Cabecera

| Campo             | Valor                                                                                                                                        |
| ----------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| **ID del Plan**   | `remediacion-instalador-runs-de-serie-2026-09-09`                                                                                            |
| **Prioridad**     | P0 — sin esto, TODA instalación limpia con Ollama arranca sin proveedor y ningún run se ejecuta                                              |
| **Bloqueado por** | Ninguno. Rama `plan/instalador-runs-de-serie-2026-09-09`, sobre la del plan de UI (necesita el arreglo del bridge por ejecución, `55cf4722`) |
| **Método**        | TDD: cada hueco con su test en rojo antes del código; integración contra el Postgres local                                                   |
| **Origen**        | Orden del operador: «aplica al instalador todo lo aprendido hoy, y probamos desde una instalación nueva»                                     |

## Resumen

El 2026-09-09 se consiguió el primer run real de la plataforma en una máquina nueva,
y el camino hasta ahí destapó que **una instalación limpia no puede ejecutar ni un
run**: el instalador valida que el `install.yaml` traiga ≥ 1 proveedor LLM y
después no lo materializa en ningún sitio. Cuatro huecos encadenados, cada uno
suficiente por sí solo para que el primer run muera:

- **G1** — Emitía `LLM_OLLAMA_ENABLED`/`LLM_OLLAMA_ENDPOINT` al `.env` y al compose,
  y **nadie las leía** (el api-server sólo lee `API_SERVER_*`; shared-llm no lee
  entorno; `init_tenant` no creaba proveedores). `llm_providers` vacía →
  `model_unresolved`. El test de contrato del `.env` las eximía «porque las lee
  shared-llm», que era falso.
- **G2** — `DEFAULT_MODEL_CONFIG` es `claude_sdk`/`claude-sonnet-5` hardcodeado y el
  ajuste de plataforma que lo sobreescribe nadie lo fijaba al instalar → los agentes
  sembrados irresolubles en una instalación solo-Ollama.
- **G3** — El `base_url` de Ollama **tiene** que llevar `/v1` (el cliente hace
  `POST {base_url}/chat/completions`) y ni el instalador ni la API lo normalizaban →
  `404 page not found` en el primer `plan`.
- **G4** — `ollama-bootstrap` bajaba sólo el modelo de embeddings; ningún modelo de
  chat, y menos uno con **tool-calling** (el runtime es un bucle LangGraph con tools;
  `orca-mini` responde «does not support tools»).

## Criterios de cierre del plan

1. Las cuatro casillas `[x]` con su test en verde (unit + integración).
2. Suites `unit` + `security` + `docs` + integración de seeds sin regresiones.
3. Test humano `human_inst_01` validado por el operador.
4. Entrada en `docs/07-changelog/remediacion-instalador-runs-de-serie-2026-09-09.md`.

## Tareas

### `task_inst_01` — El `base_url` de Ollama lleva `/v1` lo escriba quien lo escriba (G3)

- [x] **Título**: `normalize_ollama_base_url` (puro) en `schemas/llm_providers.py`, aplicado en el
      `Create` (esquema) y en el `Update` (router, donde el `kind` se conoce por la fila). Los demás
      kinds no se tocan: `/v1` es la convención OpenAI-compatible de Ollama, no de APIM.
      **Test**: `tests/unit/test_ollama_base_url_has_v1.py` (6 casos de normalización + create + APIM
      intacto). **Coste**: 0,1 d.
      **Entregada el 2026-09-09.** Vista en rojo (`ImportError`) antes del código.

### `task_inst_02` — El bootstrap baja también un modelo de chat con tool-calling (G4)

- [x] **Título**: `OllamaProvider.chat_model` en el `install.yaml` (default `qwen2.5:3b`, verificado con
      `tool_calls` reales el 2026-09-09; vacío → error de config), y `ollama-bootstrap` hace
      `ollama pull <embeddings> && ollama pull <chat>` (el `&&` es a propósito: si el primero falla el
      one-shot sale distinto de cero y `up --wait` lo cuenta).
      **Test**: `test_compose_generator.py::test_bootstrap_pulls_the_chat_model_too` y
      `::test_the_default_chat_model_supports_tool_calling`. **Coste**: 0,1 d.
      **Entregada el 2026-09-09.**

### `task_inst_03` — El proveedor que el instalador configuró existe al arrancar (G1)

- [x] **Título**: el `.env` y el compose emiten `API_SERVER_LLM_OLLAMA_ENABLED`/`_ENDPOINT`/`_CHAT_MODEL`
      (las únicas que el api-server lee; `Settings` gana los tres campos), y desaparecen las
      `LLM_<KIND>_*` huérfanas de los cuatro kinds. `init_tenant._amain` llama a
      `seeds/init_llm_providers.py::ensure_llm_providers_from_settings`, que crea (idempotente por
      slug `ollama`) la fila con el `/v1`. Sólo Ollama: es el único kind sembrable sin credencial; los
      otros tres se configuran desde el panel, y una variable que prometa lo contrario mentiría.
      **Test**: `tests/unit/installer/test_ollama_seed_env.py` (4), `test_compose_env_contract`
      (ahora SÍ valida esas claves contra `Settings`; su exención falsa quedó reescrita),
      `test_compose_generator::test_provider_toggle_*` (reescritos al contrato real) e
      `tests/integration/test_seed_init_tenant.py` (fila creada con `/v1`; idempotente; nada si
      no está habilitado). **Coste**: 0,4 d.
      **Entregada el 2026-09-09.**

### `task_inst_04` — El modelo por defecto de plataforma apunta al proveedor que existe (G2)

- [x] **Título**: en la misma siembra, si `model.default_config` no está fijado se pone a
      `{provider: ollama, model: <chat_model>}`. **No pisa** uno ya configurado: si el operador lo
      cambió desde el panel, manda el operador. Con `claude_sdk` habilitado desde el panel, el
      operador decide el default; `DEFAULT_MODEL_CONFIG` no cambia.
      **Test**: `test_seed_init_tenant.py::test_seed_creates_the_ollama_provider_with_v1_and_a_platform_default_model`
      y `::test_seed_does_nothing_when_ollama_is_not_enabled`. **Coste**: 0,2 d.
      **Entregada el 2026-09-09.**

## Tests humanos

| ID              | Qué valida                                                                                                                                                                                                                                                          |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `human_inst_01` | Instalación **desde cero** (sin ningún contenedor previo) con el perfil `minimal.yaml`: al terminar, `/admin/llm-providers` muestra `Ollama` activo con `…/v1`, y una tarea `ready` de un agente sembrado llega a `awaiting_human_approval` o `done` sin tocar nada |

## Lo que este plan NO hace, y por qué

- **No siembra los kinds con credencial** (`claude_sdk`, `copilot`, `azure_foundry`): el
  instalador no escribe sus secretos en Vault, y una fila sin credencial es la misma
  mentira que la variable huérfana. Es trabajo del follow-up del instalador (prod-09).
- **No adapta el instalador a Docker Desktop**: el bind `{data_root}:{data_root}` del
  worker es correcto para un daemon Linux nativo y se rompe bajo Docker Desktop
  (gotcha `worktree-bind-dood-empty-vs-named-volume`). La instalación limpia de
  `human_inst_01` se hace sobre un Linux con daemon nativo (WSL2 con Docker Engine).
