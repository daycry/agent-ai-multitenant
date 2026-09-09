---
plan_id: remediacion-instalador-runs-de-serie-2026-09-09
title: Remediación del instalador — una instalación limpia ejecuta runs de serie
completed_at: null
docs_language: es
---

# Plan remediacion-instalador-runs-de-serie-2026-09-09 — runs de serie

## Resumen

Una instalación limpia con Ollama como proveedor **no podía ejecutar ni un run**: el
instalador validaba «≥ 1 proveedor» en el `install.yaml` y después no lo
materializaba en ningún sitio. Cuatro huecos encadenados —variables que nadie leía,
un modelo por defecto hardcodeado a `claude_sdk`, un `base_url` de Ollama sin el
`/v1` que el cliente exige, y un bootstrap que sólo bajaba el modelo de
embeddings— y cada uno bastaba solo para que el primer run muriera. Se
encontraron depurando en vivo el primer run real de una máquina nueva
(2026-09-09) y se cerraron el mismo día con TDD.

## Cambios por tarea

- **`task_inst_01`** — `normalize_ollama_base_url` en el esquema (Create) y el router
  (Update): el `/v1` se añade exactamente una vez y sólo para `kind=ollama`.
- **`task_inst_02`** — `OllamaProvider.chat_model` en el `install.yaml` (default
  `qwen2.5:3b`, con tool-calling verificado) y `ollama-bootstrap` baja embeddings
  **y** chat encadenados con `&&`.
- **`task_inst_03`** — el `.env` y el compose emiten `API_SERVER_LLM_OLLAMA_*` (lo que
  el api-server lee) y dejan de emitir las `LLM_<KIND>_*` huérfanas de los cuatro
  kinds; `Settings` gana `llm_ollama_enabled/endpoint/chat_model`;
  `seeds/init_llm_providers.py` siembra (idempotente) la fila desde
  `init_tenant._amain`. La exención falsa de `test_compose_env_contract` («las lee
  shared-llm») quedó reescrita.
- **`task_inst_04`** — la misma siembra fija `model.default_config` a
  `ollama/<chat_model>` si nadie lo fijó antes; nunca pisa un default del operador.

## Decisiones que conviene conocer

- **Sólo se siembra Ollama.** Los otros tres kinds necesitan un secreto que el
  instalador no escribe en Vault; sembrar una fila sin credencial sería la misma
  mentira que la variable huérfana. Van por el panel.
- **`DEFAULT_MODEL_CONFIG` no cambia** (`claude_sdk`/`claude-sonnet-5`): sigue
  siendo el fallback de código cuando no hay ajuste de plataforma. Lo que cambia es
  que el instalador FIJA el ajuste cuando sabe que sólo hay Ollama.

## Cómo verificarlo

- `pytest tests/unit/test_ollama_base_url_has_v1.py tests/unit/installer/ tests/unit/test_compose_generator.py tests/unit/test_compose_env_contract.py`
  → 294 en verde el 2026-09-09.
- `pytest tests/integration/test_seed_init_tenant.py` → 8 en verde (corre en local
  contra el Postgres del stack de dev).
- El test humano `human_inst_01`: instalación desde cero con `minimal.yaml` y un run
  que llega a `awaiting_human_approval` o `done` sin tocar nada.

## Documentación tocada

- `docs/06-runbooks/01-installation-from-scratch.md` — qué siembra el instalador y
  qué comprobar tras instalar.
- `scripts/install-profiles/*.yaml` — `chat_model` explícito en los tres perfiles.
