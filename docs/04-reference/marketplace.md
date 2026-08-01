---
title: Marketplace de skills y tools — Referencia de endpoints y seguridad
audience: backend-dev, architect, security
phase: 09-marketplace
updated: 2026-08-01
---

# Marketplace de skills y tools — Referencia

Esta página documenta el marketplace: el catálogo de skills, tools y MCP
servers, los niveles de confianza, el pipeline de instalación gated, el
**despliegue en proyectos**, la revisión de lo publicado, las versiones, el
formato SKILL.md / manifest de tool y el modelo de catálogo híbrido
global/privado + compartir cross-tenant. Para la matriz de roles general ver
[`rbac.md`](./rbac.md); para los ADR de fondo,
[ADR 0032](../05-architecture-decisions/0032-marketplace-confianza-catalogo-hibrido-instalacion-gated.md)
(confianza),
[ADR 0100](../05-architecture-decisions/0100-materializacion-marketplace.md)
(materialización),
[ADR 0142](../05-architecture-decisions/0142-marketplace-despliegue-tres-capas.md)
(despliegue y tres capas) y
[ADR 0001](../05-architecture-decisions/0001-postgres-rls-from-day-one.md) (RLS).

> **Qué cambió con el ADR 0142 (v2).** Antes, instalar era «comprar sin
> recibir»: la instalación no escribía ni una fila `agent_tools`, no
> configuraba ningún servidor MCP en ningún proyecto, y la config guiada se
> pedía **al instalar** y se guardaba a nivel de tenant — con lo que dos
> proyectos no podían apuntar a URLs distintas. El modelo v2 parte la
> configuración en **tres capas** (§Las tres capas) e introduce el
> **despliegue** como entidad: instalar añade la capacidad al fondo del tenant,
> desplegar la entrega a un proyecto concreto.

## Modelo de datos (resumen)

| Tabla                          | Tenancy                                                                            |
| ------------------------------ | ---------------------------------------------------------------------------------- |
| `marketplace_sources`          | Tenant-agnóstica (sin RLS). `owner_tenant_id` nullable marca una fuente privada    |
| `marketplace_listings`         | **Híbrida**: `tenant_id` NULL = global público; no-NULL = privado del tenant (RLS) |
| `marketplace_listing_versions` | **Híbrida, espejo del listing** (mismas tres policies): una fila por versión       |
| `marketplace_installations`    | Tenant-owned (`tenant_id NOT NULL`, RLS)                                           |
| `marketplace_deployments`      | Tenant-owned, RLS **ENABLE + FORCE** con policy `tenant_isolation`                 |
| `marketplace_audit_entries`    | Tenant-owned, **append-only a nivel de BD** (RLS `FOR SELECT` + `FOR INSERT`)      |
| `marketplace_shares`           | Grant cross-tenant; RLS dual-scope (owner gestiona, target solo lee)               |

Las tres columnas que sostienen la trazabilidad de v2 (migraciones `0128`,
`0129` y `0130`):

- `marketplace_installations.pinned_version_id` — la fila de versión que este
  tenant **consintió**. El delta de permisos de una actualización se calcula
  contra ella. Nullable a conciencia: `deploy.ensure_listing_version`
  la crea y la pina en el primer despliegue (un `NOT NULL` cuyo escritor llega
  dos fases más tarde convierte cada instalación nueva en un 500).
- `marketplace_deployments.created_refs` (JSONB) — **exactamente** las filas que
  este despliegue creó. Retirar deshace eso y nada más: una tool que el operador
  asignó a mano al mismo agente sobrevive a la retirada.
- `marketplace_deployments.disabled_reason` — por qué un refresco de versión
  dejó el despliegue `disabled` en vez de aplicarlo a medias.

Un despliegue vive en tres estados: `active`, `disabled` y `retired`. La fila
**nunca se borra** (es el rastro de auditoría); retirar la marca `retired` y
sella `retired_at` / `retired_by`. Un índice UNIQUE parcial
`(installation_id, project_id) WHERE status = 'active'` es lo que hace el
re-despliegue idempotente en la BD y no solo en el código.

