---
adr_id: "0062"
title: "Acceso de preview a la app levantada en la validación humana (review-runtime)"
status: accepted
date: 2026-06-18
decided_at: 2026-06-18
decided_by: claude-code (delegación explícita del operador — "implementación completa end-to-end, producto profesional")
authors: [claude-code-2026-06]
plan_referenced: 06-testing-revision-git
docs_language: es
---

# ADR 0062 — Acceso de preview a la app levantada (validación humana)

> **Estado: `accepted`** (2026-06-18). Completa el cableado de extremo a
> extremo del **review-runtime** (Plan 06 Fase G): el diseño existía y el
> backend estaba ~70% escrito, pero **0% conectado** — la URL firmada nunca
> se generaba, no se exponía a la UI, no había forma de que el navegador
> **alcanzara** el puerto de la app, ni de emitir el veredicto.

## Contexto

Cuando todas las tareas de un plan terminan, el plan pasa a
`pending_human_validation` y se levanta un **review-runtime**: un contenedor
persistente con la **app del usuario** servida en `main_port` (8080 por
defecto), el worktree del plan montado, y los servicios auxiliares. El humano
debe **abrir la app en su navegador, probarla** y aprobar/rechazar el plan.

El problema: la red es **zero-trust** (ADR 0060/0061). Los contenedores de
agente/runtime viven en redes internas (`agentic-agents`, `internal: true`),
**sin mapeo de puertos al host** y **sin ruta en Caddy** hacia contenedores de
usuario. Caddy solo publica `/` (admin-panel) y `/api/*` (api-server). No hay
ingress para que un navegador alcance el `main_port` de un review-runtime.

## Decisión

**El api-server es el proxy inverso HMAC del review-runtime.** No se añade
ningún ingress nuevo ni se publica ningún puerto.

1. **Ruta de preview** (en el router de review, raíz del api-server, por tanto
   pública como `/api/review/{session_id}/app/{path}` a través de Caddy):
   `GET|POST /review/{session_id}/app/{path:path}` reenvía la petición al
   servicio principal del contenedor de review (`{main_host}:{main_port}`) y
   devuelve su respuesta (vía `httpx`, en streaming).
2. **Misma autenticación que el resto del review-runtime**: la firma HMAC del
   query (`?exp=&sig=`, ADR/Plan 06 `sign_review_url`/`verify_review_url`). Sin
   JWT — el revisor puede no tener cuenta. La ruta de preview **hereda** la
   firma de la URL de la sesión.
3. **El contenedor de review vive en `agentic-agents`** (red interna que el
   api-server ya comparte) y se direcciona por **nombre determinista**
   (`agentic-review-{session_id}`), persistido en `spec.main_host`. Nunca se
   publica al host.
4. **Veredicto**: `POST /review/{session_id}/verdict` (HMAC) `{verdict,
rejection_reason?}` → marca la sesión terminal, destruye el contenedor y
   transiciona el plan (`completed` / `rejected`).
5. **Exposición a la UI** (con JWT+RBAC, para el panel del operador):
   `GET /plans/{plan_id}/review-session` devuelve la sesión + una URL firmada
   recién emitida, de modo que el admin-panel pueda mostrar un **link
   clicable** "Abrir app para probar".

La URL pública base para firmar es `review_public_base_url`
(`https://{dominio}/api`, igual patrón que `sso_redirect_base_url`; en dev
`http://localhost:8080/api`).

## Alternativas consideradas

- **Subdominio por sesión + ruta dinámica en Caddy** (`{session}.review.dominio`).
  Más limpio para apps con rutas de asset absolutas (`/css/...`), pero exige DNS
  comodín + certificado comodín + reconfigurar Caddy en caliente. **Diferido**
  como mejora futura (la limitación de subpath, abajo).
- **Servicio preview-proxy dedicado.** Otra pieza móvil; el api-server ya está
  en la red correcta y es dueño del secreto HMAC. Rechazado por innecesario.
- **Publicar el puerto del contenedor al host.** Rompe el zero-trust. Rechazado.

## Consecuencias

- **Zero-trust intacto**: la app solo es alcanzable a través de la URL firmada y
  del api-server; jamás directamente. Sin puertos nuevos.
- **Limitación de subpath (conocida)**: la app se sirve bajo
  `/api/review/{id}/app/`. Apps que emiten enlaces/asset **absolutos** (`/css`,
  `/api`) se romperán bajo el subpath; funciona limpio para apps que respetan un
  base path o son simples (el ejemplo _Hello World PHP_). La solución completa
  (subdominio por sesión) queda como mejora futura.
- **HTTP request/response** en v1; tráfico WebSocket de la app propia queda
  fuera de alcance (los logs del runtime sí usan WS, por canal aparte).
- Cierra los GAP 1/3/5 de la investigación 2026-06-18 (URL no expuesta, sin
  veredicto, sin acceso desde el panel).
