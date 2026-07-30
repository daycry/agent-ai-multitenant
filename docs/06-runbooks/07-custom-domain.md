---
title: Publicar la plataforma bajo un dominio propio (custom domain)
docs_language: es
audience: system admin, operador
updated: 2026-07-18
---

# Runbook — Publicar la plataforma bajo un dominio propio

Cómo exponer la plataforma en un dominio público (usamos
**`https://example.com`** como ejemplo en todo el runbook), qué piezas se
configuran y **cómo afecta a los proveedores SSO**. Para el camino completo de
una instalación de producción de cero a publicada, ver
[08-instalacion-produccion.md](08-instalacion-produccion.md). Se apoya en el reverse proxy del
[ADR 0061](../05-architecture-decisions/0061-reverse-proxy-tls.md) (Caddy,
single-origin) y en el
[ADR 0069](../05-architecture-decisions/0069-origen-publico-y-prefijo-api-separados-sso.md)
(origen público + prefijo de API separados). Para operar SSO en detalle, ver
[sso-global-auth.md](sso-global-auth.md).

> **TL;DR.** Apunta el DNS al host, deja que Caddy emita TLS para el dominio, y
> fija en la plataforma el **origen público** (`https://tu-dominio`) y, si vas
> **single-origin**, el **prefijo de API** (`/api`). Luego **re-registra** en el
> IdP las URLs SSO (callback / ACS / EntityID) que la pantalla SSO te muestra.

## 1. Elige la topología

El dominio (host) ya está soportado; lo que cambia es **dónde cuelga el API**:

| Topología                                 | SPA                    | API                     | Origen público        | Prefijo API |
| ----------------------------------------- | ---------------------- | ----------------------- | --------------------- | ----------- |
| **Single-origin** (recomendada, ADR 0061) | `https://dominio/`     | `https://dominio/api/*` | `https://dominio`     | `/api`      |
| **API en subdominio**                     | `https://app.dominio/` | `https://api.dominio/*` | `https://api.dominio` | _(vacío)_   |

El stack por defecto (Caddy) es **single-origin**: Caddy publica el puerto 443 y
enruta `/api/*` al api-server (con strip del prefijo) y el resto a la SPA. En ese
caso el API es alcanzable en `https://dominio/api`, por eso el prefijo es `/api`.

## 2. DNS + TLS (Caddy)

1. **DNS**: un registro `A`/`AAAA` (o `CNAME`) del dominio al host del stack.
2. **TLS**: Caddy obtiene certificado automáticamente (ACME) para el dominio. En
   el instalador, fija el dominio + el modo TLS (`acme` con email, `provided`
   con cert+clave, o `internal` para pruebas) — ver
   [01-installation-from-scratch.md](01-installation-from-scratch.md) y el ADR 0061. Caddy es la **única superficie publicada** (80/443); api-server y
   admin-panel quedan internos.
3. Verifica: `https://dominio/` sirve la SPA y `https://dominio/api/healthz`
   responde `200`.

> **nginx en vez de Caddy:** el modelo es el mismo (single-origin). Configura
> nginx para terminar TLS y hacer `proxy_pass` de `/api/` → api-server (quitando
> el prefijo `/api`) y de `/` → admin-panel, soportando upgrade WebSocket en
> `/api/`. La plataforma no depende del proxy concreto; solo de **cómo se enruta
> públicamente**, que es justo lo que capturan los dos settings del paso 3.

## 3. Configura el origen público y el prefijo de API

Dos settings independientes (System Admin), live (sin reinicio):

- **Origen público** (`app.public_base_url`): el `scheme://host[:port]` del
  dominio, **sin path** (p.ej. `https://example.com`). Es la URL del
  frontend; de ella penden las rutas SSO.
- **Prefijo de API** (`app.api_path_prefix`, ADR 0069): el segmento bajo el que
  se publica el API. **`/api`** en single-origin; **vacío** si el API cuelga de
  la raíz (subdominio propio o api-server directo en dev).

**Desde la UI** (recomendado): Plataforma → **SSO / Autenticación** → tarjeta
"URL base pública de la aplicación". Rellena **URL base pública** (el origen) y
**Prefijo de API** (`/api` o vacío) y Guarda. La **URL de callback** que se
muestra abajo se recalcula a `origen + prefijo + ruta` — ese es el valor a
registrar en el IdP.

**Por env (bootstrap)**, p.ej. en el `.env` del despliegue:

```bash
API_SERVER_SSO_REDIRECT_BASE_URL=https://example.com   # origen
API_SERVER_API_PATH_PREFIX=/api                                  # prefijo (vacío si no aplica)
```

El override de la UI (platform setting) gana sobre el env. El prefijo del env lo
usan también las URLs **SCIM** (`Location`), que no leen el override.