## Las tres capas de configuración (ADR 0142 D8)

Quién guarda qué, y cuándo se pregunta. Es la distinción que hace posible que
el proyecto A pruebe `app-a.example` y el B `app-b.example` con la MISMA
capacidad instalada una sola vez:

| Capa            | Qué guarda                                                                          | Cuándo se pide                |
| --------------- | ----------------------------------------------------------------------------------- | ----------------------------- |
| **Listing**     | qué ES la cosa: manifest, permisos declarados, `config_schema`, defaults, `targets` | al publicar                   |
| **Instalación** | el consentimiento de permisos del tenant — **nada más**                             | al instalar                   |
| **Despliegue**  | los VALORES por proyecto (`config` JSONB), validados contra el `config_schema`      | al desplegar en cada proyecto |

El manifest ganó dos campos **opcionales** (`marketplace/_format_common.py`):

- `targets: [rol, …]` — los roles de agente que el manifest **sugiere**; quien
  despliega confirma o ajusta (D5). Se validan contra el vocabulario cerrado
  `AgentRole`: un `backend-dev` mal escrito no casaría con ningún agente y el
  despliegue «funcionaría» sin entregar nada.
- `config_schema` — el descriptor del formulario guiado.

Un manifest **sin** los dos campos sigue siendo válido (hay catálogo
publicado): sin `targets` no se pre-marca nada, sin `config_schema` el
despliegue no muestra formulario.

### El dialecto de `config_schema` y su válvula tipada

`marketplace/config_schema.py::validate_deployment_config` valida los valores
contra el esquema: tipos (`string` / `integer` / `number` / `boolean` / `array`
/ `object`, con `bool` NO colando como entero), requeridos, `enum`,
`items.enum`, `minItems`, `minimum` / `maximum`, y **campos desconocidos
rechazados, no ignorados** (un `base_ur1` ignorado en silencio da un despliegue
que apunta a otro sitio). Devuelve una **lista** de errores, no una excepción,
para que el formulario los pinte todos a la vez.

Un campo `secret: true` **solo admite un puntero a Vault** (prefijo `vault:`, el
mismo contrato que `MCPServerConfigModel.auth_ref`), y su mensaje de error
**nunca ecoa el valor**: un error de validación que imprime la contraseña la
copia al log.

Para las reglas que el dialecto no sabe expresar, el esquema **nombra** su
validador tipado con `x-typed-validator` y `validate_deployment_config` lo
invoca tras validar la estructura. Es **fail-closed**: un validador declarado y
no registrado es un error, nunca un «pues no valido». La tool Playwright es su
primer usuario (§Playwright).

## Despliegue: instalar deja de ser comprar sin recibir

| Endpoint                                      | Método | Rol mínimo     |
| --------------------------------------------- | ------ | -------------- |
| `/marketplace/installations/{id}/deployments` | POST   | `tenant_admin` |
| `/marketplace/installations/{id}/deployments` | GET    | `tenant_user`  |
| `/marketplace/deployments/{id}/retire`        | POST   | `tenant_admin` |
| `/projects/{id}/marketplace/available`        | GET    | `tenant_user`  |

Viven en `routers/marketplace_deployments.py` (fichero propio: `marketplace.py`
pasa de 1.700 líneas). El servicio es `marketplace/deploy.py`.

**Qué materializa, por tipo de listing:**

- `mcp_server` → una entrada en `projects.mcp_servers` **más** la política
  rol→tool en `projects.mcp_tool_roles`. **Sin política paralela**: el
  `role_map` del despliegue rellena la política del ADR 0128 que ya existía, no
  un mecanismo competidor. Si el manifest declara OAuth, la entrada nace
  pendiente de conexión y el flujo «Conectar» del ADR 0127 la completa.
