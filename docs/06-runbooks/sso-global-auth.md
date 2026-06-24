---
title: Operar SSO/auth platform-global + acceso por membership
docs_language: es
audience: system admin, operador
updated: 2026-06-03
---

# Runbook — SSO/auth platform-global + acceso por membership (ADR 0047)

Procedimientos operativos para configurar y operar la autenticación
**platform-global** del sistema:
[ADR 0047](../05-architecture-decisions/0047-sso-auth-global-platform-membership-access.md)
(supersede la parte per-tenant de ADR 0031, re-alinea con ADR 0028). Para
la referencia de endpoints ver
[auth-sso.md](../04-reference/auth-sso.md); para la matriz de roles,
[rbac.md](../04-reference/rbac.md).

> **Modelo (ADR 0047).** Los providers OIDC/SAML se configuran **una vez**
> (System Admin) y sirven a **todos** los tenants. El login es **por
> provider** (sin tenant en la URL); el acceso a un tenant lo concede una
> `UserOrganizationMembership` que asigna el admin **después** del login.
> Un usuario sin memberships ve la pantalla **"sin permisos, contacta al
> administrador"** (deny-by-default). El **login por contraseña + las
> sesiones + MFA + SCIM siguen funcionando sin cambios**. Las rutas viejas
> `/auth/sso/{tenant_id}/...` **están retiradas** (sin redirección).

## 0. Comprobación previa

- Stack dev arriba (`scripts/dev/up.ps1`): api-server `:8001`, admin-panel
  `:3000`, PostgreSQL, Redis, Vault.
- El esquema incluye la migración **`0076_sso_global`** (`sso_configurations`
  platform-global, sin `tenant_id`/RLS, con `button_label`). Verifícalo:

  ```bash
  alembic current   # debe mostrar 0076_sso_global (head)
  alembic heads      # cabeza ÚNICA
  ```

- Acceso como **System Admin** (`users.is_system_admin = true`). La config
  de auth providers vive bajo el grupo **Plataforma** del admin-panel.
- `sso_redirect_base_url` fijado a tu despliegue (ver §1.3). El default
  `http://localhost:8000` es un placeholder y **no** es el api-server dev
  (`:8001`).

## 1. Configurar un provider global (System Admin)

### 1.1 OIDC

1. En el admin-panel, **Plataforma → Auth/SSO** (`/admin/settings/sso`).
2. Elige una plantilla de IdP (Azure AD, Google, Okta, Auth0, GitHub,
   GitLab, Apple, Facebook) o "OIDC genérico". Rellena `issuer`,
   `client_id`, scopes, claim mappings y un `display_name` /
   `button_label` (texto del botón en `/login`; si lo dejas vacío, la UI
   usa un default por `kind`).
3. Introduce el **`client_secret`** en claro: el backend lo **cifra Fernet**
   (`sso_encryption_key`) en `client_secret_encrypted`, o pásalo como
   `client_secret_ref` (puntero a Vault). **Nunca** se devuelve ni se
   registra; la UI solo muestra `has_client_secret` + el origen.
4. Marca `enabled` y guarda. Hay **una** config OIDC para toda la
   plataforma (un segundo `POST` → 409: edita la existente).
5. La modal muestra la **callback URL** a registrar en el IdP
   (`{sso_redirect_base_url}/auth/sso/oidc/callback`) con botón de copiar.
   Regístrala en el allowlist de redirect-URIs del IdP.

### 1.2 SAML 2.0

1. **Plataforma → Auth/SSO → SAML** (`/admin/settings/sso/saml`).
2. Pega la **metadata XML del IdP** (botón "Parsear metadata") para
   pre-rellenar `idp_entity_id` / `idp_sso_url` / `idp_x509_cert`.
3. Si firmas el AuthnRequest o cifras assertions, sube el **certificado SP**
   junto con la **clave privada SP** (`sp_private_key` PEM en claro → se
   cifra Fernet, o `sp_private_key_ref` Vault). Activar firma/cifrado sin
   certificado y clave → 422.
