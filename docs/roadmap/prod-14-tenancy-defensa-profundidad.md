---
plan_id: prod-14-tenancy-defensa-profundidad
title: Multi-tenancy — defensa en profundidad (junctions, service_user, meta-test)
status: pending_approval
blocking_plan: null
started_at: null
completed_at: null
estimated_duration_calendar: 7-10 días
estimated_effort_person_days: 7
estimated_cost_human_eur: 3.150 € – 4.200 €
estimated_cost_ai_eur: 30 € – 60 €
created_by: auditoria-claude-2026-06
spec_sections_referenced: []
docs_language: es
priority: P2
---

# Plan prod-14 — Multi-tenancy: defensa en profundidad (junctions, service_user, meta-test)

## Cabecera

| Campo                              | Valor                                 |
| ---------------------------------- | ------------------------------------- |
| **ID del Plan**                    | `prod-14-tenancy-defensa-profundidad` |
| **Estado**                         | `pending_approval`                    |
| **Prioridad**                      | P2                                    |
| **Bloqueado por**                  | — (ninguno)                           |
| **Tiempo estimado (calendario)**   | 7-10 días                             |
| **Tiempo estimado (persona-días)** | 7                                     |
| **Rama git sugerida**              | `plan/prod-14-tenancy-defensa`        |

---

## Resumen

La auditoría integral de producción (2026-06-10) califica el aislamiento multi-tenant
como **good**: RLS ENABLE+FORCE fail-closed en las tablas tenant-scoped, dos roles de
BD, `get_tenant_session` consistente en los 61 routers muestreados y gate CI dedicado
con 73 tests `@cross_tenant`. **No hay fugas explotables hoy.** Este plan NO corrige
una brecha activa: añade las capas de defensa que faltan para que el aislamiento no
dependa de la disciplina de código en cada query futura:

1. **tenant_id + RLS en las 4 tablas junction** (`agent_skills`, `agent_tools`,
   `team_members`, `task_dependencies`) que la migración 0002 dejó fuera de
   `_TENANT_SCOPED_TABLES` — desviación literal del Principio Rector nº 1.
2. **Rol `service_user`** (BYPASSRLS + DML, SIN DDL ni ownership) para workers,
   orchestrator, notification-dispatcher y el engine admin del api-server, que hoy
   conectan TODOS como `migrations_user` (owner con `GRANT ALL`: un servicio
   comprometido podría hacer `ALTER TABLE ... DISABLE ROW LEVEL SECURITY`).
3. **Meta-test invariante de cobertura RLS**: toda tabla con columna `tenant_id`
   debe tener `relrowsecurity + relforcerowsecurity + policy`; toda tabla sin
   `tenant_id` debe estar en una allowlist documentada. Cierra la clase de regresión
   "migración futura que olvida el bloque `_enable_rls` y pasa CI en verde".
4. **Tabla `users` global**: test de no-fuga del directorio + ADR con opciones
   (vista `tenant_users` / política RLS con EXISTS) para que un humano decida.
5. **Coherencia transversal**: unicidad `(tenant_id, name)` para teams/skills/agents
   (patrón 0077 de tools) y extracción del guard `_verify_project_visible`
   cuadruplicado a un módulo común.

## Alcance

**Entra**:

- Migración Alembic (siguiente a `0083_llm_provider_slug`) que añade `tenant_id`
  denormalizado + RLS a las 4 tablas junction, con backfill desde el padre y
  verificación de consistencia.
- Actualización de los modelos ORM y de todos los caminos de escritura de esas
  tablas para poblar `tenant_id`.
- Rol `service_user` en `docker/postgres/init/02-roles.sh` + script idempotente
  para BD existentes + migración de los defaults de conexión de los 4 servicios.
- Meta-test invariante en `tests/integration/` marcado `@cross_tenant` (entra en
  el gate CI existente sin tocar `ci.yml`).
- Auditoría dirigida de queries sobre `User` en contexto tenant + test cross_tenant.
- ADR (proposed) para el endurecimiento de `users`.
- Migración de unicidad parcial `(tenant_id, name)` para teams, skills y agents,
  con dedup previo; de paso `Document.source_size_bytes → BigInteger` y tipado
  `Plan.created_by: UUID | None` (incoherencias fusionadas en db-9).
