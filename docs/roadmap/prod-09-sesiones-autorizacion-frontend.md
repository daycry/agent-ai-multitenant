---
plan_id: prod-09-sesiones-autorizacion-frontend
title: Sesiones y autorización de producción — admin hardening, SSO, 401 global y cookies
status: pending_approval
blocking_plan: null
started_at: null
completed_at: null
estimated_duration_calendar: 4-5 semanas
estimated_effort_person_days: 20
estimated_cost_human_eur: 9.000 € – 12.000 €
estimated_cost_ai_eur: 80 € – 150 €
created_by: auditoria-claude-2026-06
spec_sections_referenced: []
docs_language: es
priority: P1
---

# Plan prod-09 — Sesiones y autorización de producción: admin hardening, SSO, 401 global y cookies

## Cabecera

| Campo                              | Valor                                       |
| ---------------------------------- | ------------------------------------------- |
| **ID del Plan**                    | `prod-09-sesiones-autorizacion-frontend`    |
| **Prioridad**                      | P1                                          |
| **Bloqueado por**                  | — (`blocking_plan: null`)                   |
| **Tiempo estimado (calendario)**   | 4-5 semanas                                 |
| **Tiempo estimado (persona-días)** | 20                                          |
| **Rama git sugerida**              | `plan/prod-09-sesiones-autorizacion`        |
| **Origen**                         | Auditoría integral de producción 2026-06-10 |

> **Estado**: la fuente de verdad es el frontmatter YAML de este fichero (`status:`). El campo duplicado que había en esta tabla se retiró en prod-15 (hallazgo docsroadmap-6): se había desincronizado en 22 de 51 planes.

---

## Resumen

La auditoría de producción confirmó que la base de autenticación es sólida (Argon2id,
JWT con sesión server-side en Redis, RBAC centralizado), pero la **capa de sesión no
está lista para producción**: el endurecimiento admin (MFA + allowlist IP + sesión de
15 min) solo cubre 1 de los ~10 routers `/admin/*` y deja sin proteger backup/restore
(destructivo) y credenciales LLM (authz-1); el guard del secreto JWT es fail-open —
un despliegue que olvide `API_SERVER_ENVIRONMENT` firma con un secreto público del
repo (authz-2); el flujo SSO termina mostrando un JSON crudo con el `access_token`
en el navegador, sin sesión en el panel (frontend-1); el token de System Admin vive
24 h en `localStorage` (frontend-2, secrets-10); no hay manejo global de 401
(frontend-3) ni purga de caché en logout (frontend-4); los WebSockets transportan el
JWT como query param y nunca re-validan la sesión (api-8/frontend-5/tenancy-4,
authz-3); y faltan cabeceras de seguridad en panel y API (frontend-6, api-7).

Este plan cierra **toda** la superficie de sesión y autorización en 5 frentes:

1. **Backend admin**: hardening en TODA la superficie `/admin/*`, guard JWT
   fail-closed, secreto interno de workers separado, claim `sys` re-verificado,
   rate limit en `/auth/register` y login constant-time.
2. **Sesión del panel**: migración a cookie httpOnly (decisión vía ADR), SSO
   end-to-end con redirect al panel, 401 global con redirect a login, purga de
   caché TanStack en logout.
3. **WebSockets**: ticket efímero de un solo uso en vez de `?token=` y
   re-validación periódica de la sesión dentro del pump.
4. **Cabeceras**: CSP/X-Frame-Options/nosniff/Referrer-Policy en `next.config.js`
   y middleware de security headers + `/docs` condicional en FastAPI.
5. **Criptografía**: anti-replay en webhooks entrantes y consolidación en una
   sola pila JOSE (`joserfc`).

## Alcance

**Entra**:

- Dependencia `require_hardened_system_admin` en todos los routers `/admin/*`
  montados en `apps/api-server/src/api_server/main.py:178-233` (backup,
  llm_providers, platform_settings, cross_tenant_stats, marketplace,
  model_prices, ollama, embeddings, copilot device flow) + test de contrato.
- Guard fail-closed del secreto JWT en `apps/api-server/src/api_server/config.py`
  (enum cerrado de `environment`, rechazo de defaults salvo `dev` explícito).
- Secreto HMAC dedicado para tokens internos worker→api (`auth/internal_agent.py`).
- Re-verificación de `is_system_admin` contra BD + revocación de sesiones al
  degradar un admin.
