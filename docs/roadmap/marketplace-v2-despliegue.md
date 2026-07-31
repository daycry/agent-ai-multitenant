---
plan_id: marketplace-v2-despliegue
title: Marketplace v2 — despliegue en proyectos, publicación con revisión y versiones
status: in_progress
blocking_plan: []
started_at: 2026-07-31
completed_at: null
estimated_duration_calendar: 3-4 semanas
estimated_effort_person_days: 14
estimated_cost_human_eur: 5.600 € – 8.400 €
estimated_cost_ai_eur: 40 € – 80 €
created_by: diseño marketplace-v2 (operador + brainstorming 2026-07-31)
spec_sections_referenced: [32]
docs_language: es
---

# Plan marketplace-v2-despliegue — que instalar sea recibir

> **Fuente de verdad del QUÉ y el POR QUÉ:**
> [`marketplace-v2-diseno.md`](marketplace-v2-diseno.md) — diseño aprobado por el
> operador el 2026-07-31, con las ocho decisiones de producto (D1-D8) y los dos
> enfoques rechazados. Este plan es el CÓMO: seis fases, cada tarea con sus
> ficheros y sus tests declarados. **Ante cualquier conflicto entre este plan y
> el diseño, gana el diseño; ante conflicto entre ambos y el código ya mergeado,
> se para y se re-verifica** (§1 de verificar-antes-de-implementar).
>
> `blocking_plan: []` a conciencia: los planes 09/09.1 sobre los que se apoya
> están mergeados y desplegados aunque su estado sea `pending_human_validation`
> — la dependencia es factual, no de protocolo (no aplica el gate del ADR 0138).

## Cabecera

| Campo           | Valor                                                                       |
| --------------- | --------------------------------------------------------------------------- |
| **ID del Plan** | `marketplace-v2-despliegue`                                                 |
| **Rama git**    | la vigente al arrancar (`work/…` nueva si la actual ya cerró su ciclo)      |
| **Origen**      | diseño `marketplace-v2-diseno.md` (D1-D8)                                   |
| **Se apoya en** | ADR 0032 (confianza), 0100 (materialización), 0127 (OAuth), 0128 (rol→tool) |
| **No toca**     | el sandbox de código propio (sigue gated por la infra del ADR 0100)         |

## Avisos al implementador (léelos, ahorran horas)

1. **Verifica los números antes de usarlos.** Este plan nombra la migración
   `0128` y el ADR `0142` porque ésas eran las cabezas al escribirlo
   (2026-07-31). Compruébalas con `ls` en el momento de implementar: en este
   repo los números se mueven cada semana. Rev-id ≤ 32 chars.
2. **Un solo pytest de integración a la vez** o `TEST_PG_DB_NAME` +
   `TEST_REDIS_URL` propios por proceso
   ([gotcha](../03-guides/gotchas/integration-tests-share-one-database.md)).
3. **El formulario guiado de Playwright de HOY es el anti-patrón que este plan
   corrige**: no lo tomes de referencia de dónde va la config; sí de CÓMO se
   renderiza un `config_schema` (`marketplace/playwright.py::config_schema`).
4. `routers/marketplace.py` tiene **1.465 líneas**: todo endpoint nuevo va a
   `routers/marketplace_deployments.py` (fichero nuevo), no ahí dentro.
5. Los tests que escribas deben poder **fallar**: rompe la implementación,
   comprueba el rojo, restaura. Un assert de banda que el default también
   cumple no verifica nada (dos casos documentados en el repo).

---

## Fase 0 — La decisión, formalizada (0,5 días)

#### `task_mkt2_00` — ADR 0142: el despliegue como entidad y la config en tres capas

- [x] **Título**: Redactar `docs/05-architecture-decisions/0142-marketplace-despliegue-tres-capas.md`, `status: accepted`
- **Tiempo**: 3 h · **Complejidad**: s
- Nace `accepted` (no `proposed`): registra una decisión que el operador YA tomó
  el 2026-07-31 — escribirlo como pendiente sería el pecado documental de la
  casa. Contenido: el problema medido (§1 del diseño), las tres capas (§3), la
  entidad de despliegue con los dos enfoques rechazados, y la regla de NO
  política paralela (el `role_map` de un MCP escribe `projects.mcp_tool_roles`
  del ADR 0128). El cuerpo y el frontmatter dicen LO MISMO. Enlaza el diseño y
  actualiza la fila de 0128 («extendido por 0142») sin tocar su estado.
