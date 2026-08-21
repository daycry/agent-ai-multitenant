---
title: "`permission denied for table …` en una BD nueva: los GRANT no viajan con la migración"
area: postgres
encountered: 2026-05-20
updated: 2026-08-20
stack: postgres 16, alembic, arnés e2e con backend vivo
---

## Síntoma

Dos caras del mismo problema.

**(1) Genérica.** `app_user` recibe `permission denied for table X` en una BD que
acabas de crear (por ejemplo, una BD de tests). En la BD dev funciona.

**(2) La que te vas a encontrar de verdad, y no menciona permisos hasta el
final.** Levantas un arnés e2e contra una BD desechable creada a mano
(`CREATE DATABASE` + `alembic upgrade head`), y **el login revienta**:

```
asyncpg.exceptions.InsufficientPrivilegeError: permission denied for table user_mfa_totp
```

Desconcierta porque la migración corrió entera y sin un solo error, y porque
`user_mfa_totp` existe: `\dt` la lista. Lo que falta no es la tabla, es el
permiso. Y sale en el **login** —no en una pantalla de MFA— porque la probe de
métodos MFA (`auth/mfa/store.py::user_mfa_methods`) forma parte del camino de
autenticación.

## Causa raíz

Los privilegios de la aplicación **no los pone la migración**: los pone el init
de PostgreSQL (`docker/postgres/init/02-roles.sh`) con un

```sql
ALTER DEFAULT PRIVILEGES FOR ROLE migrations_user IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_user;
```

De ahí salen dos trampas encadenadas:

1. **`ALTER DEFAULT PRIVILEGES` es per-database.** Está configurado sobre
   `agentic_platform`; al hacer `CREATE DATABASE otra`, la nueva **no lo
   hereda**.
2. **Solo aplica a objetos creados _después_** de su definición. Si la migración
   corrió antes de configurar los defaults, las tablas existentes tampoco quedan
   cubiertas.

Y una tercera, específica del arnés hecho a mano: si migras conectado como
`postgres` (o como `migrations_user`) y das por hecho que «la migración deja la
base lista», la base queda **estructuralmente completa y funcionalmente muda**
para el rol de la aplicación. Alembic no tiene por qué avisar: crear tablas y
concederlas son dos responsabilidades distintas y en producción las hace otro.

## Fix

Tres pasos en la BD nueva. Los dos primeros son los de siempre; el tercero es el
que casi nadie hace y es el que separa «arnés fiel» de «arnés cómodo».

1. Repetir `ALTER DEFAULT PRIVILEGES` justo después de crearla:

   ```sql
   ALTER DEFAULT PRIVILEGES FOR ROLE migrations_user IN SCHEMA public
     GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_user;
   ALTER DEFAULT PRIVILEGES FOR ROLE migrations_user IN SCHEMA public
     GRANT USAGE, SELECT ON SEQUENCES TO app_user;
   GRANT USAGE ON SCHEMA public TO app_user;
   ```

2. Tras correr la migración, **retro-grant** a las tablas ya creadas:

   ```sql
   GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_user;
   GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_user;
   ```

3. **Volver a aplicar los REVOKE que una migración puso a propósito.** Leer el
   apartado siguiente antes de saltárselo.

`tests/integration/conftest.py` hace exactamente estos tres pasos al construir
la BD de pruebas (`_drop_create_db` + `_grant_app_user_existing_tables`), y
[`scripts/dev/e2e_live_harness.py`](../../../scripts/dev/e2e_live_harness.py) los
repite para el arnés de Playwright **leyendo la lista de revokes del propio
conftest**, para que los dos arneses no puedan divergir. Si montas uno nuevo,
cópialo de ahí en vez de reinventarlo: es la única versión que ya pagó los tres
errores.

## El peligro contrario: conceder de más deja el arnés MÁS PERMISIVO que producción

Esto es peor que dejarlo roto, porque pasa en verde. El retro-grant del paso 2 es
un `ON ALL TABLES` sin excepciones, así que **deshace los REVOKE deliberados**.
Hoy hay uno:

- `approval_policy_backfill_0133` — la migración
  [`0138_revoke_backfill_grants`](../../../apps/api-server/migrations/versions/20260810_0138_revoke_backfill_grants.py)
  le retira todo acceso a `app_user` **y** a `service_user`. Es el respaldo
  fila-a-fila de la política de aprobación de cada proyecto de la plataforma, no
  tiene `tenant_id` ni RLS, y sin el revoke cualquier sesión de tenant leía la
  configuración de aprobación de **todos los demás tenants**.

Un arnés que concede de más no falla: **aprueba** un código que en producción
sería un 500 o una fuga. `_APP_REVOKED_TABLES` en `tests/integration/conftest.py`
es la lista canónica, y el comentario que la acompaña cuenta cómo se destapó:
`test_the_backfill_table_is_unreachable_from_the_app` pasaba **en aislamiento** y
fallaba en lote, porque cualquier test anterior que reconstruyera los grants
devolvía lo que la 0138 había quitado. Ese patrón —una guarda que solo pasa
sola— es el que acaba con la guarda borrada por «flaky», y la guarda tenía razón.

Dos reglas al copiar esto a un arnés nuevo:

- Revoca a **los dos roles de aplicación** (`app_user` y `service_user`), que es
  lo que hace la migración. Quedarte en `app_user` deja el agujero abierto por el
  camino de `/admin/*`, que conecta con el otro.
- **NUNCA** a `migrations_user`: es quien escribe la tabla y quien la lee al
  bajar, así que quitarle el acceso rompe el `downgrade` de la 0133.

Y la salida más barata de todas, cuando puedas permitírtela: **no crees la BD a
mano**. Usa la del compose, cuyo init ya pone los privilegios y cuyos REVOKE
llegan con la propia cadena de migraciones. La trampa de esta nota solo existe
cuando el `CREATE DATABASE` lo haces tú.

## Cómo verificar el fix

```sql
\dp public.organizations
```

`app_user` debe aparecer con `arwd` (SELECT/INSERT/UPDATE/DELETE).

Y la mitad que se olvida — el revoke debe seguir puesto:

```sql
\dp public.approval_policy_backfill_0133
```

`app_user` y `service_user` **no** deben aparecer. La aserción ejecutable es
`tests/integration/test_rls_invariant.py::test_the_backfill_table_is_unreachable_from_the_app`.

## Relacionado

- [`postgres-roles-bypassrls.md`](./postgres-roles-bypassrls.md) — qué rol necesita
  `BYPASSRLS` y cuál no.
- [`test-fixture-admin-db-url-override.md`](./test-fixture-admin-db-url-override.md)
  — el otro fallo de la misma familia: el api-server usa DOS URLs de BD y la que
  se olvida apunta a la base del operador.
