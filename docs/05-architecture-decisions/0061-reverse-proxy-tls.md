---
adr_id: "0061"
title: "Reverse proxy y terminación TLS: Caddy como única superficie publicada"
status: accepted
date: 2026-06-17
decided_at: 2026-06-17
decided_by: claude-code (delegación explícita del operador)
authors: [claude-code-2026-06]
plan_referenced: prod-01-despliegue-ejecutable
docs_language: es
---

# ADR 0061 — Reverse proxy y terminación TLS

> **Estado: `accepted`** (2026-06-17, por delegación del operador) e
> **implementado** en prod-01 Fase E (tasks 14-15): servicio `caddy`
> (caddy:2.8-alpine) como única superficie publicada (80/443), terminación TLS
> (internal/provided/acme) + HSTS, enrutado single-origin (`/api/*` al backend
> con `/api/v1` sin strip, SPA en `/`); retirados los `ports` de api-server y
> admin-panel. **Dependencia delegada a prod-09:** los fixes JS de frontend
> same-origin (`wsUrl`, review page, fetches inline, preview del wizard). Es la
> "decisión clave 2" del plan prod-01; cierra deploy-7.

## Contexto

El stack se sirve hoy con dos superficies publicadas en claro:

- `admin-panel` (Next.js) en `0.0.0.0:3000`.
- `api-server` (FastAPI) en `0.0.0.0:8000` (REST + WebSocket + API pública
  versionada `/api/v1` + callbacks externos: SSO, SCIM, webhooks entrantes).

No hay TLS, no hay HSTS, y cualquiera en la red alcanza ambos sin cifrado. El
documento maestro y el resto del stack (Vault con `tls_disable=true`) asumen que
el tráfico entra por **un único reverse proxy que termina TLS**.

### El contrato real frontend ↔ backend (investigado, no asumido)

- `apps/admin-panel/lib/api.ts:15` y `lib/ws.ts:15` leen `NEXT_PUBLIC_API_URL`
  (default `http://localhost:8001`) y hacen **fetch absolutos** a
  `${API_URL}${path}` con _path_ server-relative: `apiFetch("/agents")`,
  `apiFetch("/admin/llm-providers")`, `wsUrl("/ws/executions/{id}")`.
- `NEXT_PUBLIC_API_URL` es una variable **NEXT*PUBLIC* → se hornea en BUILD**
  (no es configurable en runtime con la build standalone actual).
- El `api-server` **no** tiene un prefijo `/api` común: monta prefijos directos
  en la raíz (`/auth`, `/admin`, `/agents`, `/projects`, `/ws`, `/scim`,
  `/webhooks/incoming`, `/healthz`, `/me`, …) y **una sola** familia ya
  prefijada con `/api`: la API pública versionada `/api/v1/*`
  (`routers/api_v1/router.py:64`, clientes externos con tokens).

### La colisión dura

El `admin-panel` sirve páginas SPA bajo `/admin/*` (p. ej.
`/admin/llm-providers`) y el `api-server` expone **REST bajo el mismo
`/admin/*`** (`/admin/llm-providers`, `/admin/backup`, `/admin/system-health`…).
En un solo origen sin diferenciación, el proxy **no puede distinguir** una
navegación de página (`GET text/html`) de un XHR a la API (`GET
application/json`): misma URL, mismo origen. Por tanto un enrutado por _path
puro_ (frontend en `/`, backend también en `/admin/*`) es **irresoluble**.

## Decisión

### 1. Reverse proxy: **Caddy** (recomendado)

| Opción                                    | Pros                                                                                                                                                        | Contras                                                                          |
| ----------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| **Caddy** (recomendado)                   | TLS automático (CA interna para self-signed, ACME para público); config mínima; recarga sin downtime; HSTS y compresión triviales; upgrade WebSocket nativo | Menos ubicuo que nginx en equipos de sistemas tradicionales                      |
| nginx + cert corporativo                  | Estándar de facto; el equipo de sistemas suele conocerlo                                                                                                    | Config verbosa; gestión de cert/renovación manual; recarga necesita orquestación |
| Proxy externo preexistente (prerequisito) | Cero superficie nueva en el stack; aprovecha PKI/WAF corporativos                                                                                           | El stack deja de ser "Docker Compose autocontenido"; depende de infra externa    |

