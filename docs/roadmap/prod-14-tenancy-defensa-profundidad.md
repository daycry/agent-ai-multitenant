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
| **Prioridad**                      | P2                                    |
| **Bloqueado por**                  | — (ninguno)                           |
| **Tiempo estimado (calendario)**   | 7-10 días                             |
| **Tiempo estimado (persona-días)** | 7                                     |
| **Rama git sugerida**              | `plan/prod-14-tenancy-defensa`        |

> **Estado**: la fuente de verdad es el frontmatter YAML de este fichero (`status:`). El campo duplicado que había en esta tabla se retiró en prod-15 (hallazgo docsroadmap-6): se había desincronizado en 22 de 51 planes.

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

- [x] **Título**: Nueva migración (down_revision `0083_llm_provider_slug`) que
      añade `tenant_id UUID` a `agent_skills`, `agent_tools`, `team_members` y
      `task_dependencies`; backfill desde el padre (`agents.tenant_id`,
      `teams.tenant_id`, `tasks.tenant_id`); `NOT NULL` + índice; y por tabla
      `ENABLE/FORCE ROW LEVEL SECURITY` + policy `{tabla}_tenant_isolation`
      idéntica a las de `_TENANT_SCOPED_TABLES` de la migración 0002. La
      migración debe **fallar ruidosamente** si detecta una fila junction cuyo
      padre e hijo pertenecen a tenants distintos (pre-check con SELECT antes del
      backfill). Downgrade real (drop policy + columna).
  - ✅ **Cerrada (2026-08-01):** el bloqueante que describía la anotación del
    2026-07-31 ya no existe. El test rezagado se corrigió en `a1091c5b`: hoy es
    `test_migrations_v2.py::test_junctions_do_have_rls_since_migration_0124`, y
    `auto_prod14_01_a` —el test que esta tarea declara— pasa **1 passed** contra
    una BD levantada desde cero con `alembic upgrade head`.
    Los seis requisitos del enunciado están verificados, no supuestos:
    `tenant_id` en las cuatro junctions con backfill desde el padre
    (`test_backfill_populated_tenant_id_from_parents`), `NOT NULL` + índice
    (`ix_{tabla}_tenant_id`, migración :268), `ENABLE`+`FORCE`+policy
    `{tabla}_tenant_isolation` (`test_rls_enabled_forced_and_policied`), el abort
    ruidoso ante filas cuyo padre e hijo son de tenants distintos
    (`test_upgrade_aborts_on_genuinely_cross_tenant_rows`, que además comprueba
    que el abort **no deja la columna a medias**) y el downgrade real.
    No es una guarda que pueda pasar en vacío: los tests son **de
    comportamiento** y llevan su control positivo en la misma aserción —«tenant B
    no ve sus propias filas» falla si el fixture no sembró nada—, más el caso
    contrario que casi nadie escribe: las filas de junction del tenant
    plataforma **siguen siendo legibles cross-tenant**
    (`test_builtin_junction_rows_stay_readable_cross_tenant`), porque una RLS que
    también tapa los builtins rompe el producto en silencio.
    Ejecutado: `tests/integration/test_junction_tenant_rls.py` +
    `test_rls_invariant.py` → **20 passed**.
- **Tiempo**: 6 h · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod14_01_a
    runtime: python-pytest
    command: "pytest tests/integration/test_migrations_v2.py -v -k junction"
  ```

#### `task_prod14_02` — ORM y caminos de escritura pueblan tenant_id

- [x] **Título**: Añadir la columna a los 4 modelos de asociación en
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

- [x] **Título**: Nuevo `tests/integration/test_junction_tenant_rls.py` marcado
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

- [x] **Título**: En `docker/postgres/init/02-roles.sh`, crear
      `service_user WITH LOGIN BYPASSRLS NOCREATEDB NOCREATEROLE`; `GRANT
