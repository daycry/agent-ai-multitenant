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

- [x] **Título**: Aplicar `require_hardened_system_admin` a los 9 routers admin restantes + test de contrato (authz-1)
  - ✅ **Hecho (2026-08-01):** el cableado ya estaba —`main._is_admin_surface` engancha la dependencia **en el montaje**, así que un router `/admin` nuevo la hereda por el hecho de montarse y no puede regresar por olvido— y el rojo que quedaba **no era de la feature, era del arnés**: `test_dev_does_not_over_enforce_the_widened_surface` prueba que en `dev` la puerta deja pasar, y «dejar pasar» significa llegar a un handler que AQUÍ revienta (el rol de la app no tiene GRANT sobre `platform_settings`). `ServerErrorMiddleware` de Starlette pinta su 500 y **re-lanza** para que el servidor lo registre, y httpx re-lanza a su vez: el test moría con `ProgrammingError` sin llegar a leer ningún código de estado. Arreglado con `ASGITransport(raise_app_exceptions=False)` y el porqué escrito en el docstring, que era la parte que faltaba. Ciclo rojo-verde comprobado por mutación: metiendo `dev` en `_ENFORCED_ENVIRONMENTS` el test vuelve a rojo con `admin session expired; re-authenticate` — sigue distinguiendo lo que dice distinguir. `auto_prod09_01_a` ejecutado: **6 passed**.
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

- [x] **Título**: `environment` como enum cerrado + rechazo del secreto default salvo `dev` explícito (authz-2)
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

- [x] **Título**: `internal_token_secret` dedicado para `AGENTIC_INTERNAL_TOKEN` (secrets-9)
  - ✅ **Hecho (2026-08-01):** lo que bloqueaba era la firma del **ADR 0136**, y está `accepted` desde `95fc7fbc`. El código lleva su parte hecha: `auth/internal_agent.py:126,167` firma y verifica contra `settings.internal_token_secret_ring`, `config.py` valida que ese anillo **no comparta ninguna clave** con el de `jwt_secret` (`config.py:1002` — sin esa comprobación «separar los secretos» se cumple sobre el papel y no en el despliegue, porque nada impedía poner el mismo valor en las dos variables), y el contrato de despliegue REAL —el que genera el instalador— emite `API_SERVER_INTERNAL_TOKEN_SECRET` **al api-server y al worker** (`compose_generator.py:615,778`). Documentado en `docs/04-reference/sesiones.md:178,229`. `auto_prod09_03_a` ejecutado verbatim: **18 passed**.
  - ⚠️ **Residuo, fuera de la propiedad de este carril:** quedan **tres comentarios rancios** que siguen diciendo que el token se firma con el `jwt_secret` — `docker/docker-compose.yml:58`, `apps/workers/src/workers/config.py:113-116` y `apps/workers/src/workers/execution.py:149`. Son documentación, no comportamiento (el `docker-compose.yml` canónico ni siquiera declara los servicios de aplicación; lo dice él mismo dos líneas antes), pero mienten sobre un contrato de seguridad y hay que corregirlos. No los toco porque esos ficheros son de otro carril.
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

- [x] **Título**: Re-verificar `is_system_admin` contra BD + revocar sesiones al cambiar el flag (authz-4)
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

- [x] **Título**: Rate limit por IP en register + verificación Argon2 dummy + setting de auto-registro (authz-6, authz-7)
  - ✅ **Hecho (2026-08-01):** (1) `register` inyecta el `RateLimiter` con ventana por IP (`API_SERVER_REGISTER_RATE_LIMIT_COUNT`, 10 / `..._WINDOW_SECONDS`, 3600), evaluada **antes** de tocar la BD y **sin excepción para la puerta de arranque** — desde el ADR 0134 el alta exige token de invitación, así que este endpoint anónimo era el único sitio donde probar un secreto en bucle gratis. (2) `login` gasta argon2 SIEMPRE: `_verify_login_password` centraliza la decisión y las tres ramas sin hash contra el que comparar (email desconocido, inactivo, identidad SSO) llaman a `burn_password_verification`, que deriva su hash del hasher VIVO — si alguien sube el `memory_cost`, el relleno sube con él. (3) El tercer punto **quedó superado**: el `allow_self_registration` que pedía el plan era la opción A del ADR 0134, y el operador firmó la **opción C (registro por invitación)**, ya implementada con su 403 genérico idéntico para email conocido y desconocido. `auto_prod09_05_a` ejecutado verbatim: **11 passed** (3 integración + 8 unitarios). Ciclo rojo-verde comprobado en los dos: los 3 de integración en rojo antes del limitador; quitando el `burn` de la rama de relleno, 3 de los unitarios se ponen rojos.
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

