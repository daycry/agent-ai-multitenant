---
title: Multi-tenancy — cobertura RLS, roles de BD y excepciones
docs_language: es
audience: backend-dev, architect, security
updated: 2026-08-19
---

# Multi-tenancy: cobertura RLS y excepciones

Referencia del **estado real** del aislamiento multi-tenant a nivel de base de
datos: qué protege la RLS, con qué rol conecta cada proceso, y la lista completa
de excepciones con su justificación.

Este documento no es aspiracional. Su contenido está **verificado por
`tests/integration/test_rls_invariant.py`**, que descubre el esquema en el
catálogo de PostgreSQL y exige que coincida con lo de aquí. Si divergen, el test
se pone rojo. La fuente de verdad de las allowlists son las constantes de ese
fichero; esta página es su explicación.

## El invariante

> Toda tabla con una columna `*tenant_id` tiene `ENABLE ROW LEVEL SECURITY`,
> `FORCE ROW LEVEL SECURITY` y al menos una policy que referencia
> `current_setting('app.tenant_id')`. Toda tabla sin columna de tenant está en la
> allowlist de globales, con su porqué.

La forma canónica de la policy (migraciones 0001/0002/0004 y todas las
posteriores):

```sql
ALTER TABLE t ENABLE ROW LEVEL SECURITY;
ALTER TABLE t FORCE ROW LEVEL SECURITY;
CREATE POLICY t_tenant_isolation ON t FOR ALL
  USING      (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
  WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
```

`NULLIF(..., '')` la hace **fail-closed**: sin `app.tenant_id`, la comparación es
`tenant_id = NULL` → falso → cero filas. El `WITH CHECK` es lo que impide
_escribir_ una fila con el tenant de otro (la migración 0002 solo tenía `USING`,
y la 0004 lo corrigió para `agents`).

## Roles de base de datos

| Rol               | RLS         | DDL | Lo usa                                                                        |
| ----------------- | ----------- | --- | ----------------------------------------------------------------------------- |
| `migrations_user` | BYPASSRLS   | sí  | **solo Alembic** (`migrations/env.py`). Es el propietario del esquema         |
| `service_user`    | BYPASSRLS   | no  | workers, orchestrator, notification-dispatcher, engine admin de la api-server |
| `app_user`        | NOBYPASSRLS | no  | api-server (sesión de request; la RLS se aplica de verdad)                    |

`service_user` se crea con `docker/postgres/init/04-service-role.sql` (arranque
limpio) o con `docker/postgres/upgrade/20260730-service-user.sh` (base de datos
viva). Su postura la verifica `tests/integration/test_db_roles_service_user.py`,
que **lee el propio fichero SQL** en vez de reescribir el DDL.

> **Estado**: el rol y sus privilegios están entregados y verificados. El cambio
> de los `database_url` de los cuatro servicios de `migrations_user` a
> `service_user` (`apps/*/config.py`) está **pendiente**. Orden de despliegue
> obligatorio: primero el rol, después los servicios.

Por qué `service_user` es BYPASSRLS: es su razón de ser. Un worker procesa la
ejecución del tenant que le toque sin un `app.tenant_id` de request al que
atarse. Lo que la separación le quita es el DDL: ya no puede
`ALTER TABLE … DISABLE ROW LEVEL SECURITY`, que es lo que un servicio
comprometido haría primero.

## Tablas de unión (migración 0124)

`agent_skills`, `agent_tools`, `team_members` y `task_dependencies` no tenían
`tenant_id` —la migración 0002 las dejó fuera razonando que «dependen de la
visibilidad del padre vía ON DELETE CASCADE»—. Eso valía para el borrado en
cascada y para nada más: sin columna no hay policy, y las comprobaciones de
clave ajena **se ejecutan como el propietario e ignoran la RLS**, así que la FK
tampoco protegía la escritura.

La 0124 les añade `tenant_id` denormalizado con tres piezas:

1. **Backfill desde el padre propietario** (agente / equipo / tarea), con un
   pre-check que ABORTA la migración si encuentra filas cuyo padre e hijo son de
   tenants distintos sin ser catálogo de plataforma.
2. **Un trigger `BEFORE INSERT OR UPDATE` por tabla que DERIVA `tenant_id` del
   padre** y rechaza cualquier valor contradictorio. Es la pieza que cubre lo que
   la policy no puede: los roles BYPASSRLS, para los que ninguna policy mira. Los
   llamantes no pasan `tenant_id`; el trigger lo estampa.
