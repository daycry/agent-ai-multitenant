---
title: SSO empresarial y MFA — Referencia de endpoints de autenticación
audience: backend-dev, architect, security
phase: 08-sso-empresarial
updated: 2026-06-03
---

# SSO empresarial y MFA — Referencia de endpoints

Esta página documenta los endpoints de autenticación avanzada añadidos en
el Plan 08 (OIDC, SAML 2.0, SCIM 2.0, MFA TOTP/WebAuthn, mapeo de grupos y
login discovery). Se **añaden en paralelo** al login local (email +
contraseña) de la Fase 0; ese login no cambia. Para la matriz de roles
general ver [`rbac.md`](./rbac.md); para los ADR de fondo ver
[ADR 0047](../05-architecture-decisions/0047-sso-auth-global-platform-membership-access.md)
(modelo global vigente),
[ADR 0031](../05-architecture-decisions/0031-sso-sesion-saml-xmlsec-reto-mfa.md)
(per-tenant, superseded) y
[ADR 0002](../05-architecture-decisions/0002-redis-server-side-sessions.md).

> **Auth providers platform-global (ADR 0047 — vigente).** Desde la
> re-arquitectura de auth, los providers OIDC/SAML son **platform-global**:
> se configuran **una vez** (System Admin, grupo **Plataforma** del
> admin-panel) y sirven a **todos** los tenants. El login es **por
> provider**, no por tenant (`/auth/sso/{provider_id}/...`), con callback
> OIDC + ACS SAML **globales**. El acceso a un tenant lo concede una
> `UserOrganizationMembership` que asigna el admin **después** del login
> (deny-by-default; sin claiming por email-domain). Las rutas viejas
> `/auth/sso/{tenant_id}/...` **se retiran sin redirección**. La tabla
> `sso_configurations` deja de tener `tenant_id` / RLS (platform-global,
> como `llm_providers`). El **login por contraseña + las sesiones + MFA +
> SCIM siguen funcionando sin cambios.**

## Principio común: una sola sesión

Cualquier login con éxito —local, OIDC, SAML o tras MFA— termina en el
**mismo** modelo: una sesión server-side en Redis (`SessionStore`) + un JWT
(`encode_jwt`). No hay JWT stateless tras OIDC/SAML. Por eso logout,
revocación, el chequeo de `sid` en `get_principal` y la revocación por
deprovisioning SCIM se comportan igual con cualquier método.

## Garantías de seguridad transversales

- **Secretos cifrados/hasheados en reposo, nunca devueltos.** Client secret
  OIDC y clave privada SP de SAML van cifrados Fernet (o referenciados a
  Vault); seed TOTP cifrado Fernet; códigos de recovery y tokens SCIM solo
  como digest SHA-256. Las respuestas exponen como mucho un booleano
  `has_*` + un discriminador de origen (`vault` | `encrypted`).
- **Sin enumeración de usuarios.** `/auth/discover` deriva su respuesta solo
  del dominio configurado (nunca consulta `users`); las rutas de login SSO
  responden igual exista o no el provider (404 genérico), nunca revelan si
  una cuenta o provider concreto existe.
- **Config platform-global, sin RLS (ADR 0047).** `sso_configurations` deja
  de ser tenant-scoped: no tiene `tenant_id` ni política RLS, igual que
  `llm_providers`. Se gestiona **solo** por el System Admin sobre el engine
  BYPASSRLS; el login resuelve el provider por su id global (no por tenant).
  Un token SCIM (que sí es per-tenant, Plan 08) se resuelve una vez en rol
  `BYPASSRLS` y luego cada query corre bajo `app.tenant_id`.
- **Lista pública de providers sin secretos.** `GET /auth/sso/providers`
  (anónimo) expone solo `id` / `kind` / `display_name` / `button_label` /
  `login_url`. El `client_secret` OIDC y la clave privada SP siguen
  cifrados en reposo (Fernet / Vault) y nunca cruzan ese borde.
- **Sin escalado a roles de plataforma.** Un grupo del IdP nunca otorga
  `system_admin` / `system_operator`: `resolve_role_from_groups` solo
  devuelve roles per-tenant.

## Login discovery

`/auth/discover` es el primer endpoint que llama la UI de login, antes de
saber si aplica SSO. Vive bajo `/auth` (no `/auth/sso`).

