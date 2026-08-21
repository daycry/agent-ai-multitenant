---
adr_id: "0069"
title: "Origen público y prefijo de API separados para URLs SSO (single-origin)"
status: accepted
date: 2026-06-19
authors: [system_architect]
plan_referenced: personalizacion-equipos-built-in
docs_language: es
extends: ["0047", "0061"]
related: ["0062"]
---

# ADR 0069 — Origen público y prefijo de API separados para URLs SSO

> **Estado: `accepted`** (operador eligió la "opción C", 2026-06-19). Extiende el
> [ADR 0047](0047-sso-auth-global-platform-membership-access.md) (SSO global: callback/ACS/EntityID derivados de
> una base pública) y el [ADR 0061](0061-reverse-proxy-tls.md) (Caddy
> single-origin: SPA en `/`, API bajo `/api`).

## Contexto

El SSO global construye el callback OIDC, el ACS SAML y el SP EntityID anexando
rutas conocidas a una **base pública** (`app.public_base_url`, override del System
Admin; bootstrap `settings.sso_redirect_base_url`). El validador de esa base
exigía un **origen desnudo** `scheme://host[:port]` (sin path).

Con el reverse proxy **single-origin** del ADR 0061, el api-server no cuelga de la
raíz del dominio: vive bajo **`/api`** (la SPA es la dueña de `/`). Entonces el
callback correcto es `https://dominio/api/auth/sso/oidc/callback`. Pero como la
base no admitía path, no se podía expresar ese `/api`, y un callback sin prefijo
(`https://dominio/auth/sso/...`) lo enruta Caddy a la **SPA**, no al api-server →
el login SSO se rompe. (Hueco de single-origin que el ADR 0061 dejó pendiente.)

Se valoraron tres opciones: (A) permitir un path en la propia base; (B) una ruta
especial en Caddy para `/auth/sso/*`; (C) separar **origen** y **prefijo de API**
en dos settings. Se elige **C**.

## Decisión

Dos settings independientes:

- **`app.public_base_url`** — el **ORIGEN** público (`scheme://host[:port]`, sin
  path). Validador **sin cambios** (sigue rechazando paths). Es la URL del
  frontend; sirve también para enlaces absolutos a la SPA.
- **`app.api_path_prefix`** (NUEVO) — el **PREFIJO** bajo el que se publica el API
  (`""` = sin prefijo, o `/api`, `/api/v1`…). Validador nuevo
  (`validate_api_path_prefix`): vacío o path absoluto bare (sin host/query/
  fragment), normalizado con barra inicial y sin barra final. Bootstrap por env
  `API_SERVER_API_PATH_PREFIX` (default `""`, retro-compatible).

Las URLs SSO se construyen como **`{origen}{prefijo}{ruta_sso}`**. La inyección
ocurre en un **único chokepoint**: `_effective_redirect_base()` devuelve
`origen+prefijo`, y los builders existentes (`_callback_redirect_uri`,
`_sp_entity_id`, `_saml_acs_url`) lo reciben y anexan su ruta **sin cambios**. Con
`prefijo=""` el comportamiento es idéntico al previo (por eso es retro-compatible).

- **SCIM** (`_scim_user_location`) no pasa por ese chokepoint (usa el env), así que
  también anexa el prefijo del env para que el header `Location` sea correcto tras
  el proxy.
- **Endpoints** `GET/PUT /auth/sso/api-path-prefix` (System Admin) para fijarlo en
  caliente; la UI de ajustes SSO muestra el campo "Prefijo de API" junto al de
  origen, y el callback mostrado (que el operador registra en el IdP) se recalcula
  con origen+prefijo.

## Por qué C (y no A ni B)

- **A (path en la base)**: el setting es también "base canónica para enlaces
  absolutos"; meter `/api` ahí ensuciaría enlaces al frontend si algún día se usan.
  C mantiene el origen limpio y separa la responsabilidad.
- **B (ruta en Caddy para `/auth/sso/*`)**: rompe el modelo single-origin (la SPA
  es dueña de `/`), exige ordenar rutas con cuidado y solo cubre SSO (no SCIM ni
  otros paths del API).
- **C**: un mismo origen sirve frontend (`/`) y SSO/API (`{prefijo}/…`); generaliza
  a cualquier gateway (Caddy hoy, nginx mañana) y a cualquier prefijo.

## Consecuencias

- **+** Publicable bajo dominio propio en single-origin: origen = `https://dominio`,
  prefijo = `/api`; el callback/ACS/EntityID quedan correctos sin tocar Caddy.
- **+** Retro-compatible: default `""` → URLs idénticas a antes; todos los tests
  SSO previos siguen verdes sin cambios.
- **+** Si el API va en su **propio subdominio** (`https://api.dominio`), prefijo
  `""` y listo — C no estorba.
- **−** Dos campos en vez de uno (origen + prefijo). Documentado en la guía de
  custom domain y en la propia pantalla SSO.
- **IdP**: al cambiar dominio o prefijo, hay que **re-registrar** callback/ACS/
  EntityID en el IdP (deben coincidir exactos). La pantalla SSO muestra los valores
  efectivos para copiarlos.

## Tests

`validate_api_path_prefix` (unit: normaliza `""`/`/`→`""`, `/api/`→`/api`,
rechaza `api`/host/query/fragment). Integración: fijar `/api` hace que
`GET /auth/sso/oidc/callback-url` y `GET /auth/sso/saml/sp-metadata` lleven el
prefijo; default `""` reproduce las URLs previas (regresión SSO en verde).