**Recomendación: Caddy** (`caddy:2.8-alpine`, pin de versión). nginx queda como
alternativa si el equipo de sistemas lo exige; el proxy externo preexistente se
admite documentando que entonces el operador desactiva el servicio `caddy` y
publica `api-server`/`admin-panel` solo en la red interna apuntando su proxy a
ellos (fuera del alcance del compose generado).

### 2. Modelo de enrutado: **single-origin con prefijo `/api` para el backend**

Un único host `https://{domain}`:

- **Backend bajo `/api/*`**, que Caddy **retira** (`handle_path /api/*`) antes de
  reenviar al `api-server:8000`. Así `apiFetch("/admin/llm-providers")` con
  `NEXT_PUBLIC_API_URL=/api` pega a `/api/admin/llm-providers` → el proxy quita
  `/api` → llega `/admin/llm-providers` al backend. La **navegación** a
  `/admin/llm-providers` (sin `/api`) cae al SPA. **Colisión eliminada de raíz.**
- **Excepción `/api/v1/*`**: es la ÚNICA ruta del backend que ya nace con `/api`.
  Un `handle_path` genérico la rompería (`/api/v1/x` → `/v1/x`, que el backend no
  sirve). Por eso el Caddyfile coloca una regla **`handle /api/v1/*` SIN strip
  ANTES** del `handle_path /api/*` genérico: `/api/v1/projects` llega íntegro
  `/api/v1/projects` al backend; el resto de `/api/*` se desprefija. El orden de
  matchers es **load-bearing** y está comentado en el Caddyfile generado.
- **Todo lo demás → `admin-panel:3000`** (SPA Next.js standalone, incluidos
  `_next/*`, `/login`, `/select-tenant`, `/admin/*` como páginas).

Se **descarta subdominio** (`api.{domain}` vs `{domain}`): exigiría
`NEXT_PUBLIC_API_URL=https://api.{domain}` (dominio-específico, imposible de
hornear en una imagen publicada genérica), dos certificados/registros DNS y
CORS. El prefijo `/api` es **dominio-independiente** → se puede hornear en la
imagen del `release-images.yml` una sola vez.

#### URLs externas inmutables (registradas out-of-band)

Todas las superficies que invocan **terceros** viven bajo `/api` del proxy y el
operador debe registrarlas **con** ese prefijo. Cambiar el prefijo tras
registrarlas rompe la integración → **inmutables**:

- Callback SSO OIDC: `https://{domain}/api/auth/sso/oidc/callback`
- SAML ACS / metadata (EntityID): `https://{domain}/api/auth/sso/saml/acs`,
  `…/api/auth/sso/saml/metadata`
- SCIM 2.0: `https://{domain}/api/scim/v2/*`, tokens en
  `…/api/auth/sso/scim/tokens`
- Webhooks entrantes: `https://{domain}/api/webhooks/incoming/*`
- API pública versionada: `https://{domain}/api/v1/*`

Para que el callback SSO entre por el proxy, el compose fija
`API_SERVER_SSO_REDIRECT_BASE_URL = https://{domain}/api` (valor literal en el
`environment` de `api-server`, builder `_api_server_service`); el código añade
`/auth/sso/oidc/callback`, y `handle_path /api/*` lo desprefija al backend.

### 3. Terminación TLS — tres modos

