---
plan_id: 09-marketplace
title: Marketplace de Skills y Tools
completed_at: null
docs_language: es
---

# Plan 09 — Marketplace de Skills y Tools

## Resumen

Hace **descubribles, instalables y compartibles** las skills, tools y MCP
servers que tras la Fase 5 solo se añadían a mano. La unidad es el
**listing** del marketplace, sobre un modelo **híbrido** de catálogo: una
fila de `marketplace_listings` con `tenant_id` **NULL** es un listing
**global** del catálogo público (visible a todo tenant); con `tenant_id`
**no-NULL** es un listing **privado** del tenant (RLS aísla un tenant de
otro). Cada instalación pasa por un **pipeline de puertas** que el **nivel
de confianza** del listing determina: verificación de firma, análisis
estático previo, sandbox de prueba y **consentimiento granular por permiso**.

En esta fase **las fronteras de tenant son la feature**. Un listing privado
está aislado por RLS; un tenant NUNCA ve los privados de otro. Compartir un
recurso entre tenants es **opt-in y auditado por el System Admin** mediante
un **grant explícito** (`marketplace_shares`): el tenant destino ve/instala
el listing compartido SOLO a través del grant vivo (política RLS aditiva
`marketplace_listings_shared_read`), nunca por un bypass implícito de RLS;
revocar el grant le quita la visibilidad de inmediato y el System Admin ve
todos los shares. Toda auditoría es **append-only** a nivel de base de datos
(migración 0043) y las firmas/secretos nunca se devuelven por la API.

Las 19 tareas se desarrollaron por TDD en cinco fases (A — modelo, B —
confianza y seguridad, C — formatos e instalación, D — Playwright, E —
privado/cross-tenant + docs), cada una con su regresión de aislamiento
cross-tenant.

## Cambios por tarea

### Fase A — Modelo de Marketplace

- ✅ **`task_09_01`** — **Modelos** `marketplace_sources` (registro
  tenant-agnóstico, `owner_tenant_id` nullable), `marketplace_listings`
  (híbrido, `tenant_id` nullable), `marketplace_installations` (tenant-owned)
  y `marketplace_audit_entries` (tenant-owned, append-only) en
  `db/marketplace.py`, con sus enums (`MarketplaceListingKind`,
  `MarketplaceTrustLevel`, `MarketplaceSourceType`, `InstallationStatus`,
  `MarketplaceAuditAction`).
- ✅ **`task_09_02`** — **Migración 0041 + RLS**. Cuatro tablas y las
  políticas: `marketplace_sources` sin RLS (la visibilidad de un catálogo
  privado se resuelve en la capa de servicio); `marketplace_listings`
  híbrido (política `FOR ALL` de aislamiento privado + política `FOR SELECT`
  `marketplace_listings_global_read` que expone las filas globales
  `tenant_id IS NULL`); `installations` y `audit_entries` tenant-owned. Las
  escrituras de filas globales quedan reservadas a roles `BYPASSRLS`
  (System Admin / publisher del catálogo).
- ✅ **`task_09_03`** — **Endpoints REST** (`routers/marketplace.py` +
  `schemas/marketplace.py`): browse (`GET /marketplace/listings` +
  `/listings/{id}`), install (`POST /marketplace/installations`), uninstall
  (`DELETE`) y list_installed (`GET /marketplace/installations`). Browse
  expone catálogo global + privados propios bajo RLS; install/uninstall son
  escrituras `tenant_admin`. Guard de duplicado-vivo (índice parcial único)
  como 409.

### Fase B — Niveles de Confianza y Seguridad

- ✅ **`task_09_04`** — **3 niveles de confianza** (`verified` / `community`
  / `experimental`) en `marketplace/trust.py`. Decisión vinculante: el nivel
  gobierna los **guardrails aplicados, NO la disponibilidad**. Cada nivel
  resuelve a una `TrustPolicy` frozen con 5 perillas (`signature_required`,
  `per_permission_consent_required`, `static_analysis_required`,
  `sandbox_required`, `max_allowed_severity`). `community`/`experimental`
  SIEMPRE exigen consentimiento por permiso; solo `verified` va firmado por
  el equipo de plataforma. Vocabulario de permisos canónico
  (`allowed_domains` / `allowed_paths` / `network_policy`) + `NetworkPolicy`
  (`none|restricted|open`) reusando el dialecto de red de los test-runtimes.
