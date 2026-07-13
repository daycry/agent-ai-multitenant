---
adr_id: "0080"
title: "Navegador real (Playwright) para el córtex: interacción/automatización en sandbox con egress controlado"
status: accepted
date: 2026-06-24
authors: [claude-opus]
plan_referenced: cortex-system-owner
docs_language: es
related: ["0067", "0019", "0021", "0074", "0076"]
supersedes: []
---

# ADR 0080 — Navegador real (Playwright) para el córtex

> **Estado: `proposed`** — DISEÑO, no implementado. Abre una superficie de seguridad muy
> superior a la lectura web del ADR 0067 (un navegador completo navega, ejecuta JS, clica,
> rellena formularios). Requiere aprobación explícita del operador antes de tocar código.

## Contexto

El ADR 0067 (`accepted`) da al córtex **leer** la web: `web-search` (Brave/SearXNG) y
`web-fetch` (GET curado de una URL, con anti-SSRF + saneo). Eso cubre "buscar y leer". El
operador pregunta además por **Playwright** — un **navegador real** que pueda **navegar e
interactuar** (login en sitios, clicar, rellenar formularios, scrapear contenido renderizado
por JS). Es una capacidad distinta y mucho más potente/peligrosa que `web-fetch`.

## Por qué es otra liga (vs ADR 0067)

| Eje        | `web-fetch` (ADR 0067)                   | Playwright (este ADR)                                                     |
| ---------- | ---------------------------------------- | ------------------------------------------------------------------------- |
| Qué hace   | UN GET a una URL, devuelve texto saneado | Sesión de navegador: navega, ejecuta JS, clica, sube/baja datos           |
| Egress     | Una petición, allowlist + anti-SSRF      | Tráfico arbitrario del navegador (subrecursos, redirecciones, websockets) |
| Estado     | Sin estado                               | Cookies, sesión, storage; puede autenticarse en sitios                    |
| Recursos   | Baratísimo                               | Browser headless (CPU/RAM), procesos hijos                                |
| Superficie | SSRF (mitigable)                         | SSRF + exfiltración + ejecución de JS hostil + fingerprinting + abuso     |

## Decisión (propuesta)

Ofrecer Playwright como una **tool de córtex de alto privilegio**, ejecutada en un
**runtime de navegador dedicado y sandboxeado**, NO en el proceso del api-server:

1. **Runtime de navegador efímero** (coherente con el Principio 2 / ADR 0019): imagen
   `browser-runtime` con Playwright + Chromium headless, `cap-drop ALL`, seccomp
   default-deny, sin socket Docker, red SOLO hacia el `egress-proxy`. El córtex no ejecuta
   el navegador in-process; lanza/decomisiona el runtime como hace con los runtimes de test.
2. **Egress controlado**: todo el tráfico del navegador sale por el `egress-proxy`
   (allowlist de dominios) + **anti-SSRF** (rechazo de IPs privadas/loopback/link-local y
   metadata cloud) reforzado a nivel de red del contenedor. Límite de tiempo, de páginas y
   de tamaño por sesión; kill-switch global.
3. **Owner-only + validación humana**: la tool de navegador es `require_system_owner` y,
   por su poder, **sensible** (validación humana configurable: en plantillas Producción /
   Cliente Externo exige aprobación por acción/sesión). Presupuesto (budget caps) por sesión.
4. **Salida como DATOS**: el contenido extraído se sanea y entra como datos (nunca se
   ejecuta en el host), igual que `web-fetch`. Capturas / DOM truncados por tamaño.
5. **Catálogo cerrado**: nueva tool de categoría `network` (sin tocar la taxonomía del ADR
   0049); reutiliza el proveedor de egress del ADR 0067.

## Alternativas

1. **Solo ADR 0067 (sin navegador):** suficiente para investigar/leer; es el _default
   seguro_. Playwright solo aporta si se necesita **interacción** real.
2. **Playwright in-process en el api-server:** RECHAZADO — mete un navegador con egress
   arbitrario en el proceso que tiene las credenciales de plataforma; rompe el Principio 2.
3. **Servicio de browser SaaS (Browserless/ScrapingBee):** menos mantenimiento, pero manda
   navegación y posibles credenciales a un tercero; choca con "una sola máquina"/privacidad.

## Preguntas abiertas para el operador

1. ¿Se aprueba un navegador real (Playwright) para el córtex, con su runtime sandbox? (sí/no)
2. ¿Self-host (imagen `browser-runtime` propia) o servicio de browser gestionado?
3. ¿Validación humana por sesión de navegación, o confianza total al ser owner-only?
4. ¿Alcance: solo lectura renderizada (scraping JS), o también interacción (login/formularios)?

## Consecuencias

- **+** El córtex podría operar sitios que requieren JS/login que `web-fetch` no alcanza.
- **−** La mayor superficie de ataque del sistema hasta la fecha. Por eso va en runtime
  sandbox + egress-proxy + anti-SSRF + validación humana + budget/kill-switch, y por eso
  este ADR está `proposed`. Si no se aprueba, el córtex se queda con `web-search`/`web-fetch`
  (ADR 0067), que cubren la mayoría de necesidades de "consultar internet".

## Ratificación del operador (2026-07-13)

Las 4 preguntas abiertas quedaron respondidas por el operador:

1. **Aprobado**: si al navegador real en runtime sandbox dedicado.
2. **Self-host**: imagen `browser-runtime` propia (Playwright+Chromium headless), coherente con una-sola-maquina y privacidad.
3. **Validacion humana POR SESION**: cada sesion de navegacion requiere aprobacion explicita del owner antes de ejecutarse.
4. **Alcance: interaccion completa** (lectura JS + login/formularios/clicks con estado), bajo los controles del punto 3 + budgets + kill-switch + egress-proxy + anti-SSRF.
