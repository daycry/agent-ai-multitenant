---
adr_id: "0067"
title: "Tools de búsqueda web + fetch con egress controlado y guardrails (web-search / web-fetch)"
status: accepted
date: 2026-06-19
authors: [system_architect]
plan_referenced: personalizacion-equipos-built-in
docs_language: es
related: ["0021", "0049", "0048", "0052"]
supersedes: []
---

# ADR 0067 — Tools de búsqueda web + fetch con egress controlado y guardrails

> **Estado: `accepted` (2026-06-24)** — el operador aprobó abrir egress web. Decisiones
> confirmadas a las preguntas abiertas: (1) **sí** se abre egress web; (2) **ambos**
> proveedores soportados y elegibles (SearXNG self-host por defecto + Brave API con key en
> Vault), igual que el catálogo LLM; (3) **ambas** tools (`web-search` + `web-fetch`).
>
> **Primer destino de implementación: el CÓRTEX del system_owner** (host tools ejecutadas
> en el api-server, a través del `egress-proxy` + anti-SSRF, provider-agnósticas: valen para
> claude_sdk/copilot/azure/ollama). El bloque nativo de `claude_sdk` (WebSearch/WebFetch del
> Agent SDK) queda como **fast-path** opcional. La variante de **runtime de agente**
> (per-proyecto `web_egress_allowlist` + guardrails pre/post_tool) es la Ola B0.2 más amplia
> y se implementa después con el mismo diseño. Sigue tocando el **Principio 2** (egress
> controlado), de ahí el anti-SSRF + allowlist + saneo obligatorios.

## Contexto

El diseño aprobado pedía "tools de navegación / búsqueda por Internet". Al
revisar el catálogo cerrado de tools (ADR 0049, categorías {file, runtime,
network, knowledge, notification, command}) resulta que **buena parte ya existe**:

| Pedido B0.2   | Estado real en el catálogo                            |
| ------------- | ----------------------------------------------------- |
| `run-tests`   | ✅ ya: `run-pytest` (categoría `runtime`)             |
| `format-code` | ✅ ya: `run-lint` / `run-build` (`runtime`)           |
| `fetch-url`   | ⚠️ parcial: existe `http-get`/`http-post` (`network`) |
| `web-search`  | ❌ NO existe — es la capacidad genuinamente nueva     |

Por tanto B0.2 se reduce a: (1) una tool **`web-search`** (consulta a un
proveedor de búsqueda) y (2) opcionalmente un **`web-fetch`** curado (GET de una
URL con saneo de contenido para RAG/lectura), distinto del `http-get` crudo
(pensado para APIs internas declaradas, no para navegar Internet abierto).

Ambas requieren que el agente **salga a Internet**, lo que hoy está cerrado por
diseño: los runtimes corren con red restringida y una allowlist de egress (la
`filter.txt` del sandbox). Abrir esto es una decisión de seguridad, no una tool
más — de ahí este ADR.

## Decisión (propuesta)

Añadir dos tools de categoría **`network`** (sin tocar la taxonomía cerrada del
ADR 0049) — `web-search` y `web-fetch` — gobernadas por **egress allowlist +
guardrails en los cuatro puntos del ciclo** (pre_tool / post_tool, ADR de
guardrails). Desglose:

### 1. Proveedor de búsqueda (catálogo cerrado, igual que LLM en ADR 0021)

`web-search` NO scrapea buscadores; llama a una **API de búsqueda** declarada.
Opciones evaluadas:

| Proveedor                          | Pros                                                     | Contras                                          |
| ---------------------------------- | -------------------------------------------------------- | ------------------------------------------------ |
| **Brave Search API** (recomendado) | Privacidad, precio plano, sin tracking, API limpia       | API key de pago                                  |
| Tavily                             | Pensada para agentes/LLM, devuelve texto resumido        | SaaS, datos a un tercero                         |
| **SearXNG** (self-host)            | Sin terceros, sin API key, encaja con "una sola máquina" | Hay que mantener el contenedor; calidad variable |
| Google CSE / Bing                  | Cobertura                                                | Cuotas, ToS estrictos, tracking                  |

**Recomendación:** soportar **dos caminos** como con LLM —
**Brave Search API** (gestionado, key en Vault) y **SearXNG** (self-host, sin
terceros, coherente con el despliegue Docker-Compose de una sola máquina). El
operador elige por tenant/proyecto. La key de Brave vive en **Vault** (nunca en
config ni en el spec del agente; viaja resuelta al runtime igual que las
credenciales LLM del ADR 0057).