- ✅ **`task_09_05`** — **Análisis estático previo** (`marketplace/static_analysis.py`):
  **Bandit** (AST de Python, scanner primario, wheel limpio) + **semgrep**
  (patrones genéricos, opcional/lazy). Ambos corren como **subproceso sobre
  una copia temporal** — el código analizado NUNCA se importa ni ejecuta.
  Normalización de severidad con escalado por confianza (`eval` /
  `shell=True` / secreto hardcodeado bloquean de forma fiable). El gate
  bloquea cuando una finding supera `max_allowed_severity` de la política.
- ✅ **`task_09_06`** — **Sandbox de ejecución** (`marketplace/sandbox.py`):
  `SandboxSpec` + run/teardown reutilizando el patrón de aislamiento de la
  plataforma (cap-drop ALL, no-new-privileges, root read-only, límites
  mem/pids/cpu, política de red honrada, **socket Docker NUNCA montado**).
  `docker` se importa **lazy** (cliente inyectable; mockeado en tests). La
  ejecución real en contenedor queda pendiente de la imagen runtime.
- ✅ **`task_09_07`** — **Consentimiento granular**. Backend
  `GET .../permissions` + `POST .../consent` sobre `marketplace/consent.py`
  (lógica pura). `community`/`experimental` exigen consentimiento por
  permiso; un install consent-gated nace **DISABLED** y solo pasa a
  **ENABLED** cuando TODOS los permisos requeridos están concedidos; un deny
  lo deja disabled + audita `consent_denied`. Migración 0042 reversible
  (`denied_permissions` JSONB). UI
  `/admin/marketplace/installations/[id]/permissions` (RoleGuard
  `tenant_admin`).
- ✅ **`task_09_08`** — **Revocación + audit_log obligatorio**. `DELETE`
  (uninstall, intent operador) y `POST .../revoke` (revocación de seguridad)
  comparten el teardown: flip a `revoked`, soft-delete (libera el slot
  live), y SIEMPRE escriben un audit en la misma transacción. Migración 0043
  endurece `marketplace_audit_entries` a **append-only a nivel de BD**:
  reemplaza la política `FOR ALL` por `FOR SELECT` + `FOR INSERT` (sin
  UPDATE ni DELETE para el rol de la app, NOBYPASSRLS).

### Fase C — Formatos Estándar e Instalación

- ✅ **`task_09_09`** — **Formato SKILL.md** (`marketplace/skill_format.py`):
  frontmatter YAML (`name`/`description`/`version` semver + `dependencies` /
  `permissions` / `examples`) + cuerpo Markdown, inspirado en Anthropic
  Skills. Parser/validador tipado (`SkillManifest` + `parse_skill_md` ->
  `SkillFormatError`). Renderiza los permisos al MISMO descriptor
  `{"type","value"}` que consume install/consent.
- ✅ **`task_09_10`** — **Formato estándar de tool**
  (`marketplace/tool_format.py`): manifest YAML (`name`/`version`/
  `description`/`kind`/`entrypoint`/`implementation` + `dependencies` +
  `input_schema`/`output_schema` + `permissions`). Vocabulario de permisos y
  semver COMPARTIDOS con SKILL.md vía `marketplace/_format_common.py` (sin
  duplicar). Parser/validador tipado (`ToolManifest` + `parse_tool_manifest`
  -> `ToolFormatError`).
- ✅ **`task_09_11`** — **Proceso de instalación end-to-end**
  (`marketplace/install.py`, `InstallOrchestrator`): encadena las puertas
  que implica la `trust_policy` en orden fail-closed — (1) FETCH (tras
  `ArtifactFetcher` Protocol; `LocalArtifactFetcher` sin red), (2) PARSE
  SKILL.md / tool manifest, (3) VERIFY SIGNATURE Ed25519 (`cryptography`)
  cuando `signature_required` (artefacto manipulado/sin firmar RECHAZADO; la
  firma nunca se devuelve), (4) STATIC ANALYSIS (bloquea sobre
  `max_allowed_severity`), (5) SANDBOX smoke test cuando `sandbox_required`,
  (6) CONSENT (`community`/`experimental` nacen DISABLED), (7) PERSIST del
  install y su audit. Cada fallo de puerta aborta con `InstallError` tipado +
  audit COMMITeado, sin install habilitado.
