---
adr_id: "0076"
title: "Razonamiento profundo del Córtex sobre claude_sdk agéntico y egress confiable del api-server"
status: accepted
date: 2026-06-22
authors: [claude-opus, workflow-diseno-cortex]
plan_referenced: cortex-system-owner
docs_language: es
related: ["0074", "0021", "0070", "0064", "0067"]
supersedes: []
---

# ADR 0076 — Razonamiento profundo del Córtex y egress confiable

> **Estado: `accepted` (2026-07-26)** — define el "razonamiento profundo" y la "búsqueda en Internet" del córtex sin abrir egress en los runtimes de agente. Ver el cierre al final: se acepta CON la divergencia deliberada del punto 3→4 y con el prerequisito de seguridad ya resuelto.

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

## Cierre (2026-07-26)

Se pasa a `accepted` porque los puntos operativos están resueltos y el registro
tenía que dejar de decir «propuesto» sobre algo que lleva meses en producción.
Punto por punto:

- **1 y 5** — hechos: transporte claude_sdk del córtex pineado por test
  (`test_cortex_claude_sdk_transport`), dependencia opcional (extra `claude`) y
  degradación limpia.
- **2** — hecho: `run_agent` recibe `effort` y lo propaga a `_build_options`.
  Era el «se ignora en silencio» que el ADR marcaba como bloqueante.
- **3 vs 4** — **divergencia deliberada y mantenida**: la vía real es el camino
  degradado del punto 4 (tool web propia desde el api-server con anti-SSRF
  obligatorio: `ssrf_guard`, searxng, `cortex.web_enabled`), porque el owner del
  stack usa Ollama y no tiene claude_sdk. El punto 3 (WebSearch nativa del SDK)
  sigue siendo lo recomendado cuando lo tenga. No se cierra como «pendiente»:
  es una bifurcación por entorno, no una tarea sin hacer.
- **Prerequisito de seguridad** — **resuelto hoy**, y era peor de lo que el ADR
  suponía. El constructor escribía la credencial en `os.environ`: global,
  permanente y heredada por cualquier hijo. Lo caro no era la exposición sino
  que el catálogo admite **varias filas del mismo kind** (columna `slug`,
  migración 0083): la clave del proveedor A quedaba puesta para siempre, y un
  proveedor B configurado con suscripción OAuth podía arrancar con la
  `ANTHROPIC_API_KEY` de A todavía en el entorno — **facturando a la cuenta de
  A, en silencio**. Ahora la credencial vive en la instancia y viaja por
  `ClaudeAgentOptions.env`, que el transporte fusiona sobre el entorno heredado.
  Como es fusión y no reemplazo, el modo elegido **anula explícitamente** la
  variable del otro modo: sin eso, una clave rancia heredada seguiría ganando.

Dos cosas que salieron al arreglarlo y conviene no olvidar:

1. **Dos tests fijaban el defecto.** Afirmaban que la clave aterrizaba en
   `os.environ` y por tanto pasaban en verde mientras la vulnerabilidad
   existía. Un test que documenta el comportamiento observado, sin preguntarse
   si es el correcto, convierte un fallo en contrato.
2. **`ClaudeAgentSessionProvider` (ADR 0097) construye sus PROPIAS opciones** en
   vez de reutilizar `_build_options`. Al dejar de escribir el entorno, los runs
   con hilo persistente se habrían quedado sin credencial y en silencio. Hay una
   guarda estática que falla si aparece un cuarto constructor de
   `ClaudeAgentOptions` sin la credencial.