- [x] **Título**: ADR `docs/05-architecture-decisions/` con opciones A/B de la Decisión 1 + Decisión 4, para aprobación humana
  - ✅ **Hecho (2026-08-01):** los **dos** ADR están `accepted` y firmados por el operador (`deciders: [operador]`, commit `95fc7fbc`). El **0133** cierra la Decisión 1 con la Opción A —cookie `httpOnly+Secure+SameSite=Lax` + doble-submit CSRF— y sus dos condiciones vinculantes cumplidas en la misma entrega (helper `seedSession` para los ~93 specs e2e, y validación de `Origin` en el WebSocket). El **0134** cierra la Decisión 4, y el operador eligió **por encima de lo que el propio ADR recomendaba**: no `allow_self_registration` sino la opción más restrictiva, registro **por invitación** con token hasheado de un solo uso. El gate de esta tarea era «revisión humana», y la revisión ocurrió.
  - ⏳ **Parcial (2026-07-31):** el **ADR 0133** (sesión del panel) está **`accepted`** — el operador eligió la Opción A, cookie `httpOnly+Secure+SameSite=Lax` con doble-submit CSRF, y sus dos condiciones (helper para los ~93 specs e2e y validación de `Origin` en el WS en la MISMA entrega) son vinculantes y están cumplidas. El **ADR 0134** (auto-registro) sigue `proposed`, así que la casilla no cierra.
- **Tiempo**: 4 h · **Complejidad**: s
- Documentar cookie httpOnly+CSRF (A, recomendada) vs localStorage+CSP (B), el
  handoff SSO asociado a cada opción y el default de `allow_self_registration`.
  Las tareas 07-09 quedan bloqueadas hasta que un humano apruebe el ADR.
- **Tests automáticos**: no aplica (documento); la revisión humana es el gate.

#### `task_prod09_07` — Backend: sesión por cookie httpOnly + CSRF

- [x] **Título**: Emitir la sesión como cookie `httpOnly+Secure+SameSite=Lax` con doble-submit CSRF (frontend-2, secrets-10)
  - ✅ **Hecho (2026-07-31):** `auth/cookies.py` (nuevo) emite `agentic_session` httpOnly+Secure+SameSite=Lax y `agentic_csrf` legible; `get_principal` acepta cookie **además** de Bearer (`read_credential`) y exige `X-CSRF-Token` en toda mutación autenticada POR COOKIE; login, `session/resolve`, `select-tenant`, `mfa/totp/verify`, `mfa/webauthn/login/finish` y el callback SSO emiten la cookie, y `logout` la expira. El `access_token` se conserva en el cuerpo: es la pata de compatibilidad de `curl`/SDK/`scripts/`, y el agujero era `localStorage`, no la respuesta del login. `auto_prod09_07_a` ejecutado: **9 passed**.
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

- [x] **Título**: `middleware.ts` de Next + `apiFetch` con `credentials: 'include'` + retirar `getToken`/`localStorage` (frontend-2, secrets-10)
  - ✅ **e2e EJECUTADO (2026-08-01):** `auto_prod09_08_a` verbatim (`npm --prefix apps/admin-panel run test -- lib/session`): **10 passed**. `auto_prod09_08_b` verbatim contra Chromium real: **2 passed** — la visita sin sesión a `/admin/dashboard` se corta en el edge y con sesión no queda nada con forma de JWT en `localStorage`, con la credencial en una cookie `httpOnly` que la página no puede leer. Ciclo rojo-verde por mutación: quitando `"/admin/:path*"` del `matcher` de `middleware.ts` el primero se pone rojo (la página protegida se sirve), lo que confirma que el test mide el gate y no el `useEffect` que sustituyó. existe `apps/admin-panel/middleware.ts` (gate de `/admin/*`, `/select-tenant` y `/no-access`, con `?next=`), `lib/api.ts` va con `credentials:'include'` + `X-CSRF-Token`, y `lib/auth.ts` **ya no exporta `getToken`/`setToken`/`clearToken`** (cero ocurrencias de `agentic.token` fuera de comentarios). Los ~93 specs e2e migrados MECÁNICAMENTE con `e2e/helpers/session.ts` (condición 1 del ADR): 100 llamadas a `seedSession` en 92 ficheros. `auto_prod09_08_a` (vitest) verde: **72 passed** en 10 ficheros. `auto_prod09_08_b` es Playwright y **no se ha ejecutado** — no hay navegador en este entorno; la casilla no cierra hasta que alguien corra la suite.
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

