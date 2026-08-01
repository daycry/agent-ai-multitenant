---
title: Sesiones y autorización
docs_language: es
audience: backend-dev, security, devops
updated: 2026-08-01
---

# Sesiones y autorización

Contrato de la **credencial de sesión**: cómo nace, dónde vive, cómo viaja, cómo
se revoca y qué la endurece. Es el complemento operativo de
[auth-sso.md](./auth-sso.md) (que cubre el catálogo de endpoints de
autenticación, OIDC/SAML y MFA) y de [rbac.md](./rbac.md) (que cubre quién puede
llamar a qué). Aquí está el **cómo viaja el credencial**, que es lo que cambió
con el ADR 0133.

Fuente de verdad: el código (`api_server/auth/`) y los ADR
[0133](../05-architecture-decisions/0133-almacenamiento-sesion-panel.md),
[0134](../05-architecture-decisions/0134-auto-registro-en-produccion.md) y
[0136](../05-architecture-decisions/0136-dominios-criptograficos-worker-api.md).

---

## 1. Anatomía de una sesión

Una sesión son **dos cosas a la vez**, con el mismo TTL:

| Pieza              | Dónde vive                         | Qué aporta                                                 |
| ------------------ | ---------------------------------- | ---------------------------------------------------------- |
| Registro de sesión | Redis (`SessionStore`, por `sid`)  | Revocación instantánea: borrarlo mata la sesión ya emitida |
| JWT HS256          | Cookie del navegador / cliente API | Claims `sub`, `sid`, `tid`, `sys`, `own`, `exp`            |

El JWT **no basta por sí solo**: `get_principal` comprueba que el `sid` sigue
vivo en Redis, así que un logout o una revocación cortan de inmediato aunque el
token no haya caducado. Ese es el motivo de que la sesión no sea un JWT «puro».

- TTL: `API_SERVER_JWT_EXPIRATION_MINUTES` (por defecto **1440 min = 24 h**). El
  registro Redis y el JWT se crean con el MISMO TTL, de modo que caducan juntos.
- La superficie `/admin/*` recorta ese TTL a **15 min** por su cuenta (§4).
- La librería JOSE es `joserfc` (`task_prod09_17`). Ojo con una trampa que el
  módulo documenta: `joserfc.jwt.decode` **no valida `exp`** por sí mismo, así
  que `auth/jwt.py` declara `exp` como claim **essential** — un token sin `exp`
  se rechaza igual de fuerte que uno caducado.

## 2. Cómo viaja: cookie httpOnly + doble-submit CSRF (ADR 0133)

El panel guardaba el JWT en `localStorage` bajo `agentic.token`. Para un System
Admin ese credencial es cross-tenant: el secreto más valioso de la plataforma,
legible por cualquier script de la página. El ADR 0133 (opción A, `accepted` el
2026-07-31) lo mueve a cookie.

**Dos cookies, a propósito:**

| Cookie            | Flags                                    | Contenido    | Por qué                                                               |
| ----------------- | ---------------------------------------- | ------------ | --------------------------------------------------------------------- |
| `agentic_session` | `HttpOnly; Secure; SameSite=Lax; Path=/` | el JWT       | JS no la lee → un XSS ya no puede **exfiltrar** la sesión             |
| `agentic_csrf`    | `Secure; SameSite=Lax; Path=/` (legible) | token random | la mitad doble-submit: el panel la lee para devolverla en la cabecera |

- **`Secure` es incondicional**, sin rama por entorno. Los navegadores aceptan
  cookies `Secure` sobre `http://localhost` (origen de confianza), así que dev
  sigue funcionando y ningún despliegue puede acabar mandando la sesión en claro
  por un `if` mal puesto.
- Son cookies de sesión (sin `Expires`) con `Max-Age` igual al TTL del JWT:
  cerrar el navegador termina la sesión. El `localStorage` viejo sobrevivía al
  cierre de pestaña durante sus 24 h completas.

**Regla del CSRF** — la aplica `auth/deps.py` de forma central, ningún router se
tiene que acordar:

| Autenticación de la petición | Método seguro (GET/HEAD/OPTIONS/TRACE) | Método que muta                                         |
| ---------------------------- | -------------------------------------- | ------------------------------------------------------- |
| Cookie `agentic_session`     | pasa                                   | exige `X-CSRF-Token` == `agentic_csrf` (si no, **403**) |
| `Authorization: Bearer`      | pasa                                   | pasa (sin CSRF)                                         |

