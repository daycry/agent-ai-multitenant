---
plan_id: adr-0056-ollama-stack
title: Ollama en el stack (embeddings + GPU opcional) + paridad de monitoring
completed_at: 2026-06-09
docs_language: es
---

# ADR 0056 — Ollama en el stack (embeddings + GPU) + paridad de monitoring

## Resumen

Ollama deja de asumirse "instalado en el host" y pasa a ser un **servicio del
stack** con modo **`none` / `cpu` / `gpu`**, de modo que los **embeddings
locales funcionan out-of-the-box** (dev e instalador). Se añade **gestión
nativa** de Ollama en el admin (sin Open WebUI) y se cierra un **gap de paridad
de monitoring** del instalador. Mergeado en **PR #38**.

## Cambios

- **Embeddings configurables** (`API_SERVER_EMBEDDING_MODEL`, default al nombre
  real del registro `nomic-embed-text`; el sufijo `-v1.5` daba `model not found`).
  Un `model_id` explícito sigue ganando.
- **Descubrimiento** de embedders (ADR 0056 S-C): `ingestion/embedding_models.py`
  (allowlist curada + compat 768 + sonda `/api/tags`) y endpoint admin
  `GET /admin/embeddings/available-models`.
- **Stack Docker (dev):** servicios `ollama` (CPU) + `ollama-bootstrap` (one-shot
  `ollama pull`) + volumen; overlay `docker-compose.gpu.yml` (reserva NVIDIA);
  override `docker-compose.windows.yml` (arranca `node-exporter` en WSL2).
- **Instalador:** `gpu_enabled` → `ollama_mode` (none/cpu/gpu), reserva NVIDIA
  solo en `gpu`, bootstrap, cableado del embedder; UI con selector 3-vías +
  modelo. Compat `gpu_enabled→gpu`.
- **Admin nativo (U-B, no Open WebUI):** `GET/POST(pull)/DELETE /admin/ollama/models`
  - página `/admin/ollama` (Embeddings + gestión de modelos), System Admin.
- **Monitoring (fix de paridad):** el instalador ya genera **Alertmanager +
  cAdvisor** (antes solo prometheus/node-exporter/grafana).

## Notas

- **Tope de diseño:** pgvector está clavado en **768 dims** y el
  `embedding_model_id` de una KB es inmutable con chunks → el modelo se **fija**
  (no swap en vivo); el re-embed masivo es del **Plan 12**.
- **Docs:** referencia [stack-services.md](../04-reference/stack-services.md),
  runbook [ollama-gpu-setup.md](../06-runbooks/ollama-gpu-setup.md) y gotchas del
  naming y de node-exporter en Windows.