- [x] **Título**: Callback OIDC y ACS SAML redirigen al panel con la sesión, en vez de devolver JSON crudo (frontend-1)
  - ✅ **e2e ESCRITO Y EJECUTADO (2026-08-01):** `auto_prod09_09_a` verbatim: **1 passed**. `e2e/sso-roundtrip.spec.ts` no existía; escrito y corrido verbatim: **3 passed**. Cubre lo que ningún test de servidor puede afirmar — que **el navegador sigue el 303 y se queda con la cookie** en el salto entre el origen de la API y el del panel. El 303 se emula con `route.fulfill`, y la emulación es fiel justo en lo que importa: quien procesa la respuesta es el navegador (sigue el `Location`, aplica el `Set-Cookie`), y no hay CORS de por medio porque es una navegación de primer nivel entre dos **puertos del mismo host** — las cookies ignoran el puerto. Tres casos: `admin` → dashboard sin rastro de `access_token` en la página ni de `agentic.token` en `localStorage`; `multiple` → `/select-tenant`, que al estar en el `matcher` del middleware demuestra **además** que la cookie sobrevivió al salto; y resolución en 500 → `/login`, no un spinner eterno (el mismo callejón sin salida de la auditoría, una pantalla más allá). Ciclo rojo-verde por mutación: quitando el `router.replace(next)` de `app/auth/callback/page.tsx` los dos primeros se ponen rojos y el tercero **sigue verde**, que es lo correcto — su rama es el `catch`.
  - ⏳ **Implementado, e2e SIN EJECUTAR (2026-07-31):** `oidc_callback` y `saml_acs` devuelven **303** a `{panel}/auth/callback` con `Set-Cookie`, ya no `LoginResponse` JSON; el destino lo construye `sso_landing_url()`, que rechaza esquemas no-http(s), `//protocol-relative`, credenciales en la autoridad y CR/LF (anti open-redirect y anti response-splitting). Página nueva `app/auth/callback/page.tsx` que llama a `resolveAndRoute()`. `auto_prod09_09_a` ejecutado: **1 passed** (`test_sso_callback_redirect.py`). `auto_prod09_09_b` es Playwright y **no se ha ejecutado**.
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

- [x] **Título**: 401 centralizado → limpiar sesión + redirect a `/login` conservando la ruta; usar `expires_in` (frontend-3)
  - ✅ **e2e EJECUTADO (2026-08-01):** `auto_prod09_10_a` corrido contra Chromium real: **3 passed**. Uno estaba rojo y su causa era del propio spec, no del panel: el `/auth/login` mockeado no emitía la cookie de sesión que el backend sí emite, así que el `router.push('/admin/agents')` posterior lo rebotaba `middleware.ts` a `/login?next=/admin/agents` — que se lee _exactamente igual_ que «el `?next=` no se honra». Sembrar la cookie **dentro del handler de la ruta** es la emulación fiel (un `set-cookie` en el `fulfill` no valdría: la respuesta es cross-origin `:8001`→`:3000` y el navegador la descarta sin CORS acreditado). Anotado en el spec para que nadie lo vuelva a leer como bug de la app.
  - ⏳ **Implementado, e2e SIN EJECUTAR (2026-07-31):** `lib/api.ts` trata el 401 en un solo sitio (limpia la cookie CSRF + el tenant y llama a un handler inyectado); `app/providers.tsx` lo cablea a `queryClient.clear()` + `router.replace('/login?next=…')`, y `app/login/page.tsx` honra el `?next=` filtrado por `safeNextRoute()` (rechaza absolutas, `//` y `/\\`). El 401 de `/auth/login` y `/auth/mfa/*` NO redirige: es la respuesta normal a una contraseña mala, y rebotar a `/login` desde `/login` se come el mensaje de error. Acreditado por vitest (`lib/api.test.ts`, 8 tests, con ciclo rojo-verde de las tres aserciones clave) — pero `auto_prod09_10_a` es Playwright y **no se ha ejecutado**.
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

