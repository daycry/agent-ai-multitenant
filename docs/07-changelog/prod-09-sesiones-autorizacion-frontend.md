---
plan_id: prod-09-sesiones-autorizacion-frontend
title: Sesiones y autorización de producción — admin hardening, SSO, 401 global y cookies
started_at: 2026-06-15
completed_at: null
status: pending_human_validation
tasks_done: 18
tasks_total: 18
docs_language: es
---

> **Estado: entregado, pendiente de validación humana.** Las 18 casillas están
> marcadas desde el 2026-08-19 y el plan seguía etiquetado `pending_approval`
> («no empezado»). Esta entrada se escribe el 2026-09-29, en la consolidación
> del roadmap, tras volver a ejecutar sus tests declarados. Los tests humanos
> del plan siguen sin validar.

# Changelog — Plan prod-09 · Sesiones y autorización de producción

La auditoría de producción de 2026-06 dio por sólida la base de autenticación
(Argon2id, JWT con sesión en Redis, RBAC centralizado), pero no la capa de sesión.
El endurecimiento admin sólo cubría uno de los diez routers `/admin/*` y dejaba
fuera backup, restore y las credenciales LLM. El guard del secreto JWT era
fail-open. El SSO terminaba enseñando un JSON crudo con el token. El token de
System Admin vivía 24 horas en `localStorage`. Los WebSockets llevaban el JWT en
la query y nunca re-validaban la sesión.

Este plan cierra 20 hallazgos (`authz-1..7`, `frontend-1..6` y `-8`, `secrets-9`
y `-10`, `api-7`, `api-8`, `tenancy-4`, `quality-10`).

## Resultado por fases

- **Fase A — Superficie admin y dominios de secreto (tasks 01-05).** MFA, allowlist
  IP y sesión corta en **toda** la superficie `/admin/*`. El guard del secreto JWT
  y del entorno es fail-closed: un despliegue sin `API_SERVER_ENVIRONMENT` ya no
  firma con el secreto público del repo. Los tokens internos del worker tienen su
  propio secreto HMAC (ADR 0136). La revocación de privilegios de System Admin es
  inmediata. `/auth/register` tiene rate limit y el login es de tiempo constante.
- **Fase B — Sesión del panel (tasks 06-11).** Cookie httpOnly con CSRF en vez de
  `localStorage` (ADR 0133, opción A, con la migración de los specs e2e por un
  helper común). Auto-registro cerrado en producción salvo con la tabla `users`
  vacía, con 403 genérico (ADR 0134). El SSO llega al panel con sesión. Un 401 en
  cualquier llamada redirige a login. El logout purga la caché de TanStack.
- **Fase C — WebSockets (tasks 12-13).** Ticket efímero de un solo uso en vez de
  `?token=`, y re-validación periódica de la sesión dentro del bucle del socket.
- **Fase D — Cabeceras (tasks 14-15).** CSP, `X-Frame-Options`, `nosniff` y
  `Referrer-Policy` en el panel y en la API; `/docs` condicional; el build del
  panel falla si falta configuración obligatoria.
- **Fase E — Webhooks y JOSE (tasks 16-17).** Anti-replay en webhooks entrantes y
  una sola pila JOSE (`joserfc`).
- **Fase F — Documentación (task 18).**

## Lo que NO entra

- **Firma asimétrica de los tokens internos** o retirar al worker la capacidad de
  firmar. El ADR 0136 las deja como mejoras futuras condicionadas.

## Verificación (2026-09-29)

Los ficheros de test que declara el plan:

| Árbol                                | Ficheros | Resultado     |
| ------------------------------------ | -------: | ------------- |
| `tests/` (unit + integration)        |       18 | 173 passed    |
| `apps/admin-panel/e2e/` (Playwright) |        5 | no ejecutados |

Los cinco specs de Playwright necesitan el stack completo levantado y un
navegador. No se ejecutaron en esta pasada, así que el flujo de sesión en el
navegador queda para el test humano del plan.