- Helper compartido `_verify_project_visible` en `api_server/routers/_guards.py`.

**Queda fuera**:

- El JWT como query param en WebSockets (tenancy-4, low): ticket efímero — se
  coordina con **prod-09** (sesiones y autorización), que ya toca el flujo de auth.
- Cualquier cambio en el compose de producción generado por el installer: las
  variables de entorno del nuevo `service_user` las consume **prod-01**
  (despliegue ejecutable) y su password sin default conocido la gestiona
  **prod-10** (Vault). Aquí solo se preparan `02-roles.sh`, los settings y el
  compose de desarrollo.
- Implementar la vista `tenant_users` o la política RLS sobre `users`: este plan
  produce el ADR y el test; la implementación es follow-up tras decisión humana.
- Particionado o índices por tenant en pgvector (db-6): pertenece a **prod-13**.
- Refactor de los duplicados menores (`_window_days` ×3, `_to_response` ×3):
  fuera de alcance, no son guards de seguridad.

## Decisiones clave

1. **`tenant_id` denormalizado en las junctions** (no derivado del padre vía JOIN
   en la policy): replica el patrón ya consolidado en `agent_knowledge_bases`
   (migración 0026) y `kb_projects` (0022). Policies con
   `tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid`
   fail-closed, idénticas a las existentes. **Recomendado: sí** — coste de
   escritura mínimo, lectura sin JOIN, coherencia con el resto del esquema.
2. **`service_user` = BYPASSRLS + DML, sin DDL**: las tablas siguen siendo
   propiedad de `migrations_user`; `service_user` recibe
   `SELECT/INSERT/UPDATE/DELETE` vía `ALTER DEFAULT PRIVILEGES` + `GRANT` sobre
   las existentes, **sin** `CREATE` en el schema ni ownership. `migrations_user`
   queda reservado a Alembic. El riesgo residual (un servicio comprometido sigue
   leyendo cross-tenant) es inherente a su función y se documenta.
3. **Allowlist del meta-test explícita y comentada**: las tablas globales sin
   `tenant_id` (`users`, `alembic_version`, `platform_settings`, `llm_providers`,
   `marketplace_sources`, etc. — el inventario exacto sale de la task 07) viven en
   una constante del test con un comentario por entrada que justifica la
   excepción, y se replican en `docs/04-reference/`. Añadir una entrada nueva a la
   allowlist exige justificación en el PR.
4. **Dedup previo a la unicidad — propuesta "latest wins" no destructiva**: ante
   duplicados vivos de `(tenant_id, name)`, conservar el más reciente con el
   nombre original y **renombrar** los anteriores con sufijo `-dup-{n}` (no
   soft-borrarlos: podría ocultar agentes/teams en uso). Si el humano prefiere
   soft-delete, se decide en la revisión del plan antes de ejecutar la task 10.
5. **`users` global — decisión NO tomada aquí**: el plan redacta un ADR
   (proposed) con tres opciones de la auditoría — (1) solo test de no-fuga,
   (2) vista `tenant_users` con el join a memberships y RLS como único camino de
   lectura tenant, (3) política RLS sobre `users` con `EXISTS` contra
   `user_org_memberships` + excepción para el propio `app.user_id`. La opción 1
   se implementa ya en este plan (task 08); 2 vs 3 las decide un humano (la 3
   exige validar el flujo de login pre-tenant).

## Tareas

### Fase A — tenant_id + RLS en tablas junction (tenancy-1)

#### `task_prod14_01` — Migración: tenant_id + RLS en las 4 junctions

- [ ] **Título**: Nueva migración (down_revision `0083_llm_provider_slug`) que
      añade `tenant_id UUID` a `agent_skills`, `agent_tools`, `team_members` y
      `task_dependencies`; backfill desde el padre (`agents.tenant_id`,
      `teams.tenant_id`, `tasks.tenant_id`); `NOT NULL` + índice; y por tabla
      `ENABLE/FORCE ROW LEVEL SECURITY` + policy `{tabla}_tenant_isolation`
      idéntica a las de `_TENANT_SCOPED_TABLES` de la migración 0002. La
      migración debe **fallar ruidosamente** si detecta una fila junction cuyo
      padre e hijo pertenecen a tenants distintos (pre-check con SELECT antes del
      backfill). Downgrade real (drop policy + columna).