- `tool` / `skill` → filas `agent_tools` / `agent_skills` para los agentes del
  equipo del proyecto cuyo rol esté en el `role_map`, **reutilizando** la fila
  `Tool`/`Skill` que la materialización del ADR 0100 creó al instalar. Si esa
  fila no existe (tipo diferido por el sandbox del ADR 0081), no se escribe nada
  y el despliegue **lo dice en un `warning`** en vez de fingir que entregó algo.

**Garantías que llevan test:** aislamiento cross-tenant (una instalación del
tenant A sobre un proyecto de B es un 404, y sus despliegues son invisibles);
idempotencia (re-desplegar sobre el mismo par devuelve 201 con
`already_deployed: true` y no duplica); retirada exacta (lo asignado a mano
sobrevive); y config inválida → **nada escrito**, la transacción entera fuera.

### Las tres puertas de UI (D4)

Las tres escriben la MISMA entidad, que es lo que impide que diverjan:

| Puerta                  | Dónde                                                                |
| ----------------------- | -------------------------------------------------------------------- |
| Ficha de la instalación | `app/admin/marketplace/installations/[id]/deployments-section.tsx`   |
| Wizard de proyecto      | `app/admin/projects/new/capabilities-step.tsx`                       |
| Pestañas del proyecto   | `app/admin/projects/[id]/mcp-servers` y `.../agent-tools-diagnostic` |

El formulario guiado es uno solo —
`components/marketplace/deployment-config-form.tsx` — y lo abren las tres.
Deriva los campos del `config_schema`, así que cualquier listing que declare uno
tiene formulario sin escribir una línea.

## Niveles de confianza

El nivel de confianza gobierna **los guardrails aplicados, no la
disponibilidad** (ADR 0032 §1): todo listing se puede navegar e instalar; el
nivel solo decide cuántas puertas impone el install. Resuelto por
`marketplace/trust.py` a una `TrustPolicy` por nivel:

| Nivel          | Firma | Consent. por permiso | Análisis estático | Sandbox | Severidad máx. tolerada          |
| -------------- | ----- | -------------------- | ----------------- | ------- | -------------------------------- |
| `verified`     | sí    | no                   | sí                | no      | MEDIUM                           |
| `community`    | no    | **sí**               | sí                | sí      | LOW                              |
| `experimental` | no    | **sí**               | sí                | sí      | NONE (cualquier finding bloquea) |

Solo `verified` va firmado por el equipo de plataforma. Un listing privado de
un tenant es siempre `community` (derivado en servidor, nunca del wire).

## Seed del catálogo oficial (Plan 09.1)

El catálogo **de arranque** lo siembra la plataforma con el loader
`seed_marketplace_listings` (`marketplace/seed.py`), cableado en el runner de
seeds (`seeds/__main__.py`). Estos listings son **`verified` + globales**
(`tenant_id NULL`) bajo la fuente `official-catalog`: la tool **Playwright**
(vía `seed_playwright_listing`) + las skills de convenciones de stack derivadas
de los docs de la plataforma (FastAPI, React/Next.js, PHP/Symfony, PostgreSQL,
diseño de APIs REST). El seed es **idempotente** (upsert por
`(fuente, tenant_id=NULL, nombre, versión)` — re-seed no duplica) y corre sobre
la sesión publicadora **BYPASSRLS** (las filas globales no las puede escribir
una sesión de tenant). Sin migración: un listing es una fila + `manifest`
JSONB, los SKILL.md son datos del seed. La guía de operador
[`../03-guides/publicar-en-marketplace.md`](../03-guides/publicar-en-marketplace.md)
cubre el flujo de publicación privada y la diferencia oficial (verified/global)
vs. tenant (community/privado).

## Garantías de seguridad transversales

- **RLS por tenant.** Un listing privado (`tenant_id` no-NULL) está aislado;
  un tenant NUNCA ve los privados de otro. Una `installation` / `audit` /
  `share` es tenant-owned: tenant A jamás lista, instala sobre, ni revoca las
  filas de B (un id ajeno es un 404 limpio). Las filas globales
  (`tenant_id IS NULL`) son visibles por la política `FOR SELECT`
  `marketplace_listings_global_read` y solo escribibles por roles `BYPASSRLS`.