SELECT/INSERT/UPDATE/DELETE ON ALL TABLES` + `USAGE/SELECT ON SEQUENCES` +
      `ALTER DEFAULT PRIVILEGES FOR ROLE migrations_user ... TO service_user`;
      **sin** `CREATE` en el schema ni ownership. Como el init solo corre en
      contenedores nuevos, añadir `docker/postgres/upgrade/2026xx-service-user.sh`
      (o equivalente documentado en runbook) idempotente para BD existentes.
      Password vía env `SERVICE_USER_PASSWORD` con placeholder solo-dev
      (la eliminación de defaults conocidos es de **prod-10**).
  - ⏳ **Pendiente (2026-08-01) — arranque limpio arreglado; falta declararla en
    el compose:** el arranque limpio ya honra `SERVICE_USER_PASSWORD` vía
    `docker/postgres/init/05-service-role-password.sh` (14/14 en
    `test_db_roles_service_user.py`). Va en un `.sh` APARTE y no dentro del
    `04-service-role.sql` porque ese fichero tiene que seguir siendo SQL plano:
    es el mismo artefacto que aplica el script de upgrade a una BD viva y el que
    LEE el test, y el SQL plano no puede leer variables de entorno. El test fija
    además el orden alfabético (`05-` después de `04-`), que es el contrato del
    entrypoint de la imagen de PostgreSQL. Por qué importa: el literal de dev
    está en el repositorio, y es la contraseña de un rol **BYPASSRLS** — la llave
    que se salta la RLS de todos los tenants. **Falta**, fuera de este carril,
    declarar `SERVICE_USER_PASSWORD` en `docker/.env.example` y pasarla al
    servicio `postgres` del compose.
  - ⏳ **Re-verificado el 2026-08-01: el hueco sigue donde decía, y ahora está
    medido.** `SERVICE_USER_PASSWORD` aparece en **cuatro** ficheros de
    `docker/postgres/` (init `05-service-role-password.sh`, `04-service-role.sql`,
    upgrade `20260730-service-user.sh` y su README) y en **cero** sitios de
    `docker/.env.example` y `docker/docker-compose.yml`. El compose sí pasa
    `MIGRATIONS_USER_PASSWORD` y `APP_USER_PASSWORD` al servicio `postgres`, ambas
    con la forma `${VAR:?set VAR in docker/.env}` —el patrón exacto que falta
    replicar—, así que hoy el init cae **siempre** al literal
    `changeme-service-dev-only`. Un arranque limpio crea el rol BYPASSRLS con la
    contraseña que está escrita en el repositorio, y nada avisa: el script lo
    imprime por `stderr` del contenedor de Postgres, donde nadie mira.
    Los tests del rol **sí pasan** (`test_db_roles_service_user.py`, dentro de los
    26 passed de los tres ficheros de este plan ejecutados hoy), y eso es lo que
    hace peligrosa a esta casilla: el contrato roto está entre el init y el
    compose, no dentro de la BD, así que ningún test del plan lo ve.
    **NO lo arreglo**: `docker/**` está fuera de la propiedad de este carril y hay
    otros agentes escribiendo en paralelo. Son dos líneas y están dictadas:
    `SERVICE_USER_PASSWORD=changeme-service-dev-only` en `docker/.env.example`
    junto a las otras dos, y
    `SERVICE_USER_PASSWORD: ${SERVICE_USER_PASSWORD:?set SERVICE_USER_PASSWORD in docker/.env (cp docker/.env.example docker/.env)}`
    en el `environment:` del servicio `postgres`.
  - ✅ **Cerrada (2026-08-02): las dos líneas están puestas**, exactamente donde
    las dos pasadas anteriores las dejaron dictadas —`docker/docker-compose.yml`
    (servicio `postgres`) y `docker/.env.example`—, más su fila en
    `docs/04-reference/mandatory-env-vars.md`, que era la tercera punta del
    contrato: si el compose exige una variable y el catálogo no la lista, el
    `cp .env.example .env` documentado deja el stack sin arrancar.
    - **Ejecutado**: `pytest tests/integration/test_db_roles_service_user.py`
      contra el Postgres del stack (`TEST_PG_DB_NAME=agentic_platform_test_supplychain`,
      `TEST_REDIS_URL=redis://localhost:6379/11`) → **14 passed**.
    - **Y una guarda nueva para la costura, porque es donde vivía el defecto**:
      `tests/security/test_service_user_password_is_wired.py` (6 tests). Los tests
      del rol pasaban con el hueco abierto —el contrato roto estaba ENTRE el init
      y el compose, no dentro de la base de datos—, así que la guarda mira las dos
      puntas a la vez: que algún `.sh` del init consuma la variable (no-vacuo),
      que el compose se la pase, que sea `${VAR:?…}` y no `${VAR:-…}` ni `${VAR}`
      a secas, que `.env.example` traiga el valor de dev, y que **las tres
      contraseñas de rol se declaren igual** — la asimetría entre ellas es
      literalmente cómo se coló este hueco.
    - **Defecto del arnés, anotado y no silenciado**: el comando declarado en
      `auto_prod14_04_a` lleva `-k grants` y **no selecciona ni un test**
      (`14 deselected`, pytest sale con **exit 5**). Un selector que no casa es
      indistinguible de una suite en verde para quien mire solo el código de
      salida. El comando bueno es el fichero entero, sin `-k`.
- **Tiempo**: 4 h · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod14_04_a
    runtime: python-pytest
    command: "pytest tests/integration/test_db_roles_service_user.py -v -k grants"
  ```

#### `task_prod14_05` — Migrar los 4 servicios a service_user

- [x] **Título**: Cambiar los defaults de `database_url` en
      `apps/workers/src/workers/config.py:35`,
      `apps/orchestrator/src/orchestrator/config.py:51`,
      `apps/notification-dispatcher/src/notification_dispatcher/config.py:52` y
      `admin_database_url` en `apps/api-server/src/api_server/config.py:31` de
      `migrations_user` a `service_user`; actualizar descripciones ("BYPASSRLS
      sin DDL"), `docker-compose.dev.yml`, `.env.example` y los docs de
      referencia. `migrations_user` queda referenciado únicamente por Alembic
      (`migrations/env.py`). Anotar en el plan **prod-01** que el
      `compose_generator` del installer debe emitir la nueva variable.
  - ⏳ **Pendiente (2026-07-31):** los defaults de `database_url` de los 4 servicios ya apuntan a `service_user` (verificado en los cuatro `config.py`), pero faltan `SERVICE_USER_PASSWORD` en `docker/.env.example` y el compose, la nota en **prod-01** sobre el `compose_generator`, y el test que cita este bloque (`tests/integration/test_execution_persistence.py`) no existe.
  - ⏳ **Re-verificado el 2026-08-01: los tres huecos siguen ahí, ninguno es
    implementación pendiente de este carril.**
    1. **Defaults: hechos.** `service_user` es el default en
       `workers/config.py:46`, `orchestrator/config.py:60`,
       `notification_dispatcher/config.py:64` y el `admin_database_url` de la
       api-server, los cuatro con la descripción «BYPASSRLS but NO DDL» que la
       tarea pedía.
    2. **Compose y `.env.example`: el mismo hueco que `task_prod14_04`**, medido
       arriba. `docker/**` está fuera de la propiedad de este carril.
    3. **`auto_prod14_05_a` cita un fichero que no existe**:
       `tests/integration/test_execution_persistence.py` no está en el repo (sí
       `test_admin_rbac.py`). El comando declarado, tal cual, **no puede salir en
       verde** — falla en la recolección, que es el peor rojo posible porque no
       distingue «la feature está rota» de «el arnés apuntaba a la nada». Es la
       misma clase de defecto que bloqueó `task_prod14_01` hasta hoy: el test que
       el plan nombra dejó de coincidir con el que existe. Corregirlo pide decidir
       **qué** cubre el hueco (¿un test de que los 4 servicios conectan como
       `service_user` y siguen escribiendo?), y ese test vive contra el stack, no
       contra la api-server.
  - ⏳ **2026-08-02: queda UNA cosa, y no es de este carril.** El hueco 2 (compose
    y `.env.example`) está **cerrado** — ver `task_prod14_04`, con su guarda nueva
    y los 14 tests de integración ejecutados. Los defaults de los cuatro
    `config.py` se re-verificaron hoy uno a uno: los cuatro dicen
    `postgresql+asyncpg://service_user:…` (`workers/config.py:46`,
    `orchestrator/config.py:60`, `notification_dispatcher/config.py:64`,
    `api_server/config.py:84`). `docker-compose.dev.yml` no aplica: ese overlay
    solo publica puertos, no declara `environment` de postgres.
    **Lo único pendiente es la nota en el plan `prod-01`**: el `compose_generator`
    del instalador tiene que emitir `SERVICE_USER_PASSWORD` en el servicio
    `postgres` que genera, o un despliegue de producción **nuevo** repetirá
    exactamente el defecto que hoy se cierra en el compose canónico — crear el rol
    BYPASSRLS con el literal que está en este repositorio. No lo escribo yo porque
    `docs/roadmap/prod-01-*.md` y `apps/installer/**` son de otro carril y hay
    cuatro escribiendo en paralelo. El requisito queda registrado aquí y en
    `docs/04-reference/mandatory-env-vars.md` para que no se pierda.
  - ✅ **Cerrada (2026-08-10): lo que faltaba era la guarda, no el cambio.** Los
    cuatro defaults llevan desde el 2026-07-31 apuntando a `service_user`
    (`workers/config.py:46`, `orchestrator/config.py:60`,
    `notification_dispatcher/config.py:64`, `api_server/config.py:84`), el compose
    y `.env.example` se cerraron en `task_prod14_04`, y `migrations_user` ya solo
    aparece en `migrations/env.py` y en **dos** campos que necesitan al dueño del
    esquema de verdad: `backup_database_url` (`pg_dump` de la copia completa) y
    `restore_required_db_role` (`pg_restore --clean` deja el ownership en el rol
    que conecta). No son restos: sin rol admin el volcado sale incompleto y el
    restore deja el esquema inservible.
    - **La guarda que faltaba**: `tests/security/test_service_role_is_the_runtime_default.py`
      (10 tests, en verde). Cubre exactamente la costura que ningún test veía:
      `test_db_roles_service_user.py` comprueba los privilegios del rol DENTRO de
      PostgreSQL y **pasa igual de verde** si mañana alguien revierte un
      `config.py` a `migrations_user` — lo que se degrada entonces no es la base
      de datos, es quién se conecta a ella. La guarda lee el `default=` por AST
      (no la prosa: las descripciones nombran `migrations_user` a propósito para
      contar de dónde se viene) y exige que cada superviviente esté en una lista
      corta y **explique en su descripción por qué necesita DDL**. Lleva su
      contrapunto: si `migrations_user` desapareciera del `env.py` de Alembic,
      también sale rojo — o las migraciones dejaron de correr como el dueño, o
      alguien "limpió" el rol creyéndolo un resto.
    - **Rojo verificado por mutación**: revertido el default de
      `workers/config.py:46` a `migrations_user` → 2 rojos nombrando el servicio;
      restaurado → 10 passed.
    - **Arnés corregido**: `auto_prod14_05_a` citaba
      `tests/integration/test_execution_persistence.py`, un fichero **que no
      existe** — tal cual fallaba en la recolección, que es el peor rojo posible
      porque no distingue «la feature está rota» de «el arnés apuntaba a la
      nada». El comando de abajo es el que se ha ejecutado.
    - **Residuo declarado, y no es código**: falta la nota de coordinación en
      `docs/roadmap/prod-01-despliegue-ejecutable.md` — el `compose_generator` del
      instalador debe emitir `SERVICE_USER_PASSWORD` en el servicio `postgres`
      que genera, o un despliegue de producción **nuevo** repetirá el defecto que
      el compose canónico ya cerró: crear el rol BYPASSRLS con el literal que
      está en este repositorio. No se escribe aquí porque `prod-01` es de otro
      carril; el requisito está registrado en este bloque y en
      `docs/04-reference/mandatory-env-vars.md`, que es el catálogo que lee quien
      genera compose.
- **Tiempo**: 4 h · **Complejidad**: m
- **Depende de**: `task_prod14_04`
- **Tests automáticos**:
  ```yaml
  - id: auto_prod14_05_a
    runtime: python-pytest
    command: "pytest tests/security/test_service_role_is_the_runtime_default.py tests/security/test_service_user_password_is_wired.py -v"
  ```

#### `task_prod14_06` — Test de privilegios: service_user no puede tocar DDL ni RLS

- [x] **Título**: `tests/integration/test_db_roles_service_user.py`: conectado
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

- [x] **Título**: Nuevo `tests/integration/test_rls_invariant.py` que, tras
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

- [x] **Título**: Inventariar todos los `select(User...)` del api-server y
      clasificarlos: anclados por membership (patrón correcto de
      `assistant/tools.py:225`), gated por `require_system_admin`
      (`routers/admin.py:248`) o flujos pre-tenant (auth/scim/sso). Corregir
      cualquier desviación encontrada. Añadir test `@cross_tenant` en
      `tests/integration/test_users_directory_isolation.py`: ningún endpoint de
      contexto tenant devuelve usuarios sin membership activa en el tenant del
      caller (mínimo: lookups del asistente, `human_agents`, listados de
      miembros).
  - ✅ **Cerrada (2026-08-01):** `tests/integration/test_users_directory_isolation.py`
    existe y pasa 3/3 marcado `@cross_tenant`. Cubre las dos superficies de
    contexto-tenant que devuelven personas —`GET /human-agents/assignable-users`
    y la resolución de usuario del asistente (`_resolve_tenant_user`)— y cada una
    desde dos ángulos, porque uno solo no basta: (a) el listado de A no contiene
    a nadie de B, y (b) **la búsqueda por el email EXACTO de alguien de B no lo
    encuentra**. Lo segundo es lo que de verdad importa: un listado puede estar
    bien filtrado y aun así el lookup dirigido delatar que una cuenta existe, que
    es enumeración del directorio de la organización. Se afirma sobre el EMAIL en
    el cuerpo, no solo sobre el id — un id filtrado es feo, un correo filtrado es
    un incidente de protección de datos. Los dos tests llevan su control de
    no-pasar-en-vacío (el propio miembro del tenant SÍ aparece / SÍ resuelve).
- **Tiempo**: 4 h · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod14_08_a
    runtime: python-pytest
    command: "pytest tests/integration/test_users_directory_isolation.py -v -m cross_tenant"
  ```

#### `task_prod14_09` — ADR: endurecimiento estructural de la tabla users

- [x] **Título**: Redactar `docs/05-architecture-decisions/00XX-users-global-rls.md`
      (status: proposed) con las opciones 2 (vista `tenant_users` + RLS como
      único camino de lectura tenant) y 3 (política RLS `EXISTS` sobre
      `user_org_memberships` + excepción `app.user_id`), trade-offs (impacto en
      login/SCIM/SSO pre-tenant, coste de migración de queries) y recomendación.
      La decisión la toma un humano; la implementación es follow-up.
  - ⏳ **Pendiente (2026-07-31):** el documento existe y es completo (`docs/05-architecture-decisions/0137-users-global-rls.md`, con inventario, opciones y recomendación), pero nació `status: accepted` decidiendo por su cuenta cuando este plan lo pedía `proposed`: falta la ratificación del operador, que es el gate declarado de esta tarea.
  - ⏳ **Confirmado el 2026-08-01, con el dato que faltaba: `deciders: [claude-code]`.**
    El frontmatter del 0137 dice `status: accepted` y `date: 2026-07-30`, y el
    campo `deciders` nombra al agente, no a una persona. La tanda de firmas del
    2026-08-01 (`95fc7fbc`, «los ocho ADR pendientes, firmados») **no lo incluyó**,
    precisamente porque ya figuraba como `accepted` y por tanto no aparecía en
    ninguna lista de pendientes: la etiqueta equivocada lo hizo invisible al
    proceso que lo habría firmado.
    Por eso la casilla sigue abierta y **no se cierra en negativo**: el entregable
    del enunciado (redactar el ADR con opciones, trade-offs y recomendación) está
    hecho, pero el gate que la propia tarea declara —«la revisión humana del ADR es
    el gate»— no se ha producido, y marcarla sería dar por firmada una decisión de
    arquitectura sobre dónde vive el aislamiento del directorio de personas.
    **Lo que necesita el operador es una firma, no trabajo**; y conviene que sepa
    qué está ratificando: un ADR que dice `accepted` desde el 2026-07-30 sin que
    él lo haya visto. **No le cambio el status a `proposed`** por mi cuenta —sería
    repetir el error en la dirección contraria, y `CONTINUE_HERE.md` ya describe
    el 0137 como técnico y aceptado.
    Nota: el criterio de cierre 4 de este plan dice que la revisión del ADR **no
    bloquea el cierre del plan**. Bloquea esta casilla, no el plan.
  - ⏳ **Re-verificado el 2026-08-02, sin cambios: `status: accepted`,
    `date: 2026-07-30`, `deciders: [claude-code]`.** Sigue **abierta y sigue
    siendo una firma, no trabajo**. Se deja escrito qué se le pide exactamente al
    operador, para que sea barato: leer
    `docs/05-architecture-decisions/0137-users-global-rls.md` y, si está de
    acuerdo con la recomendación, sustituir `claude-code` por su nombre en
    `deciders` y poner la fecha de hoy. Si NO está de acuerdo, bajarlo a
    `proposed` y decirlo — pero no lo bajo yo por mi cuenta: sería repetir el
    error del 2026-07-30 en la dirección contraria, y `CONTINUE_HERE.md` ya
    describe el 0137 como técnico y aceptado.
  - ✅ **Cerrada en NEGATIVO (2026-08-10): la premisa de la tarea es falsa — aquí
    no hay decisión de producto que firmar.** Leído el ADR entero, no una
    recomendación entre tres alternativas viables: las opciones 2 y 3 se descartan
    por una **imposibilidad**, y está bien argumentada en una línea —
    > «La tabla de identidades tiene que ser legible ANTES de que haya identidad.
    > Una RLS que dependa de quién eres no puede gobernar la consulta que averigua
    > quién eres.»
    > El login arranca con `SELECT … FROM users WHERE email = $1`, sin
    > `app.tenant_id` (el tenant se resuelve después, con las membresías) y sin
    > `app.user_id` (que es lo que esa consulta va a averiguar). La «excepción para
    > el propio `app.user_id`» de la opción 3 no salva el caso porque en el paso
    > donde hace falta ese GUC aún no existe; y la opción 2 (vista) solo sería
    > invariante con `REVOKE SELECT ON users`, que rompe el login. El propio ADR lo
    > remata con el dato que lo convierte en verificable y no en opinión: la opción
    > 3 **empeora la postura de seguridad real** —empuja más superficie del router
    > de autenticación al engine BYPASSRLS— mientras mejora la métrica «tablas con
    > RLS».
    - **Por eso NO lo bajo a `proposed`**: `proposed` significa «hay una elección
      abierta», y no la hay. Es una decisión técnica del mismo tipo que el ADR
      0147, que nació `accepted` por ser toolchain puro. Lo que pedía este carril
      era bajarlo **si pedía decisión de producto**; no la pide.
    - **Lo que sí queda para el operador es cosmético y de una línea**: el
      frontmatter dice `deciders: [claude-code]`. Si está de acuerdo, sustituir
      por su nombre y poner la fecha. Es trazabilidad, no una decisión — y por eso
      no bloquea esta casilla, coherente con el **criterio de cierre 4** de este
      mismo plan, que dice literalmente que la revisión del ADR no bloquea.
    - **El seguimiento real que el ADR deja escrito, y que NO es paperwork**: la
      **guarda estática sobre `select(User…)`** — un test que recorra el AST del
      api-server y exija que cada consulta esté (a) unida a
      `UserOrganizationMembership`, (b) filtrada por `User.id`, o (c) en una
      allowlist de módulos pre-tenant/admin (`auth`, `scim`, `sso`, `admin`,
      `seeds`, `mfa`), con aserción de «vio al menos N consultas» para que no
      envejezca en vacío. Sin ella, la conclusión del ADR («hoy no hay
      desviación») se sostiene sobre **un inventario con fecha**: el del
      2026-07-30. No se implementa aquí porque el ADR la declara follow-up y
      porque recorre `api_server` entero, con otros carriles escribiendo dentro.
      Complementa —no sustituye— al test de endpoint de `task_prod14_08`, que ya
      está y pasa: aquél mira lo que sale por la API, éste miraría lo que se
      escribe en el código.
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
  - ⏳ **Pendiente (2026-08-01) — el 409 ya está; el dedup sigue desviado:** los
    tres routers usan `flush_or_conflict` (`routers/_integrity.py`) en creación
    Y edición, así que un nombre duplicado devuelve **409 con código de dominio**
    (`duplicate_team_name` / `duplicate_skill_name` / `duplicate_agent_name`) en
    vez del 500 que salía. Verificado 9/9 en el fichero que este bloque cita,
    `tests/integration/test_tenant_name_uniqueness.py`, escrito para esto: RED
    con 3 fallos (los tres 500), GREEN tras el cambio. Cubre además los dos
    contornos que un test ingenuo se dejaría: el mismo nombre en OTRO tenant se
    acepta, y el nombre de una fila soft-borrada se puede reutilizar (el índice
    es parcial sobre `deleted_at IS NULL`) — un 409 de más ahí sería una
    regresión funcional silenciosa. **Sigue abierto** el dedup: la 0126 ya
    soft-borró a los perdedores en vez de renombrarlos `-dup-{n}`, contra la
    decisión clave nº 4. No se puede "arreglar" con una migración nueva sin
    inventarse qué filas fueron víctimas de aquélla: es una decisión del
    operador (aceptar la desviación o restaurar a mano en los entornos vivos).
  - ⏳ **Re-ejecutado el 2026-08-01: `auto_prod14_10_a` sigue verde** (dentro de
    los 26 passed de `test_db_roles_service_user.py` +
    `test_tenant_name_uniqueness.py` + `test_users_directory_isolation.py`). Lo
    que queda abierto no es código: es qué hacer con los datos que la 0126 ya
    soft-borró en los entornos vivos, y eso solo lo puede decidir quien pueda
    mirarlos. Concretando la decisión para que sea barata de tomar: (a) aceptar la
    desviación y corregir la decisión clave nº 4 de este plan para que diga
    soft-delete —lo que la migración hace de verdad—, o (b) inventariar en cada
    entorno vivo las filas soft-borradas por la 0126 y restaurarlas con el sufijo
    `-dup-{n}`. La (a) cuesta una edición de este fichero; la (b), una ventana de
    mantenimiento por entorno.
  - ⏳ **2026-08-02: sigue siendo (a) o (b), y sigue siendo del operador.** Se
    añade el dato que hace la decisión barata: la consulta que dice cuántas filas
    hay realmente en juego, para que no haya que elegir a ciegas. En cada entorno
    vivo, contra la BD de la plataforma:
    ```sql
    SELECT 'teams' AS tabla, tenant_id, name, COUNT(*)
      FROM teams  WHERE deleted_at IS NOT NULL GROUP BY 1,2,3 HAVING COUNT(*) > 0
    UNION ALL SELECT 'skills', tenant_id, name, COUNT(*)
      FROM skills WHERE deleted_at IS NOT NULL GROUP BY 1,2,3
    UNION ALL SELECT 'agents', tenant_id, name, COUNT(*)
      FROM agents WHERE deleted_at IS NOT NULL GROUP BY 1,2,3;
    ```
    Si sale vacío o solo con borrados intencionales, **(a) es gratis** y basta con
    corregir la decisión clave nº 4 de este plan para que diga lo que la migración
    hace de verdad. **No la ejecuto yo**: la orden permanente de no tocar entornos
    vivos sin verificación previa del operador está por encima de cerrar una
    casilla.
  - ⏳ **2026-08-10 — dos cosas: NO queda migración por escribir, y la decisión
    (a) es casi con seguridad gratis.**
    - **No hay migración pendiente.** Este carril venía con el encargo de
      «describir con precisión la migración que replica el patrón 0077 para que la
      escriba el dueño de migraciones». Verificado: **ya está escrita y aplicada**,
      es la `20260730_0126_perf_indexes_uniqueness.py`, y hace las tres cosas del
      enunciado — los índices únicos parciales
      (`uq_teams_tenant_name_live`, `uq_skills_tenant_name_live`,
      `uq_agents_tenant_name_global_live` **+**
      `uq_agents_tenant_project_name_live`), el `ALTER TYPE` de
      `documents.source_size_bytes` a `BIGINT` (verificado en
      `db/knowledge.py:154`) y `Plan.created_by: Mapped[UUID | None]`
      (`db/domain.py:1056`). **Nada que encargar al carril de migraciones.**
    - **Y con una mejora sobre lo que pedía el plan que conviene no deshacer**: en
      `agents` no puso un `(tenant_id, name)` a secas sino **dos índices con
      predicados disjuntos**, separando los agentes globales de los
      `project_local`. El plan pedía el índice simple, y la migración explica por
      qué habría sido un error: habría exigido soft-borrar la mitad de las filas
      del `global_tenant_template` y **roto el fork de agentes** desde ese momento.
    - **La decisión (a)/(b) sigue siendo del operador, pero el precio está
      medido**: el docstring de la propia 0126 dice que con el par de índices los
      duplicados reales de `agents` bajan «de 10 grupos a **0** (medido), así que
      el dedup previo **no toca ninguna fila en la práctica** y queda como red de
      seguridad para datos sucios». O sea: la desviación respecto de la decisión
      clave nº 4 (soft-delete en vez de renombrar `-dup-{n}`) es real **en el
      código** y probablemente **inocua en los datos**. Falta confirmarlo en
      `teams` y `skills` con la consulta de solo-lectura de arriba. Si sale vacío,
      **(a) es gratis**: una edición de la decisión clave nº 4 de este fichero para
      que diga lo que la migración hace de verdad.
- **Tiempo**: 5 h · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod14_10_a
    runtime: python-pytest
    command: "pytest tests/integration/test_tenant_name_uniqueness.py -v"
  ```

#### `task_prod14_11` — Extraer \_verify_project_visible a módulo común

- [x] **Título**: Crear `apps/api-server/src/api_server/routers/_guards.py` con
      `verify_project_visible(session, project_id) -> Project` (docstring del
      original de `tasks.py:57`: "convierte 0 filas bajo RLS en 404 explícito",
      filtro `deleted_at IS NULL` incluido) y sustituir las 4 copias en
      `routers/tasks.py:57`, `routers/plans.py:84`, `routers/conversations.py:82`
      y `routers/incoming_webhook_configs.py:88`, unificando el detail del 404
      (`"project not found"`). Si **prod-06/prod-13** añaden el filtro
      `deleted_at` al dispatch del orchestrator, este helper es la referencia
      canónica del predicado.
  - ✅ **Cerrada (2026-08-01):** las cuatro copias retiradas y sus **17 llamadas**
    apuntando ya al módulo canónico (`tasks.py` 6, `plans.py` 6,
    `conversations.py` 3, `incoming_webhook_configs.py` 2 — esta última a la
    variante `_id`). Lo que faltaba de verdad no era el módulo, era que alguien
    lo llamara: el patrón «mecanismo entregado, cero llamantes» del apartado 5 de
    `verificar-antes-de-implementar.md`. Para que no vuelva, la guarda estática
    de `tests/unit/test_project_visibility_guard.py` recorre los 69 routers con
    AST y suspende si CUALQUIERA redefine `*verify_project_visible` — y lleva
    `assert len(sources) >= 20` para no pasar en vacío el día que el
    descubrimiento deje de encontrar ficheros.
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
