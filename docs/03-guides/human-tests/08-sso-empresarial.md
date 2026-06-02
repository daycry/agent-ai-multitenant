# Plan 08 — tests humanos

> **⚠️ Modelo superseded por ADR 0047 (auth platform-global).** Esta guía
> describe el SSO **per-tenant** original del Plan 08 (config en
> `/admin/settings/sso` por tenant, login con tenant en la URL, JIT que
> crea membership + mapea grupos). El plan **`sso-global-user-admin`**
> (ADR 0047) re-arquitecturó auth a **platform-global**: los providers se
> configuran **una vez** (System Admin), el login es **por provider** (sin
> tenant en la URL), el acceso a un tenant lo concede una **membership** que
> asigna el admin en `/admin/users`, y un usuario sin memberships ve la
> pantalla **"sin permisos, contacta al administrador"**. Las rutas viejas
> `/auth/sso/{tenant_id}/...` están **retiradas**. Para el modelo vigente,
> los procedimientos y los tests humanos actuales ver el
> [runbook SSO global](../../06-runbooks/sso-global-auth.md), la
> [referencia auth-sso](../../04-reference/auth-sso.md) y el bloque de tests
> humanos del plan en
> [`docs/roadmap/sso-global-user-admin.md`](../../roadmap/sso-global-user-admin.md).
> El **login por contraseña + MFA (TOTP/WebAuthn) + SCIM siguen funcionando
> igual** y los pasos de MFA/SCIM de abajo siguen siendo válidos.

Esta guía cubre los **3 tests humanos** del Plan 08 (SSO Empresarial y
Auth Avanzada). Validan, contra integraciones reales, lo que los tests
automáticos solo cubren con mocks/fixtures: un **login OIDC con un IdP
real** (con JIT provisioning y mapeo de grupos), el **MFA TOTP**
end-to-end, y el ciclo de **provisioning/deprovisioning vía SCIM**.

> **Estado del plan**: `pending_human_validation`. Las 13 tareas y sus
> tests automáticos están en verde (OIDC genérico + plantillas por IdP,
> SAML 2.0 con firma/cifrado XML, JIT, SCIM 2.0, MFA TOTP + WebAuthn,
> mapeo de grupos, login discovery por email). Los e2e de UI
> (`sso-oidc-config.spec.ts`, `sso-saml-config.spec.ts`) están escritos
> pero requieren navegador, y la verificación con IdPs reales no se
> puede automatizar — estos 3 tests humanos son el último paso antes de
> pasar a `completed`.

## TL;DR

No hay `setup_demo_08.py` ni launcher dedicado para este plan. El setup
es manual y, sobre todo, requiere **un IdP real al que conectarte**
(Azure AD para OIDC, un IdP/Authenticator para TOTP, un IdP con SCIM
para provisioning):

```powershell
.\scripts\dev\up.ps1                          # api-server :8001 + admin-panel :3000 + postgres + redis
```

La configuración SSO por tenant se hace desde el admin-panel
(Tenant Admin):

```
http://localhost:3000/admin/settings/sso          # OIDC por tenant
http://localhost:3000/admin/settings/sso/saml      # SAML por tenant (upload metadata IdP)
```

MFA TOTP es un flujo de **endpoints** (`/auth/mfa/totp/...`) sin página
de admin-panel dedicada: el enroll/confirm/verify se conduce con curl o
desde el cliente que consume la API (el QR se escanea con la app
Authenticator). SCIM se conduce desde el IdP (o con curl simulando al
IdP) contra `/scim/v2/Users`.

## Pre-requisitos

| Requisito                                      | Por qué                                                              |
| ---------------------------------------------- | -------------------------------------------------------------------- |
| Stack dev arriba (`up.ps1`)                    | api-server + admin-panel + postgres + redis                          |
| Un tenant + un usuario `tenant_admin`          | La config SSO/SAML/SCIM es operación de Tenant Admin                 |
| IdP OIDC real (Azure AD recomendado)           | `human_08_01` requiere un login OIDC redirigiendo a un IdP de verdad |
| App Authenticator (Google Authenticator/Authy) | `human_08_02` escanea el QR TOTP                                     |
| IdP con SCIM 2.0 (o curl simulando al IdP)     | `human_08_03` crea/actualiza/suspende usuarios vía `/scim/v2/Users`  |
| `curl` / cliente API                           | Para conducir los flujos TOTP/SCIM fuera de la UI                    |

> Las credenciales del IdP (client_id/client_secret OIDC, token SCIM,
> claves SAML) son secretos: el sistema las cifra en reposo (Fernet) o
> las guarda solo como digest. Nunca las comitees.

---

## `human_08_01` — Login OIDC con un IdP real