### 2. Egress controlado (Principio 2)

- El runtime sigue **deny-by-default**. `web-search`/`web-fetch` se habilitan
  **solo** si el proyecto los tiene asignados Y la **egress allowlist** del
  runtime admite el destino: el endpoint del proveedor de búsqueda y, para
  `web-fetch`, una **allowlist de dominios por proyecto** (`projects` gana un
  `web_egress_allowlist` JSONB, default `[]` = nada).
- **Anti-SSRF (obligatorio):** `web-fetch` resuelve el host y **rechaza** IPs
  privadas/loopback/link-local y endpoints de metadatos cloud (169.254.169.254,
  `*.internal`, etc.). Sin esto, un fetch-url es una puerta a la red interna.
- Sin socket Docker, cap-drop ALL, seccomp default-deny se mantienen: el egress
  se concede a nivel de red del contenedor (allowlist DNS/host), no relajando el
  hardening del proceso.

### 3. Guardrails (declarativos, por capas plataforma→tenant→proyecto)

- **pre_tool** `web-search`/`web-fetch`: valida la URL/host contra la allowlist +
  anti-SSRF; recorta la query; aplica **rate limit** por tenant/proyecto.
- **post_tool**: **sanea** el contenido devuelto (strip de scripts/HTML
  peligroso), **trunca** por tamaño (p. ej. 256 KB), marca la procedencia, y pasa
  el texto por el pipeline de PII si aplica. El contenido externo **nunca** se
  ejecuta; entra como datos.
- Auditoría: cada llamada se registra (URL, bytes, veredicto del guardrail).

### 4. Taxonomía y validación humana

- Categoría `network` (existente) — **sin migración de taxonomía**.
- `web-search`/`web-fetch` se marcan como **acción sensible** (red saliente) en
  el catálogo de validación humana configurable por proyecto: las plantillas
  "Producción" y "Cliente Externo" exigen aprobación; "Sandbox"/"Desarrollo" no.

## Alternativas consideradas

1. **Reusar `http-get` para navegar**: rechazada — `http-get` es para APIs
   internas declaradas; usarlo para Internet abierto sin allowlist+anti-SSRF
   convierte cada agente en un proxy SSRF. `web-fetch` es la versión gobernada.
2. **Scraping de un buscador HTML**: rechazada — frágil, contra ToS, sin saneo
   fiable. Una API de búsqueda es contractual y devuelve datos estructurados.
3. **No añadir nada (statu quo)**: válido si el operador no quiere egress; los
   agentes siguen con KBs/RAG + APIs internas. Es el _default seguro_ si este ADR
   no se aprueba.

## Consecuencias

- **+** Los agentes de investigación (`researcher`/`specialist`, que ya tienen la
  skill `web-research` de la Ola B0.1) podrían **ejecutar** búsquedas, no solo
  "saber" investigar.
- **−** Superficie de ataque nueva (SSRF, exfiltración, contenido hostil). Por eso
  va con allowlist + anti-SSRF + guardrails + validación humana, y por eso este
  ADR está `proposed` y no implementado.
- **Trabajo de implementación (cuando se apruebe):**
  1. Migración `projects.web_egress_allowlist` JSONB `[]`.
  2. Proveedor de búsqueda en `shared-llm`-style (Brave + SearXNG), key en Vault.
  3. Tools `web-search`/`web-fetch` (categoría `network`) + ejecutores en el
     runtime con anti-SSRF.
  4. Guardrails pre/post_tool + rate limit + saneo + auditoría.
  5. Egress allowlist del runtime parametrizada por proyecto.
  6. Tests: anti-SSRF (rechaza IP privada/metadata), allowlist (bloquea dominio no
     listado), saneo/truncado de contenido, key nunca en logs.

## Pregunta abierta para el operador → RESUELTAS (2026-06-24)

1. ¿Se aprueba abrir egress web? → **Sí.**
2. ¿Proveedor? → **Ambos, elegibles** (SearXNG self-host por defecto; Brave API con key en Vault).
3. ¿`web-fetch` además de `web-search`? → **Sí, ambas.**

> Capa de **navegador real (Playwright)** — interacción/automatización, no solo leer — se
> trata APARTE en el **ADR 0080** (sandbox de navegador + egress), por su superficie de
> seguridad mucho mayor.