| Endpoint                       | Método | Auth |
| ------------------------------ | ------ | ---- |
| `/auth/discover?email=<email>` | GET    | anon |

Mapea el **dominio** del email al tenant cuya config SSO habilitada lo
reclama (`email_domains`). Respuesta:

```json
{ "method": "sso", "provider": "oidc", "tenant_id": "…", "login_url": "/auth/sso/…/oidc/login" }
```

o, si ningún dominio coincide (o el email es malformado), la respuesta
genérica `{ "method": "password" }` — byte a byte idéntica exista o no la
cuenta.

> **ADR 0047:** la respuesta SSO de `/auth/discover` ya **no** lleva
> `tenant_id` (el provider es global); el `login_url` apunta a la ruta por
> provider (`/auth/sso/{provider_id}/oidc|saml/login`).

## Providers públicos para `/login`

La página de login no conoce el tenant antes de autenticar. Lista los
providers habilitados con un endpoint **público sin secretos** y pinta un
botón de marca por cada uno (icono por `kind`, label configurable) + el
formulario de contraseña.

| Endpoint              | Método | Auth |
| --------------------- | ------ | ---- |
| `/auth/sso/providers` | GET    | anon |

Devuelve una lista de `{ id, kind, display_name, button_label, login_url }`
de cada provider habilitado, ordenados por `created_at`. **No** existe
campo de secreto en el modelo de respuesta. `button_label` es configurable
por provider (si es `null`, la UI usa un default derivado del `kind`).

## OIDC

| Endpoint                             | Método      | Auth            |
| ------------------------------------ | ----------- | --------------- |
| `/auth/sso/{provider_id}/oidc/login` | GET         | anon            |
| `/auth/sso/oidc/callback`            | GET         | anon (IdP)      |
| `/auth/sso/oidc/templates`           | GET         | `tenant_member` |
| `/auth/sso/oidc/callback-url`        | GET         | `tenant_member` |
| `/auth/sso/config`                   | GET         | `tenant_member` |
| `/auth/sso/config`                   | POST        | `tenant_admin`  |
| `/auth/sso/config/{config_id}`       | PUT, DELETE | `tenant_admin`  |

- **`/login`** se direcciona por el **`provider_id` global** (ADR 0047, no
  por tenant): resuelve ESE provider habilitado en el engine BYPASSRLS,
  acuña `state` + `nonce` single-use en Redis (TTL
  `sso_login_state_ttl_seconds`; el `state` lleva el `provider_id`) y
  redirige 307 al IdP. Provider desconocido / deshabilitado / id no-UUID →
  404 genérico (no revela qué providers existen).
- **`/callback`** (única para todos los providers) valida el `state`
  (single-use, anti-CSRF), recupera el **provider** que inició el flujo,
  intercambia el `code`, verifica el ID token (firma + `iss`/`aud`/`nonce`),
  lee userinfo, provisiona la **identidad global** (linkea por email
  verificado; sin crear membership — ADR 0047) y acuña una sesión de
  **identidad sin tenant**. Un `state`/token forjado o caducado → 400. El
  cliente continúa por `/auth/session/resolve` (ver abajo).
- **`/templates`** devuelve las plantillas por IdP (Azure AD, Google
  Workspace, Okta, Auth0, GitHub, GitLab, Apple, Facebook) para el selector
  de proveedor de la UI.
- **CRUD `/config`**: una config OIDC **para toda la plataforma** (unique en
  `provider`; un segundo `POST` → 409). El secret va en el cuerpo como
  `client_secret` (plano, se cifra Fernet) o `client_secret_ref` (puntero
  Vault); nunca se devuelve (la respuesta solo lleva `has_client_secret` +
  `client_secret_source`).

## SAML 2.0

> SAML usa la extensión nativa `xmlsec` (`python3-saml`). En un nodo sin ese
> backend, los endpoints que necesitan cripto nativa devuelven **501** y el
> resto de la auth sigue intacto (ver ADR 0031). El CRUD de config, la
> validación de invariantes y el parseo de metadata del IdP **no** necesitan
> `xmlsec` y funcionan en todos los nodos.