- [x] **Título**: `queryClient.clear()` en logout; `resetQueries` en cambio de tenant (frontend-4)
  - ✅ **e2e ESCRITO Y EJECUTADO (2026-08-01):** `e2e/logout-cache-purge.spec.ts` no existía; ahora existe y `auto_prod09_11_a` corre verbatim: **2 passed**. Prueba lo que solo se puede ver en navegador —que el `QueryClient` SOBREVIVE al logout porque el layout raíz no se desmonta— cerrando sesión y entrando como otro usuario **en la misma pestaña**. Dos precauciones contra el falso verde, escritas en el spec: el `/me` del usuario entrante llega **tarde a propósito** (3 s) para abrir la ventana en la que el bug sería visible, y la aserción negativa es una **lectura única sin reintento** (un `not.toHaveAttribute` se pondría verde en cuanto el entrante sustituyera al saliente, que es justo el defecto que se busca). Ciclo rojo-verde por mutación: dejando `purgeSessionCache()` como no-op, el test se pone rojo — y no «un parpadeo»: con `staleTime` de 5 min en `/me` la caché contesta con el usuario SALIENTE y ni siquiera refetchea.
  - ⏳ **Implementado, e2e SIN EJECUTAR (2026-07-31):** `lib/session-cache.ts` (nuevo) expone `purgeSessionCache()` —llamado desde el logout de `components/layout/admin-header.tsx` y de `app/no-access/page.tsx`, y desde el 401 global— y `resetTenantScopedQueries()`, que sustituye el `invalidateQueries` de `lib/tenant-context.tsx` por `resetQueries` (invalidar seguía sirviendo las filas del tenant SALIENTE hasta que aterrizaba el refetch). `auto_prod09_11_a` es Playwright y **no se ha ejecutado**.
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

- [x] **Título**: `POST /ws/ticket` autenticado → nonce Redis TTL 30 s canje único; los 4 endpoints `/ws/*` aceptan `?ticket=` (api-8, frontend-5, tenancy-4)
  - ❌ **Cerrada en NEGATIVO (2026-08-01): la premisa es falsa y quien la anuló firmó.** El ticket existía para sacar el JWT de la URL; con la sesión en cookie el handshake la lleva solo y **ya no hay JWT que sacar** — `lib/ws.ts:63` solo adjunta `tenant_id`, que no es secreto, y `lib/ws.test.ts:67` es la guarda que impide reponer el `token=`. El propio ADR 0133 lo dice («hace innecesario el ticket WS de `task_prod09_12`») y desde `95fc7fbc` está **`accepted` por el operador**: la decisión que el 2026-07-31 se dejaba a su criterio ya está tomada, así que no hace falta esperar a nadie para cerrar esto. Los tres hallazgos que la casilla cubría (api-8, frontend-5, tenancy-4) quedan cerrados por la vía de la cookie.
  - Lo que **sí** era obligatorio y entró en la misma entrega es la condición 2 del ADR: los 8 handlers `/ws/*` validan `Origin` contra `cors_allowed_origins` ∪ el origen público propio. Sin eso la migración habría dejado el WebSocket **peor** que antes — el navegador manda la cookie sola en el handshake desde cualquier origen, que es la definición de CSWSH. Verificado hoy: `tests/unit/test_ws_origin_gate.py` + `test_ws_origin_gate_wired.py`, **12 passed**.
  - ⏳ **El ADR 0133 la deja sin objeto; su OBJETIVO está cumplido (2026-07-31):** con la sesión en cookie el handshake la lleva solo, así que `lib/ws.ts` **ya no adjunta el JWT a la URL** (`?token=` retirado; queda `?tenant_id=`, que no es secreto) y el ticket deja de hacer falta — el propio ADR lo anticipa («hace innecesario el ticket WS de `task_prod09_12`»). Lo que SÍ era obligatorio, y entra en la misma entrega, es la **condición 2**: los 8 handlers `/ws/*` que autentican con la sesión (5 de `ws.py` + `cortex_ws` + `cortex_voice` + `assistant_voice`) validan `Origin` contra `cors_allowed_origins` ∪ el origen público propio derivado de `Host`/`X-Forwarded-Proto`, cerrando el CSWSH que la cookie abriría. Sin eso la migración habría dejado el WebSocket PEOR que antes. Tests: `tests/unit/test_ws_origin_gate.py` (11) + `tests/unit/test_ws_origin_gate_wired.py` (guarda estática que falla si un handler resuelve el principal por su cuenta) — **12 passed**, con ciclo rojo-verde comprobado desactivando el gate en `cortex_ws`. **Cerrar o cancelar esta casilla es decisión del operador**: el `POST /ws/ticket` literal no existe y, según el ADR, no debería.
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

- [x] **Título**: `_pump` re-comprueba sesión Redis y expiración cada N segundos; cierre 1008 al revocarse (authz-3)
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