- **Tests automáticos**:
  ```yaml
  - id: auto_mkt2_00_a
    runtime: python-pytest
    command: "pytest tests/docs/test_docs_internal_links.py -q"
  ```

## Fase 1 — Esquema y servicio de despliegue: comprar = recibir (4,5 días)

#### `task_mkt2_01` — Migración: `marketplace_deployments` + `marketplace_listing_versions`

- [x] **Título**: Migración reversible (dos tablas, RLS FORCE, backfill de versiones) + modelos ORM
- **Tiempo**: 6 h · **Complejidad**: m
- Una sola migración encadenada a la cabeza real. `marketplace_deployments`:
  columnas del §4 del diseño (`installation_id` FK CASCADE, `project_id` FK
  CASCADE, `tenant_id`, `config` JSONB, `role_map` JSONB, `deployed_version`,
  `status` CHECK in (active,disabled,retired), `created_refs` JSONB (las filas
  que la materialización creó — el contrato de la retirada exacta),
  `deployed_by` SET NULL, timestamps), ENABLE+FORCE+policy `tenant_isolation` (patrón calcado de
  `user_invitations`, migración 0127), índice parcial de activos por
  `(project_id)` e índice por `(installation_id)`. UNIQUE parcial
  `(installation_id, project_id) WHERE status = 'active'` — el candado que hace
  idempotente el re-despliegue. `marketplace_listing_versions`: snapshot del
  manifest + permisos + `config_schema` + changelog + `published_by`/
  `reviewed_by`; UNIQUE `(listing_id, version)`. **Backfill**: cada listing
  existente pare su fila de versión (v = `listing.version`) y cada instalación
  pina esa fila (`pinned_version_id` FK nueva en installations, NOT NULL tras el
  backfill). Downgrade real: retira policies, tablas y la columna del pin.
  Modelos en `db/marketplace.py` con los mismos nombres.
- **Tests automáticos**:
  ```yaml
  - id: auto_mkt2_01_a
    runtime: python-pytest
    command: "pytest tests/unit/test_marketplace_deployment_models.py -q"
  - id: auto_mkt2_01_b
    runtime: python-pytest
    command: "pytest tests/integration/test_marketplace_v2_migration.py -q -p no:randomly"
  ```
  El de integración hace el round-trip head→antes→head **anclado a la revisión
  por nombre, NUNCA `downgrade(\"-1\")`** (gotcha de la 0125/0126), verifica el
  backfill sobre datos sembrados (listing con instalación → versión creada y
  pinada) y que `test_rls_invariant.py` sigue verde con la tabla nueva.

#### `task_mkt2_02` — Validador de config contra `config_schema`

- [x] **Título**: `marketplace/config_schema.py`: validar los VALORES de un despliegue contra el esquema del manifest
- **Tiempo**: 4 h · **Complejidad**: s
- Función pura `validate_deployment_config(schema: dict, values: dict) ->
list[str]` (lista de errores legibles, vacía = válido): tipos, requeridos,
  defaults aplicados, campos desconocidos rechazados, y **campos `secret: true`
  cuyo valor debe ser puntero a Vault, nunca el secreto en claro** (prefijo
  `vault:`); más `apply_schema_migration(old_values, new_schema) ->
tuple[values, list[str]]` para la Fase 4 (campos nuevos → default, retirados →
  fuera, requerido-sin-default → error señalado). Generaliza lo que
  `PlaywrightToolConfig` hace ad-hoc; NO borres esa clase todavía (Fase 5).
  Y la otra mitad del contrato: **el parser estándar del manifest**
  (`marketplace/tool_format.py::parse_tool_manifest` y el de skills) acepta y
  valida los dos campos opcionales nuevos — `targets` (lista de roles del
  vocabulario del equipo) y `config_schema` — con test de que un manifest SIN
  ellos sigue siendo válido (retro-compatibilidad con todo lo publicado).