- ✅ **`task_09_12`** — **Versionado semver y updates**
  (`marketplace/versioning.py`, sobre `packaging`): `parse_version` /
  `compare_versions` / `is_outdated` / `is_major_bump` / `select_update_target`.
  Compatibilidad: un update NUNCA salta un MAJOR sin `allow_major` explícito.
  `InstallOrchestrator.update()` re-ejecuta las MISMAS puertas del install
  contra el artefacto de la nueva versión y re-apunta `listing_id`+`version`
  con audit `action="update"`. Endpoints `GET .../update-check` y
  `POST .../update`.

### Fase D — Playwright como Caso Destacado

- ✅ **`task_09_13`** — **Tool Playwright destacada** (`marketplace/playwright.py`):
  listing **GLOBAL verificado** (`tenant_id` NULL) en el formato estándar de
  tool. `PlaywrightToolConfig` (config guiada tipada: browsers
  chromium/firefox/webkit, headless, screenshots, traces, base_url,
  timeout_ms) + `config_schema()` que la UI renderiza. Loader
  `seed_playwright_listing()` idempotente bajo la official source (requiere
  sesión publisher BYPASSRLS para escribir `tenant_id` NULL). UI
  `/admin/marketplace/listings/[id]/playwright-config`.
- ✅ **`task_09_14`** — **Agente plantilla 'QA E2E Automator'**
  (`seeds/qa_e2e_automator.py`): plantilla GLOBAL (`scope='global_builtin'`,
  `is_template=true`) reutilizando el MISMO modelo de agente de Plan 01/02/03,
  con prompts bilingües es/en y referencia a la tool Playwright por la
  identidad del listing (`name=playwright`+`version`+`kind=tool`) en
  `model_config.marketplace_tools`. Loader idempotente cableado en
  `seeds/__main__.py`; es el 12º global_builtin (fuera de `BUILTIN_AGENTS`
  para preservar el conteo de 11 de Plan 01).
- ✅ **`task_09_15`** — **Plantillas de tests E2E pre-cargadas**
  (`marketplace/e2e_templates.py`): registro curado y versionado de 5
  skeletons `.spec.ts` parametrizados (login, signup, checkout, search,
  form-submit). `E2ETestTemplate` valida semver + acuerdo body↔params;
  `instantiate(values)` substituye placeholders. Contenido de plataforma,
  puro, sin tabla nueva ni frontera cross-tenant.

### Fase E — Marketplace Privado y Cross-Tenant

- ✅ **`task_09_16`** — **Marketplace privado del tenant** (backend +
  frontend). `POST /marketplace/private/listings` + `PUT .../{id}` +
  `DELETE .../{id}` (RBAC `tenant_admin`, RLS-scoped); el manifest se VALIDA
  con los parsers de la Fase C vía `marketplace/private_listing.py`
  (`parse_private_listing`; manifest malo -> 422, sin fila). `tenant_id` (=
  caller), la fuente privada y el `trust_level` (`community`) son SIEMPRE
  derivados en servidor — un listing privado NO puede falsificarse como
  global/verified (RLS WITH CHECK rechaza un `tenant_id` ajeno). UI
  `/admin/marketplace/private`. Sin migración.
- ✅ **`task_09_17`** — **Compartir recursos entre tenants** (opt-in +
  audit del System Admin). Nueva tabla `marketplace_shares` (migración 0044
  reversible) con RLS dual-scope (`marketplace_shares_owner_manage` para el
  OWNER + `marketplace_shares_target_read` para el TARGET) y una política
  aditiva `FOR SELECT` `marketplace_listings_shared_read` en
  `marketplace_listings` que expone el listing al target SOLO si existe un
  share vivo. Endpoints: `POST /marketplace/shares`, `GET /marketplace/shares`,
  `DELETE /marketplace/shares/{id}` (owner `tenant_admin`) y
  `GET /admin/marketplace/shares` (System Admin BYPASSRLS, enumera TODOS los
  shares para audit). Cada share/revoke escribe un audit append-only
  `action=share` en la misma transacción. Default = nada compartido.
