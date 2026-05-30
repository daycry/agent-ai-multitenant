---
plan_id: 08-sso-empresarial
title: SSO Empresarial y Auth Avanzada
completed_at: null
docs_language: es
---

# Plan 08 — SSO Empresarial y Auth Avanzada

## Resumen

Abre el sistema a organizaciones que exigen **SSO empresarial** sin tocar
la auth básica de la Fase 0 (email + contraseña local). Todo lo nuevo se
**añade en paralelo**: el login local, OIDC, SAML y MFA conviven y un
callback SSO acaba exactamente en el mismo modelo de sesión que el login
local — una **sesión server-side en Redis** (`SessionStore`) + un JWT
(`encode_jwt`), de modo que logout/revocación y los gates `get_principal`
se comportan igual sea cual sea el método de autenticación (no hay JWT
stateless tras OIDC; ver ADR 0002 y la nueva ADR 0031).

Las 13 tareas se desarrollaron por TDD en tres fases (A — OIDC, B — SAML,
C — provisioning + MFA + discovery). Cada función nueva trae su regresión
de aislamiento cross-tenant; toda configuración sensible (client secret
OIDC, clave privada SP de SAML, seed TOTP, tokens SCIM, códigos de
recovery) va **cifrada o hasheada en reposo** y NUNCA se devuelve por la
API. Ninguna ruta filtra existencia de cuentas (sin user enumeration) y
un grupo del IdP NUNCA puede otorgar un rol de plataforma
(`system_admin` / `system_operator`).

## Cambios

### Fase A — OIDC genérico y plantillas

- ✅ **`task_08_01`** — **Login OIDC genérico** con `authlib`
  (`routers/sso.py`, `auth/sso/oidc.py`). Dos endpoints:
  `GET /auth/sso/{tenant_id}/oidc/login` (resuelve la config OIDC habilitada
  del tenant bajo RLS, acuña `state` + `nonce` single-use en Redis y
  redirige 307 al IdP) y `GET /auth/sso/oidc/callback` (valida el `state`
  single-use, recupera el tenant, intercambia el `code`, verifica el
  ID token —firma + `iss`/`aud`/`nonce`—, lee userinfo, hace JIT y acuña la
  sesión). El `state`/`nonce` viven en `OIDCStateStore` (Redis, `GETDEL`
  atómico single-use).
- ✅ **`task_08_02`** — **Plantillas por IdP** (`auth/sso/templates.py`):
  Azure AD, Google Workspace, Okta, Auth0, GitHub, GitLab, Apple y Facebook,
  con `issuer_template`, scopes por defecto, claim mappings y parámetros
  requeridos. Expuestas (read-only, gateadas a miembro del tenant) en
  `GET /auth/sso/oidc/templates` para el selector de proveedor de la UI.
- ✅ **`task_08_03`** — **UI de configuración OIDC por tenant** + CRUD
  backend: `GET/POST/PUT/DELETE /auth/sso/config` y
  `GET /auth/sso/oidc/callback-url` (la redirect-URI a registrar en el IdP).
  RBAC: lectura = miembro del tenant, escritura = `tenant_admin`; RLS scopea
  cada query al tenant activo (un `config_id` de otro tenant da 404). El
  secret **nunca** se devuelve: la respuesta lleva `has_client_secret` +
  `client_secret_source` (`vault` | `encrypted`). El e2e Playwright
  (`e2e/sso-oidc-config.spec.ts`) está escrito pero **pendiente de
  verificación humana**.

### Fase B — SAML 2.0

- ✅ **`task_08_04`** — **SAML 2.0** con `python3-saml`
  (`auth/sso/saml.py`), **SP-initiated** (`GET /auth/sso/{tenant_id}/saml/login`
  → AuthnRequest con RelayState single-use) e **IdP-initiated/unsolicited**
  (`POST /auth/sso/{tenant_id}/saml/acs`, el tenant se toma de la URL ACS
  por-tenant). El import de `python3-saml` es **perezoso**: en un nodo SIN
  backend nativo `xmlsec` los endpoints SAML devuelven **501** (guard
  testeado) y el resto de la auth sigue intacto (ver ADR 0031).
- ✅ **`task_08_05`** — **Firma y cifrado XML**: columnas SP en
  `sso_configurations` (`sp_x509_cert` público, `sp_private_key_ref` /
  `sp_private_key_encrypted` cifrada en reposo con Fernet) y flags de
  política (`authn_requests_signed`, `want_assertions_signed`,
  `want_assertions_encrypted`, `want_name_id_encrypted`).
  `validate_saml_security()` valida invariantes sin necesitar `xmlsec`
  (camino degradado seguro); `resolve_sp_private_key()` refleja
  `resolve_client_secret` (Vault-first, Fernet fallback). La firma de la
  AuthnRequest y la verificación/descifrado del assertion usan SHA-256.