El Bearer no necesita CSRF porque una página de terceros **no puede añadir esa
cabecera**; es justo lo que hacía inmune al esquema anterior. Por eso `curl`,
los SDK y `scripts/` siguen funcionando sin cambios: `read_credential` acepta
las dos patas y el **header siempre gana** sobre la cookie (una cookie rancia en
el navegador no puede pisar el credencial que un script mandó a propósito).

Un verbo desconocido cuenta como que muta: la lista blanca es de métodos
seguros, no de métodos protegidos.

### Endpoints que emiten o retiran las cookies

`POST /auth/login` · `POST /auth/session/resolve` · `POST /auth/select-tenant` ·
`POST /auth/mfa/totp/verify` · `POST /auth/mfa/webauthn/login/finish` · el
callback SSO (§3) emiten; `POST /auth/logout` las expira (`Max-Age=0` **y**
valor vacío, para el navegador que ignore una de las dos formas de borrado).

`select-tenant` **re-emite** la cookie a propósito: si el token con `tid` se
quedara solo en el cuerpo, el navegador seguiría mandando la identidad sin
tenant y toda escritura respondería «active tenant required».

El `access_token` sigue apareciendo en el cuerpo de la respuesta. No es un
descuido: es la pata de compatibilidad de `curl`/SDK/scripts. El agujero que
cerró el ADR 0133 era `localStorage`, no la respuesta del login.

## 3. Handoff SSO

El callback OIDC y el ACS SAML ya **no** devuelven un `LoginResponse` en JSON
(el usuario acababa mirando un JSON crudo con su `access_token` en la barra del
navegador). Ahora responden **303** hacia `{panel}/auth/callback` con
`Set-Cookie`, y la página del panel resuelve el tenant.

El destino lo construye `sso_landing_url()`, que rechaza: esquemas que no sean
http(s), `//protocol-relative`, credenciales en la autoridad (`user:pass@`) y
CR/LF. Es decir, anti open-redirect y anti response-splitting — el redirect
nuevo era precisamente el riesgo 5 del plan prod-09.

## 4. La superficie `/admin/*` endurecida

Tres controles **independientes**, activos solo cuando `environment` es
`staging` o `prod` (en dev no estorban):

| Control         | Knob                                    | Defecto                 | Rechazo |
| --------------- | --------------------------------------- | ----------------------- | ------- |
| MFA obligatoria | `API_SERVER_ADMIN_REQUIRE_MFA`          | `true`                  | 403     |
| Allowlist de IP | `API_SERVER_ADMIN_IP_ALLOWLIST` (CIDRs) | vacía = sin restricción | 403     |
| Sesión corta    | `API_SERVER_ADMIN_SESSION_TTL_MINUTES`  | `15`                    | 401     |

La sesión corta es **independiente** del TTL general: un usuario normal puede
tener 24 h, pero el mismo `sid` deja de valer para `/admin/*` a los 15 minutos
de su `created_at`.

**Cómo se cablea** (authz-1): la dependencia `require_hardened_system_admin`
**no se pone router a router**. `main._is_admin_surface()` la engancha en el
momento del montaje a todo router cuya superficie completa cuelgue de `/admin`,
así que un router admin nuevo queda endurecido por el mero hecho de montarse.
Un router que MEZCLE rutas admin y no-admin es un error de cableado y revienta
en el arranque, en vez de adivinar. La guarda permanente es
`tests/integration/test_admin_hardening_surface.py`, que itera `app.routes`.

> Este endurecimiento es también el que te puede dejar fuera. El procedimiento
> de recuperación está en
> [`06-runbooks/recuperacion-lockout-admin.md`](../06-runbooks/recuperacion-lockout-admin.md).
> **Léelo ANTES de configurar la allowlist en producción**, no después.

## 5. WebSockets

No hay `POST /ws/ticket`, y no debe haberlo. El plan prod-09 preveía un ticket
efímero para sacar el JWT de la query string; el ADR 0133 lo dejó sin objeto:
con la sesión en cookie, **el handshake la lleva solo**. `lib/ws.ts` ya no
adjunta el JWT a la URL (queda `?tenant_id=`, que no es secreto), que era el
problema real — el token terminaba en access logs, proxies y Loki.