- **Tiempo**: 6 h · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod14_01_a
    runtime: python-pytest
    command: "pytest tests/integration/test_migrations_v2.py -v -k junction"
  ```

#### `task_prod14_02` — ORM y caminos de escritura pueblan tenant_id

- [ ] **Título**: Añadir la columna a los 4 modelos de asociación en
      `apps/api-server/src/api_server/db/domain.py` y revisar TODOS los puntos
      que insertan filas junction (al menos: asignación de tools/skills en
      `routers/agents.py` — incluido el fork de agentes, línea ~370 —,
      membresías en `routers/teams.py`, dependencias de tareas en
      `routers/tasks.py`/repos, seeds y el flujo de instalación del marketplace)
      para que pasen `tenant_id` explícito. Bajo `app_user` con RLS la omisión
      revienta sola (policy fail-closed), pero los servicios BYPASSRLS no: grep
      dirigido + mypy deben cubrir ambos caminos.
- **Tiempo**: 5 h · **Complejidad**: m
- **Depende de**: `task_prod14_01`
- **Tests automáticos**:
  ```yaml
  - id: auto_prod14_02_a
    runtime: python-pytest
    command: "pytest tests/integration/test_agent_skills.py tests/integration/test_agent_tools_assignment.py tests/integration/test_agents_endpoints.py -v"
  ```

#### `task_prod14_03` — Tests cross_tenant de denegación en junctions

- [ ] **Título**: Nuevo `tests/integration/test_junction_tenant_rls.py` marcado
      `@cross_tenant`: para cada una de las 4 tablas, sesión `app_user` con
      `app.tenant_id` del tenant B no ve ni puede insertar/borrar filas del
      tenant A (incluido `agent_tools.config_override`, que transporta config
      potencialmente sensible). Verificar también que el INSERT de una junction
      apuntando a un padre de otro tenant es rechazado por la policy.
- **Tiempo**: 4 h · **Complejidad**: m
- **Depende de**: `task_prod14_02`
- **Tests automáticos**:
  ```yaml
  - id: auto_prod14_03_a
    runtime: python-pytest
    command: "pytest tests/integration/test_junction_tenant_rls.py -v -m cross_tenant"
  ```

### Fase B — Rol service_user: DML cross-tenant sin DDL (tenancy-2)

#### `task_prod14_04` — Crear service_user en init de PostgreSQL + script de upgrade

- [ ] **Título**: En `docker/postgres/init/02-roles.sh`, crear
      `service_user WITH LOGIN BYPASSRLS NOCREATEDB NOCREATEROLE`; `GRANT
SELECT/INSERT/UPDATE/DELETE ON ALL TABLES` + `USAGE/SELECT ON SEQUENCES` +
      `ALTER DEFAULT PRIVILEGES FOR ROLE migrations_user ... TO service_user`;
      **sin** `CREATE` en el schema ni ownership. Como el init solo corre en
      contenedores nuevos, añadir `docker/postgres/upgrade/2026xx-service-user.sh`
      (o equivalente documentado en runbook) idempotente para BD existentes.
      Password vía env `SERVICE_USER_PASSWORD` con placeholder solo-dev
      (la eliminación de defaults conocidos es de **prod-10**).
- **Tiempo**: 4 h · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod14_04_a
    runtime: python-pytest
    command: "pytest tests/integration/test_db_roles_service_user.py -v -k grants"
  ```

#### `task_prod14_05` — Migrar los 4 servicios a service_user