- **Compartir = grant explícito + audit, nunca un bypass implícito.** Un
  recurso privado se comparte con otro tenant creando una fila
  `marketplace_shares`; la política RLS aditiva
  `marketplace_listings_shared_read` expone el listing al target SOLO si
  existe un share vivo. Revocar quita la visibilidad de inmediato; el target
  no tiene ruta de escritura; el System Admin ve todos los shares.
- **Verificación de firma.** Un listing `verified` trae una firma desprendida
  Ed25519 que el install verifica con `cryptography` sobre los bytes exactos
  del manifest parseado, contra `MARKETPLACE_SIGNING_PUBLIC_KEY`. Un artefacto
  manipulado o sin firmar se RECHAZA. La firma **nunca** se devuelve por la
  API.
- **Análisis estático previo.** Bandit (primario) + semgrep (opcional/lazy)
  corren como **subproceso sobre una copia temporal** del código — nunca se
  importa ni ejecuta el código analizado en la api-server. El gate bloquea
  sobre `max_allowed_severity`.
- **Sandbox.** El smoke test del listing corre en un **contenedor efímero
  endurecido** (cap-drop ALL, no-new-privileges, root read-only, límites
  mem/pids/cpu, política de red honrada, **socket Docker NUNCA montado**), no
  en el proceso de la api-server. Desde prod-12 `task_prod12_net_01` el bridge
  del probe es **siempre `internal`** — `network_policy='open'` ya NO entrega
  internet crudo: significa egress **proxificado** por el `registry-proxy`
  allowlistado (registries públicos de paquetes y git), conectado al bridge e
  inyectado como `HTTP(S)_PROXY`; sin proxy configurado el probe queda
  offline. Cada uso de `open` queda en el log estructurado y en el audit row
  (`SandboxResult.network_policy` / `proxied_egress`). Ver la tabla de
  `network_policy` en [`tools.md`](./tools.md).
- **Consentimiento por permiso.** Para `community`/`experimental`, el project
  owner aprueba CADA permiso (`allowed_domains` / `allowed_paths` /
  `network_policy`) uno a uno; el install nace `disabled` y solo se habilita
  cuando todos están concedidos.
- **Auditoría obligatoria append-only.** Cada install / consent / uninstall /
  revoke / share escribe una fila `marketplace_audit_entries` en la MISMA
  transacción; la tabla es inmutable a nivel de BD (sin UPDATE/DELETE para el
  rol de la app, migración 0043).

## Catálogo (browse + detalle)

| Endpoint                     | Método | Rol mínimo      |
| ---------------------------- | ------ | --------------- |
| `/marketplace/listings`      | GET    | `tenant_member` |
| `/marketplace/listings/{id}` | GET    | `tenant_member` |

Browse devuelve el catálogo global público + los listings privados propios
del caller + los listings compartidos con él vía un grant vivo (RLS); NUNCA
los privados de otro tenant. Filtros opcionales `kind`
(`skill`/`tool`/`mcp_server`) y `trust_level` (422 si el valor es
desconocido); paginación `limit`/`offset` con orden determinista.

**Encima de la RLS**, desde el ADR 0142 D6 se aplica el filtro de revisión
(`marketplace/review.py::catalog_visibility_clause`): solo se ve lo
`published`, más lo propio del tenant en cualquier estado (el autor necesita
leer el motivo de su rechazo). Un `pending_review` ajeno es un **404**, no un
403 — un 403 confirmaría que existe.

## Publicar pasa por revisión (ADR 0142 D6)

La máquina de estados del listing vive en `marketplace/review.py` y es la
**única** puerta por la que `marketplace_listings.review_status` cambia:

```
draft → pending_review → published | rejected
published → (promoción de trust_level por el System Admin)
```

