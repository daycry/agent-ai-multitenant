---
adr: "0037"
title: API pública con X-API-Token scoped por tenant, versionado en el path, webhooks entrantes HMAC-verify con config-id-en-URL, y SDKs generados desde el OpenAPI v1
status: accepted
date: 2026-05-30
deciders: System Architect, Security
phase: 13-api-publica-webhooks
---

# ADR 0037 — API pública (X-API-Token scoped por tenant), versionado en el path, webhooks entrantes HMAC-verify con config-id-en-URL, y SDKs generados desde el OpenAPI v1

> **Estado: `accepted`.** Recoge cuatro decisiones arquitectónicas tomadas
> durante el Plan 13 que no estaban registradas en un ADR previo: la
> **autenticación de la API pública con `X-API-Token` en cabecera + scope por
> tenant**; el **versionado en el path (`/api/v1`)** con cabecera
> `X-API-Version` opcional; el contrato de **webhooks entrantes con verificación
> HMAC y `config_id` (no el secreto) en la URL**; y la **generación de los SDKs
> oficiales DESDE el OpenAPI v1** (in-process) con un cliente fino escrito a mano
> y el código generado excluido de los linters. La firma de webhooks reusa el
> helper HMAC de la **ADR 0034** (Plan 10, dirección saliente); el aislamiento
> por RLS arranca de la **ADR 0001**.

## Contexto

Hasta el Plan 13 el sistema era una isla: no había forma de que las herramientas
de un tenant (CI, issue trackers, monitoring, scripts) leyeran su estado ni le
empujaran eventos. El plan abre tres superficies nuevas y dos SDKs. Varias
cuestiones de diseño no quedaban cerradas por ADRs previos:

1. **¿Cómo se autentica una herramienta externa contra la API pública, y cómo se
   garantiza que no se sale de su tenant?** El sistema es multi-tenant con RLS
   desde el día uno (ADR 0001), pero la API interna se autentica por sesión/JWT
   de usuario — inadecuado para una integración máquina-a-máquina de larga vida.

2. **¿Cómo se versiona la API pública** para poder evolucionarla sin romper a los
   consumidores?

3. **¿Cómo se autentica un webhook ENTRANTE** (la dirección inversa al firmado
   saliente del Plan 10) y qué va en su URL pública sin filtrar secretos?

4. **¿Cómo se construyen y mantienen SDKs oficiales** Python y TypeScript que no
   se desincronicen del servidor, y cómo conviven con los linters del repo si el
   código es generado?

## Decisión

### 1. API pública con `X-API-Token` en cabecera + scope por tenant

La API pública `/api/v1` se autentica **exclusivamente** con un **`ApiToken` por
tenant** presentado en la cabecera **`X-API-Token`** (nunca un query param: una
URL con el secreto se filtra en logs, history y referers). El token lo acuña el
**Tenant Admin** (`/auth/api-tokens`) con `scope` (`read`/`write`), vigencia
(`expires_at`), `rate_limit` por token e `ip_allowlist` opcional. **Solo se
persiste el digest SHA-256** del token (más un `prefix` claro para desambiguar en
listados), nunca el token — el secreto se devuelve en claro **exactamente una
vez** al acuñar (mismo precedente que SCIM, ADR 0031, y marketplace, ADR 0032).

La resolución token → tenant corre **una vez sobre el rol BYPASSRLS** (la request
está sin autenticar hasta casar el hash), cacheada en Redis con TTL corto (la
revocación borra la clave, así que el TTL es solo el techo de staleness). A partir
de ahí **cada** consulta `/api/v1` corre sobre el rol de app (NOBYPASSRLS) con
`app.tenant_id` fijado al tenant resuelto, de modo que **RLS — no el código del
endpoint — garantiza el aislamiento**: un token de tenant A nunca lee ni escribe
filas de tenant B (un id ajeno es un 404 limpio). Los GET piden el scope `read`,
los POST piden `write` (403 si el token es válido pero le falta el scope; 401 si
es inválido/ausente; un `write` **no** concede `read` implícitamente). Cada token
lleva un **rate limit por sliding-window en Redis** (default 100 req/min) que
adjunta cabeceras `X-RateLimit-*` (429 al exceder).

