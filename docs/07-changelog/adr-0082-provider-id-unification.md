---
title: "Unificación de selección+resolución de modelo por provider_id (ADR 0082)"
date: 2026-06-25
adr: "0082"
docs_language: es
---

# Unificación de modelo por `provider_id`

**Problema:** con dos providers `ollama` activos (local + cloud), la selección y la
resolución de modelo de los AGENTES iban "por kind → fila más nueva", así que no se
podía elegir `ollama-cloud` (ni el selector lo mostraba, ni el runtime lo alcanzaba). El
chat/asistente/córtex ya iban "por provider concreto"; faltaba unificar.

**Cambio (ADR 0082):** `{provider_id, model}` es la forma canónica en toda la plataforma
(con `provider`=kind alongside + fallback a kind→fila-más-nueva para configs legacy).

- **Backend:** `validate_model_config` acepta la forma pinned por `provider_id`;
  `config_needs_default_model` la reconoce como pin; la herencia + el dispatch propagan
  `provider_id` al worker, cuyo `_resolve_by_provider_id` resuelve la fila exacta.
- **Frontend:** nuevo `ProviderModelSelects` reutilizable (consume
  `/agents/provider-options`, lista cada fila concreta). Lo usan persona/agente/equipo/
  adopción de equipo. Borrado el `DefaultModelSection` huérfano.
- **Diagnóstico claude_sdk:** el adaptador surfacea el motivo real del CLI ("Not logged
  in") como `AuthError` en vez del críptico "error result: success".

**Efecto:** se puede elegir `ollama-cloud` (o cualquier 2ª fila de un kind) para los
agentes; el default de plataforma (ollama-cloud) se honra correctamente.

**Compatibilidad:** los configs legacy `{provider:kind, model}` siguen resolviendo por
kind→fila-más-nueva. Sin migración de datos (ningún agente/equipo/proyecto tenía
`provider_id`).

**Follow-ups:** converger `chat-model-section` al mismo `ProviderModelSelects`; deprecar
`GET /agents/model-options` (ya sin consumidores de frontend).