Cada transición comprueba la arista contra un vocabulario cerrado, **exige
actor** (obligatorio en la firma, no un `None` por defecto) y escribe
auditoría por partida doble: una fila `marketplace_audit_entries` para que el
tenant autor vea el veredicto en su propio rastro, y una fila `audit_log` de
plataforma —cuyo `tenant_id` sí es nullable— para que revisar un listing GLOBAL
también deje huella.

| Endpoint                                    | Método | Rol mínimo     |
| ------------------------------------------- | ------ | -------------- |
| `/admin/marketplace/review-queue`           | GET    | `system_admin` |
| `/admin/marketplace/listings/{id}/versions` | GET    | `system_admin` |
| `/admin/marketplace/listings/{id}/approve`  | POST   | `system_admin` |
| `/admin/marketplace/listings/{id}/reject`   | POST   | `system_admin` |
| `/admin/marketplace/listings/{id}/promote`  | POST   | `system_admin` |

Van sobre la sesión BYPASSRLS porque revisar es, por definición, mirar lo de
otro tenant. Un **rechazo sin motivo escrito es un 422**: un rechazo mudo es
indistinguible de un borrado y no se puede recurrir. `promote` exige que el
listing esté ya `published` y admite **bajar** además de subir (degradar un
`verified` estropeado sin despublicarlo, porque despublicar rompería las
instalaciones vivas). La cola del admin es
`app/admin/marketplace/review/page.tsx`.

## Versiones y actualización explícita (ADR 0142 D7)

Cada publicación deja una fila en `marketplace_listing_versions`
(`marketplace/listing_versions.py::snapshot_version`) con el manifest, los
permisos y el `config_schema` **tal como se publicaron**. La instalación pina la
versión que consintió; **nada se actualiza solo**.

`POST /marketplace/installations/{id}/update` (`tenant_admin`) hace, en este
orden:

1. resuelve el destino (semver, `marketplace/versioning.py`) — un salto MAJOR
   exige `allow_major` explícito;
2. calcula el **delta de permisos** contra la versión pinada
   (`listing_versions.permission_diff` → `added` / `removed` / `changed`) y
   aplica el re-consentimiento **solo del delta**
   (`marketplace/update_consent.py`): lo ya concedido no se vuelve a preguntar;
3. re-ejecuta las puertas del install contra el artefacto nuevo (un fallo
   aborta con 422 y la instalación se queda en su versión vieja);
4. re-pina la versión y **refresca cada despliegue**
   (`marketplace/deployment_refresh.py`): campos nuevos toman su default, los
   retirados se limpian y, si el esquema nuevo exige un campo que no existe ni
   tiene default, ese despliegue queda `disabled` **con el motivo escrito** en
   `disabled_reason` — nunca aplicado a medias, y sin arrastrar a los demás;
5. escribe un audit `refresh` con el informe.

**Rollback = el mismo endpoint** apuntando a una versión anterior del histórico:
mismo mecanismo de refresco, misma auditoría (el audit marca `rollback: true`).

## Instalación, consentimiento, updates y revocación

| Endpoint                                       | Método | Rol mínimo      |
| ---------------------------------------------- | ------ | --------------- |
| `/marketplace/installations`                   | GET    | `tenant_member` |
| `/marketplace/installations`                   | POST   | `tenant_admin`  |
| `/marketplace/installations/{id}/permissions`  | GET    | `tenant_member` |
| `/marketplace/installations/{id}/consent`      | POST   | `tenant_admin`  |
| `/marketplace/installations/{id}/update-check` | GET    | `tenant_member` |
| `/marketplace/installations/{id}/update`       | POST   | `tenant_admin`  |
| `/marketplace/installations/{id}/revoke`       | POST   | `tenant_admin`  |
| `/marketplace/installations/{id}`              | DELETE | `tenant_admin`  |