- ADR de almacenamiento de sesión del panel + implementación (cookie httpOnly +
  CSRF, middleware de Next, eliminación de `localStorage`).
- Callback SSO funcional end-to-end (OIDC + SAML) con redirect al panel.
- Manejo global de 401 + uso de `expires_in`; `queryClient.clear()` en logout.
- Ticket WS de un solo uso + re-validación periódica en `_pump`.
- Cabeceras de seguridad en `next.config.js` y FastAPI; assert de
  `NEXT_PUBLIC_API_URL` en builds de producción; `/docs` condicional.
- Anti-replay (timestamp + dedup determinista) en webhooks entrantes.
- Migración de `python-jose` a `joserfc`.

**Queda fuera** (cubierto por otros planes de la serie):

- El compose del installer que no propaga `API_SERVER_*` (secrets-2) → **prod-01**.
  Aquí solo se garantiza que, si las variables faltan, el arranque **falla**.
- El guard general de la familia completa de secretos default (secrets-3) y la
  operativa de Vault (tokens, unseal) → **prod-10**. authz-2 se cierra aquí porque
  es la pieza JWT/sesiones; coordinar para no duplicar el validador.
- CI de vitest/Playwright (frontend-7) → **prod-02**. Los tests nuevos de este
  plan deben quedar listados para que prod-02 los incorpore a los gates.
- **DEUDA HEREDADA (diferida aquí desde prod-01, 2026-06-17):** el subset mockeado
  de Playwright en CI está **rojo por deuda pre-existente** (reproducible en local,
  no CI-only). Dos modos: (1) **colisión de glob** — `page.route("**/X")` en
  Playwright 1.60 intercepta también la navegación `page.goto(".../X")`, ~15 specs
  con globs de recurso desnudos (projects/agents/${id}/teams/${id}/human-agents/
  memories/plans/${id}); fix = predicado por `pathname` exacto (ver gotcha
  `docs/03-guides/gotchas/playwright-route-glob-intercepts-navigation.md`); (2)
  **otros modos** (p. ej. `sidebar-complete`, `sso-oidc-config`) sin enumerar del
  todo. Como este plan ya reescribe los specs e2e (task_prod09_08, fin de
  localStorage), **arreglar la colisión de glob aquí de paso** para dejar el job
  Playwright verde. El job tiene `timeout-minutes: 60` y hoy llega al timeout;
  valorar bajarlo o marcarlo no-bloqueante hasta arreglarlo.
- Rate limiting del endpoint LLM `/assistant/chat` (api-4) → **prod-07**.
- TLS/reverse proxy de producción → **prod-01** (las cabeceras HSTS de aquí
  asumen que prod-01 provee TLS; coordinar el orden de despliegue).
- i18n del panel y partición de componentes → **prod-16**.

## Decisiones clave

1. **Almacenamiento de la sesión del panel** (ADR propuesto, decide humano):
   - **Opción A (recomendada)**: cookie `httpOnly + Secure + SameSite=Lax`
     emitida por el api-server, doble-submit CSRF token, gate en `middleware.ts`
     de Next. Elimina la exfiltración por XSS; CORS ya usa allowlist con
     credenciales. Coste: tocar `apiFetch`, los 99 specs e2e y el flujo SSO.
   - **Opción B**: mantener `localStorage` con mitigación documentada (CSP
     estricta + TTL admin de 15 min en todos los entornos). Menos coste, riesgo
     residual de XSS alto para un token de System Admin multi-tenant.
   - El resto del plan asume la Opción A; si el humano elige B, las tareas 07-08
     se sustituyen por el endurecimiento documentado en el ADR.
2. **Transporte de credencial WS**: ticket efímero de un solo uso (nonce en Redis,
   TTL 30 s, canje único) frente a `Sec-WebSocket-Protocol`. Recomendado: ticket —
   no requiere parsear subprotocolos en proxies y degrada el credencial expuesto
   en logs a un valor ya consumido.
3. **Dominios criptográficos worker/api**: secreto HMAC dedicado
   (`internal_token_secret`) frente a firma asimétrica. Recomendado: secreto
   dedicado ahora (cambio S), dejar la asimétrica como mejora futura si el radio
   de explosión del worker vuelve a crecer.