- **Tests automáticos**:
  ```yaml
  - id: auto_mkt2_02_a
    runtime: python-pytest
    command: "pytest tests/unit/test_marketplace_config_schema.py -q"
  ```
  Incluye el caso que muerde: un secreto en claro en un campo `secret` →
  rechazado con mensaje que NO ecoa el valor.

#### `task_mkt2_03` — El servicio de despliegue (el corazón del plan)

- [x] **Título**: `marketplace/deploy.py`: `deploy_installation` / `retire_deployment`, materialización por tipo con provenance
- **Tiempo**: 10 h · **Complejidad**: l
- `async def deploy_installation(session, *, installation_id, project_id,
config, role_map, actor) -> DeploymentResult` y `async def
retire_deployment(session, *, deployment_id, actor) -> int`. Por tipo:
  `mcp_server` → entrada en `Project.mcp_servers` (mismo shape que escribe la
  pestaña MCP hoy; si el manifest declara OAuth, la entrada nace
  `pending_connection` y el flujo «Conectar» del ADR 0127 la completa) **y** el
  `role_map` escrito en `projects.mcp_tool_roles` (ADR 0128 — SIN mecanismo
  paralelo); `tool`/`skill` → filas `agent_tools`/`agent_skills` para los
  agentes del equipo cuyo rol esté en `role_map`, reutilizando las filas
  `Tool`/`Skill` que la materialización del ADR 0100 ya creó. Cada fila creada
  queda anotada en `deployment.created_refs` (JSONB) — retirar borra EXACTAMENTE
  eso y marca `retired`, sin tocar nada que el operador hubiera añadido a mano.
  Idempotencia: segundo deploy activo sobre el mismo par → no-op con aviso
  (`already_deployed=True`), garantizado por el UNIQUE parcial. Todo pasa por
  `validate_deployment_config` antes de escribir. Auditoría append-only con
  actor en deploy y retire.
- **Tests automáticos**:
  ```yaml
  - id: auto_mkt2_03_a
    runtime: python-pytest
    command: "pytest tests/integration/test_marketplace_deploy_service.py -q -p no:randomly"
  ```
  Los nodos que NO pueden faltar: cross-tenant (instalación de A no despliega en
  proyecto de B → 404, ni lee sus despliegues), retirada exacta (siembra una
  tool asignada A MANO al mismo agente → retirar el despliegue no se la lleva),
  idempotencia, y config inválida → nada escrito (transacción entera fuera).

#### `task_mkt2_04` — Router `marketplace_deployments.py` + disponibilidad por proyecto

- [x] **Título**: Endpoints de despliegue (fichero nuevo) + `GET /projects/{id}/marketplace/available`
- **Tiempo**: 5 h · **Complejidad**: m
- `routers/marketplace_deployments.py` (aviso 4): `POST
/marketplace/installations/{id}/deployments` (body: project_id, config,
  role_map), `GET /marketplace/installations/{id}/deployments`, `POST
/marketplace/deployments/{id}/retire`, y del lado proyecto `GET
/projects/{id}/marketplace/available` (instalaciones del tenant aún no
  desplegadas ahí, con su `config_schema` y `targets` para que la UI pinte el
  formulario). Gating `require_tenant_admin` en mutaciones, `require_tenant_member`
  en lecturas — y fila en `docs/04-reference/rbac.md` ANTES de correr el test de
  drift, que falla si la ruta no está en la matriz.
- **Tests automáticos**:
  ```yaml
  - id: auto_mkt2_04_a
    runtime: python-pytest
    command: "pytest tests/integration/test_marketplace_deployments_api.py tests/unit/test_rbac_matrix_drift.py -q -p no:randomly"
  ```

#### `task_mkt2_05` — La cadena entera, de publicar a usar