- **`POST /installations`** instala un listing en el tenant del caller
  (opcionalmente en un proyecto). **No captura configuración alguna** (ADR 0142
  D2/D8): el cuerpo admite `listing_id`, `project_id` y `granted_permissions`, y
  la tabla `marketplace_installations` no tiene columna donde guardarla. Los
  valores se piden al desplegar. Un `community`/`experimental` nace
  `disabled` (sin permisos concedidos); un `verified` instala `enabled`. El
  guard de duplicado-vivo (índice parcial único
  `uq_marketplace_installations_live`) devuelve 409. Desde prod-12
  `task_prod12_mkt_01` la instalación fresca corre el **gate de análisis
  estático** (el MISMO pipeline bandit/semgrep del update): un hallazgo por
  encima de la política de confianza aborta con 422 + audit row
  (`static_analysis_blocked`) sin persistir nada; el informe viaja en el
  audit del install (`detail.gates.static_analysis`). Un listing **sin
  artefacto en disco** (`MARKETPLACE_ARTIFACT_ROOT`) instala registrando un
  skip honesto (`skipped_reason=no_artifact`) — el hueco pre-registry que
  ADR 0081 documenta. Firma y sandbox en el install fresco siguen diferidos
  a la Fase B/C (ADR 0081).
- **`GET .../permissions`** lista los permisos solicitados + su estado
  (GRANTED / DENIED / PENDING) para la UI de consentimiento.
- **`POST .../consent`** registra la decisión grant/deny por permiso. Cuando
  TODOS los requeridos están concedidos, el install pasa a `enabled` (audit
  `consent`); un deny lo deja `disabled` (audit `consent_denied`). Una
  decisión sobre un permiso que el listing no pidió es un 422.
- **`GET .../update-check`** reporta si el install está desactualizado y qué
  versión puede tomar (semver). Un bump MAJOR solo se propone con
  `allow_major=true`.
- **`POST .../update`** actualiza a una versión compatible más nueva
  re-ejecutando TODAS las puertas del install (firma / análisis / sandbox)
  contra el artefacto nuevo; un fallo de puerta aborta (422) dejando el
  install en su versión vieja. Un MAJOR exige `allow_major=true`. Desde el ADR
  0142 también re-consiente el **delta** de permisos y refresca los despliegues
  — detalle en §Versiones y actualización explícita.
- **`DELETE .../{id}`** (uninstall, intent operador) y **`POST .../revoke`**
  (revocación de seguridad) flipean el install a `revoked`, lo deshabilitan,
  soft-deletean (liberan el slot live) y SIEMPRE escriben un audit. Difieren
  solo en la `action` auditada (`uninstall` vs. `revoke`).

## Marketplace privado del tenant

Un tenant publica sus propias skills/tools internas como listings PRIVADOS.

| Endpoint                             | Método      | Rol mínimo     |
| ------------------------------------ | ----------- | -------------- |
| `/marketplace/private/listings`      | POST        | `tenant_admin` |
| `/marketplace/private/listings/{id}` | PUT, DELETE | `tenant_admin` |

El manifest del cuerpo se VALIDA con los parsers de formato (SKILL.md para
`skill`, manifest YAML para `tool`/`mcp_server`); un manifest malo es un 422 y
NO se crea fila. `tenant_id` (= caller), la fuente privada y el `trust_level`
(`community`) son SIEMPRE derivados en servidor — un listing privado no puede
falsificarse como global/verified (la `WITH CHECK` de RLS rechaza un
`tenant_id` ajeno). `name`/`version` salen del manifest; re-publicar la misma
`(kind, name, version)` es un 409 (bump la versión o usa update). DELETE es un
soft-delete (la auditoría y los FKs sobreviven).

## Compartir entre tenants (opt-in + audit del System Admin)

| Endpoint                    | Método    | Rol mínimo     |
| --------------------------- | --------- | -------------- |
| `/marketplace/shares`       | POST, GET | `tenant_admin` |
| `/marketplace/shares/{id}`  | DELETE    | `tenant_admin` |
| `/admin/marketplace/shares` | GET       | `system_admin` |