### 2. Versionado en el path (`/api/v1`)

La API se versiona **en el path** (`/api/v1/...`), no en una cabecera. El path es
**explícito y cacheable**, aparece en logs y en el propio OpenAPI, y un consumidor
no puede "olvidarse" de elegir versión. Una cabecera **`X-API-Version`** opcional
es un pin/observe **encima** del path: un caller puede afirmar "espero v1" y un
mismatch surge como un **400 limpio** en vez de tener éxito silencioso contra el
contrato equivocado; la versión servida se **anuncia** de vuelta en cada respuesta
(`X-API-Version: v1`). El uso por versión se **trackea** con un contador diario en
Redis (`apiusage:v1:<yyyymmdd>`), observabilidad best-effort: una pérdida ocasional
de incremento es aceptable, así que NO se añade tabla ni migración para una métrica
(mismo criterio que las claves `apitoken:rl:`).

El contrato se publica como **OpenAPI 3.1** autocontenido (solo las rutas v1, no la
app entera) en `/api/v1/openapi.json` + Swagger UI en `/api/v1/docs`. La versión
`3.1.0` se **pinea explícitamente** (no heredar un default mutable del framework
para un contrato publicado) y el esquema de seguridad **`apiKey`/`X-API-Token`
(`ApiTokenAuth`)** se **inyecta a mano** + se aplica como requisito global, porque
la dependencia de cabecera de Fase A es opaca a la generación automática de FastAPI
(sin esto el spec mostraría las rutas pero no diría que hace falta un token).

### 3. Webhooks entrantes: HMAC-verify + `config_id` en la URL (no el secreto)

El endpoint de recepción `POST /webhooks/incoming/{origin}/{config_id}` es la
inversa del firmado saliente del Plan 10 (ADR 0034): un tool externo (GitHub,
GitLab, Jira, Sentry, Linear, genérico) estampa una **firma HMAC-SHA256** sobre el
body crudo con un secreto compartido, y el endpoint la **reverifica** con el secreto
por proyecto y compara en **tiempo constante** (`hmac.compare_digest`, el mismo
primitivo del `verify_webhook()` de la ADR 0034). El endpoint es **PÚBLICO** — la
HMAC ES la autenticación — así que el **orden de los checks es el contrato de
seguridad**: body-cap (413, antes de leer el body — guarda anti-DDoS) → resolver
config (404) → rate limit por config (429) → **verificar HMAC (401, sin acción)** →
mapear + actuar → persistir.

La URL lleva el **`config_id` (un UUID, no un secreto)**, no el secreto de firma. El
id resuelve a una fila `incoming_webhook_configs` y, a través de ella, a su
`tenant_id` + `project_id`, de modo que un evento de proyecto A nunca puede actuar
sobre tenant B. El **secreto de firma se guarda solo como ciphertext Fernet** (at
rest, mismo patrón que la ADR 0034) y se devuelve en claro **una sola vez** al
crear/rotar; nunca aparece en una URL, una respuesta de listado, ni un log. Poner el
**secreto en la URL** se descarta explícitamente: una URL pública se filtra
trivialmente y rotar el secreto cambiaría la URL registrada en el proveedor.

El evento verificado se **persiste** (raw body + headers) con un **UNIQUE parcial
`(config_id, delivery_id)`** que hace la redelivery **idempotente** (ni el evento ni
su acción se reaplican), y la acción mapeada se ejecuta **en la misma transacción**
que registra el evento (atómica, exactamente una vez por delivery). El **replay**
operador-iniciado re-corre verify+parse+map+action contra el payload almacenado,
auditado como una fila propia (`replayed_from_event_id`, `delivery_id = NULL`).

### 4. SDKs oficiales generados DESDE el OpenAPI v1 (in-process) + cliente fino a mano

Los SDKs Python y TypeScript se **generan DESDE** el OpenAPI v1, no se mantienen a
mano contra el servidor vivo. El spec se construye **en proceso**
(`build_v1_openapi()`, la misma función que sirve `/api/v1/openapi.json`) y se
escribe a `openapi-v1.json` **sin necesidad de un servidor en marcha**, de modo que
la generación es reproducible + revisable y el SDK no puede desviarse del contrato.