- [x] **Título**: Test de integración de la cadena completa (el criterio de éxito §9 del diseño)
- **Tiempo**: 4 h · **Complejidad**: m
- `tests/integration/test_marketplace_v2_chain.py`: seed de un listing
  `mcp_server` con `targets: [backend_dev]` y `config_schema` con `base_url` →
  instalar (consentir) → desplegar en un proyecto con config →
  **assert el agente backend_dev del equipo tiene las tools en `agent_tools` Y
  el proyecto tiene la entrada en `mcp_servers` Y `mcp_tool_roles` refleja el
  role_map** → retirar → assert todo limpio y el `retired` conserva la
  auditoría. Y el mismo viaje con un `skill` (→ `agent_skills`). Es el test que
  convierte «comprar» en «recibir»; si solo puedes salvar uno, es éste.
- **Tests automáticos**:
  ```yaml
  - id: auto_mkt2_05_a
    runtime: python-pytest
    command: "pytest tests/integration/test_marketplace_v2_chain.py -q -p no:randomly"
  ```

## Fase 2 — Las tres puertas de UI (3 días)

#### `task_mkt2_06` — Ficha de instalación: desplegado-en + «Desplegar a…» + retirar

- [ ] **Título**: `app/admin/marketplace/installations/[id]/deployments-section.tsx` + formulario de despliegue
- **Tiempo**: 6 h · **Complejidad**: m
- Sección «Desplegado en N proyectos» (lista con estado y versión), botón
  «Desplegar a…» (multi-select de proyectos → por proyecto, el formulario del
  `config_schema` renderizado como **formulario guiado** — generaliza el
  renderer que hoy es solo de Playwright — con los roles de `targets`
  pre-marcados y editables), y retirar con confirmación. Componente del
  formulario en `components/marketplace/deployment-config-form.tsx`, separado y
  con test propio: es la pieza que reutilizan las tres puertas.
- **Tests automáticos**:
  ```yaml
  - id: auto_mkt2_06_a
    runtime: vitest
    command: "npx vitest run components/marketplace/deployment-config-form.test.tsx app/admin/marketplace/installations"
  ```
  El del formulario afirma: defaults del schema aplicados, campo `secret` pinta
  input de puntero Vault (nunca texto libre del secreto), roles pre-marcados
  desde `targets`, y submit bloqueado con errores de validación visibles.

#### `task_mkt2_07` — Wizard de proyecto: paso «Capacidades»

- [ ] **Título**: Paso nuevo en `app/admin/projects/new` que ofrece lo instalado y despliega al crear
- **Tiempo**: 5 h · **Complejidad**: m
- Tras el paso de equipo (necesita los roles para pre-marcar): lista de
  instalaciones del tenant con checkbox; lo marcado abre su
  `deployment-config-form` inline; al crear el proyecto, el wizard **encadena los POST** al endpoint de
  despliegue de `task_mkt2_04` — decidido aquí: NO se toca la API de creación de
  proyectos (menos superficie, y el fallo de un despliegue no aborta la creación:
  se reporta por-item y el proyecto nace con lo que sí entró). Si un MCP
  exige OAuth, el proyecto nace con la entrada `pending_connection` y el wizard
  lo dice en claro («conectar después desde la pestaña MCP») en vez de fingir
  que quedó vivo.
- **Tests automáticos**:
  ```yaml
  - id: auto_mkt2_07_a
    runtime: vitest
    command: "npx vitest run app/admin/projects/new"
  ```

#### `task_mkt2_08` — Pestañas del proyecto: «disponibles del tenant»

- [ ] **Título**: Sección de activación local en las pestañas MCP y Tools del proyecto
- **Tiempo**: 4 h · **Complejidad**: s
- Lee `GET /projects/{id}/marketplace/available`, pinta las disponibles con su
  badge de confianza, «Activar» abre el MISMO `deployment-config-form`. Lo ya
  desplegado se muestra con su origen («del marketplace: Jira MCP v1.2») y
  enlace a la ficha — las dos vías (D4) enseñan el mismo estado porque leen la
  misma entidad. Además: `e2e/marketplace-deploy.spec.ts` ESCRITA (el viaje de
  human_mkt2_01 con mocks de red donde toque) — aquí no hay navegador, así que
  queda escrita, tipada y lintada, y su ejecución es del entorno que lo tenga.
- **Tests automáticos**:
  ```yaml
  - id: auto_mkt2_08_a
    runtime: vitest
    command: "npx vitest run app/admin/projects/[id]/mcp-servers app/admin/projects/[id]/tools"
  ```