- **`POST /shares`** comparte uno de los listings PRIVADOS del caller con un
  único `target_tenant_id`. Compartir consigo mismo es 422; un listing global
  o ajeno es 404 (RLS); un duplicado vivo o un tenant target inexistente es 409. La fila se sella con `owner_tenant_id` = caller (la `WITH CHECK` de RLS
  rechaza un owner forjado) y escribe un audit `share`.
- **`GET /shares`** lista los grants que el caller OWNS (RLS owner-scope);
  `include_revoked` off por defecto.
- **`DELETE /shares/{id}`** revoca un grant (opt-out): quita la visibilidad al
  target de inmediato y libera el slot live; un share ajeno o ya revocado es 404. Escribe un audit `share` (`cross_tenant_share_revoke`).
- **`GET /admin/marketplace/shares`** (System Admin, sesión BYPASSRLS)
  enumera TODOS los shares de TODOS los tenants para audit; incluye revocados
  por defecto.

## Formato SKILL.md (skill)

Inspirado en Anthropic Skills: un Markdown cuyo head es un **frontmatter
YAML** (delimitado por `---`) con la metadata, seguido de un cuerpo Markdown
de documentación. Parser/validador en `marketplace/skill_format.py`.

```markdown
---
name: web-researcher
description: Researches a topic across the web and cites sources.
version: 1.2.0
dependencies:
  - httpx>=0.27
permissions:
  allowed_domains: [api.search.example, docs.python.org]
  allowed_paths: [/workspace/output]
  network_policy: restricted
examples:
  - title: Quick lookup
    prompt: "Find the latest pgvector release notes"
---

# Web Researcher

Descripción Markdown de lo que hace la skill...
```

Campos requeridos: `name`, `description`, `version` (semver). Un frontmatter
ausente/malformado, un semver inválido o una clave de permiso fuera del
vocabulario son un `SkillFormatError` tipado (422 al publicar). Los permisos
se normalizan al descriptor canónico `{"type": ..., "value": ...}` que
consumen el install y el consentimiento.

## Formato manifest de tool

Una tool es una función ejecutable: su manifest es un **YAML plano** (sin
cuerpo Markdown) con un schema de entrada/salida y un puntero a la
implementación. Parser/validador en `marketplace/tool_format.py`.

```yaml
name: web-fetch
version: 2.0.1
description: Fetch a URL and return its body.
kind: tool
entrypoint: web_fetch.main:run
implementation:
  runtime: python
  module: web_fetch.main
  reference: git+https://example.test/tools/web-fetch@v2.0.1
dependencies:
  - httpx>=0.27
permissions:
  allowed_domains: [api.example.test]
  network_policy: restricted
input_schema:
  type: object
  properties:
    url: { type: string }
  required: [url]
output_schema:
  type: object
  properties:
    status: { type: integer }
    body: { type: string }
```

Campos requeridos: `name`, `version` (semver), `description`, `entrypoint`,
`implementation`. `kind` reusa `MarketplaceListingKind`. El vocabulario de
permisos y la validación semver están COMPARTIDOS con SKILL.md vía
`marketplace/_format_common.py` (no se duplican). Un documento malformado es
un `ToolFormatError` tipado (422).

## Playwright como caso destacado

La tool **Playwright** es un listing GLOBAL verificado
(`marketplace/playwright.py`) en el formato estándar de tool, con una **config
guiada** tipada (`PlaywrightToolConfig`: browsers chromium/firefox/webkit,
headless, screenshots, traces, base_url, timeout_ms) cuyo `config_schema()`
la UI renderiza. El agente plantilla GLOBAL **QA E2E Automator**
(`seeds/qa_e2e_automator.py`) la referencia por la identidad del listing
(`name=playwright`+`version`+`kind=tool`). El registro
`marketplace/e2e_templates.py` aporta plantillas `.spec.ts` parametrizadas
(login, signup, checkout, search, form-submit).