Lo que la cookie SÍ obliga a añadir es el **gate de `Origin`**: el navegador
adjunta la cookie a un handshake abierto desde CUALQUIER origen, así que sin
esta comprobación la migración habría dejado el WebSocket peor que antes
(CSWSH). El origen se compara contra `cors_allowed_origins` ∪ el origen público
propio, derivado de `Host`/`X-Forwarded-Proto`.

**Un solo punto de entrada, a propósito**: `_authenticate_socket` hace el gate
de `Origin` **y** resuelve el principal, y todos los handlers `/ws/*` (los de
`ws.py` más `cortex_ws`, `cortex_voice` y `assistant_voice`) pasan por él. Un
endpoint que resolviera el principal por su cuenta se saltaría el gate en
silencio, y un CSWSH abierto no se nota hasta que alguien lo usa; por eso
`tests/unit/test_ws_origin_gate_wired.py` es una guarda **estática** que se pone
roja si aparece uno.

Matiz importante de `origin_is_allowed`, porque decide en qué dirección falla:
un `Origin` **ausente** es normal en un cliente que no es un navegador, así que
se acepta… **salvo cuando la credencial vino de la cookie**
(`require_origin=from_cookie`). Es exactamente el caso peligroso: si la sesión
la puso el navegador, el `Origin` tiene que estar y tiene que valer. Un cliente
que se autentica con `?token=` explícito no paga esa exigencia.

Además el pump re-valida la sesión cada `API_SERVER_WS_SESSION_REVALIDATE_SECONDS`
(por defecto **30 s**, `0` desactiva) y cierra con **1008** cuando la sesión se
revocó o el token caducó. Sin eso, la garantía de «el logout cierra los sockets
abiertos» solo se evaluaba en el `accept`.

## 6. Dominios criptográficos: dos secretos, no uno

| Secreto                            | Firma                                   | Lo necesita          |
| ---------------------------------- | --------------------------------------- | -------------------- |
| `API_SERVER_JWT_SECRET`            | sesiones de usuario                     | api-server           |
| `API_SERVER_INTERNAL_TOKEN_SECRET` | `AGENTIC_INTERNAL_TOKEN` (worker → api) | api-server + workers |

Separarlos (secrets-9, ADR 0136) es lo que hace que **comprometer un worker no
permita forjar sesiones de usuario**. Los tokens internos son efímeros por
contenedor, así que rotarlos no pide migración: basta un reinicio coordinado de
api-server y workers.

- El worker mintea a través de `api_server.config`, por eso la variable lleva el
  prefijo `API_SERVER_` también en el contenedor de workers.
- La configuración admite **anillo** de secretos (`..._SECRETS`, en plural) para
  rotar sin ventana de corte, y hay una validación que **rechaza el arranque si
  los dos anillos comparten algún valor** — el modo de fallo obvio al rotar sería
  volver a igualarlos sin darse cuenta.
- Con `environment` en `staging`/`prod`, el guard fail-closed de `config.py`
  rechaza los valores por defecto (`dev-only-…`). Un despliegue que se olvide de
  una variable **no arranca**; antes firmaba con un secreto que está en el repo.

## 7. Alta de usuarios (ADR 0134)

El registro público está **cerrado**. Solo dos formas de pasar por
`POST /auth/register`:

1. **Arranque**: con la tabla `users` literalmente vacía se permite sin
   invitación y el primer usuario sale System Admin **y** System Owner. Sin esa
   puerta, una instalación nueva quedaría inaccesible para siempre.
2. **Invitación válida** emitida desde `/admin/invitations`: no caducada, no
   revocada, no canjeada y para ESE email. El canje es atómico (compare-and-set)
   y crea la membresía del tenant/rol que la invitación llevaba.

Cualquier otra cosa es un **403 idéntico** (mismo código y mismo cuerpo tanto si
el email existe como si no): cerrar el registro tenía que cerrar también el
oráculo de enumeración del 409, no moverlo de sitio.

Dos controles anti-abuso sobre esta superficie anónima:

- **Ventana por IP en `register`** (`API_SERVER_REGISTER_RATE_LIMIT_COUNT`,
  defecto 10 / `..._WINDOW_SECONDS`, defecto 3600). Se aplica ANTES de tocar la
  base de datos y **también** a la puerta de arranque. Sin ella, el token de
  invitación se podía probar en bucle gratis.
- **Login de tiempo constante**: cuando el email no existe, el usuario está
  inactivo o la identidad es de SSO, `login` gasta igualmente una verificación
  Argon2id de relleno con los MISMOS parámetros. Sin eso, la latencia de
  `/auth/login` enumeraba el padrón de usuarios sin acertar ni una contraseña.

## 8. Variables de entorno introducidas o afectadas

Para el inventario de secretos de prod-10 y el compose de prod-01:

| Variable                                           | Defecto  | Nota                                          |
| -------------------------------------------------- | -------- | --------------------------------------------- |
| `API_SERVER_JWT_SECRET` / `..._SECRETS`            | dev-only | fail-closed fuera de `dev`                    |
| `API_SERVER_INTERNAL_TOKEN_SECRET` / `..._SECRETS` | dev-only | **también en el contenedor de workers**       |
| `API_SERVER_JWT_EXPIRATION_MINUTES`                | 1440     | TTL de sesión (Redis + JWT)                   |
| `API_SERVER_ADMIN_REQUIRE_MFA`                     | `true`   | solo staging/prod                             |
| `API_SERVER_ADMIN_IP_ALLOWLIST`                    | vacía    | solo staging/prod; vacía = sin restricción    |
| `API_SERVER_ADMIN_SESSION_TTL_MINUTES`             | 15       | solo staging/prod                             |
| `API_SERVER_WS_SESSION_REVALIDATE_SECONDS`         | 30       | `0` desactiva la re-validación                |
| `API_SERVER_REGISTER_RATE_LIMIT_COUNT`             | 10       | por IP                                        |
| `API_SERVER_REGISTER_RATE_LIMIT_WINDOW_SECONDS`    | 3600     | ventana deslizante                            |
| `API_SERVER_LOGIN_RATE_LIMIT_COUNT`                | 5        | por IP **y** por email, independientes        |
| `API_SERVER_LOGIN_RATE_LIMIT_WINDOW_SECONDS`       | 900      |                                               |
| `API_SERVER_INCOMING_WEBHOOK_MAX_SKEW_SECONDS`     | 300      | ventana de frescura del webhook entrante (§9) |

## 9. Webhooks entrantes: anti-replay

Dos piezas, y conviene no confundir cuál es cuál:

- **Clave de dedup** (el control de verdad): el `delivery_id` que se persiste
  **nunca es NULL**. Cuando el emisor no manda cabecera de entrega, se deriva
  del **cuerpo** — que es el material que la firma cubre — como
  `body-sha256:<hex>`. Antes se guardaba NULL, el índice único parcial
  `(config_id, delivery_id) WHERE delivery_id IS NOT NULL` no aplicaba, y una
  entrega capturada podía reproducirse infinitas veces re-ejecutando su acción.
  Precio explícito: para un emisor sin id de entrega, dos cuerpos idénticos se
  responden `duplicate`.
- **Ventana de frescura** (higiene, no autenticación): los orígenes que declaran
  cabecera de timestamp (hoy solo `generic`, con `X-Agentic-Timestamp`, la misma
  convención que el firmado saliente) se rechazan con 401 fuera de
  `±API_SERVER_INCOMING_WEBHOOK_MAX_SKEW_SECONDS`. **Esa cabecera no está
  firmada** en el esquema entrante, así que quien capture una entrega puede
  reescribirla: sirve contra reintentos rancios de un emisor legítimo, no contra
  un atacante. Se evalúa **detrás** del MAC, para no dar un oráculo a quien no
  conoce el secreto.

## Relacionado

- [auth-sso.md](./auth-sso.md) — endpoints de autenticación, OIDC/SAML, MFA.
- [rbac.md](./rbac.md) — matriz de roles por endpoint.
- [multi-tenancy.md](./multi-tenancy.md) — cómo el `tid` de la sesión aterriza en RLS.
- [`06-runbooks/recuperacion-lockout-admin.md`](../06-runbooks/recuperacion-lockout-admin.md) — recuperar acceso admin.
- [`06-runbooks/sso-global-auth.md`](../06-runbooks/sso-global-auth.md) — operación del SSO.
