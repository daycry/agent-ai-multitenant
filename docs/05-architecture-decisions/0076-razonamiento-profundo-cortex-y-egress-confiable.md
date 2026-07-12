---
adr_id: "0076"
title: "Razonamiento profundo del Córtex sobre claude_sdk agéntico y egress confiable del api-server"
status: proposed
date: 2026-06-22
authors: [claude-opus, workflow-diseno-cortex]
plan_referenced: cortex-system-owner
docs_language: es
related: ["0074", "0021", "0070", "0064", "0067"]
supersedes: []
---

# ADR 0076 — Razonamiento profundo del Córtex y egress confiable

> **Estado: `proposed`** — define el "razonamiento profundo" y la "búsqueda en Internet" del córtex sin abrir egress en los runtimes de agente. **Requiere aprobación del operador.**

## Contexto

La visión pide **razonamiento profundo** y **búsqueda en Internet**. El catálogo LLM es cerrado (ADR 0021). ADR 0067 (web-search/fetch desde runtimes) está `proposed`/gated porque abre egress en el sandbox de agentes. El córtex, en cambio, corre **dentro del api-server (servicio confiable)**.

## Decisión

1. **Razonamiento profundo = `claude_sdk` en modo agéntico** (`run_agent` con `effort high|xhigh|max`) y/o `reasoning_effort` (ADR 0070). No hay 5º proveedor ni "tool de razonamiento" externa.
2. **Fix bloqueante:** `ClaudeAgentProvider.run_agent` hoy llama `_build_options` **sin** `effort` (`claude_agent.py:425-430`) — hay que añadir el parámetro y propagarlo, o el effort se ignora en silencio.
3. **Egress recomendado:** **WebSearch/WebFetch nativas del Claude Agent SDK** vía `ClaudeAgentOptions.allowed_tools`. La salida es la del api-server (internet directo por `agentic-net`); Anthropic gestiona el fetch → **anti-SSRF gratis, sin abrir egress en runtimes, sin depender del ADR 0067**.
4. **Camino degradado** (owner sin claude_sdk): tool web propia desde el api-server con **anti-SSRF OBLIGATORIO** (un fetch sin anti-SSRF desde el api-server confiable alcanza Vault/red interna/metadata — peor que en sandbox). **Requiere su propio ADR** antes de implementar.
5. `claude-agent-sdk` es dependencia **opcional** (extra `claude`, ADR 0064): degradar limpio a loop clásico/503 si no está. Secretos solo en Vault.

## Consecuencias

- ✅ Cumple ADR 0021; obtiene búsqueda web con anti-SSRF sin tocar el aislamiento del sandbox.
- ⚠️ Depende de que la imagen del api-server traiga `WITH_CLAUDE`. Sin él, no hay búsqueda web (camino degradado gated).
- ⚠️ **Prerequisito de seguridad:** arreglar antes el hallazgo "credencial en `os.environ` global" de `ClaudeAgentProvider` (auditoría, zona LLM providers).

## Estado de implementación (2026-07-12)

PARCIAL y con divergencia deliberada — se mantiene `proposed` como registro fiel. Lo implementado: transporte claude_sdk del cortex pineado por test (`test_cortex_claude_sdk_transport`), dependencia opcional (extra `claude`) y degradacion limpia. La via de web elegida en la practica fue el CAMINO DEGRADADO del punto 4 — tool web propia desde el api-server con anti-SSRF obligatorio (`ssrf_guard`, searxng, `cortex.web_enabled`) — porque el owner del stack dev usa gpt-oss/Ollama, sin claude_sdk. El punto 3 (WebSearch nativa del SDK) sigue siendo lo recomendado cuando el owner tenga claude_sdk.
