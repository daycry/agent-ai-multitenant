---
title: SSO empresarial y MFA — Referencia de endpoints de autenticación
audience: backend-dev, architect, security
phase: 08-sso-empresarial
updated: 2026-05-30
---

# SSO empresarial y MFA — Referencia de endpoints

Esta página documenta los endpoints de autenticación avanzada añadidos en
el Plan 08 (OIDC, SAML 2.0, SCIM 2.0, MFA TOTP/WebAuthn, mapeo de grupos y
login discovery). Se **añaden en paralelo** al login local (email +
contraseña) de la Fase 0; ese login no cambia. Para la matriz de roles
general ver [`rbac.md`](./rbac.md); para el ADR de fondo ver
[ADR 0031](../05-architecture-decisions/0031-sso-sesion-saml-xmlsec-reto-mfa.md)
y [ADR 0002](../05-architecture-decisions/0002-redis-server-side-sessions.md).

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
  responden igual exista o no la config (404 genérico), nunca revelan si una
  cuenta existe.
- **RLS por tenant.** Toda config SSO vive en `sso_configurations` bajo RLS;
  un `config_id` de otro tenant da 404 a nivel de base de datos. Un token
  SCIM se resuelve una vez en rol `BYPASSRLS` y luego cada query corre bajo
  `app.tenant_id`.
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

## OIDC

| Endpoint                           | Método      | Auth            |
| ---------------------------------- | ----------- | --------------- |
| `/auth/sso/{tenant_id}/oidc/login` | GET         | anon            |
| `/auth/sso/oidc/callback`          | GET         | anon (IdP)      |
| `/auth/sso/oidc/templates`         | GET         | `tenant_member` |
| `/auth/sso/oidc/callback-url`      | GET         | `tenant_member` |
| `/auth/sso/config`                 | GET         | `tenant_member` |
| `/auth/sso/config`                 | POST        | `tenant_admin`  |
| `/auth/sso/config/{config_id}`     | PUT, DELETE | `tenant_admin`  |

- **`/login`** resuelve la config OIDC habilitada del tenant bajo RLS, acuña
  `state` + `nonce` single-use en Redis (TTL `sso_login_state_ttl_seconds`) y
  redirige 307 al IdP. Sin config habilitada → 404 genérico.
- **`/callback`** valida el `state` (single-use, anti-CSRF), recupera el
  tenant, intercambia el `code`, verifica el ID token (firma + `iss`/`aud`/
  `nonce`), lee userinfo, hace JIT provisioning y acuña la sesión. Un
  `state`/token forjado o caducado → 400.
- **`/templates`** devuelve las plantillas por IdP (Azure AD, Google
  Workspace, Okta, Auth0, GitHub, GitLab, Apple, Facebook) para el selector
  de proveedor de la UI.
- **CRUD `/config`**: una config OIDC por tenant (unique `tenant_id,
provider`; un segundo `POST` → 409). El secret va en el cuerpo como
  `client_secret` (plano, se cifra) o `client_secret_ref` (puntero Vault);
  nunca se devuelve.

## SAML 2.0

> SAML usa la extensión nativa `xmlsec` (`python3-saml`). En un nodo sin ese
> backend, los endpoints que necesitan cripto nativa devuelven **501** y el
> resto de la auth sigue intacto (ver ADR 0031). El CRUD de config, la
> validación de invariantes y el parseo de metadata del IdP **no** necesitan
> `xmlsec` y funcionan en todos los nodos.

| Endpoint                                  | Método      | Auth            |
| ----------------------------------------- | ----------- | --------------- |
| `/auth/sso/{tenant_id}/saml/login`        | GET         | anon            |
| `/auth/sso/{tenant_id}/saml/acs`          | POST        | anon (IdP)      |
| `/auth/sso/saml/sp-metadata`              | GET         | `tenant_member` |
| `/auth/sso/{tenant_id}/saml/metadata-url` | GET         | `tenant_member` |
| `/auth/sso/saml/parse-metadata`           | POST        | `tenant_member` |
| `/auth/sso/saml/config`                   | GET         | `tenant_member` |
| `/auth/sso/saml/config`                   | POST        | `tenant_admin`  |
| `/auth/sso/saml/config/{config_id}`       | PUT, DELETE | `tenant_admin`  |

- **`/login`** (SP-initiated) construye una AuthnRequest con un RelayState
  single-use y redirige 302 al IdP.
- **`/acs`** (Assertion Consumer Service) consume el `SAMLResponse` POSTeado.
  Maneja **SP-initiated** (con el RelayState que acuñamos, single-use, con
  guard cross-tenant) e **IdP-initiated/unsolicited** (sin RelayState; el
  tenant se toma de la URL ACS por-tenant). Un assertion forjado/expirado → 400. Acuña la sesión tras JIT provisioning.
- **`/sp-metadata`** y **`/metadata-url`** devuelven el SP EntityID + la ACS
  URL por-tenant a registrar en el IdP.
- **`/parse-metadata`** parsea metadata XML del IdP (lxml endurecido
  anti-XXE) para pre-rellenar el formulario; no necesita `xmlsec`.
- **CRUD `/saml/config`**: una config por tenant. La clave privada SP va como
  `sp_private_key` (PEM plano, se cifra) o `sp_private_key_ref` (Vault);
  nunca se devuelve. Flags de política: `authn_requests_signed`,
  `want_assertions_signed`, `want_assertions_encrypted`,
  `want_name_id_encrypted` (activar firma/cifrado exige certificado + clave
  SP, o 422).

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

No es un endpoint propio: es un campo (`group_role_mappings`) de la config
OIDC/SAML, aplicado en cada login por `_jit_provision_user`. El rol de la
membership se re-sincroniza al rol per-tenant de mayor privilegio que mapee
algún grupo asertado (default `tenant_user`). Si el tenant no configuró
ningún mapeo, se conserva el flujo legacy (default JIT, el admin promueve
manualmente) y no se degrada un `tenant_admin` manual. Un grupo **nunca**
otorga un rol de plataforma.

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

## Tests que pinean estos endpoints

```bash
pytest tests/integration/test_oidc_generic.py tests/integration/test_oidc_templates.py
pytest tests/integration/test_saml.py tests/integration/test_saml_crypto.py tests/integration/test_saml_config_crud.py
pytest tests/integration/test_jit_provisioning.py tests/integration/test_scim.py
pytest tests/integration/test_mfa_totp.py tests/integration/test_mfa_webauthn.py
pytest tests/integration/test_group_mapping.py tests/integration/test_login_discovery.py
```