- ✅ **`task_08_06`** — **UI de configuración SAML por tenant** con upload de
  metadata IdP + CRUD: `GET/POST/PUT/DELETE /auth/sso/saml/config`,
  `GET /auth/sso/saml/sp-metadata` (+ variante
  `GET /auth/sso/{tenant_id}/saml/metadata-url` para superadmin) y
  `POST /auth/sso/saml/parse-metadata` (parseo server-side de metadata IdP
  con lxml endurecido anti-XXE, **sin** `xmlsec`). La clave privada SP nunca
  se devuelve (`has_sp_private_key` + `sp_private_key_source`). El e2e
  `e2e/sso-saml-config.spec.ts` está escrito pero **pendiente de
  verificación humana**.

### Fase C — Provisioning, MFA, mapeo de grupos y discovery

- ✅ **`task_08_07`** — **JIT provisioning** al primer login SSO
  (`_jit_provision_user`, compartido por OIDC y SAML): enlaza por email
  verificado (normalizado a minúsculas), **nunca duplica** usuario; el
  primer login crea el usuario con hash sentinel sin contraseña local
  utilizable y `users.is_sso_provisioned = true` (el login local lo rechaza
  con el mismo 401 genérico). La membership se crea/actualiza bajo
  `app.tenant_id`, idempotente bajo concurrencia (resuelve carreras en los
  índices únicos de `users.email` y `uq_membership_user_tenant`).
- ✅ **`task_08_08`** — **SCIM 2.0** (RFC 7643/7644): `/scim/v2/Users`
  (POST / GET-id / GET-list+filter / PUT / PATCH / DELETE) autenticados por
  bearer token **por-tenant** (tabla `scim_tokens` — solo el digest SHA-256
  en reposo, el token identifica el tenant). El token se resuelve una vez en
  rol `BYPASSRLS` y luego cada query corre bajo `app.tenant_id` (RLS → un
  token de A no toca B). Deprovisioning (`active=false` / DELETE) desactiva
  la membership **y revoca las sesiones vivas** del usuario en el tenant.
  Gestión de tokens (mint/list/revoke) vía UI `tenant_admin`:
  `GET/POST/DELETE /auth/sso/scim/tokens`.
- ✅ **`task_08_09`** — **MFA TOTP** (RFC 6238, `pyotp`) como **segundo
  factor opt-in**: `/auth/mfa/totp` (`GET` status, `POST /enroll`,
  `POST /confirm`, `DELETE`) tenant-scoped. Cableado en login: si el usuario
  tiene TOTP confirmado, la contraseña NO emite sesión — devuelve
  `{status: mfa_required, mfa_token}` (token de reto interino en Redis,
  single-use `GETDEL`, TTL corto, **no** una sesión; ver ADR 0031);
  `POST /auth/mfa/totp/verify` (token + código, o un código de recovery de
  un solo uso) completa la sesión real. El seed TOTP va cifrado Fernet y los
  códigos de recovery solo como digest SHA-256.
- ✅ **`task_08_10`** — **MFA WebAuthn** (`py_webauthn`, passkeys / YubiKey)
  como segundo factor: registro
  (`POST /auth/mfa/webauthn/register/begin` + `/finish`), listado/borrado de
  credenciales (`GET /auth/mfa/webauthn`,
  `DELETE /auth/mfa/webauthn/{credential_id}`) y ceremonia de login
  (`POST /auth/mfa/webauthn/login/begin` + `/finish` → sesión real). El reto
  se guarda en Redis single-use con TTL corto (mismo patrón que el `state`
  OIDC). RP id / origin / RP name configurables.
- ✅ **`task_08_11`** — **Mapeo grupos IdP → roles tenant**
  (`auth/sso/group_mapping.py`): `resolve_role_from_groups` toma el rol
  per-tenant de mayor privilegio que mapea cualquier grupo asertado, con
  default `tenant_user`. En cada login se re-sincroniza el rol de la
  membership (un cambio de grupo en el IdP surte efecto al siguiente login)
  **excepto** cuando el tenant no configuró ningún mapeo (conserva el flujo
  legacy "default JIT, el admin promueve"). Un grupo **nunca** otorga un rol
  de plataforma.
- ✅ **`task_08_12`** — **Login discovery email → tenant**: público
  `GET /auth/discover?email=...`. Mapea el **dominio** del email al tenant
  cuya config SSO habilitada lo reclama (`email_domains`, JSONB) y devuelve
  el método (`password` | `sso`), el provider, el `tenant_id` y la URL de
  login. **Sin user enumeration**: nunca consulta `users`, así que la
  respuesta es idéntica exista o no la cuenta; un email malformado o un
  dominio no configurado obtiene la misma respuesta genérica de login local.