3. Policy de aislamiento canónica + una policy `{tabla}_builtin_read`
   (SELECT-only) en las tres junctions que cuelgan del catálogo de plataforma.

Esa última pieza no es cosmética: `_clone_agent_capabilities` (fork de agentes) y
`_fork_team_deep` (adopción de equipos) LEEN las filas de unión de los built-in
de `PLATFORM_TENANT_ID` desde la sesión de otro tenant. Con una policy estricta
devolverían cero filas y el fallo sería **silencioso**: forkear un agente
built-in daría un agente sin herramientas y adoptar un equipo built-in daría un
equipo vacío.

`task_dependencies` no lleva `builtin_read` (no hay catálogo de tareas) y su
trigger exige además que **las dos** tareas sean del mismo tenant: una
dependencia cross-tenant es un DAG imposible.

## Excepciones: tablas globales

Sin ninguna columna de tenant. Cada una está en `GLOBAL_TABLES_ALLOWLIST`.

| Tabla                                                                                                                | Por qué es global                                                                                                                                                            |
| -------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `alembic_version`                                                                                                    | Contabilidad interna de Alembic.                                                                                                                                             |
| `organizations`                                                                                                      | **Es** el tenant: se aísla con `id = app.tenant_id` (`org_self_only`), no con `tenant_id = …`.                                                                               |
| `users`                                                                                                              | Directorio global de identidades; el login es pre-tenant. Decisión razonada en el **ADR 0137**.                                                                              |
| `platform_settings`                                                                                                  | Ajustes de plataforma (System Admin).                                                                                                                                        |
| `llm_providers`                                                                                                      | Catálogo cerrado de proveedores (ADR 0021). Las credenciales por tenant no viven aquí.                                                                                       |
| `model_prices`, `price_sync_audit`, `exchange_rates`                                                                 | Tarifas y tipos de cambio: mismo dato para todos los tenants.                                                                                                                |
| `sso_configurations`                                                                                                 | Global por decisión de la migración 0076: el descubrimiento de proveedor SSO ocurre antes del tenant.                                                                        |
| `cortex_identity`, `cortex_identity_history`, `cortex_turns`, `cortex_affect_snapshots`, `cortex_curiosity_pursuits` | Córtex (ADR 0074): pertenecen al usuario dueño, no a un tenant. **No** son «globales sin protección»: se aíslan por el eje owner, con RLS propia (ver la sección siguiente). |

## Excepciones: aislamiento por PERSONA (eje owner)

No toda tabla que no lleva `tenant_id` es global. Hay una familia cuyo dato
pertenece a **alguien**, no a un tenant, y su aislamiento es
`owner_user_id = app.user_id`. El [**ADR 0156**](../05-architecture-decisions/0156-aislamiento-estructural-del-cortex.md)
fija la regla: que el eje no sea el tenant **no exime de RLS**, obliga a ponerla
sobre el eje que sí es.

La forma canónica (migración 0001 para `sessions`, 0125 y 0140 para el córtex):

```sql
ALTER TABLE t ENABLE ROW LEVEL SECURITY;
ALTER TABLE t FORCE ROW LEVEL SECURITY;
CREATE POLICY t_owner_only ON t FOR ALL
  USING      (owner_user_id = NULLIF(current_setting('app.user_id', true), '')::uuid)
  WITH CHECK (owner_user_id = NULLIF(current_setting('app.user_id', true), '')::uuid);
```

| Tabla                       | De quién es el dato                                        | Policy desde |
| --------------------------- | ---------------------------------------------------------- | ------------ |
| `sessions`                  | La sesión es de la PERSONA, que puede tener varios tenants | 0001         |
| `cortex_conversations`      | Los hilos del córtex del System Owner                      | 0125         |
| `cortex_turns`              | El texto literal de esas conversaciones                    | 0140         |
| `cortex_identity`           | La identidad evolutiva del córtex (ADR 0077)               | 0140         |
| `cortex_identity_history`   | Su versionado append-only                                  | 0140         |
| `cortex_affect_snapshots`   | La serie temporal del motor afectivo (ADR 0075)            | 0140         |
| `cortex_curiosity_pursuits` | Lo que el córtex investiga por su cuenta (ADR 0078)        | 0140         |

Dos de ellas —`sessions` y `cortex_conversations`— **sí** tienen columna
`tenant_id`, así que además entran por el invariante nº 1 y aparecen en
`POLICY_WITHOUT_TENANT_GUC_ALLOWLIST` con el porqué de que su policy no cite
`app.tenant_id`. En `cortex_conversations` ese `tenant_id` es el **discriminante
físico** que la memoria del owner necesita (`memory_entries` lo exige `NOT NULL`),
no un eje de autorización; el ADR 0156 descarta expresamente renombrarlo o
borrarlo, y explica por qué.

