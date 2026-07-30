---
title: Multi-tenancy — cobertura RLS, roles de BD y excepciones
docs_language: es
audience: backend-dev, architect, security
updated: 2026-07-30
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

| Tabla                                                                                                                | Por qué es global                                                                                     |
| -------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| `alembic_version`                                                                                                    | Contabilidad interna de Alembic.                                                                      |
| `organizations`                                                                                                      | **Es** el tenant: se aísla con `id = app.tenant_id` (`org_self_only`), no con `tenant_id = …`.        |
| `users`                                                                                                              | Directorio global de identidades; el login es pre-tenant. Decisión razonada en el **ADR 0137**.       |
| `platform_settings`                                                                                                  | Ajustes de plataforma (System Admin).                                                                 |
| `llm_providers`                                                                                                      | Catálogo cerrado de proveedores (ADR 0021). Las credenciales por tenant no viven aquí.                |
| `model_prices`, `price_sync_audit`, `exchange_rates`                                                                 | Tarifas y tipos de cambio: mismo dato para todos los tenants.                                         |
| `sso_configurations`                                                                                                 | Global por decisión de la migración 0076: el descubrimiento de proveedor SSO ocurre antes del tenant. |
| `cortex_identity`, `cortex_identity_history`, `cortex_turns`, `cortex_affect_snapshots`, `cortex_curiosity_pursuits` | Córtex (ADR 0074): pertenecen al usuario dueño, no a un tenant; aislados por `owner_user_id`.         |

## Excepciones: columna de tenant sin RLS

| Tabla                 | Por qué                                                                                                                                                                                                            |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `marketplace_sources` | Decisión declarada en el modelo: una source es un endpoint de registro de plataforma. `owner_tenant_id` es NULLABLE y solo marca el catálogo privado de un tenant; la visibilidad la resuelve la capa de servicio. |
| `sessions`            | Tiene `tenant_id`, pero se aísla por `app.user_id` (`session_owner_only`): una sesión es de la PERSONA, que puede tener varios tenants.                                                                            |

## Huecos conocidos (ratchet)

Encontrados por el invariante la primera vez que se ejecutó (2026-07-30). **No
son decisiones: son olvidos.** El plan `prod-14` los deja anotados en vez de
arreglarlos para no hacer scope creep. El test exige que el conjunto sea exacto:
si aparece uno nuevo, rojo; si se arregla uno y no se borra de la lista, rojo
también. La lista solo puede encoger.

| Tabla                  | Hueco                                      | Arreglo                                                                                           |
| ---------------------- | ------------------------------------------ | ------------------------------------------------------------------------------------------------- |
| `cortex_conversations` | tiene `tenant_id` y **cero** protección    | `ENABLE` + `FORCE` + policy canónica, tras comprobar que el bucle del córtex fija `app.tenant_id` |
| `review_sessions`      | RLS + policy OK, falta `FORCE` (mig. 0024) | `ALTER TABLE review_sessions FORCE ROW LEVEL SECURITY`                                            |
| `task_audit_events`    | RLS + policy OK, falta `FORCE` (mig. 0025) | `ALTER TABLE task_audit_events FORCE ROW LEVEL SECURITY`                                          |
| `tenant_settings`      | RLS + policy OK, falta `FORCE` (mig. 0023) | `ALTER TABLE tenant_settings FORCE ROW LEVEL SECURITY`                                            |

Sin `FORCE`, el **propietario** de la tabla salta la policy. Hoy el propietario
es `migrations_user`, que ya es BYPASSRLS, así que el impacto práctico es nulo;
pasa a ser real el día que una tabla la posea un rol sin BYPASSRLS. Lo de
`cortex_conversations` es de otra categoría: ahí no hay protección ninguna.

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
- ADR 0076 — por qué `sso_configurations` dejó de ser por tenant.