- ✅ **`task_08_13`** — **Documentación del plan** (este changelog, la
  referencia de endpoints `docs/04-reference/auth-sso.md` y la ADR 0031).

## Endpoints nuevos

| Endpoint                                  | Método               | Auth                |
| ----------------------------------------- | -------------------- | ------------------- |
| `/auth/discover`                          | GET                  | anon (público)      |
| `/auth/sso/{tenant_id}/oidc/login`        | GET                  | anon (inicia flujo) |
| `/auth/sso/oidc/callback`                 | GET                  | anon (callback IdP) |
| `/auth/sso/oidc/templates`                | GET                  | `tenant_member`     |
| `/auth/sso/oidc/callback-url`             | GET                  | `tenant_member`     |
| `/auth/sso/config`                        | GET                  | `tenant_member`     |
| `/auth/sso/config`                        | POST                 | `tenant_admin`      |
| `/auth/sso/config/{config_id}`            | PUT, DELETE          | `tenant_admin`      |
| `/auth/sso/{tenant_id}/saml/login`        | GET                  | anon (inicia flujo) |
| `/auth/sso/{tenant_id}/saml/acs`          | POST                 | anon (POST del IdP) |
| `/auth/sso/saml/sp-metadata`              | GET                  | `tenant_member`     |
| `/auth/sso/{tenant_id}/saml/metadata-url` | GET                  | `tenant_member`     |
| `/auth/sso/saml/parse-metadata`           | POST                 | `tenant_member`     |
| `/auth/sso/saml/config`                   | GET                  | `tenant_member`     |
| `/auth/sso/saml/config`                   | POST                 | `tenant_admin`      |
| `/auth/sso/saml/config/{config_id}`       | PUT, DELETE          | `tenant_admin`      |
| `/scim/v2/Users`                          | POST, GET            | bearer SCIM         |
| `/scim/v2/Users/{user_id}`                | GET, PUT, PATCH, DEL | bearer SCIM         |
| `/auth/sso/scim/tokens`                   | GET, POST            | `tenant_admin`      |
| `/auth/sso/scim/tokens/{token_id}`        | DELETE               | `tenant_admin`      |
| `/auth/mfa/totp`                          | GET, DELETE          | `tenant_member`     |
| `/auth/mfa/totp/enroll`                   | POST                 | `tenant_member`     |
| `/auth/mfa/totp/confirm`                  | POST                 | `tenant_member`     |
| `/auth/mfa/totp/verify`                   | POST                 | mfa_token (reto)    |
| `/auth/mfa/webauthn/register/begin`       | POST                 | `tenant_member`     |
| `/auth/mfa/webauthn/register/finish`      | POST                 | `tenant_member`     |
| `/auth/mfa/webauthn`                      | GET                  | `tenant_member`     |
| `/auth/mfa/webauthn/{credential_id}`      | DELETE               | `tenant_member`     |
| `/auth/mfa/webauthn/login/begin`          | POST                 | mfa_token (reto)    |
| `/auth/mfa/webauthn/login/finish`         | POST                 | mfa_token (reto)    |

> Detalle completo (forma de request/response, RBAC, RLS y notas de
> seguridad) en [`docs/04-reference/auth-sso.md`](../04-reference/auth-sso.md).

## Migraciones (todas reversibles, single head)

| Revisión | Contenido                                                                        |
| -------- | -------------------------------------------------------------------------------- |
| **0032** | Tabla `sso_configurations` (per-tenant, RLS, CHECK por provider) + secret OIDC   |
| **0033** | Columnas SAML del IdP en `sso_configurations` (entity_id / sso_url / cert)       |
| **0034** | Columnas SP de firma/cifrado SAML + flags de política + CHECK de cripto          |
| **0035** | `users.is_sso_provisioned` (identidad SSO sin contraseña local)                  |
| **0036** | Tabla `scim_tokens` (digest SHA-256) + `user_org_memberships.external_id`        |
| **0037** | Tabla `user_mfa_totp` (seed cifrado + recovery hasheados), RLS por tenant        |
| **0038** | Tabla `webauthn_credentials` (claves públicas + signature counter), RLS          |
| **0039** | Mapeos grupo→rol del IdP (`group_role_mappings`) en `sso_configurations`         |
| **0040** | Dominios de email para login discovery (`email_domains`) en `sso_configurations` |

## Configuración / variables de entorno nuevas