| `tls_mode`               | Comportamiento                                                                                                      | Cuándo                                           |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------ |
| **`internal`** (DEFECTO) | CA interna de Caddy, certificado **autofirmado**. El instalador lo marca como **acción pendiente**.                 | Arranque inmediato sin PKI; dominio interno o IP |
| `provided`               | El operador aporta `server.crt` + `server.key` (cert corporativo), montados `ro`.                                   | Hay PKI corporativa / cert válido emitido        |
| `acme`                   | Caddy gestiona el cert por ACME (Let's Encrypt u otra CA, incl. ACME interna). Requiere 80/443 alcanzables + email. | Dominio público resoluble                        |

El modo por defecto `internal` arranca el stack **sin dependencias externas**;
el navegador (y el e2e de task_20) avisará de cert no confiable hasta que el
operador importe la root CA interna o pase a `provided`/`acme`. `acme` se
**rechaza si el dominio es una IP** (ACME no emite para IPs). Esto NO sustituye
la PKI corporativa (riesgo 6 del plan): es el suelo seguro por defecto.

### 4. Política de superficie publicada

Tras esta decisión, **el único servicio con `ports:` al host es `caddy`**
(`80:80`, `443:443`). Se **retiran** los `ports` de `api-server` y
`admin-panel`: quedan solo en la red interna `agentic-net`, alcanzados por Caddy
vía DNS de compose (`api-server:8000`, `admin-panel:3000`). `0.0.0.0:8000` y
`0.0.0.0:3000` dejan de existir. `PortsConfig` se conserva en el modelo del
wizard (back-compat / overrides de development) pero ya no se mapea a host en el
compose de producción.

### 5. Postura de hardening del proxy

`caddy` hereda `_hardening` (cap_drop `[ALL]`, `no-new-privileges`,
`apparmor=agentic-default`, `deploy.limits`, logging) y añade **`cap_add:
[NET_BIND_SERVICE]`** — la única capability necesaria para escuchar en 80/443
con `cap_drop:[ALL]` —, mismo patrón que Vault con `IPC_LOCK`. El healthcheck
apunta a un endpoint **plano en `:80`** (`/healthz` que responde 200 sin
redirigir a https) para que el redirect 308→https con cert autofirmado **no** lo
marque `unhealthy`.

## Dependencias delegadas a prod-09 (frontend)

El modelo single-origin exige que el **frontend** hable consigo mismo en el
mismo origen. Eso son cambios de **JS/build del admin-panel**, dominio de
**prod-09** (no de prod-01). prod-01 hornea `NEXT_PUBLIC_API_URL=/api` en la
imagen (es su `release-images.yml` + `admin-panel/Dockerfile`); prod-09 debe:

1. **`lib/ws.ts:18`** — `wsUrl()` hace `API_URL.replace(/^http/,"ws")`; con base
   relativa `/api` ese `.replace` es **código muerto** y devuelve `/api/ws/…`.
   El navegador resuelve un WS relativo contra el documento `https` →
   `wss://{domain}/api/ws/…` (funciona hoy), pero debe **endurecerse**
   construyendo `wss://` explícito desde `window.location` (frontend-8).
2. **`app/admin/review/[id]/page.tsx:51,63,73`** — usa `fetch` relativo a
   `/api/review/…` con `credentials:"include"` (cookie) y `ws://localhost:8001`
   hardcodeado: contradice el patrón global (Bearer vía `apiFetch`). Resolver el
   mecanismo de auth de esa página y normalizar.
3. **Fetches inline** fuera de `lib/api.ts` (`kb-documents-panel.tsx:254`,
   `projects/[id]/knowledge-bases/page.tsx:330`,
   `projects/[id]/incoming-webhooks/page.tsx:133`): normalizar a
   `apiFetch`/`wsUrl`.

Hasta que prod-09 cierre lo anterior, el **REST** del SPA funciona tras el proxy
(rutas server-relative + `/api` horneado) y los **WebSocket** funcionan por
resolución relativa del navegador; solo la página de review y los outliers
quedan rotos. El e2e de prod-01 (task_20) valida los **caminos de API** vía
`https://{domain}/api/...` (smoke), no la SPA completa.

## Consecuencias

- ✅ Cierra deploy-7: una sola superficie TLS publicada; sin HTTP plano.
- ✅ Alinea `vault/config.hcl` (tls_disable tras proxy) con la realidad.
- ✅ Resuelve la colisión `/admin` y preserva la API pública `/api/v1`.
- ⚠️ La SPA no es 100 % funcional en navegador hasta el cierre de prod-09
  (frontend-8). Documentado y delegado, no oculto.
- ⚠️ `internal` (autofirmado) por defecto: navegadores avisan hasta importar la
  root CA o pasar a `provided`/`acme`. Riesgo 6 (PKI corporativa) sigue vigente.
- ⚠️ Si el `DataPurger` (task_18) borra `{data_root}/caddy/data`, se pierde la CA
  interna y los clientes que confiaron en ella fallan → documentar en runbooks.

## Alternativas consideradas y descartadas

- **Subdominio** (`api.{domain}`): build-arg dominio-específico + CORS + 2 certs.
- **Prefijo `/api` con strip genérico sin excepción**: rompe `/api/v1`.
- **Prefijo `/_api`** (sin colisión con nada): obliga a `/_api/api/v1` para la
  API pública (feo para clientes externos). Se prefiere `/api` + regla explícita.
- **Montar todo el backend bajo `/api` en FastAPI**: cambio invasivo del backend
  y rompe `/api/v1` → `/api/api/v1`.
