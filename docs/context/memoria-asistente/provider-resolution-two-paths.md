---
name: provider-resolution-two-paths
description: Hay DOS vías de resolver un LLM provider — por provider_id (sync/asistente/test) vs por kind (dispatch). No confundirlas.
metadata:
  node_type: memory
  type: project
  originSessionId: 8bbedf68-c595-435e-a78b-1d7720411f7e
---

> **DOCUMENTADO EN EL REPO (2026-07-26)**: `docs/03-guides/gotchas/llm-provider-resolution-two-paths.md`. La fuente de verdad es esa; esta nota queda como puntero.

El catálogo de `llm_providers` puede tener **varios proveedores del mismo `kind`** (p. ej. `ollama-local` + `ollama-cloud`). Hay **dos vías** de resolver cuál usar, y no deben mezclarse:

1. **Por `provider_id` (fila concreta)** — operaciones donde el operador eligió UN proveedor: `sync-models`, `test-connection`, y el **asistente** (guarda `provider_id`). Deben usar el `base_url` + secreto (`secret_vault_path`) de **esa** fila. `build_llm_provider(provider_id)` es esta vía.
2. **Por `kind` (más nuevo activo)** — el **dispatch de agentes**: `model_config.provider` es un _kind_, y `resolve_provider_config(kind)` devuelve el proveedor **más nuevo ACTIVO** de ese kind (`rows[0]`). Esta vía NO pasa por `build_llm_provider`.

**Why:** el bug del PR #46 fue que `build_llm_provider(provider_id)` resolvía por kind → sincronizar `ollama-cloud` traía modelos de `ollama-local` (más nuevo+activo). Fix: usa la fila concreta (como `test-connection`).

**How to apply:** si añades una operación sobre un proveedor concreto, usa su fila (no el resolver por kind). El resolver por kind es solo para el dispatch. Relacionado: [[estado-trabajo-en-curso]] (en dev, `ollama-local` quedó ACTIVO y más nuevo → el dispatch de agentes resuelve a local `llama3.2:1b`; para que los agentes usen cloud `qwen3-coder` hay que desactivar local o que el agente fije el provider).