> **Por qué dos campos:** bajo single-origin la SPA es la dueña de `/`, así que
> un callback `https://dominio/auth/sso/...` (sin `/api`) lo enrutaría Caddy a la
> SPA, no al api-server, y el login SSO fallaría. El prefijo `/api` hace que el
> callback efectivo sea `https://dominio/api/auth/sso/oidc/callback`, que Caddy
> entrega al api-server. Con prefijo vacío, el comportamiento es el de siempre.

## 4. Cómo afecta a los proveedores SSO (IdP)

Las URLs que el IdP debe conocer se **derivan** del origen + prefijo:

| Valor (SSO global, ADR 0047)     | Cómo se forma                              | Dónde verlo                                     |
| -------------------------------- | ------------------------------------------ | ----------------------------------------------- |
| **Callback OIDC** (redirect URI) | `{origen}{prefijo}/auth/sso/oidc/callback` | tarjeta SSO / `GET /auth/sso/oidc/callback-url` |
| **ACS SAML** (global)            | `{origen}{prefijo}/auth/sso/saml/acs`      | modal SAML / `GET /auth/sso/saml/sp-metadata`   |
| **SP EntityID**                  | `{origen}{prefijo}/auth/sso/saml/metadata` | modal SAML / `GET /auth/sso/saml/sp-metadata`   |

Pasos:

1. Tras fijar dominio + prefijo, **copia** desde la pantalla SSO el callback OIDC
   y/o el SP EntityID + ACS. Desde la migración 0115 puedes tener **varios
   proveedores SSO configurados a la vez** (p.ej. Google Y Microsoft): cada
   config habilitada pinta su botón en `/login` y todas comparten el mismo
   callback/ACS global — registra las URLs en CADA IdP.
2. **Regístralos en el IdP** (Okta/Entra/Auth0/ADFS…): el redirect URI en la app
   OIDC; el EntityID + ACS en el SP SAML.
3. Estos valores deben **coincidir EXACTAMENTE** con los registrados. Si más
   tarde **cambias el dominio o el prefijo**, las URLs cambian → debes
   **re-registrarlas** en el IdP, o el login fallará (`redirect_uri_mismatch` en
   OIDC; rechazo de aserción / `Recipient`/`Audience` en SAML).

> El **SP EntityID** identifica a la plataforma ante el IdP. Cambiarlo equivale a
> un SP nuevo: actualiza el SP en el IdP. Evita cambiar dominio/prefijo una vez en
> producción con SSO activo salvo ventana de mantenimiento.

## 5. Verificación

```bash
# La cadena pública (sustituye el dominio):
curl -s -o /dev/null -w "%{http_code}\n" https://example.com/            # 200 (SPA)
curl -s -o /dev/null -w "%{http_code}\n" https://example.com/api/healthz # 200 (API)

# El callback efectivo que verá el IdP (System Admin, vía la API):
#   GET /api/auth/sso/oidc/callback-url  ->  {"callback_url": "https://.../api/auth/sso/oidc/callback"}
```

- Login por **contraseña** sigue igual (no depende del prefijo).
- Login por **SSO**: tras re-registrar en el IdP, prueba el botón del provider en
  `/login`; debe volver por el callback y crear sesión.
- **WebSocket** (kanban/ejecuciones en vivo): same-origin a través del proxy
  (`wss://dominio/api/ws/...`).

## 6. Diagnóstico

| Síntoma                                                    | Causa probable                                  | Acción                                                            |
| ---------------------------------------------------------- | ----------------------------------------------- | ----------------------------------------------------------------- |
| El aviso "sigue usando el valor de arranque" no desaparece | El origen sigue en el bootstrap (`localhost`)   | Fija el origen real en la tarjeta SSO (§3)                        |
| Login SSO 404 / cae en la SPA tras volver del IdP          | Falta el prefijo `/api` en single-origin        | Fija **Prefijo de API** = `/api` (§3) y re-registra el callback   |
| IdP rechaza `redirect_uri_mismatch`                        | El callback registrado ≠ el efectivo            | Copia el callback de la pantalla SSO y regístralo igual en el IdP |
| SAML: aserción rechazada (Recipient/Audience)              | ACS/EntityID registrados ≠ efectivos            | Re-registra ACS + EntityID desde la modal SAML                    |
| `Location` de SCIM sin `/api`                              | SCIM usa el prefijo del **env**, no el override | Fija `API_SERVER_API_PATH_PREFIX=/api` en el `.env`               |

## Relacionado

- [ADR 0061](../05-architecture-decisions/0061-reverse-proxy-tls.md) — reverse proxy Caddy + TLS (single-origin).
- [ADR 0069](../05-architecture-decisions/0069-origen-publico-y-prefijo-api-separados-sso.md) — origen + prefijo separados.
- [ADR 0047](../05-architecture-decisions/0047-sso-auth-global-platform-membership-access.md) — SSO global.
- [sso-global-auth.md](sso-global-auth.md) — operar SSO. [auth-sso.md](../04-reference/auth-sso.md) — referencia de endpoints.