- ✅ **`task_09_18`** — **UI de gestión del marketplace por Tenant Admin**
  (frontend). Página `/admin/marketplace` con 3 pestañas: Catálogo (browse
  global + privados propios, enlaza a la config de Playwright), Instaladas
  (enlaza consent 09_07, revoca, desinstala) y Compartir (gestiona los shares
  cross-tenant del owner; el picker solo ofrece listings privados propios).
  Copy explícito de que compartir es opt-in + auditado por el System Admin y
  nunca un bypass implícito de RLS. Nuevo item Marketplace en el sidebar
  (admin-only). Sin migración.
- ✅ **`task_09_19`** — **Documentación del plan** (este changelog, la
  referencia de endpoints `docs/04-reference/marketplace.md` y la ADR 0032).

## Endpoints nuevos del marketplace

| Endpoint                                       | Método      | Auth            |
| ---------------------------------------------- | ----------- | --------------- |
| `/marketplace/listings`                        | GET         | `tenant_member` |
| `/marketplace/listings/{id}`                   | GET         | `tenant_member` |
| `/marketplace/private/listings`                | POST        | `tenant_admin`  |
| `/marketplace/private/listings/{id}`           | PUT, DELETE | `tenant_admin`  |
| `/marketplace/shares`                          | GET, POST   | `tenant_admin`  |
| `/marketplace/shares/{id}`                     | DELETE      | `tenant_admin`  |
| `/marketplace/installations`                   | GET, POST   | ver detalle\*   |
| `/marketplace/installations/{id}`              | DELETE      | `tenant_admin`  |
| `/marketplace/installations/{id}/permissions`  | GET         | `tenant_member` |
| `/marketplace/installations/{id}/consent`      | POST        | `tenant_admin`  |
| `/marketplace/installations/{id}/update-check` | GET         | `tenant_member` |
| `/marketplace/installations/{id}/update`       | POST        | `tenant_admin`  |
| `/marketplace/installations/{id}/revoke`       | POST        | `tenant_admin`  |
| `/admin/marketplace/shares`                    | GET         | `system_admin`  |

> \* `GET /marketplace/installations` es `tenant_member`;
> `POST /marketplace/installations` (install) es `tenant_admin`. Detalle
> completo (forma de request/response, RBAC, RLS y notas de seguridad) en
> [`docs/04-reference/marketplace.md`](../04-reference/marketplace.md).

## Migraciones (todas reversibles, single head)

| Revisión | Contenido                                                                                |
| -------- | ---------------------------------------------------------------------------------------- |
| **0041** | Cuatro tablas (`sources` / `listings` híbrido / `installations` / `audit_entries`) + RLS |
| **0042** | `marketplace_installations.denied_permissions` (JSONB) — consentimiento granular         |
| **0043** | `marketplace_audit_entries` append-only a nivel de BD (`FOR SELECT` + `FOR INSERT`)      |
| **0044** | Tabla `marketplace_shares` + RLS dual-scope + `marketplace_listings_shared_read` aditiva |

Single head `0044_marketplace_shares`. El objetivo de downgrade para probar
el rollback completo del marketplace es la revisión pre-marketplace
`0040_sso_email_domains`.

## Configuración / variables / dependencias nuevas

| Item                             | Tipo          | Para qué                                                                                                                                               |
| -------------------------------- | ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `bandit>=1.7,<2`                 | dev-dep       | Scanner primario del análisis estático previo (subproceso sobre copia temporal)                                                                        |
| `semgrep`                        | opcional/lazy | Segundo scanner; NO pineado en `pyproject` (su wheel choca con OTel/protobuf); CLI localizada lazy, degrada a "unavailable" si falta                   |
| `docker`                         | opcional/lazy | Cliente del sandbox; importado lazy (cliente inyectable, mockeado en tests). Ejecución real pendiente de la imagen runtime                             |
| `MARKETPLACE_SIGNING_PUBLIC_KEY` | env var       | Clave pública Ed25519 del equipo de plataforma para verificar la firma de un listing `verified` (la verificación falla cerrada si no está configurada) |

`cryptography` (verificación de firma Ed25519) y `packaging` (semver) ya
eran dependencias del proyecto.

## Decisiones

- **El nivel de confianza gobierna los guardrails, no la disponibilidad.**
  Todo listing — verified/community/experimental — se puede navegar e
  instalar; el nivel solo decide cuántas puertas impone el flujo. Una
  `TrustPolicy` por nivel, fuente única de verdad, sin literales dispersos.
  Registrado en **ADR 0032**.