**Nunca la policy de tenant sobre estas tablas.** Sería a la vez más permisiva
(pertenecer al tenant bastaría para leer la mente privada del owner) y
funcionalmente rota: `open_tenant_session` fija `app.tenant_id` al tenant
**elegido en la request**, así que el owner entrando con otro contexto perdería
su historial en silencio. Lo vigila
`test_rls_invariant.py::test_the_cortex_is_not_isolated_by_tenant`.

Con los roles de hoy estas policies son **inertes**: todos los caminos del córtex
conectan con `migrations_user` o `service_user`, que son BYPASSRLS, y BYPASSRLS
gana a `FORCE`. Lo que cierran es el camino `app_user`, que tiene DML sobre todas
ellas por los default privileges de `02-roles.sh` y hasta la 0140 no encontraba
NADA que se le opusiera. El día que los servicios dejen de ser BYPASSRLS hay que
**cablear el GUC `app.user_id`**, no relajar las policies; hay un test que se pone
rojo para recordarlo.

## Excepciones: columna de tenant sin RLS

| Tabla                 | Por qué                                                                                                                                                                                                            |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `marketplace_sources` | Decisión declarada en el modelo: una source es un endpoint de registro de plataforma. `owner_tenant_id` es NULLABLE y solo marca el catálogo privado de un tenant; la visibilidad la resuelve la capa de servicio. |

## Los huecos que hubo, y cómo se cerraron

La primera ejecución del invariante (2026-07-30) destapó cuatro desviaciones
reales. Se anotaron en un ratchet (`KNOWN_RLS_GAPS_*`) para no hacer scope creep,
y **ese ratchet ya no existe**: las cuatro están cerradas y el test pasó a la
forma cerrada, donde toda exención vive en una allowlist con justificación escrita
y caduca sola en cuanto deja de hacer falta.

| Tabla                  | Hueco                                        | Cerrado por                                                        |
| ---------------------- | -------------------------------------------- | ------------------------------------------------------------------ |
| `cortex_conversations` | tenía `tenant_id` y **cero** protección      | Migración **0125**: `ENABLE`+`FORCE`+policy por owner              |
| `review_sessions`      | RLS + policy OK, faltaba `FORCE` (mig. 0024) | Migración **0125**                                                 |
| `task_audit_events`    | RLS + policy OK, faltaba `FORCE` (mig. 0025) | Migración **0125**                                                 |
| `tenant_settings`      | RLS + policy OK, faltaba `FORCE` (mig. 0023) | Migración **0125**                                                 |
| `marketplace_sources`  | `owner_tenant_id` sin RLS                    | No era un olvido: decisión documentada, catalogada en la allowlist |

Sin `FORCE`, el **propietario** de la tabla salta la policy. Hoy el propietario es
`migrations_user`, que ya es BYPASSRLS, así que el impacto práctico fue nulo; pasa
a ser real el día que una tabla la posea un rol sin BYPASSRLS.

Y quedó **un quinto hueco que el invariante no podía ver**, porque su
descubrimiento solo mira columnas `%tenant_id`: las otras cinco tablas del córtex
seguían sin una sola policy, despachadas con una frase en la allowlist de globales
que nadie comprobaba. Lo cerraron el ADR 0156 y la migración **0140**, y el
invariante nº 5 (`OWNER_SCOPED_TABLES`) es lo que impide que vuelva a pasar.

## Cómo comprobarlo

```bash
# El invariante completo (descubre el esquema y lo compara con las allowlists):
TEST_PG_DB_NAME=... TEST_REDIS_URL=... \
  .venv/Scripts/python.exe -m pytest tests/integration/test_rls_invariant.py -q

# Aislamiento efectivo de las junctions:
  ... -m pytest tests/integration/test_junction_tenant_rls.py -q

# Postura del rol de servicio:
  ... -m pytest tests/integration/test_db_roles_service_user.py -q
```

## Relacionado

- [`domain-model.md`](./domain-model.md) — el esquema completo.
- [`rbac.md`](./rbac.md) — la capa de autorización (por encima de la RLS).
- ADR 0137 — por qué `users` se queda global.
- ADR 0074 — por qué el córtex es tenant-less.
- ADR 0156 — por qué tenant-less no significa sin RLS: el eje owner y sus policies.
- ADR 0076 — por qué `sso_configurations` dejó de ser por tenant.