- [ ] **Título**: Cambiar los defaults de `database_url` en
      `apps/workers/src/workers/config.py:35`,
      `apps/orchestrator/src/orchestrator/config.py:51`,
      `apps/notification-dispatcher/src/notification_dispatcher/config.py:52` y
      `admin_database_url` en `apps/api-server/src/api_server/config.py:31` de
      `migrations_user` a `service_user`; actualizar descripciones ("BYPASSRLS
      sin DDL"), `docker-compose.dev.yml`, `.env.example` y los docs de
      referencia. `migrations_user` queda referenciado únicamente por Alembic
      (`migrations/env.py`). Anotar en el plan **prod-01** que el
      `compose_generator` del installer debe emitir la nueva variable.
- **Tiempo**: 4 h · **Complejidad**: m
- **Depende de**: `task_prod14_04`
- **Tests automáticos**:
  ```yaml
  - id: auto_prod14_05_a
    runtime: python-pytest
    command: "pytest tests/integration/test_execution_persistence.py tests/integration/test_admin_rbac.py -v"
  ```

#### `task_prod14_06` — Test de privilegios: service_user no puede tocar DDL ni RLS

- [ ] **Título**: `tests/integration/test_db_roles_service_user.py`: conectado
      como `service_user`, (a) `ALTER TABLE agents DISABLE ROW LEVEL SECURITY`
      falla con `InsufficientPrivilege`; (b) `DROP POLICY`/`DROP TABLE`/`CREATE
TABLE` fallan; (c) SELECT/INSERT cross-tenant sobre `executions` funciona
      (su razón de ser). Marcar `@cross_tenant` para entrar en el gate CI.
- **Tiempo**: 3 h · **Complejidad**: s
- **Depende de**: `task_prod14_04`
- **Tests automáticos**:
  ```yaml
  - id: auto_prod14_06_a
    runtime: python-pytest
    command: "pytest tests/integration/test_db_roles_service_user.py -v -m cross_tenant"
  ```

### Fase C — Meta-test invariante de cobertura RLS (tenancy-3)

#### `task_prod14_07` — Meta-test: toda tabla con tenant_id tiene RLS completa

- [ ] **Título**: Nuevo `tests/integration/test_rls_invariant.py` que, tras
      `alembic upgrade head`, cruza `information_schema.columns` (tablas públicas
      con columna `tenant_id`) contra `pg_class.relrowsecurity`,
      `pg_class.relforcerowsecurity` y `pg_policies`: (a) toda tabla con
      `tenant_id` tiene RLS ENABLE+FORCE y ≥1 policy; (b) toda tabla SIN
      `tenant_id` está en `GLOBAL_TABLES_ALLOWLIST` (constante comentada entrada
      a entrada: `users`, `alembic_version`, `llm_providers`…) y la allowlist no
      contiene entradas muertas (tabla inexistente → fail). Marcar
      `@cross_tenant`. Documentar la allowlist y el porqué de cada excepción en
      `docs/04-reference/multi-tenancy.md` (o crearlo). Las listas manuales
      existentes (`EXPECTED_RLS_TABLES` en `tests/integration/test_migrations.py:119`)
      se conservan como tests por migración; el invariante las complementa.
- **Tiempo**: 4 h · **Complejidad**: s
- **Depende de**: `task_prod14_01` (para que las 4 junctions no necesiten
  entradas temporales en la allowlist)
- **Tests automáticos**:
  ```yaml
  - id: auto_prod14_07_a
    runtime: python-pytest
    command: "pytest tests/integration/test_rls_invariant.py -v -m cross_tenant"
  ```

### Fase D — Tabla users global: PII anclada por membership (tenancy-5)

#### `task_prod14_08` — Auditoría dirigida + test de no-fuga del directorio

- [ ] **Título**: Inventariar todos los `select(User...)` del api-server y
      clasificarlos: anclados por membership (patrón correcto de
      `assistant/tools.py:225`), gated por `require_system_admin`
      (`routers/admin.py:248`) o flujos pre-tenant (auth/scim/sso). Corregir
      cualquier desviación encontrada. Añadir test `@cross_tenant` en
      `tests/integration/test_users_directory_isolation.py`: ningún endpoint de
      contexto tenant devuelve usuarios sin membership activa en el tenant del
      caller (mínimo: lookups del asistente, `human_agents`, listados de
      miembros).
- **Tiempo**: 4 h · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod14_08_a
    runtime: python-pytest
    command: "pytest tests/integration/test_users_directory_isolation.py -v -m cross_tenant"
  ```

#### `task_prod14_09` — ADR: endurecimiento estructural de la tabla users

- [ ] **Título**: Redactar `docs/05-architecture-decisions/00XX-users-global-rls.md`
      (status: proposed) con las opciones 2 (vista `tenant_users` + RLS como
      único camino de lectura tenant) y 3 (política RLS `EXISTS` sobre
      `user_org_memberships` + excepción `app.user_id`), trade-offs (impacto en
      login/SCIM/SSO pre-tenant, coste de migración de queries) y recomendación.
      La decisión la toma un humano; la implementación es follow-up.
- **Tiempo**: 2 h · **Complejidad**: s
- **Tests automáticos**: no aplica (documento); la revisión humana del ADR es el
  gate.

### Fase E — Coherencia de unicidad y helper compartido (db-9, quality-8)

#### `task_prod14_10` — Unicidad (tenant_id, name) en teams, skills y agents

- [ ] **Título**: Migración que replica el patrón 0077 de tools
      (`uq_tools_tenant_name`, `domain.py:535`): índice único parcial
      `(tenant_id, name) WHERE deleted_at IS NULL` para `teams` (convertir
      `ix_teams_tenant_name`, `domain.py:634`, en único), `skills` y `agents`,
      con dedup previo en la misma migración según la decisión clave nº 4
      (latest wins + renombrado `-dup-{n}`). En la misma pasada de `domain.py`:
      `Document.source_size_bytes → BigInteger` y
      `Plan.created_by: Mapped[UUID | None]` (la columna ya es `nullable=True`,
      `domain.py:855`). Los endpoints de creación/edición deben mapear la
      violación de unicidad a 409 con mensaje claro (la resolución por nombre en
      la UI de asignación es prioridad declarada del operador).
- **Tiempo**: 5 h · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod14_10_a
    runtime: python-pytest
    command: "pytest tests/integration/test_tenant_name_uniqueness.py -v"
  ```

#### `task_prod14_11` — Extraer \_verify_project_visible a módulo común

- [ ] **Título**: Crear `apps/api-server/src/api_server/routers/_guards.py` con
      `verify_project_visible(session, project_id) -> Project` (docstring del
      original de `tasks.py:57`: "convierte 0 filas bajo RLS en 404 explícito",
      filtro `deleted_at IS NULL` incluido) y sustituir las 4 copias en
      `routers/tasks.py:57`, `routers/plans.py:84`, `routers/conversations.py:82`
      y `routers/incoming_webhook_configs.py:88`, unificando el detail del 404
      (`"project not found"`). Si **prod-06/prod-13** añaden el filtro
      `deleted_at` al dispatch del orchestrator, este helper es la referencia
      canónica del predicado.
- **Tiempo**: 2 h · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod14_11_a
    runtime: python-pytest
    command: "pytest tests/integration -v -k 'tasks or plans or conversations or incoming_webhook' -m cross_tenant"
  ```

## Hallazgos de auditoría cubiertos

| fid       | Severidad | Tarea(s) que lo cierran                        |
| --------- | --------- | ---------------------------------------------- |
| tenancy-1 | medium    | task_prod14_01, task_prod14_02, task_prod14_03 |
| tenancy-2 | medium    | task_prod14_04, task_prod14_05, task_prod14_06 |
| tenancy-3 | medium    | task_prod14_07                                 |
| tenancy-5 | low       | task_prod14_08, task_prod14_09                 |
| db-9      | low       | task_prod14_10                                 |
| quality-8 | low       | task_prod14_11                                 |

Coordinación con otros planes de la serie: variables de entorno de `service_user`
en el compose generado → **prod-01**; password sin default conocido → **prod-10**;
el gate `cross_tenant` de CI donde entran los tests nuevos → **prod-02**;
tenancy-4 (JWT en query param de WS) → **prod-09**; filtro `deleted_at` en el
dispatch del orchestrator (db-5) → **prod-06/prod-13**.

## Riesgos

1. **Backfill de junctions sobre datos inconsistentes**: si en alguna BD existe
   ya una fila junction cuyo padre pertenece a otro tenant (las FK ignoran RLS),
   el `NOT NULL` + backfill produciría un tenant_id "elegido". Mitigación:
   pre-check que aborta la migración con listado de filas ofensivas (task 01).
2. **Despliegues existentes no re-ejecutan el init de PostgreSQL**: `02-roles.sh`
   solo corre en contenedores nuevos; sin el script/runbook de upgrade
   (task 04), los servicios fallarían al conectar como `service_user`
   inexistente. Mitigación: script idempotente + nota de upgrade en runbook,
   verificado en el test humano 02.
3. **Permisos insuficientes descubiertos tarde**: algún camino de los workers
   podría requerir un privilegio no contemplado (p. ej. `TRUNCATE` en
   mantenimiento, locks advisory). Mitigación: la suite de integración completa
   corre contra `service_user` antes de cerrar la Fase B.
4. **El meta-test destapa tablas sin catalogar**: el invariante puede encontrar
   tablas sin `tenant_id` no previstas. Mitigación: se catalogan en la allowlist
   con justificación, o se registran como hallazgo nuevo — NO se "arreglan"
   dentro de este plan (scope creep).
5. **El dedup de nombres renombra entidades en uso**: seeds, plantillas o la UI
   pueden referenciar por nombre un team/skill/agent renombrado a `-dup-{n}`.
   Mitigación: la migración loggea cada renombrado y el test humano 03 revisa el
   resultado en un tenant con duplicados sembrados.
6. **Cambio simultáneo de rol en 4 servicios**: un error de GRANT tumbaría
   workers + orchestrator + dispatcher a la vez. Mitigación: orden de despliegue
   documentado (primero rol+grants, luego servicios) y rollback trivial
   (revertir env a `migrations_user`).

## Tests humanos del Plan

```yaml
- id: human_prod14_01
  description: "RLS efectiva en tablas junction"
  hint: "psql como app_user con app.tenant_id de un tenant B"
  checklist:
    - "SET app.tenant_id = '<tenant_B>'; SELECT * FROM agent_tools → solo filas de B"
    - "INSERT en agent_skills apuntando a un agent del tenant A → rechazado por policy"
    - "Sin app.tenant_id: SELECT sobre las 4 junctions devuelve 0 filas (fail-closed)"
    - "\\d agent_tools muestra tenant_id NOT NULL + policy agent_tools_tenant_isolation"

- id: human_prod14_02
  description: "service_user operativo y sin DDL"
  hint: "psql como service_user contra la BD de dev tras aplicar el script de upgrade"
  checklist:
    - "SELECT cross-tenant sobre executions funciona (sin SET app.tenant_id)"
    - "ALTER TABLE agents DISABLE ROW LEVEL SECURITY → ERROR: must be owner"
    - "CREATE TABLE pwned(id int) → ERROR: permission denied for schema public"
    - "docker compose dev arranca: workers/orchestrator/dispatcher procesan una ejecución end-to-end con el nuevo rol"
    - "grep migrations_user en apps/*/config.py → solo queda en migrations/env.py y docs"

- id: human_prod14_03
  description: "Invariante RLS y unicidad por tenant verificables"
  hint: "Provocar la regresión a mano y comprobar que el sistema la detecta"
  checklist:
    - "Crear (en una rama desechable) una tabla de prueba con columna tenant_id sin RLS → test_rls_invariant falla señalando la tabla"
    - "La allowlist de tablas globales está documentada en docs/04-reference/ y cada entrada tiene justificación"
    - "Crear dos teams con el mismo nombre en el mismo tenant desde la UI → segundo intento da error claro (409), no duplicado silencioso"
    - "En un tenant con duplicados pre-sembrados, tras la migración el más reciente conserva el nombre y los demás llevan sufijo -dup-N"
    - "El ADR de users (proposed) está redactado con opciones y recomendación, pendiente de decisión humana"
```

## Criterios de cierre

1. Todas las tareas con `[x]` y sus tests automáticos en verde
   (`test_junction_tenant_rls.py`, `test_db_roles_service_user.py`,
   `test_rls_invariant.py`, `test_users_directory_isolation.py`,
   `test_tenant_name_uniqueness.py` + suites de regresión citadas).
2. El gate `cross_tenant` de CI colecciona y pasa los tests nuevos (recuento > 73).
3. Los 3 tests humanos validados por un humano.
4. ADR de `users` revisado por un humano (aceptarlo o rechazarlo NO bloquea el
   cierre: es `proposed` por diseño).
5. Entrada de changelog en `docs/07-changelog/prod-14-tenancy-defensa-profundidad.md`.
6. PR del plan mergeado a `master`.
7. Frontmatter actualizado a `status: completed` con `completed_at`.

## Próximo Plan

**prod-15-gobernanza-roadmap-docs** [P2] — Gobernanza: roadmap sincerado,
CLAUDE.md real y validación humana pendiente. Cierra la serie de gobernanza
documental; no depende técnicamente de este plan y puede prepararse en paralelo.