El patrón es **modelos generados + cliente fino escrito a mano** en ambos lenguajes:
el cliente fija la cabecera `X-API-Token` **una vez** y expone métodos tipados que
reflejan los endpoints v1, elevando un error tipado (401/403/404/429). El código
**generado** sigue el estilo de su generador (no el del repo), así que se **excluye
de los linters** del repo (`black`/`ruff`/`mypy` para Python; `eslint`/`prettier`
para TS) por una exclusión per-path **documentada** en cada `README.md`; lo que NO
se excluye es el **test de cada SDK**, que corre en CI (imports, paridad
modelo⇄schema, header check con transport mockeado, errores tipados).

Sobre los generadores nombrados por la hoja de ruta:

- **Python** usa **`datamodel-code-generator`** en lugar de `openapi-python-client`:
  su salida es **Pydantic v2** (la librería de modelado del proyecto) en vez de un
  cliente `attrs` que arrastra deps extra y choca con `ruff-format`/`mypy strict`.
- **TypeScript** usa **`openapi-typescript-codegen`** (el generador nombrado) para
  los **tipos de modelo**; el **cliente se escribe a mano** porque el generador no
  respeta el esquema `apiKey`/`X-API-Token` (solo emite `Authorization: Bearer`).

## Alternativas consideradas

- **Reusar la sesión/JWT de usuario para la API pública.** Inadecuado para
  integraciones máquina-a-máquina (tokens de larga vida, sin scope acotado, sin
  rate limit propio, atados al ciclo de vida de un usuario). Descartado a favor de
  un `ApiToken` por tenant.
- **Token en query param.** Se filtra en logs de acceso, history del navegador y
  cabecera Referer. Descartado: el token va siempre en la cabecera.
- **Versionado por cabecera (`Accept: application/vnd.api+json; version=1`).** Menos
  explícito, no aparece en logs/URLs, fácil de olvidar y peor de cachear.
  Descartado a favor del path; la cabecera `X-API-Version` queda solo como
  pin/observe opcional.
- **Secreto del webhook en la URL** (`/webhooks/incoming/{origin}/{secret}`, como
  insinuaba el borrador del plan). Una URL pública se filtra trivialmente y rotar el
  secreto cambiaría la URL registrada en el proveedor. Descartado: la URL lleva el
  `config_id` y el secreto solo vive cifrado.
- **Confiar en un payload de webhook sin firma (allowlist de IPs sola).** Las IPs de
  origen rotan y se falsean; no prueban integridad del body. Descartado: la HMAC es
  obligatoria.
- **SDK 100% generado (cliente incluido).** El cliente generado de cada herramienta
  no respeta bien el esquema `apiKey` y produce código que pelea con los linters del
  repo en cada regeneración. Descartado a favor de "modelos generados + cliente fino
  a mano".
- **SDK escrito a mano completo (sin generar modelos).** Se desincronizaría del
  contrato a cada cambio. Descartado: los modelos se generan desde el spec.

## Consecuencias

- Una herramienta externa autentica con un token por tenant que **no puede salirse
  de su tenant** (lo garantiza RLS, no el endpoint), con scope, vigencia, rate limit
  e IP allowlist propios, y revocación inmediata.
- La API es evolucionable: un futuro `/api/v2` se sirve desde su propio path y se
  añade a `SUPPORTED_VERSIONS`; los consumidores v1 no se rompen.
- El receptor de webhooks es fail-closed y resistente a redelivery/replay sin crear
  tareas duplicadas; un evento de un proyecto nunca actúa sobre otro tenant.
- Los SDKs se regeneran con un comando desde el spec in-process y se mantienen
  verdes en CI por su test sin contaminar el lint gate con código generado.
- **Pendiente (no decidido aquí):** los checks `curl` del OpenAPI/Swagger y los
  tests humanos `human_13_*` requieren un **stack vivo + un proveedor externo real**;
  los specs Playwright de la UI de webhooks están escritos-no-ejecutados. Ver el
  changelog del Plan 13, sección Pendiente.