4. **Auto-registro en producción**: `platform_setting allow_self_registration`
   con default `false` en staging/prod (provisión por admin/SSO/SCIM) y `true`
   en dev. Decisión de producto → se documenta en el mismo ADR de sesión.
5. **Pila JOSE única**: migrar `auth/jwt.py` a `joserfc` (ya presente vía
   authlib) y retirar `python-jose` + su override mypy. Sin opción alternativa:
   es deuda de mantenimiento pura.

## Tareas

### Fase A — Backend: superficie admin y dominios de secreto

#### `task_prod09_01` — Hardening en TODA la superficie `/admin/*`

- [ ] **Título**: Aplicar `require_hardened_system_admin` a los 9 routers admin restantes + test de contrato (authz-1)
- **Tiempo**: 8 h · **Complejidad**: m
- Cablear `dependencies=[Depends(require_hardened_system_admin)]` en los
  `APIRouter` de `routers/backup.py:61` (prioritario: restore destructivo),
  `llm_providers.py:155`, `platform_settings.py:68`, `cross_tenant_stats.py:69`
  y los routers admin de marketplace, model_prices, ollama, embeddings y
  copilot device flow; o re-montarlos como sub-routers de un router padre
  `/admin` con la dependencia a nivel de router en `main.py:178-233`. Corregir
  el docstring de `auth/admin_hardening.py:27-30`. Revisar que
  `GET /admin/backup/schedule` no quede expuesto a `require_tenant_member`.
  El test de contrato itera `app.routes` y falla si alguna ruta cuyo path
  empiece por `/admin` carece de la dependencia endurecida.
- **Tests automáticos**:
  ```yaml
  - id: auto_prod09_01_a
    runtime: python-pytest
    command: "pytest tests/integration/test_admin_hardening_surface.py -v"
  ```

#### `task_prod09_02` — Guard fail-closed para el secreto JWT y el entorno

- [ ] **Título**: `environment` como enum cerrado + rechazo del secreto default salvo `dev` explícito (authz-2)
- **Tiempo**: 4 h · **Complejidad**: s
- En `config.py`: (1) validar `environment` contra `{dev, staging, prod}` y
  fallar el arranque ante valores no reconocidos (hoy `config.py:377` los trata
  como dev); (2) invertir `_forbid_dev_secrets_outside_dev` a fail-closed:
  rechazar `jwt_secret == "dev-only-jwt-secret-change-me"` (config.py:42)
  SIEMPRE salvo `environment == "dev"` explícito; (3) mínimo de longitud para
  el secreto HMAC. **Coordinación**: prod-10 generaliza este guard al resto de
  familias de secretos (secrets-3) y prod-01 arregla el compose del installer
  (secrets-2); esta tarea cierra la pieza JWT y deja el validador extensible.
- **Tests automáticos**:
  ```yaml
  - id: auto_prod09_02_a
    runtime: python-pytest
    command: "pytest tests/unit/test_config_fail_closed.py -v"
  ```

#### `task_prod09_03` — Separar el secreto de tokens internos de workers

- [ ] **Título**: `internal_token_secret` dedicado para `AGENTIC_INTERNAL_TOKEN` (secrets-9)
- **Tiempo**: 8 h · **Complejidad**: m
- Nuevo setting `internal_token_secret` (sin default en staging/prod, sujeto al
  guard de task_prod09_02). `auth/internal_agent.py:112,138` firma y verifica
  con él en lugar de `settings.jwt_secret`; actualizar el contrato documentado
  en `docker/docker-compose.yml:47-60` para que workers reciba
  `WORKERS_INTERNAL_TOKEN_SECRET` (distinto del JWT de usuarios) y el código de
  workers que mintea el token. Comprometer el worker ya no permite forjar
  sesiones de usuario. **Coordinación**: despliegue simultáneo api-server +
  workers (los tokens internos son efímeros por contenedor, no hay migración).
- **Tests automáticos**:
  ```yaml
  - id: auto_prod09_03_a
    runtime: python-pytest
    command: "pytest tests/unit/test_internal_token_secret_separation.py tests/integration/test_internal_agent_auth.py -v"
  ```

#### `task_prod09_04` — Revocación inmediata de privilegios de System Admin