## Fase 3 — Publicar pasa por revisión (2,5 días)

#### `task_mkt2_09` — Máquina de estados del listing + migración

- [ ] **Título**: `draft → pending_review → published | rejected` (+ promoción a verified), con migración y transiciones auditadas
- **Tiempo**: 5 h · **Complejidad**: m
- Migración (cabeza real del momento): `listings.review_status` CHECK +
  `reviewed_by`/`reviewed_at`/`rejection_reason`; backfill: lo publicado hoy →
  `published` (no rompas el catálogo vivo). Transiciones en
  `marketplace/review.py` como funciones con actor obligatorio y fila de
  auditoría; **la visibilidad del catálogo filtra por `published`** — un
  `pending_review` no existe para quien no sea su autor o un admin. La
  promoción a `verified` sigue siendo la columna `trust_level` existente, ahora
  solo mutable por la transición del admin.
- **Tests automáticos**:
  ```yaml
  - id: auto_mkt2_09_a
    runtime: python-pytest
    command: "pytest tests/unit/test_marketplace_review_transitions.py tests/integration/test_marketplace_review_flow.py -q -p no:randomly"
  ```
  Con el negativo que importa: un tenant_user NO revisa, un `pending_review` NO
  aparece en el catálogo de otro usuario, y un rechazo sin motivo → 422.

#### `task_mkt2_10` — Cola de revisión del admin + publicar desde la UI

- [ ] **Título**: `app/admin/marketplace/review/page.tsx` (cola) + el flujo publicar deja el listing en `pending_review`
- **Tiempo**: 5 h · **Complejidad**: m
- Cola con diff del manifest cuando es versión nueva de algo publicado
  (reutiliza el helper de diff de la Fase 4 si ya existe; si no, muestra el
  manifest entero y el diff llega con `task_mkt2_12`). Aprobar / promocionar a
  verified / rechazar con motivo. El CTA «Publicar» existente pasa a crear
  `pending_review` y la UI lo dice («pendiente de revisión», no «publicado»).
  Ruta nueva → fila en `rbac.md` (system_admin).
- **Tests automáticos**:
  ```yaml
  - id: auto_mkt2_10_a
    runtime: vitest
    command: "npx vitest run app/admin/marketplace/review"
  ```

## Fase 4 — Versiones: actualización explícita (3 días)

#### `task_mkt2_11` — Publicar versión nueva = fila de versión + revisión

- [ ] **Título**: Re-publicar crea `marketplace_listing_versions` nueva en `pending_review`; el diff de permisos es un helper puro
- **Tiempo**: 4 h · **Complejidad**: m
- `marketplace/versions.py`: `permission_diff(old_manifest, new_manifest) ->
{added: [...], removed: [...]}` (puro, con test propio) y el alta de versión
  colgando del flujo de publicación de la Fase 3 (misma revisión, mismo audit).
  La versión vigente del listing solo avanza al aprobarse.
- **Tests automáticos**:
  ```yaml
  - id: auto_mkt2_11_a
    runtime: python-pytest
    command: "pytest tests/unit/test_marketplace_permission_diff.py tests/integration/test_marketplace_versioning.py -q -p no:randomly"
  ```

#### `task_mkt2_12` — Actualizar instalación: re-consentir SOLO el delta + refrescar despliegues

- [ ] **Título**: `POST /marketplace/installations/{id}/update` con re-consentimiento del delta y refresco de despliegues; rollback por el mismo mecanismo
- **Tiempo**: 8 h · **Complejidad**: l
- El endpoint compara la versión pinada con la vigente: permisos nuevos → exige
  el consentimiento de ESOS (los ya concedidos no se re-preguntan; los
  denegados nuevos dejan la instalación `disabled`, coherente con el flujo de
  consent existente); al confirmar, re-pina y **refresca cada despliegue** vía
  `apply_schema_migration` (task_mkt2_02): campos nuevos → default, retirados →
  fuera, requerido-sin-default → el despliegue queda `disabled` con motivo
  visible y NO se aplica a medias. Rollback = el mismo endpoint apuntando a una
  versión anterior del histórico. UI: banner «v X.Y disponible» en ficha y
  catálogo con el diff de permisos en claro.
