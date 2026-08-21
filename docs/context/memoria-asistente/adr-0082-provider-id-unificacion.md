---
name: adr-0082-provider-id-unificacion
description: "ADR 0082 implementado+desplegado: selección/resolución de modelo por provider_id en toda la plataforma."
metadata:
  node_type: memory
  type: project
  originSessionId: cc6008fc-23fa-4218-be2b-123a3f5cd8cc
---

2026-06-25: **ADR 0082 (accepted) — unificación de modelo por `provider_id`** HECHO y DESPLEGADO en dev (rama `feat/provider-llm-selection`, sin mergear/push aún; el operador decide).

**Problema:** con dos providers ollama activos (local+cloud), la selección/resolución de AGENTES iba "por kind→fila más nueva" → no se podía elegir ollama-cloud. chat/asistente/córtex ya iban por provider_id.

**Solución:** `{provider_id, model}` canónico en toda la plataforma (con `provider`=kind alongside + fallback kind→fila-más-nueva para legacy). Backend: `validate_model_config` + `config_needs_default_model` aceptan/propagan provider_id; el worker `_resolve_by_provider_id` ya resolvía la fila exacta. Frontend: nuevo **`ProviderModelSelects`** reutilizable (consume `/agents/provider-options`) usado por persona/agente/equipo/adopt; borrado `DefaultModelSection` huérfano. Commits: docs → claude_sdk(e5cebaa) → Fase1(f2ad7d9) → Fase2(ce5d2f6) → Fase3(6c81a99) → changelog(e75756e).

**Bonus:** [[bug-asistente-voz-no-funciona]] NO; el bug nuevo era **claude_sdk del asistente** daba "error result: success" críptico → arreglado: el adaptador surfacea el motivo real (AuthError "Not logged in"). PERO el desbloqueo real es que el operador configure la credencial del provider Claude SDK (oauth_token de `claude setup-token` o api_key) en `/admin/llm-providers` — el provider claude_sdk no tiene credencial válida (ADR 0064).

**Deploy:** 5 imágenes (api-server/workers/orchestrator/agent-runtime/admin-panel:manuals) + recreate, todas healthy. GOTCHA Windows: admin-panel se construye con contexto `apps/admin-panel` y **desde PowerShell** (Git Bash mangla `--build-arg NEXT_PUBLIC_API_URL=/api` → `C:/Program Files/Git/api`). Sin migraciones.

**Follow-ups (no urgentes):** converger `chat-model-section` al mismo `ProviderModelSelects`; deprecar `GET /agents/model-options` (ya sin consumidores front). Relacionado [[provider-resolution-two-paths]] (esta unificación generaliza la vía por provider_id).