- [ ] **Título**: Re-verificar `is_system_admin` contra BD + revocar sesiones al cambiar el flag (authz-4)
- **Tiempo**: 6 h · **Complejidad**: m
- En `auth/deps.py:170`, `require_system_admin` re-verifica `users.is_system_admin`
  por PK (lectura barata, ya se hace en `/auth/me`) en vez de fiarse solo del
  claim `sys` (fijado al login, `auth/jwt.py:42-45`, TTL 24 h en `config.py:47`).
  Además, el endpoint que muta `is_system_admin` invoca el ya existente
  `revoke_user_sessions` para matar las sesiones del usuario degradado.
  Actualizar el caveat documentado en `jwt.py`.
- **Tests automáticos**:
  ```yaml
  - id: auto_prod09_04_a
    runtime: python-pytest
    command: "pytest tests/integration/test_sys_claim_revalidation.py -v"
  ```

#### `task_prod09_05` — `/auth/register` con rate limit y login constant-time

- [ ] **Título**: Rate limit por IP en register + verificación Argon2 dummy + setting de auto-registro (authz-6, authz-7)
- **Tiempo**: 6 h · **Complejidad**: s
- (1) Inyectar el `RateLimiter` existente (patrón de `routers/auth.py:217-255`)
  en `register` (`auth.py:169-211`) con ventana por IP. (2) En `login`
  (`auth.py:268-278`), ejecutar `verify_password` contra un hash Argon2 dummy
  fijo cuando el usuario no exista / sea SSO / inactivo, igualando tiempos.
  (3) `platform_setting allow_self_registration` (default `false` en
  staging/prod) que devuelve 403 genérico cuando está cerrado — mitiga también
  la enumeración por 409.
- **Tests automáticos**:
  ```yaml
  - id: auto_prod09_05_a
    runtime: python-pytest
    command: "pytest tests/integration/test_register_rate_limit.py tests/unit/test_login_constant_time.py -v"
  ```

### Fase B — Sesión del panel: cookie httpOnly, SSO y 401 global

#### `task_prod09_06` — ADR: almacenamiento de sesión del panel y auto-registro

- [ ] **Título**: ADR `docs/05-architecture-decisions/` con opciones A/B de la Decisión 1 + Decisión 4, para aprobación humana
- **Tiempo**: 4 h · **Complejidad**: s
- Documentar cookie httpOnly+CSRF (A, recomendada) vs localStorage+CSP (B), el
  handoff SSO asociado a cada opción y el default de `allow_self_registration`.
  Las tareas 07-09 quedan bloqueadas hasta que un humano apruebe el ADR.
- **Tests automáticos**: no aplica (documento); la revisión humana es el gate.

#### `task_prod09_07` — Backend: sesión por cookie httpOnly + CSRF

- [ ] **Título**: Emitir la sesión como cookie `httpOnly+Secure+SameSite=Lax` con doble-submit CSRF (frontend-2, secrets-10)
- **Tiempo**: 12 h · **Complejidad**: l
- Depende de: `task_prod09_06`. En `routers/auth.py` (login, select-tenant,
  logout): `Set-Cookie` httpOnly con el token de sesión + cookie legible de
  CSRF; los deps de auth aceptan cookie (con verificación CSRF en mutaciones)
  además del header Bearer (que se conserva para API pública y scripts). Revisar
  CORS (`allow_credentials` ya con allowlist). Mantener compatibilidad dual
  (cookie O bearer) durante la transición para no romper e2e ni curl.
- **Tests automáticos**:
  ```yaml
  - id: auto_prod09_07_a
    runtime: python-pytest
    command: "pytest tests/integration/test_session_cookie_auth.py tests/integration/test_csrf_double_submit.py -v"
  ```

#### `task_prod09_08` — Frontend: eliminar localStorage y gate en middleware

- [ ] **Título**: `middleware.ts` de Next + `apiFetch` con `credentials: 'include'` + retirar `getToken`/`localStorage` (frontend-2, secrets-10)
- **Tiempo**: 12 h · **Complejidad**: l
- Depende de: `task_prod09_07`. Sustituir `lib/auth.ts:9-13` (localStorage) y el
  Bearer de `lib/api.ts:54-57` por cookies; añadir cabecera CSRF en mutaciones;
  crear `middleware.ts` (edge) que proteja `/admin/*` comprobando la cookie de
  sesión, retirando el gate solo-presencia de `app/admin/layout.tsx:24`.
  Actualizar los specs e2e que stubean el token en localStorage.