**Su config es del despliegue, no de la instalación** (`task_mkt2_13`). Era el
anti-patrón que motivó el ADR 0142: la `base_url` del sitio bajo prueba es del
proyecto, y al instalar los proyectos que la usarán aún no existen. La pantalla
`/admin/marketplace/listings/[id]/playwright-config` **ya no existe** y el
catálogo no ofrece «Configurar»; el formulario que se rinde hoy es el genérico
del despliegue, una vez por proyecto.

`PlaywrightToolConfig` no se retiró con el formulario: pasó de guardar la config
a **validarla**. `config_schema()` declara `x-typed-validator: playwright` y
`validate_deployment_config` invoca `validate_playwright_config` en cada
despliegue, así que lo que el dialecto genérico no sabe expresar —una `base_url`
de solo espacios es un `type: string` perfectamente válido y una URL inútil—
se sigue rechazando.

> **Limitación honesta, hoy.** El listing de Playwright declara
> `implementation_type: docker_command`, que la materialización del ADR 0100
> deja **diferida** hasta el sandbox out-of-process (ADR 0081 Fase B/C). Así
> que instalarlo NO crea fila en el catálogo `tools` del tenant y desplegarlo
> registra config + auditoría pero **no asigna la tool a ningún agente**: el
> despliegue devuelve un `warning` que lo dice. Hasta `task_mkt2_13` el manifest
> no declaraba `implementation_type` en absoluto y el listing destacado
> **no se podía instalar** (422 en `POST /marketplace/installations`).

## Variables de configuración

| Variable                         | Para qué                                                                                                                                               |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `MARKETPLACE_SIGNING_PUBLIC_KEY` | Clave pública Ed25519 del equipo de plataforma para verificar la firma de un listing `verified` (la verificación falla cerrada si no está configurada) |

Dependencias: `bandit` (dev-dep) es el scanner primario; `semgrep` y `docker`
son **opcionales/lazy** (degradan limpio si faltan, precedente xmlsec del
ADR 0031). `cryptography` (firma Ed25519) y `packaging` (semver) ya eran
dependencias del proyecto.

## Tests que pinean estos endpoints

```bash
# Plan 09 — catálogo, consentimiento, instalación, privado, compartir
pytest tests/integration/test_marketplace_endpoints.py tests/integration/test_consent.py
pytest tests/integration/test_revocation.py tests/integration/test_install_flow.py
pytest tests/integration/test_marketplace_versioning.py tests/integration/test_private_marketplace.py
pytest tests/integration/test_cross_tenant_sharing.py

# ADR 0142 — despliegue, revisión, versiones, actualización
pytest tests/unit/test_marketplace_config_schema.py -q
pytest tests/integration/test_marketplace_v2_chain.py -q -p no:randomly       # la cadena entera
pytest tests/integration/test_marketplace_deploy_service.py -q -p no:randomly
pytest tests/integration/test_marketplace_deployments_api.py -q -p no:randomly
pytest tests/integration/test_marketplace_review_flow.py -q -p no:randomly
pytest tests/integration/test_marketplace_update_flow.py -q -p no:randomly
pytest tests/integration/test_playwright_deploy_config.py -q -p no:randomly   # dos base_url
```

> Un solo pytest de integración a la vez, o dale a cada proceso su
> `TEST_PG_DB_NAME` y su `TEST_REDIS_URL`
> ([gotcha](../03-guides/gotchas/integration-tests-share-one-database.md)).

Los e2e Playwright de las UIs del marketplace (`permission-consent.spec.ts`,
`playwright-templates.spec.ts`, `private-marketplace.spec.ts`,
`marketplace-admin.spec.ts`, `marketplace-deploy.spec.ts`) están escritos
pero **pendientes de verificación humana** (el runtime node-playwright de este
entorno no tiene navegador). El de la pantalla de config guiada
(`playwright-tool-config.spec.ts`) se retiró con la pantalla.