- **Catálogo híbrido global/privado + compartir por grant explícito.**
  `marketplace_listings.tenant_id` NULL = global público; no-NULL = privado
  del tenant (RLS). Compartir cross-tenant es opt-in mediante una fila
  `marketplace_shares` y una política RLS aditiva — nunca un bypass
  implícito. El System Admin audita todos los shares. Registrado en
  **ADR 0032**.
- **Pipeline de instalación gated, fail-closed.** Firma -> análisis estático
  -> sandbox -> consentimiento por permiso, cada puerta según la
  `TrustPolicy`; cada fallo aborta con audit append-only. Registrado en
  **ADR 0032**.
- **Dependencias de seguridad opcionales/lazy** (semgrep, docker), mismo
  precedente que `xmlsec`/`python3-saml` del Plan 08 (ADR 0031): la
  superficie pura importa en cualquier nodo; el camino que necesita la
  dependencia degrada limpio cuando falta.

## Pendiente

- **e2e Playwright de las UIs del marketplace** — `permission-consent.spec.ts`,
  `playwright-tool-config.spec.ts`, `playwright-templates.spec.ts`,
  `private-marketplace.spec.ts`, `marketplace-admin.spec.ts` están
  **escritos pero PENDIENTES DE VERIFICACIÓN HUMANA**: el runtime
  node-playwright de este entorno no tiene navegador. El typecheck/lint/build
  del admin-panel sí pasan y el backend está cubierto por pytest.
- **Sandbox real-container** pendiente de la imagen runtime del marketplace
  (el spec + el teardown corren con cliente Docker mockeado; la ejecución
  real del probe es un paso de integración).
- **semgrep como segundo scanner** queda opcional: donde el entorno (CI/Linux)
  lo trae en PATH se ejercita; en otros lados los tests semgrep skip-guard.
- **Camino de aborto del endpoint install/update vs. runtime de catálogo.**
  El endpoint `POST /marketplace/installations` (Fase A) sigue persistiendo
  directamente; cablear vivo el `InstallOrchestrator` (con artefactos en
  disco por listing) en el camino de aborto del endpoint es un follow-up del
  runtime de catálogo. El aborto del orquestador se valida conduciéndolo
  directamente.
- **Persistir la config guiada en el install + cableado del bootstrap de
  seeds.** Persistir la `PlaywrightToolConfig` guiada sobre la instalación, y
  el wiring de arranque que siembra el listing Playwright global + la
  plantilla QA E2E Automator en bootstrap, quedan como follow-up.

## Tests humanos pendientes

Los `human_09_01`…`human_09_04` (instalación con consentimiento granular,
análisis estático bloquea código sospechoso, Playwright end-to-end, compartir
entre tenants con audit) quedan **pendientes de ejecutar por un humano** antes
de pasar el plan a `completed`.

## Verificación

- `pre-commit run --files <cambiados>` (black/ruff/mypy/prettier) ✅ por tarea.
- Suite completa del marketplace en verde:

  ```bash
  pytest tests/unit/test_marketplace_models.py tests/unit/test_trust_levels.py \
    tests/unit/test_skill_md_format.py tests/unit/test_tool_manifest_format.py
  pytest tests/integration/test_marketplace_migration.py \
    tests/integration/test_marketplace_endpoints.py \
    tests/integration/test_static_analysis.py tests/integration/test_install_sandbox.py \
    tests/integration/test_consent.py tests/integration/test_revocation.py \
    tests/integration/test_install_flow.py tests/integration/test_marketplace_versioning.py \
    tests/integration/test_playwright_tool.py tests/integration/test_qa_e2e_automator.py \
    tests/integration/test_e2e_test_templates.py tests/integration/test_private_marketplace.py \
    tests/integration/test_cross_tenant_sharing.py
  ```

  (incl. las regresiones `@pytest.mark.cross_tenant` de aislamiento de
  listings privados y de compartir cross-tenant).

- Migraciones 0041..0044 reversibles (up/down/up) con single head; downgrade
  completo del marketplace a `0040_sso_email_domains`.
- admin-panel: `npm run typecheck && lint && build` ✅; e2e Playwright del
  marketplace **pendiente de verificación humana**.

## PR

Pendiente de apertura/merge a `main` (lo gestiona el humano tras validar los
tests humanos del plan).