- **Tests automáticos**:
  ```yaml
  - id: auto_prod09_08_a
    runtime: node-jest
    command: "npm --prefix apps/admin-panel run test -- lib/session"
  - id: auto_prod09_08_b
    runtime: node-jest
    command: "npm --prefix apps/admin-panel run e2e -- e2e/login-cookie-session.spec.ts"
  ```

#### `task_prod09_09` — SSO end-to-end: del IdP al panel con sesión

- [ ] **Título**: Callback OIDC y ACS SAML redirigen al panel con la sesión, en vez de devolver JSON crudo (frontend-1)
- **Tiempo**: 12 h · **Complejidad**: l
- Depende de: `task_prod09_07`. En `routers/sso.py` (callback OIDC :602-660, ACS
  SAML :747-818, `_issue_identity_session` :845): sustituir el `LoginResponse`
  JSON por `Set-Cookie` de sesión + `RedirectResponse` a una ruta del panel
  (`/auth/callback`) validada contra una **allowlist de orígenes** (anti
  open-redirect, base en `sso-redirect-base.ts`). Crear la página
  `apps/admin-panel/app/auth/callback/page.tsx` que resuelve tenant y llama a
  `resolveAndRoute()`. e2e del round-trip completo con IdP mockeado (hoy los
  specs solo cubren configuración y botones).
- **Tests automáticos**:
  ```yaml
  - id: auto_prod09_09_a
    runtime: python-pytest
    command: "pytest tests/integration/test_sso_callback_redirect.py -v"
  - id: auto_prod09_09_b
    runtime: node-jest
    command: "npm --prefix apps/admin-panel run e2e -- e2e/sso-roundtrip.spec.ts"
  ```

#### `task_prod09_10` — Manejo global de 401 y expiración de sesión

- [ ] **Título**: 401 centralizado → limpiar sesión + redirect a `/login` conservando la ruta; usar `expires_in` (frontend-3)
- **Tiempo**: 6 h · **Complejidad**: m
- En `lib/api.ts:74-77` (o `QueryCache.onError` en `app/providers.tsx:13`):
  ante 401, limpiar sesión/tenant, `queryClient.clear()` y redirect a
  `/login?next=<ruta>`. Consumir el `expires_in` ya tipado
  (`lib/session.ts:39`) para aviso de expiración o re-login proactivo. Eliminar
  la situación actual donde el panel pinta el body crudo del 401 y el usuario
  queda atascado.
- **Tests automáticos**:
  ```yaml
  - id: auto_prod09_10_a
    runtime: node-jest
    command: "npm --prefix apps/admin-panel run e2e -- e2e/session-expiry-401.spec.ts"
  ```

#### `task_prod09_11` — Purga de caché TanStack en logout y cambio de identidad

- [ ] **Título**: `queryClient.clear()` en logout; `resetQueries` en cambio de tenant (frontend-4)
- **Tiempo**: 3 h · **Complejidad**: s
- En `components/layout/admin-header.tsx:48-58` y `app/no-access/page.tsx:35-46`
  añadir `queryClient.clear()` al logout (hoy la caché sobrevive en el root
  layout con staleTime 30 s / 5 min para `/me`). En
  `lib/tenant-context.tsx:111`, sustituir `invalidateQueries` por
  `resetQueries`/`removeQueries` para no pintar datos del tenant saliente.
- **Tests automáticos**:
  ```yaml
  - id: auto_prod09_11_a
    runtime: node-jest
    command: "npm --prefix apps/admin-panel run e2e -- e2e/logout-cache-purge.spec.ts"
  ```

### Fase C — WebSockets: credencial y re-validación

#### `task_prod09_12` — Ticket WS efímero de un solo uso (fuera el `?token=`)

- [ ] **Título**: `POST /ws/ticket` autenticado → nonce Redis TTL 30 s canje único; los 4 endpoints `/ws/*` aceptan `?ticket=` (api-8, frontend-5, tenancy-4)
- **Tiempo**: 10 h · **Complejidad**: m
- Endpoint REST autenticado que deposita un nonce en Redis ligado a
  `(user_id, session_id)`; `_resolve_principal` en `routers/ws.py:85-118` canjea
  el ticket (DEL atómico) y resuelve la sesión real. `lib/ws.ts:23` pide ticket
  antes de abrir el socket y deja de adjuntar el JWT a la URL (hoy acaba en
  access logs, proxies y OTEL/Loki). Mantener `?token=` tras flag de
  compatibilidad un release y redactar el parámetro en logs como defensa en
  profundidad.