**Qué prueba**: el flujo OIDC completo contra un IdP real (Azure AD):
redirección y vuelta correctas, creación JIT del usuario en el primer
login, mapeo de grupos del IdP a roles del tenant, y limpieza de sesión
local tras logout.

**Precondiciones**:

- Un tenant con OIDC configurado en `/admin/settings/sso` (plantilla
  Azure AD: client_id, client_secret, discovery URL/issuer).
- En Azure AD, una app registrada con el redirect URI del sistema y, si
  vas a probar el mapeo, al menos un grupo asignado al usuario de prueba.
- El usuario de prueba **no** existe todavía en el sistema (para validar
  el JIT en su primer login).

**Pasos**:

1. Como `tenant_admin`, abre `/admin/settings/sso` y configura/activa la
   integración OIDC (plantilla Azure AD).
2. Cierra sesión. En la pantalla de login, inicia el flujo SSO del
   tenant.
3. El navegador debe **redirigir al IdP** (Azure AD), pedir credenciales
   allí y **volver** al sistema autenticado.
4. Como es su primer login, verifica que el usuario se ha **creado en
   JIT** (rol por defecto `tenant_user`) — míralo en la lista de
   usuarios del tenant.
5. Si el IdP envía grupos, comprueba que se **mapean a roles** del tenant
   según el mapeo configurado (task_08_11).
6. Haz **logout** y comprueba que no queda sesión local viva (un nuevo
   request sin re-login debe pedir autenticación; la sesión Redis se ha
   invalidado).

**Resultado esperado**: redirección/vuelta OIDC correctas; usuario
creado en JIT al primer login; grupos del IdP mapeados a roles; tras
logout no hay sesión local viva.

**Checklist**:

- [ ] El flujo OIDC redirige y vuelve correctamente.
- [ ] El usuario se crea en JIT si es su primer login.
- [ ] Si el IdP envía grupos, se mapean a roles del tenant.
- [ ] Tras logout local, no queda sesión local viva.

**Pitfalls conocidos**:

- El modelo de sesión sigue siendo **server-side en Redis** tras el
  login OIDC (no JWT stateless) — ADR 0031. Si tras logout sigues
  pudiendo navegar, la sesión Redis no se revocó.
- El redirect URI registrado en el IdP debe coincidir **exactamente**
  con el del sistema (incluido el esquema/puerto), o el IdP rechaza la
  vuelta.

---

## `human_08_02` — MFA TOTP funciona

**Qué prueba**: el segundo factor TOTP opt-in: el QR se escanea, el
código de 6 dígitos verifica, sin TOTP el login no completa tras la
contraseña, y los códigos de recovery sirven si se pierde el
dispositivo.

**Precondiciones**:

- Una cuenta de login local con contraseña conocida.
- App Authenticator (Google Authenticator, Authy) en el móvil.
- El TOTP se gestiona vía endpoints `/auth/mfa/totp/...` (no hay página
  de admin-panel dedicada): usa curl o el cliente API.

**Pasos**:

1. Con la cuenta logueada, llama a `POST /auth/mfa/totp/enroll`: devuelve
   un `otpauth://` URI / QR + códigos de recovery. Escanea el QR con la
   app Authenticator.
2. Llama a `POST /auth/mfa/totp/confirm` con un código de 6 dígitos
   válido de la app → el factor queda **activado**.
3. Cierra sesión. Inicia login con email + contraseña:
   - Tras la contraseña el sistema **no** emite sesión: devuelve
     `{status: "mfa_required", mfa_token: ...}`.
   - Llama a `POST /auth/mfa/totp/verify` con `mfa_token` + el código de
     6 dígitos actual → ahora sí se emite la sesión real (`/auth/me` la
     acepta).
4. Repite el login pero **no** introduzcas el código TOTP (o mete uno
   inválido): el login **no** debe completar.
5. Repite el login y, en lugar del código de la app, usa uno de los
   **códigos de recovery**: debe funcionar **una sola vez** (se consume).

**Resultado esperado**: QR escaneable; código de 6 dígitos verifica; sin
TOTP no se pasa de la contraseña; los códigos de recovery funcionan una
vez.

**Checklist**:

- [ ] El QR se escanea correctamente con Authenticator.
- [ ] El código de 6 dígitos verifica.
- [ ] Sin TOTP el login no pasa tras la contraseña.
- [ ] Los códigos de recovery funcionan en caso de pérdida del
      dispositivo.

**Pitfalls conocidos**:

- El `mfa_token` es un **reto interino single-use en Redis** con TTL
  corto (no es una sesión): si tardas, caduca y hay que reiniciar el
  login.
