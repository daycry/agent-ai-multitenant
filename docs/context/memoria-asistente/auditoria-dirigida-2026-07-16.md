---
name: auditoria-dirigida-2026-07-16
description: "AUD16 auditada E IMPLEMENTADA ENTERA (plan pending_human_validation, 23 commits TDD, desplegado dev 07-16 + migración 0113); quedan tests humanos + gated del operador"
metadata:
  node_type: memory
  type: project
  originSessionId: 50eee157-5b9f-4f4f-85b3-9a5c1e232a6e
---

Auditoría dirigida 2026-07-16 ENTREGADA (`docs/roadmap/auditoria-dirigida-2026-07-16.md`) y **REMEDIACIÓN IMPLEMENTADA ENTERA el mismo día** por orden del operador («crea el docs/roadmap e implementalo»): plan `remediacion-auditoria-dirigida-2026-07-16` en `pending_human_validation`, 23 commits TDD (07f0b8a8…2317fea9) en plan/runs-visor-trabajo, changelog en docs/07-changelog. **Desplegado en dev y verificado**: 6 imágenes reconstruidas (api-server:manuals WITH_CLAUDE→workers:ci→dispatcher:manuals, agent-runtime:v1, admin-panel:manuals, php-phpunit:v1), migración 0113 aplicada, 6 memorias duplicadas consolidadas, reglas nuevas en Prometheus y heartbeat del sampler con 4 colectores up=1. Fixes clave: envelope OpenAI submit_result/submit_verdict; inbox de PLATAFORMA + subject/body persistidos; price-snapshot con clave real del runtime; destilador con herencia de modelo; task_comment drenado a PlanComment (resto honestos/des-anunciados); stack_exec_unavailable; audit events sweeper/supersede + administrative_finalize; provider_credential_invalid; login con audit_log; transporte tipado en complete() HTTP. **PENDIENTE**: tests humanos del plan (smoke por kind HTTP, inbox visual, cadvisor/staleness) + gated operador (neonize, canal externo, offsite real, plan demo MVP, ADR 0108, política guardrails/aprobaciones).

**P0 hallados**: (1) AUD16-01 crítico — `submit_result`/`submit_verdict` sin envelope OpenAI hacia ollama/copilot/azure (`agent_runtime/providers.py:179-201,141-159`); test `test_azure_decide_targets_apim_url_with_subscription_key` ROJO desde 27-06; nunca explotó porque el 100% de runs son claude_sdk. (2) AUD16-10/11 — ninguna notif llega a un humano: inbox excluye tenant NULL (todas las filas son platform-scoped) y in_app no persiste body. (3) AUD16-15 — coste facturable ciego 128/128: `claude-opus-4-8` no está en `model_prices`. (4) AUD16-14 — destilador de memorias siempre cae a `llama3.2:1b` (herencia de modelo no materializada en `agents.model_config` → `_build_agent_llm` devuelve None).

**Estado NOTIF actualizado** (corrige [[tanda-inteligencia-2026-07-11]]): WhatsApp body bug ARREGLADO+desplegado (6392a5f0), alerts ingest VIVO e2e verificado con alerta real (b5e45fe7), neonize implementado pero SIN desplegar (profile off, sin QR, sin canal).

**Otros clave**: cAdvisor ciego en Docker Desktop (containerd snapshotter → per-container y OOM-alert muertos); tools de orquestación devuelven ok=true sin aplicar efectos (sink sin drenar); `search_code` anunciado en prompts pero inexistente (7/7 fallos); claude_sdk degrada JSON Schema (pierde enum/required); guardrails/approval_requests/audit_log con CERO dato vivo en toda la historia; backups sin offsite (uploaded=[]); imagen agent-runtime SÍ está a HEAD (refutado).

**Why**: es la foto verificada del sistema a 2026-07-16 (HEAD ebee9680); evita re-auditar y localiza los fixes.
**How to apply**: al implementar remediación, seguir la priorización del informe; el camino HTTP de providers no tiene NINGÚN run e2e jamás — smoke por kind obligatorio tras el fix del envelope.