- **Tests automáticos**:
  ```yaml
  - id: auto_prod09_12_a
    runtime: python-pytest
    command: "pytest tests/integration/test_ws_ticket_auth.py -v"
  ```

#### `task_prod09_13` — Re-validación periódica de sesiones WS

- [ ] **Título**: `_pump` re-comprueba sesión Redis y expiración cada N segundos; cierre 1008 al revocarse (authz-3)
- **Tiempo**: 6 h · **Complejidad**: m
- En `routers/ws.py:136-184`, el bucle XREAD re-valida cada ~30 s (configurable)
  que la sesión sigue viva y el token/ticket no ha expirado, cerrando con 1008
  si no. Cumple la garantía documentada en `ws.py:16-21` («logout/revocation
  closes existing sockets») que hoy solo se evalúa en el accept: logout, SCIM
  deprovisioning y expiración cortan los sockets abiertos.
- **Tests automáticos**:
  ```yaml
  - id: auto_prod09_13_a
    runtime: python-pytest
    command: "pytest tests/integration/test_ws_session_revalidation.py -v"
  ```

### Fase D — Cabeceras de seguridad y configuración de build

#### `task_prod09_14` — Security headers + `/docs` condicional en FastAPI

- [ ] **Título**: Middleware de cabeceras (nosniff, frame-deny, Referrer-Policy; HSTS con TLS) y Swagger UI apagado en producción (api-7)
- **Tiempo**: 4 h · **Complejidad**: s
- En `main.py:243-253`: middleware ligero que añade `X-Content-Type-Options:
nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy` y (cuando prod-01 provea
  TLS) `Strict-Transport-Security`; condicionar `docs_url="/docs"` a un setting
  (`off` en staging/prod) para no exponer el esquema OpenAPI completo —
  incluidos `/admin/*` e `/internal/agent/*` — sin autenticación.
- **Tests automáticos**:
  ```yaml
  - id: auto_prod09_14_a
    runtime: python-pytest
    command: "pytest tests/unit/test_security_headers_middleware.py -v"
  ```

#### `task_prod09_15` — Cabeceras en `next.config.js` + build fail-fast

- [ ] **Título**: `headers()` con CSP, `frame-ancestors 'none'`, nosniff y Referrer-Policy; assert de `NEXT_PUBLIC_API_URL` en build prod (frontend-6, frontend-8)
- **Tiempo**: 8 h · **Complejidad**: m
- En `apps/admin-panel/next.config.js:8-12`: `async headers()` con CSP
  restrictiva (calibrar para Next/mermaid: nonce o hashes, evitar
  `unsafe-inline` de script), `frame-ancestors 'none'`, nosniff y
  Referrer-Policy. Además, fallar el build cuando `NODE_ENV=production` y
  `NEXT_PUBLIC_API_URL` no esté definida — hoy `lib/api.ts:15` y `lib/ws.ts:15`
  caen en silencio a `http://localhost:8001`, apuntando el panel al localhost
  de cada usuario. **Coordinación**: si prod-01 sirve el panel tras reverse
  proxy, las cabeceras pueden duplicarse allí; la fuente canónica es esta.
- **Tests automáticos**:
  ```yaml
  - id: auto_prod09_15_a
    runtime: node-jest
    command: "npm --prefix apps/admin-panel run e2e -- e2e/security-headers.spec.ts"
  ```

### Fase E — Webhooks entrantes y consolidación JOSE

#### `task_prod09_16` — Anti-replay en webhooks entrantes

- [ ] **Título**: Ventana de frescura + clave de dedup obligatoria para entregas sin `delivery_id` (authz-5)
- **Tiempo**: 8 h · **Complejidad**: m
- En `webhooks/signatures.py:148-186` y `routers/incoming_webhooks.py:229-281`:
  (1) verificar timestamp dentro de una ventana cuando el origen lo soporte,
  reutilizando el patrón nonce/timestamp ya existente en el firmado saliente
  (`notification_dispatcher/webhook_signing.py:108-160`); (2) para orígenes
  `generic` sin cabecera de delivery (hoy `delivery_id=NULL` esquiva el índice
  único parcial de `db/models.py:1031-1037` y permite replay infinito), derivar
  una clave de dedup determinista `hash(body)+timestamp` o rechazar con 400.