4. La modal muestra el **SP EntityID** + la **ACS URL GLOBAL**
   (`{sso_redirect_base_url}/auth/sso/saml/acs`) — una sola identidad de SP
   para toda la plataforma (ADR 0047). Regístralos en el IdP.
5. Marca `enabled` y guarda. Hay **una** config SAML para toda la
   plataforma. SAML requiere `xmlsec` nativo; en un nodo sin ese backend
   los endpoints de cripto devuelven 501 y el resto de la auth sigue intacto.

### 1.3 Origen público + prefijo de API (`sso_redirect_base_url` + `api_path_prefix`)

La callback OIDC, la ACS SAML global y el SP EntityID se derivan de **dos**
valores (ADR 0069), editables en caliente desde la tarjeta "URL base pública" de
la pantalla SSO (o por env como bootstrap):

- **Origen público** (`API_SERVER_SSO_REDIRECT_BASE_URL` / override
  `app.public_base_url`): el `scheme://host[:port]` público, sin path
  (p.ej. `https://plataforma.example.com`).
- **Prefijo de API** (`API_SERVER_API_PATH_PREFIX` / override `app.api_path_prefix`):
  el segmento bajo el que se publica el API tras el reverse proxy single-origin
  (`/api`); **vacío** si el api-server cuelga de la raíz del dominio (subdominio
  propio o dev directo).

Las URLs efectivas son `{origen}{prefijo}/auth/sso/...`. Si el origen sigue en el
default, la modal avisa. El override (UI) gana sobre el env y aplica sin
reiniciar; el env es el bootstrap. **Publicar bajo un dominio propio** (DNS, TLS,
estos settings y el re-registro en el IdP) tiene su guía dedicada:
[07-custom-domain.md](07-custom-domain.md).

## 2. Login por provider (qué ve el usuario)

1. El usuario abre **`/login`**. La página llama al endpoint **público**
   `GET /auth/sso/providers` (sin secretos) y pinta **un botón de marca por
   provider habilitado** (icono por `kind`, `button_label` configurable) +
   el formulario de contraseña.
2. Al pulsar un botón, el navegador va a
   `/auth/sso/{provider_id}/oidc|saml/login` (por **provider**, nunca por
   tenant), redirige al IdP, vuelve por la **callback OIDC** o la **ACS SAML
   global**, y se acuña una **sesión de identidad sin tenant**.
3. El cliente llama a `GET /auth/session/resolve` para el siguiente paso
   (§3).

> El login por **contraseña** sigue exactamente igual y produce la misma
> sesión de identidad sin tenant; converge en `/auth/session/resolve`.

## 3. Acceso por membership + pantalla "sin acceso"

`/auth/session/resolve` traduce las memberships **activas** del usuario:

| Memberships activas | `state`     | Qué pasa                                                       |
| ------------------- | ----------- | -------------------------------------------------------------- |
| 0                   | `no_access` | Pantalla "sin permisos, contacta al administrador"; sin tenant |
| 1                   | `single`    | Entra directo (token tenant-scoped acuñado)                    |
| >1                  | `multiple`  | Tenant-picker → `POST /auth/session/select-tenant`             |

- La sesión de identidad **es válida** aunque haya 0 memberships: prueba
  identidad, pero no da acceso a ningún tenant. No se crea membership
  automáticamente (deny-by-default).
- `/auth/session/select-tenant` re-valida la membership activa (un
  `tenant_id` forjado → 403) antes de acuñar el token con `tid`.

## 4. Conceder/quitar acceso a un tenant (System Admin)

Desde **`/admin/users`** (UI) o por API (`require_system_admin`, engine
BYPASSRLS):

