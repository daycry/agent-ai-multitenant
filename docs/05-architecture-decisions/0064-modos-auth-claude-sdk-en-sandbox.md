---
adr_id: "0064"
title: "Modos de autenticación de claude_sdk (API key y suscripción) en el sandbox"
status: accepted
date: 2026-06-18
decided_at: 2026-06-18
decided_by: claude-code (delegación explícita del operador — "hazlo todo de forma autónoma, decisión profesional")
authors: [claude-code-2026-06]
plan_referenced: 06-testing-revision-git
docs_language: es
extends: ["0021", "0057"]
---

# ADR 0064 — Modos de autenticación de `claude_sdk` en el sandbox

> **Estado: `accepted`** (2026-06-18). Extiende ADR 0021 (catálogo cerrado) y
> ADR 0057 (resolución del modelo en el worker). NO añade un 5º proveedor: las
> dos vías de auth viven en el mismo kind `claude_sdk`.

## Contexto

El operador quería usar **Claude Agent SDK** para los agentes (mucho más capaz
que el modelo local 1b) y poder configurarlo **desde la UI** con la credencial
en **Vault**, en sus **dos modos**:

- **API key** de Anthropic (`sk-ant-…`), facturación por API.
- **Suscripción Pro/Max**, sin consumir créditos de API.

La auditoría (workflow multi-agente, 2026-06-18) encontró que la cadena estaba
**rota**: la UI/Vault solo capturaba un `oauth_token` genérico, y la rama
`claude_sdk` de la resolución del modelo era un **no-op** — la credencial de
Vault **nunca llegaba al contenedor** del agente, así que un agente `claude_sdk`
no se autenticaba. Además, el SDK distingue dos variables de entorno:
`ANTHROPIC_API_KEY` (API key) y `CLAUDE_CODE_OAUTH_TOKEN` (token de suscripción
de `claude setup-token`).

## Decisión

Ambos modos sobre el **mismo kind `claude_sdk`**, distinguidos por **qué campo
de credencial** se guarda en Vault:

| Modo UI             | Campo Vault   | Spec resuelto      | Env var en el sandbox     |
| ------------------- | ------------- | ------------------ | ------------------------- |
| API key             | `api_key`     | `spec.api_key`     | `ANTHROPIC_API_KEY`       |
| Suscripción Pro/Max | `oauth_token` | `spec.oauth_token` | `CLAUDE_CODE_OAUTH_TOKEN` |

Cableado (capa a capa, TDD):

1. **Schema/router** (`schemas/llm_providers.py`): `claude_sdk` acepta `api_key`
   **o** `oauth_token` (al menos uno en create); el secreto se escribe en Vault
   bajo ese campo (la BD solo guarda `secret_vault_path`, ADR 0028).
2. **UI** (`admin/llm-providers`): selector **Modo de autenticación** (API key /
   Suscripción) que enruta la credencial al campo correcto, con etiqueta clara.
3. **Worker** (`model_resolver._overlay_provider_fields`): traslada
   `secret['api_key']`/`secret['oauth_token']` al spec (antes se descartaban).
   La credencial viaja al contenedor dentro de `AGENT_TASK_SPEC['model']`, igual
   que el resto de kinds (azure/ollama/copilot) — **sin** env var aparte ni
   montar `~/.claude` (eso rompería el aislamiento, principio #2).
4. **Agent-runtime** (`providers.build_provider_client` + `ClaudeSDKModelClient`):
   lee `spec.api_key`/`spec.oauth_token` y los pasa a `ClaudeAgentProvider`.
   `_overlay_resolved` espeja al worker.
5. **shared-llm** (`ClaudeAgentProvider`): `api_key` → `ANTHROPIC_API_KEY`;
   `oauth_token` → `CLAUDE_CODE_OAUTH_TOKEN` (nuevo). Sin ninguno: auth ambiental.
6. **api-server** (`factory._build_claude`): pasa ambos campos a
   `ClaudeAgentProvider` para las rutas que no pasan por el sandbox (asistente,
   liveness, test de conexión).

Red: el sandbox alcanza `api.anthropic.com` por el **egress-proxy** (ya
allowlisted, ADR 0019).

## Consecuencias

- **Positivo:** un agente `claude_sdk` se autentica de verdad en el sandbox, en
  cualquiera de los dos modos, configurable 100% desde la UI con el secreto solo
  en Vault. La credencial nunca se loguea (`safe_spec_summary` solo expone
  `has_credential`).
- **Seguridad:** la credencial viaja en `AGENT_TASK_SPEC` (env del contenedor
  efímero), el mismo canal que las demás credenciales de proveedor; el contenedor
  es la frontera de confianza. No se monta el home del usuario.
- **Aislamiento intacto:** no se introduce ningún montaje del host ni login
  interactivo en el sandbox.

## Alternativas descartadas

- **Montar `~/.claude` en el contenedor** (para la suscripción): rompe el
  aislamiento (principio #2) y acopla el sandbox al home del operador.
- **Un 5º proveedor `anthropic_api`**: innecesario — el SDK admite ambos modos;
  un kind nuevo pediría romper el catálogo cerrado (ADR 0021) sin beneficio.
- **Device-flow interactivo para Claude** (como Copilot): el SDK de suscripción
  usa un token de `claude setup-token` que el operador pega; un device-flow
  propio es alcance futuro si Anthropic lo ofrece.