- **Tests automáticos**:
  ```yaml
  - id: auto_prod09_16_a
    runtime: python-pytest
    command: "pytest tests/integration/test_incoming_webhook_antireplay.py -v"
  ```

#### `task_prod09_17` — Una sola pila JOSE: migrar a `joserfc`

- [ ] **Título**: `auth/jwt.py` con `joserfc`; retirar `python-jose`, su override mypy y `types-python-jose` (quality-10)
- **Tiempo**: 6 h · **Complejidad**: m
- Depende de: `task_prod09_03` (toca los mismos módulos de firma). Migrar la
  superficie estrecha (`jwt.encode`/`jwt.decode`/`JWTError`) de
  `apps/api-server/pyproject.toml:31` a `joserfc` (ya presente vía authlib),
  manteniendo HS256 fijado y los mismos claims. Eliminar el override mypy de
  `pyproject.toml:168-171`. Los tokens emitidos antes del despliegue siguen
  siendo válidos (misma firma HS256, solo cambia la librería).
- **Tests automáticos**:
  ```yaml
  - id: auto_prod09_17_a
    runtime: python-pytest
    command: "pytest tests/unit/test_jwt_roundtrip_joserfc.py tests/integration/test_auth_role_helpers.py -v"
  ```

### Fase F — Documentación y cierre

#### `task_prod09_18` — Documentación de sesión y autorización

- [ ] **Título**: Actualizar `docs/04-reference/` (auth/sesiones, matriz admin endurecida, tickets WS) y runbook de lockout admin
- **Tiempo**: 6 h · **Complejidad**: s
- Documentar: flujo de sesión por cookie + CSRF, handoff SSO, ciclo de vida del
  ticket WS, separación de secretos (`jwt_secret` vs `internal_token_secret`,
  variables nuevas para prod-01/prod-10), y un runbook en `docs/06-runbooks/`
  para recuperar acceso si la allowlist de IP/MFA bloquea al operador
  (consecuencia directa de task_prod09_01). Listar los tests nuevos para que
  prod-02 los incorpore a los gates de CI.
- **Tests automáticos**: revisión humana del docs PR (no aplica runtime).

## Hallazgos de auditoría cubiertos

| fid        | Severidad | Tarea(s) que lo cierran            |
| ---------- | --------- | ---------------------------------- |
| authz-1    | high      | task_prod09_01                     |
| authz-2    | high      | task_prod09_02 (coord. prod-01/10) |
| authz-3    | medium    | task_prod09_13                     |
| authz-4    | medium    | task_prod09_04                     |
| authz-5    | medium    | task_prod09_16                     |
| authz-6    | low       | task_prod09_05                     |
| authz-7    | low       | task_prod09_05                     |
| frontend-1 | high      | task_prod09_09                     |
| frontend-2 | high      | task_prod09_06, 07, 08             |
| frontend-3 | high      | task_prod09_10                     |
| frontend-4 | medium    | task_prod09_11                     |
| frontend-5 | medium    | task_prod09_12                     |
| frontend-6 | medium    | task_prod09_15                     |
| frontend-8 | medium    | task_prod09_15                     |
| secrets-9  | medium    | task_prod09_03                     |
| secrets-10 | medium    | task_prod09_06, 07, 08             |
| api-7      | low       | task_prod09_14                     |
| api-8      | low       | task_prod09_12                     |
| tenancy-4  | low       | task_prod09_12                     |
| quality-10 | low       | task_prod09_17                     |

## Riesgos

1. **Regresión masiva en e2e**: 99 specs de Playwright asumen token en
   localStorage y mocks contra `localhost:8001`; la migración a cookie (08) y el
   assert de build (15) los rompen en bloque. Mitigación: compatibilidad dual
   cookie/bearer durante la transición y actualización de specs en la misma PR.
2. **CSP que rompe el panel**: Next.js inyecta inline scripts y mermaid renderiza
   SVG; una CSP estricta mal calibrada deja el panel en blanco. Mitigación:
   empezar en `Content-Security-Policy-Report-Only` y promover tras una semana
   sin violaciones.