```bash
# 1) Encontrar al usuario
curl -sS http://localhost:8001/admin/users -H "Authorization: Bearer $ADMIN"

# 2) Asignar acceso: usuario -> tenant + rol (tenant_admin|tenant_user|system_operator)
curl -sS -X POST http://localhost:8001/admin/users/$UID/memberships \
  -H "Authorization: Bearer $ADMIN" -H "Content-Type: application/json" \
  -d '{"tenant_id":"'$TID'","role":"tenant_user"}'

# 3) Cambiar rol / desactivar (quita acceso sin borrar la fila)
curl -sS -X PATCH http://localhost:8001/admin/users/$UID/memberships/$MID \
  -H "Authorization: Bearer $ADMIN" -H "Content-Type: application/json" \
  -d '{"is_active":false}'

# 4) Revocar (soft-delete + is_active=false)
curl -sS -X DELETE http://localhost:8001/admin/users/$UID/memberships/$MID \
  -H "Authorization: Bearer $ADMIN"
```

- Re-asignar un tenant del que el usuario fue revocado **revive** la
  membership (no choca con `UNIQUE(user_id, tenant_id)`); un duplicado
  activo → 409.
- El `role` se limita a roles **per-tenant**: **nunca** otorga
  `system_admin` por esta vía. Cada mutación deja `audit_log` con el
  `tenant_id` afectado.
- Tras asignar, el usuario re-resuelve (`/auth/session/resolve`) y entra al
  tenant. Tras revocar, deja de poder resolver/entrar ese tenant.

## 5. Migración / consolidación desde per-tenant (Plan 08)

La migración `0076_sso_global`:

- **Consolida** las filas SSO per-tenant existentes en una global por
  `provider`/kind. Si varios tenants tenían el mismo provider, gana la
  **última actualizada**; el resto se elimina (un `NOTICE` registra cuántas
  por provider, para que el operador reconcilie — en dev suele haber pocas).
- Es **reversible**: el `downgrade` restaura el _shape_ per-tenant
  (re-añade `tenant_id` NULLABLE, FK a `organizations`, índices, RLS +
  `tenant_isolation`, el unique per-tenant) y elimina `button_label`. Las
  filas consolidadas no se resucitan (pérdida de datos en downgrade
  explícita, igual que migraciones previas).
- El manejo de secretos (`client_secret_*` / SP-key + CHECKs) **no cambia**.

Verificación up/down/up:

```bash
TEST_PG_PORT=15432 TEST_REDIS_URL=redis://localhost:6379/15 \
  pytest tests/integration/test_migrations.py tests/integration/test_sso_global_config.py -q
```

## 6. Diagnóstico

| Síntoma                                             | Causa probable / fix                                                                           |
| --------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `/login` no muestra botones de provider             | Ningún provider `enabled`; `GET /auth/sso/providers` devuelve `[]`. Habilita uno (§1).         |
| Login SSO devuelve siempre la pantalla "sin acceso" | El usuario no tiene membership activa. Asígnale una (§4) y que re-resuelva.                    |
| 404 al iniciar login SSO                            | `provider_id` desconocido/deshabilitado/no-UUID (respuesta uniforme, no revela existencia).    |
| IdP rechaza la redirect/callback                    | `sso_redirect_base_url` mal fijado o no registrado en el IdP (§1.3). Copia la URL de la modal. |
| SAML devuelve 501                                   | El nodo no tiene `xmlsec` nativo. El resto de la auth (contraseña/OIDC) sigue intacto.         |
| 403 en `select-tenant`                              | El usuario no es member activo del `tenant_id` pedido (o el id no existe).                     |

> **Seguridad.** El `client_secret` OIDC y la clave privada SP están
> cifrados en reposo (Fernet `sso_encryption_key` / Vault) y **no** se
> devuelven ni se registran nunca; el endpoint público de providers no
> expone secretos. No comitees credenciales: Vault es la única vía.

## 7. Verificación

```bash
TEST_PG_PORT=15432 TEST_REDIS_URL=redis://localhost:6379/15 pytest \
  tests/integration/test_sso_global_config.py \
  tests/integration/test_sso_global_login.py \
  tests/integration/test_post_login_membership_resolution.py \
  tests/integration/test_admin_user_memberships.py -q
```