| Endpoint                             | Método      | Auth            |
| ------------------------------------ | ----------- | --------------- |
| `/auth/sso/{provider_id}/saml/login` | GET         | anon            |
| `/auth/sso/saml/acs`                 | POST        | anon (IdP)      |
| `/auth/sso/saml/sp-metadata`         | GET         | `tenant_member` |
| `/auth/sso/saml/parse-metadata`      | POST        | `tenant_member` |
| `/auth/sso/saml/config`              | GET         | `tenant_member` |
| `/auth/sso/saml/config`              | POST        | `tenant_admin`  |
| `/auth/sso/saml/config/{config_id}`  | PUT, DELETE | `tenant_admin`  |

- **`/login`** (SP-initiated) se direcciona por el **`provider_id` global**
  (ADR 0047): construye una AuthnRequest con un RelayState single-use (que
  lleva el `provider_id`) y redirige 302 al IdP. Provider desconocido →
  404 genérico.
- **`/acs`** (Assertion Consumer Service) es **GLOBAL** (ADR 0047: una sola
  identidad de SP — entityID + ACS — para toda la plataforma). Consume el
  `SAMLResponse` POSTeado y maneja **SP-initiated** (con el RelayState que
  acuñamos, single-use, que recupera el provider + el id de AuthnRequest
  para el `InResponseTo`) e **IdP-initiated/unsolicited** (sin RelayState;
  el provider es la única config SAML global habilitada). Un assertion
  forjado/expirado → 400. Acuña una sesión de identidad sin tenant.
- **`/sp-metadata`** devuelve el SP EntityID + la **ACS URL global** a
  registrar en el IdP, derivados de `sso_redirect_base_url`.
- **`/parse-metadata`** parsea metadata XML del IdP (lxml endurecido
  anti-XXE) para pre-rellenar el formulario; no necesita `xmlsec`.
- **CRUD `/saml/config`**: una config SAML **para toda la plataforma**. La
  clave privada SP va como `sp_private_key` (PEM plano, se cifra) o
  `sp_private_key_ref` (Vault); nunca se devuelve. Flags de política:
  `authn_requests_signed`,
  `want_assertions_signed`, `want_assertions_encrypted`,
  `want_name_id_encrypted` (activar firma/cifrado exige certificado + clave
  SP, o 422).

## Resolución de tenant post-login (ADR 0047)

El login (local **o** SSO) acuña primero una **sesión de identidad sin
tenant** (`tenant_id = None`, exactamente como la sesión pre-tenant del
login por contraseña). El acceso a un tenant lo concede una
`UserOrganizationMembership` que asigna el admin; el cliente resuelve el
siguiente paso con estos endpoints:

| Endpoint                      | Método | Auth      |
| ----------------------------- | ------ | --------- |
| `/auth/session/resolve`       | GET    | principal |
| `/auth/session/select-tenant` | POST   | principal |

- **`/session/resolve`** lee las memberships **activas** del usuario y
  devuelve un `state` tipado:
  - **0 memberships** → `state="no_access"`: la sesión sigue siendo válida
    (prueba identidad) pero **sin tenant**; la admin-panel muestra la
    pantalla **"sin permisos, contacta al administrador"**. **No** se acuña
    token de tenant ni se crea membership (deny-by-default).
  - **1 membership** → `state="single"`: acuña y devuelve un token
    **tenant-scoped** para entrar directo a ese tenant.
  - **>1** → `state="multiple"`: el cliente muestra el **tenant-picker** y
    POSTea a `/session/select-tenant`.
- **`/session/select-tenant`** re-valida que el usuario tiene una membership
  **activa** en el `tenant_id` pedido (un id forjado → 403) y acuña una
  sesión + JWT con ese tenant en el claim `tid`. Es la vía por la que un
  usuario normal (que no puede usar el override `X-Tenant-Id` de
  superadmin) adquiere una sesión con tenant.
- Un **`system_admin`** no se trata distinto aquí: `resolve` solo reporta
  memberships reales; su poder cross-tenant viene del override `X-Tenant-Id`
  - el engine BYPASSRLS, no de esta resolución.

> **Provisioning de identidad sin membership.** En el primer login SSO se
> crea (o se reutiliza, linkeando por email verificado) el usuario **global**
> sin password local utilizable (`is_sso_provisioned=true`); **no** se crea
> ninguna membership ni se leen grupos del IdP para conceder acceso (ADR
> 0047 descarta el claiming automático). El acceso es siempre explícito vía
> `/admin/users`.