- **Tests automáticos**:
  ```yaml
  - id: auto_mkt2_12_a
    runtime: python-pytest
    command: "pytest tests/integration/test_marketplace_update_flow.py -q -p no:randomly"
  ```
  Nodos irrenunciables: delta-solo (un permiso ya concedido NO se re-pide),
  despliegue con campo requerido nuevo sin default → `disabled` con motivo y los
  demás despliegues SÍ actualizados, y rollback restaura config y pin.

## Fase 5 — Playwright al modelo nuevo + honestidad documental (1,5 días)

#### `task_mkt2_13` — La config guiada de Playwright se muda al despliegue

- [ ] **Título**: El install de Playwright deja de pedir config; su `config_schema` se rinde en el despliegue; migración de datos si hay config previa
- **Tiempo**: 5 h · **Complejidad**: m
- Quitar el formulario del flujo de instalación (UI) y el almacenamiento de
  valores en la instalación (data migration: si alguna instalación lleva config,
  crear un despliegue `disabled` por cada proyecto del tenant que la use o —si
  no es atribuible— descartarla dejando aviso en el log de migración; el caso
  esperado es el vacío). `PlaywrightToolConfig` pervive como la VALIDACIÓN
  tipada que `config_schema` declara — se invoca desde
  `validate_deployment_config`, no desde el install.
- **Tests automáticos**:
  ```yaml
  - id: auto_mkt2_13_a
    runtime: python-pytest
    command: "pytest tests/integration/test_playwright_deploy_config.py -q -p no:randomly"
  ```
  Con el caso de dos proyectos con `base_url` DISTINTA conviviendo — el que el
  modelo viejo no podía expresar.

#### `task_mkt2_14` — Referencia, changelog y guía humana

- [ ] **Título**: `docs/04-reference/marketplace.md` reescrito al modelo v2 + changelog + guía de tests humanos
- **Tiempo**: 4 h · **Complejidad**: s
- La referencia cuenta las tres capas y las tres puertas con capturas de flujo;
  `docs/07-changelog/marketplace-v2-despliegue.md` al cierre;
  `docs/03-guides/human-tests/marketplace-v2-despliegue.md` con los pasos de los
  tests humanos de abajo. Cero afirmaciones sin verificar contra el código.
- **Tests automáticos**:
  ```yaml
  - id: auto_mkt2_14_a
    runtime: python-pytest
    command: "pytest tests/docs/ -q"
  ```

---

## Tests humanos del Plan

```yaml
- id: human_mkt2_01
  title: El viaje completo en navegador
  steps: >-
    Publicar un MCP de prueba desde un tenant → verlo en pending_review → como
    system admin, aprobarlo → instalarlo (solo consentimiento, SIN formulario de
    config) → crear un proyecto nuevo y marcarlo en el paso Capacidades con una
    base_url → verificar en la pestaña MCP del proyecto que está configurado y
    en la ficha del agente del rol destino que tiene las tools → desplegarlo a
    un SEGUNDO proyecto con base_url distinta desde la ficha → retirarlo del
    primero y comprobar que el segundo sigue intacto.
- id: human_mkt2_02
  title: Actualización con delta de permisos
  steps: >-
    Publicar v2 del mismo listing añadiendo un permiso → aprobar → ver el banner
    en la instalación con el diff → re-consentir solo lo nuevo → verificar que
    los despliegues se refrescaron → rollback a v1 y verificar config restaurada.
- id: human_mkt2_03
  title: OAuth de un MCP desplegado
  steps: >-
    Desplegar un MCP que declare OAuth → la entrada nace pendiente de conexión →
    completar el flujo «Conectar» (ADR 0127) en la pestaña MCP → el agente lo usa.
```

## Criterios de cierre

1. Todas las casillas `[x]` con sus tests automáticos en verde.
2. La cadena `test_marketplace_v2_chain.py` en verde en CI (o en local con el
   stack, mientras CI siga caído).
3. Tests humanos validados por el operador.
4. Entrada en `docs/07-changelog/marketplace-v2-despliegue.md`.
5. PR del plan mergeado.