- El seed TOTP se guarda **cifrado (Fernet)** y los códigos de recovery
  solo como **digest SHA-256** — nunca en claro. Si el código de la app
  no verifica, comprueba que el reloj del móvil esté sincronizado (TOTP
  depende del tiempo).
- Un usuario **sin** TOTP confirmado entra exactamente igual que antes
  (el factor es opt-in).

---

## `human_08_03` — SCIM provisiona y deprovisiona

**Qué prueba**: el IdP crea, actualiza y deshabilita usuarios vía SCIM
2.0: aparecen al instante, las actualizaciones se reflejan, y al marcar
un usuario como suspended se le revoca el acceso inmediatamente.

**Precondiciones**:

- Un tenant con un **token SCIM** emitido (gestión de tokens vía UI
  `tenant_admin`): el token identifica al tenant y se guarda solo como
  digest SHA-256.
- Un IdP con SCIM 2.0 apuntando a `/scim/v2/Users`, o curl simulando al
  IdP con el bearer token SCIM.

**Pasos**:

1. **Crear**: el IdP (o `POST /scim/v2/Users` con el bearer SCIM) crea un
   usuario nuevo. Comprueba que **aparece al instante** en el tenant
   (`GET /scim/v2/Users/{id}` lo devuelve; también se ve en la lista de
   usuarios).
2. **Actualizar**: el IdP cambia un atributo (p.ej. el nombre o el email)
   vía `PUT`/`PATCH`. Comprueba que el cambio **se refleja** en el
   sistema.
3. **Deprovisionar**: el IdP marca el usuario como suspended
   (`PATCH active=false` o `DELETE`). Comprueba que:
   - La membership del usuario en el tenant se **desactiva**.
   - Las **sesiones vivas** del usuario en ese tenant se **revocan de
     inmediato** (un request con su sesión anterior ya no funciona).

**Resultado esperado**: alta instantánea, actualizaciones reflejadas, y
revocación de acceso inmediata al suspender.

**Checklist**:

- [ ] Los usuarios aparecen en el sistema al instante de la creación.
- [ ] Las actualizaciones de atributos del IdP se reflejan.
- [ ] Cuando el IdP marca usuario como suspended, se le revoca acceso
      inmediatamente.

**Pitfalls conocidos**:

- El token SCIM es **per-tenant**: un token de Tenant A no puede tocar a
  Tenant B (se resuelve el tenant una vez en BYPASSRLS y luego cada query
  corre bajo `app.tenant_id` con RLS). Un 401 suele ser token ausente,
  malo o revocado.
- El deprovisioning desactiva la membership **y** mata las sesiones
  vivas: si el usuario suspendido sigue navegando, comprueba que el
  índice usuario→sesiones del `SessionStore` esté poblado (es
  retrocompatible, pero las sesiones creadas antes del fix podrían no
  estar indexadas — re-login para reproducir limpio).

---

## Cierre del plan

Tras pasar los 3 tests humanos:

1. Edita `docs/roadmap/08-sso-empresarial.md`:
   ```yaml
   status: completed
   completed_at: 2026-MM-DD
   ```
2. Verifica la entrada en
   [`docs/07-changelog/08-sso-empresarial.md`](../../07-changelog/) y la
   referencia [`docs/04-reference/auth-sso.md`](../../04-reference/).
3. Verifica que el PR `plan/08-sso-empresarial` está mergeado a
   `master`.

## Troubleshooting

| Síntoma                                      | Causa probable                                                   | Fix                                                                          |
| -------------------------------------------- | ---------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| El IdP rechaza la vuelta del flujo OIDC      | Redirect URI no coincide exactamente con el registrado en el IdP | Corrige el redirect URI en la app del IdP (esquema/host/puerto/path exactos) |
| Tras logout OIDC sigue habiendo sesión       | La sesión Redis no se revocó (modelo server-side, ADR 0031)      | Comprueba el logout del SessionStore; re-login + logout limpio               |
| El código TOTP no verifica                   | Reloj del móvil desincronizado (TOTP es time-based)              | Sincroniza la hora del dispositivo; reintenta con el código actual           |
| Endpoints SAML devuelven 501                 | El nodo no tiene el backend nativo `xmlsec` (import perezoso)    | Instala `xmlsec` + `python3-saml`; el resto de auth (local/OIDC) sigue OK    |
| SCIM responde 401                            | Token SCIM ausente, malo o revocado                              | Re-emite el token desde la UI `tenant_admin` y usa el nuevo bearer           |
| Usuario suspendido vía SCIM sigue con acceso | Sus sesiones no estaban indexadas usuario→sesiones               | Fuerza re-login para reproducir limpio; verifica el índice del SessionStore  |

Errores transversales viven en `docs/03-guides/gotchas/`.