| Variable                                    | Default                                | Para qué                                                                        |
| ------------------------------------------- | -------------------------------------- | ------------------------------------------------------------------------------- |
| `API_SERVER_SSO_ENCRYPTION_KEY`             | `dev-only-…` (prod debe sobreescribir) | Deriva la clave Fernet que cifra secretos SSO/MFA en reposo cuando no hay Vault |
| `API_SERVER_SSO_REDIRECT_BASE_URL`          | `http://localhost:8000`                | Base pública para construir la redirect-URI OIDC y las URLs SP de SAML          |
| `API_SERVER_SSO_LOGIN_STATE_TTL_SECONDS`    | `600`                                  | TTL del `state`/`nonce` OIDC y del RelayState SAML en Redis                     |
| `API_SERVER_MFA_CHALLENGE_TTL_SECONDS`      | `300`                                  | TTL del token de reto MFA interino (NO una sesión) en Redis                     |
| `API_SERVER_WEBAUTHN_RP_ID`                 | `localhost`                            | Relying Party id (sufijo registrable del host de origen)                        |
| `API_SERVER_WEBAUTHN_RP_NAME`               | `Agentic Platform`                     | Nombre del RP mostrado por el autenticador                                      |
| `API_SERVER_WEBAUTHN_ORIGIN`                | `http://localhost:3000`                | Origen esperado verificado contra el `clientDataJSON` firmado                   |
| `API_SERVER_WEBAUTHN_CHALLENGE_TTL_SECONDS` | `300`                                  | TTL del reto WebAuthn en Redis (single-use)                                     |

Los tokens SCIM no tienen variable de entorno: se acuñan por tenant desde
la UI `tenant_admin` y solo su digest SHA-256 vive en `scim_tokens`.

## Decisiones

- **El SSO reutiliza el modelo de sesión Redis, no un JWT stateless tras
  OIDC/SAML.** Un callback SSO acaba en `SessionStore` + JWT idéntico al
  login local (logout/revocación uniformes). Confirma ADR 0002 y se amplía
  en la nueva **ADR 0031**.
- **Dependencia nativa `xmlsec` de SAML con degradación a 501.** El import de
  `python3-saml` es perezoso; un nodo sin el backend nativo reporta SAML como
  no disponible (501) en vez de tumbar el arranque — login local + OIDC + MFA
  siguen funcionando. Decisión registrada en **ADR 0031**.
- **Token de reto MFA distinto de una sesión.** Entre el primer factor y el
  segundo factor se emite un token de reto interino en Redis (single-use
  `GETDEL`, TTL corto) que **no** concede acceso; solo el `verify` acuña la
  sesión real. Decisión registrada en **ADR 0031**.

## Pendiente

- **e2e Playwright de las UIs de configuración SSO**
  (`e2e/sso-oidc-config.spec.ts`, `e2e/sso-saml-config.spec.ts`) están
  **escritos pero PENDIENTES DE VERIFICACIÓN HUMANA** — este entorno no tiene
  app + navegador para ejecutarlos. El typecheck/lint/build del admin-panel
  sí pasan, y el CRUD backend de ambas está cubierto por tests de integración.
- **SAML firma/cifrado en nodos sin `xmlsec`**: la superficie de configuración
  y validación de invariantes corre en todas partes; la parte cripto nativa
  (firma de AuthnRequest, verificación/descifrado del assertion) requiere el
  backend `xmlsec` y degrada a 501 donde no está.
- **LDAP opcional** (sincronización periódica): estaba en el alcance amplio
  del plan pero NO se implementó en esta iteración; SCIM 2.0 cubre el
  provisioning bidireccional con los IdPs modernos. Queda como follow-up.
- Tests humanos del plan (`human_08_01`…`human_08_03`) pendientes de ejecutar
  por un humano antes de pasar a `completed` (login OIDC con IdP real, MFA
  TOTP, SCIM provisiona/deprovisiona).

## Verificación

- `pre-commit run --files <cambiados>` (black/ruff/mypy/prettier) ✅ por tarea.
- `pytest tests/integration/test_oidc_generic.py test_oidc_templates.py
test_saml.py test_saml_crypto.py test_saml_config_crud.py
test_jit_provisioning.py test_scim.py test_mfa_totp.py test_mfa_webauthn.py
test_group_mapping.py test_login_discovery.py` ✅ (incl. regresiones de
  login local + OIDC + SAML + MFA y aislamiento cross-tenant).
- Migraciones 0032..0040 reversibles (up/down/up) con single head.
- admin-panel: `npm run typecheck && lint && build` ✅; e2e Playwright de las
  UIs SSO **pendiente de verificación humana**.

## PR

Pendiente de apertura/merge a `main` (lo gestiona el humano tras validar los
tests humanos del plan).