## Administración de usuarios y acceso (System Admin — ADR 0047)

El System Admin lista usuarios y gestiona su acceso a tenants desde
`/admin/users` (UI) sobre estos endpoints (engine BYPASSRLS; ver la matriz
en [`rbac.md`](./rbac.md#adminpy--system_admin)):

| Endpoint                                         | Método     | Rol mínimo     |
| ------------------------------------------------ | ---------- | -------------- |
| `/admin/users`                                   | GET        | `system_admin` |
| `/admin/users/{user_id}/memberships`             | GET, POST  | `system_admin` |
| `/admin/users/{user_id}/memberships/{member_id}` | PATCH, DEL | `system_admin` |

- **`POST`** asigna `usuario↔tenant + rol` (revive una membership revocada
  en vez de chocar con el `UNIQUE(user_id, tenant_id)`; duplicado activo →
  409).
- **`PATCH`** cambia `role` y/o `is_active` (desactivar quita acceso sin
  borrar la fila — el resolver solo cuenta `is_active`).
- **`DELETE`** revoca (soft-delete + `is_active=false`).
- El `role` se limita a roles **per-tenant** (`tenant_admin` / `tenant_user`
  / `system_operator`): un IdP/admin **nunca** otorga `system_admin` por esta
  vía. Cada mutación deja `audit_log` con el `tenant_id` afectado.

SCIM (Plan 08) se mantiene como vía de aprovisionamiento **per-tenant**
(ortogonal): la administración manual de aquí es complementaria.

## SCIM 2.0

Provisioning bidireccional con IdPs que lo soporten (RFC 7643/7644).

| Endpoint                           | Método                  | Auth           |
| ---------------------------------- | ----------------------- | -------------- |
| `/scim/v2/Users`                   | POST, GET (list+filter) | bearer SCIM    |
| `/scim/v2/Users/{user_id}`         | GET, PUT, PATCH, DELETE | bearer SCIM    |
| `/auth/sso/scim/tokens`            | GET, POST               | `tenant_admin` |
| `/auth/sso/scim/tokens/{token_id}` | DELETE                  | `tenant_admin` |

- **Auth SCIM**: bearer token **por-tenant** (tabla `scim_tokens`, solo el
  digest SHA-256 en reposo). El token identifica el tenant; se resuelve una
  vez en rol `BYPASSRLS` y cada query corre luego bajo `app.tenant_id` (un
  token de A no toca B). Token ausente/erróneo/revocado → 401.
- **Deprovisioning** (`PATCH active=false` o `DELETE`) desactiva la
  membership **y revoca las sesiones vivas** del usuario en ese tenant.
- **Tokens**: el valor claro se devuelve **una sola vez** en el `POST`; solo
  su digest persiste. La UI `tenant_admin` los acuña/lista/revoca.

## MFA — segundo factor opt-in

MFA es un **segundo factor opcional por usuario**. Un usuario sin segundo
factor confirmado entra exactamente igual que antes. Si lo tiene, el primer
factor (contraseña o SSO) devuelve `{status: "mfa_required", mfa_token}` — un
token de reto interino en Redis (single-use `GETDEL`, TTL corto, **no** una
sesión). Solo el `verify`/`finish` acuña la sesión real (ver ADR 0031).

### TOTP (RFC 6238)

| Endpoint                 | Método | Auth             |
| ------------------------ | ------ | ---------------- |
| `/auth/mfa/totp`         | GET    | `tenant_member`  |
| `/auth/mfa/totp`         | DELETE | `tenant_member`  |
| `/auth/mfa/totp/enroll`  | POST   | `tenant_member`  |
| `/auth/mfa/totp/confirm` | POST   | `tenant_member`  |
| `/auth/mfa/totp/verify`  | POST   | mfa_token (reto) |

- **`/enroll`** genera el secreto (cifrado Fernet en reposo) + URI/QR
  `otpauth://` + códigos de recovery (solo digest SHA-256). **`/confirm`**
  verifica un código válido y activa el factor.
- **`/verify`** recibe `mfa_token` + código (TOTP o un código de recovery de
  un solo uso) y acuña la sesión. Código erróneo o reto reutilizado → 400.

### WebAuthn / FIDO2 (passkeys, YubiKey)

| Endpoint                             | Método | Auth             |
| ------------------------------------ | ------ | ---------------- |
| `/auth/mfa/webauthn/register/begin`  | POST   | `tenant_member`  |
| `/auth/mfa/webauthn/register/finish` | POST   | `tenant_member`  |
| `/auth/mfa/webauthn`                 | GET    | `tenant_member`  |
| `/auth/mfa/webauthn/{credential_id}` | DELETE | `tenant_member`  |
| `/auth/mfa/webauthn/login/begin`     | POST   | mfa_token (reto) |
| `/auth/mfa/webauthn/login/finish`    | POST   | mfa_token (reto) |

- **Registro**: `begin` emite opciones con un reto single-use (Redis, TTL
  corto); `finish` verifica el attestation y guarda la clave pública +
  signature counter.
- **Login**: `begin` emite opciones de autenticación con reto single-use;
  `finish` verifica la assertion (origen contra `clientDataJSON`, RP id,
  signature counter) y acuña la sesión real.
- Config: `webauthn_rp_id`, `webauthn_rp_name`, `webauthn_origin`,
  `webauthn_challenge_ttl_seconds`.

## Mapeo de grupos IdP → roles del tenant

`group_role_mappings` es un campo de la config OIDC/SAML que persiste el
mapeo grupo→rol per-tenant. Un grupo **nunca** otorga un rol de plataforma.

> **Cambio en ADR 0047.** Con auth global, el login SSO ya **no** crea
> memberships ni aplica el mapeo grupo→rol para conceder acceso: el provider
> es global y `_provision_identity` solo establece la **identidad global**
> del usuario (sin leer grupos del IdP). El acceso a un tenant lo concede
> **exclusivamente** una membership explícita que asigna el System Admin en
> `/admin/users`. El campo `group_role_mappings` se conserva en el esquema
> (compatibilidad + posible re-uso por SCIM / futuras políticas), pero no
> participa de la concesión de acceso por login. Deny-by-default.

## Variables de configuración

| Variable                                    | Default                  |
| ------------------------------------------- | ------------------------ |
| `API_SERVER_SSO_ENCRYPTION_KEY`             | dev-only (prod override) |
| `API_SERVER_SSO_REDIRECT_BASE_URL`          | `http://localhost:8000`  |
| `API_SERVER_SSO_LOGIN_STATE_TTL_SECONDS`    | `600`                    |
| `API_SERVER_MFA_CHALLENGE_TTL_SECONDS`      | `300`                    |
| `API_SERVER_WEBAUTHN_RP_ID`                 | `localhost`              |
| `API_SERVER_WEBAUTHN_RP_NAME`               | `Agentic Platform`       |
| `API_SERVER_WEBAUTHN_ORIGIN`                | `http://localhost:3000`  |
| `API_SERVER_WEBAUTHN_CHALLENGE_TTL_SECONDS` | `300`                    |

> **`sso_redirect_base_url` (ADR 0047 §6).** De este valor se derivan la
> callback OIDC, la **ACS SAML global** y el SP entityID que el operador
> registra en el IdP. El default `http://localhost:8000` es un placeholder
> que **no** coincide con el api-server de dev (`:8001`): el operador debe
> fijarlo según su despliegue. La modal de config SSO muestra estas URLs de
> forma informativa (con copiar) y avisa si sigue en el default.

## Tests que pinean estos endpoints

```bash
pytest tests/integration/test_oidc_generic.py tests/integration/test_oidc_templates.py
pytest tests/integration/test_saml.py tests/integration/test_saml_crypto.py tests/integration/test_saml_config_crud.py
pytest tests/integration/test_jit_provisioning.py tests/integration/test_scim.py
pytest tests/integration/test_mfa_totp.py tests/integration/test_mfa_webauthn.py
pytest tests/integration/test_group_mapping.py tests/integration/test_login_discovery.py
# ADR 0047 — auth global + acceso por membership:
pytest tests/integration/test_sso_global_config.py tests/integration/test_sso_global_login.py
pytest tests/integration/test_post_login_membership_resolution.py
pytest tests/integration/test_admin_user_memberships.py
```