- [x] **Título**: Middleware de cabeceras (nosniff, frame-deny, Referrer-Policy; HSTS con TLS) y Swagger UI apagado en producción (api-7)
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

- [x] **Título**: `headers()` con CSP, `frame-ancestors 'none'`, nosniff y Referrer-Policy; assert de `NEXT_PUBLIC_API_URL` en build prod (frontend-6, frontend-8)
  - ✅ **Hecho (2026-08-01):** implementado en `next.config.js` + `lib/security-headers.js` (unitarios `lib/security-headers.test.ts`, 23 en verde) y **`auto_prod09_15_a` por fin ejecutado** contra Chromium: `e2e/security-headers.spec.ts`, **4 passed**. La casilla estaba abierta porque el spec «no existía»; existía desde `503880a5` y lo que faltaba era correrlo.
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

- [x] **Título**: Ventana de frescura + clave de dedup obligatoria para entregas sin `delivery_id` (authz-5)
  - ✅ **Hecho (2026-08-01):** `webhooks/signatures.py` gana `derive_delivery_id()` y `verify_incoming_freshness()`, y el router las cablea. **El `delivery_id` que se persiste ya nunca es NULL**: sin cabecera de entrega se deriva del cuerpo como `body-sha256:<hex>`, de modo que el índice único parcial vuelve a aplicar y el replay literal responde `duplicate` sin re-ejecutar la acción. La derivación usa **solo material autenticado** (el cuerpo que la firma cubre) — es la decisión clave: la variante `hash(body)+timestamp` que sugería el plan habría dejado la clave a merced del atacante, porque en el esquema entrante el timestamp NO va firmado. Por eso la ventana de frescura (`API_SERVER_INCOMING_WEBHOOK_MAX_SKEW_SECONDS`, 300) se documenta como **higiene contra reintentos rancios, no como el control anti-replay**, y se evalúa **detrás** del MAC para no regalar un oráculo. Cabecera de entrega >255 caracteres: se sustituye por su hash (antes reventaba el INSERT con un 500 en un endpoint público). `auto_prod09_16_a` ejecutado: **8 passed**; con las suites vecinas (`test_webhook_signature.py`, `test_webhook_replay.py`), **23 passed**. Ciclo rojo-verde por mutación: revirtiendo el `derive_delivery_id` y desactivando el gate de frescura, **4 failed**.
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

- [x] **Título**: `auth/jwt.py` con `joserfc`; retirar `python-jose`, su override mypy y `types-python-jose` (quality-10)
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

- [x] **Título**: Actualizar `docs/04-reference/` (auth/sesiones, matriz admin endurecida, tickets WS) y runbook de lockout admin
  - ✅ **Hecho (2026-08-01):** `docs/04-reference/sesiones.md` (9 secciones: anatomía sesión Redis+JWT, cookie httpOnly + doble-submit CSRF con la tabla de cuándo se exige, handoff SSO 303 con las cuatro formas de open-redirect que rechaza, superficie `/admin/*` endurecida y cómo se cablea sola en el montaje, WebSockets, separación `jwt_secret` / `internal_token_secret`, alta por invitación, **tabla completa de variables de entorno** para el inventario de prod-10 y el compose de prod-01, y anti-replay del webhook entrante) + `docs/06-runbooks/recuperacion-lockout-admin.md` (síntomas exactos por control, diagnóstico, tres vías de recuperación de menos a más invasiva, prevención y verificación de vuelta al estado bueno). Ambos indexados en el README de su carpeta y enlazados en los dos sentidos.
  - **Sobre los «tickets WS» que pedía el título**: no se documentan porque **no existen y no deben existir** — el ADR 0133 los dejó sin objeto (ver `task_prod09_12`). La referencia documenta lo que sí hay: el handshake por cookie y el gate de `Origin` que la cookie obliga a añadir.
  - **Sobre el gate**: el plan declara «revisión humana del docs PR (no aplica runtime)», y esa revisión sigue siendo del PR. Lo que se ha añadido para que la doc no envejezca en silencio es `tests/docs/test_session_docs.py`, **10 guardas de descubrimiento** ejecutadas en verde: los nombres de cookie salen de `auth/cookies.py`, los knobs de `/admin/*` se derivan de los `settings.<campo>` que lee `admin_hardening.py` (no de un prefijo: `admin_database_url` no es un knob de acceso), y los mensajes del runbook se extraen de los `detail=` que levanta `require_hardened_system_admin`. Renombrar una cookie, añadir un cuarto control o reescribir un mensaje pone la doc en rojo.
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