3. **Lockout del operador**: extender MFA + allowlist IP a todo `/admin/*`
   (incluido backup/restore) puede dejar fuera al único System Admin si la
   allowlist está mal configurada. Mitigación: runbook de recuperación
   (task_prod09_18) antes de desplegar task_prod09_01 en producción.
4. **Despliegue coordinado api-server + workers**: la separación del secreto
   interno (03) exige actualizar ambos servicios a la vez; un worker viejo
   minteará tokens que el api nuevo rechaza. Mitigación: ventana de despliegue
   conjunta documentada y verificación post-deploy.
5. **Open redirect en el handoff SSO**: el redirect del callback al panel (09)
   introduce un parámetro de retorno; sin allowlist estricta de orígenes se
   crea una vulnerabilidad nueva. Mitigación: allowlist cerrada + test negativo.
6. **Dependencia de decisión humana**: las tareas 07-09 (≈4,5 días, el grueso de
   la fase B) están bloqueadas por la aprobación del ADR (06); un retraso en la
   decisión alarga el calendario. Mitigación: presentar el ADR la primera semana
   en paralelo a la fase A.

## Tests humanos del Plan

```yaml
- id: human_prod09_01
  description: "Superficie /admin/* completamente endurecida"
  hint: "En staging, System Admin sin MFA configurada"
  checklist:
    - "GET /admin/backup/schedule sin MFA → 403 (no 200)"
    - "POST /admin/backup/restore sin MFA → 403"
    - "GET /admin/llm-providers y /admin/platform-settings sin MFA → 403"
    - "Con MFA + IP en allowlist → todo funciona; sesión expira a los 15 min"
    - "Arrancar api-server con API_SERVER_ENVIRONMENT=production (typo) → el proceso NO arranca y el error lo explica"

- id: human_prod09_02
  description: "Sesión por cookie y SSO end-to-end"
  hint: "Navegador limpio contra staging con IdP de prueba"
  checklist:
    - "Login password → no existe ningún token en localStorage (DevTools); la cookie es httpOnly"
    - "Login por SSO (OIDC) → aterriza en el panel autenticado, NUNCA en un JSON crudo"
    - "Mutación sin cabecera CSRF (curl con la cookie) → 403"
    - "Acceso directo a /admin sin cookie → redirect a /login desde middleware (no flash de contenido)"

- id: human_prod09_03
  description: "Expiración, logout y revocación"
  checklist:
    - "Dejar expirar la sesión → cualquier pantalla redirige a /login?next=... (sin errores crípticos)"
    - "Logout y login con otro usuario en la misma pestaña → no aparece ningún dato del usuario anterior"
    - "Quitar is_system_admin a un usuario en BD → su siguiente request admin devuelve 403 sin esperar 24 h"
    - "Con un WebSocket abierto (kanban), hacer logout → el socket se cierra en <60 s"

- id: human_prod09_04
  description: "Credenciales fuera de logs y cabeceras presentes"
  checklist:
    - "Abrir el kanban y revisar access logs del api-server → ninguna URL contiene ?token= con un JWT"
    - "Reenviar dos veces el mismo webhook entrante firmado (origen generic) → la segunda entrega se rechaza/deduplica"
    - "curl -I al panel y al API → CSP/X-Frame-Options/nosniff presentes; /docs no responde en staging"
```

## Criterios de cierre

1. Todas las tareas con `[x]` y sus tests automáticos en verde.
2. ADR de sesión del panel aprobado por un humano (task_prod09_06).
3. Los 4 tests humanos del plan validados por un humano.
4. Test de contrato `test_admin_hardening_surface.py` integrado como gate
   permanente (cualquier router `/admin/*` futuro sin endurecer rompe CI —
   coordinado con prod-02).
5. Entrada de changelog en
   `docs/07-changelog/prod-09-sesiones-autorizacion-frontend.md`.
6. PR del plan mergeado a `master`.

## Próximo Plan

- **prod-10-vault-secretos-operables** (P1) — Vault operable y secretos sin
  defaults conocidos: generaliza el guard fail-closed de task_prod09_02 al
  resto de familias de secretos (secrets-3), tokens de Vault renovables y
  custodia de unseal keys. Las variables nuevas de este plan
  (`internal_token_secret`) deben entrar en su inventario de secretos.
