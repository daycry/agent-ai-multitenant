---
title: Marketplace de skills y tools — Referencia de endpoints y seguridad
audience: backend-dev, architect, security
phase: 09-marketplace
updated: 2026-06-01
---

# Marketplace de skills y tools — Referencia

Esta página documenta el marketplace del Plan 09: el catálogo de skills,
tools y MCP servers instalables, los niveles de confianza, el pipeline de
instalación gated, el formato SKILL.md / manifest de tool, y el modelo de
catálogo híbrido global/privado + compartir cross-tenant. Para la matriz de
roles general ver [`rbac.md`](./rbac.md); para el ADR de fondo ver
[ADR 0032](../05-architecture-decisions/0032-marketplace-confianza-catalogo-hibrido-instalacion-gated.md)
y [ADR 0001](../05-architecture-decisions/0001-postgres-rls-from-day-one.md).

## Modelo de datos (resumen)

| Tabla                       | Tenancy                                                                            |
| --------------------------- | ---------------------------------------------------------------------------------- |
| `marketplace_sources`       | Tenant-agnóstica (sin RLS). `owner_tenant_id` nullable marca una fuente privada    |
| `marketplace_listings`      | **Híbrida**: `tenant_id` NULL = global público; no-NULL = privado del tenant (RLS) |
| `marketplace_installations` | Tenant-owned (`tenant_id NOT NULL`, RLS)                                           |
| `marketplace_audit_entries` | Tenant-owned, **append-only a nivel de BD** (RLS `FOR SELECT` + `FOR INSERT`)      |
| `marketplace_shares`        | Grant cross-tenant; RLS dual-scope (owner gestiona, target solo lee)               |

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
  (opcionalmente en un proyecto). Un `community`/`experimental` nace
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
  install en su versión vieja. Un MAJOR exige `allow_major=true`.
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
pytest tests/integration/test_marketplace_endpoints.py tests/integration/test_consent.py
pytest tests/integration/test_revocation.py tests/integration/test_install_flow.py
pytest tests/integration/test_marketplace_versioning.py tests/integration/test_private_marketplace.py
pytest tests/integration/test_cross_tenant_sharing.py
```

Los e2e Playwright de las UIs del marketplace (`permission-consent.spec.ts`,
`playwright-tool-config.spec.ts`, `playwright-templates.spec.ts`,
`private-marketplace.spec.ts`, `marketplace-admin.spec.ts`) están escritos
pero **pendientes de verificación humana** (el runtime node-playwright de este
entorno no tiene navegador).
