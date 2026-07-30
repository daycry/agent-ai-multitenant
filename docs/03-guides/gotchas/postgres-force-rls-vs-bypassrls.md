---
title: "`FORCE ROW LEVEL SECURITY` NO alcanza a un rol `BYPASSRLS`, ni siendo el dueño"
area: postgres
encountered: 2026-07-30
stack: postgres 16.13
---

## Síntoma

Dos formas, y la segunda es la peligrosa:

1. Añades `ALTER TABLE t FORCE ROW LEVEL SECURITY` esperando que el dueño de la
   tabla empiece a respetar sus propias policies… y no cambia nada: sigue viendo
   todas las filas y escribiendo lo que quiere.
2. Escribes un test que dice «con `FORCE` puesto, esta sesión ve 0 filas» y pasa
   en verde por el motivo equivocado, o falla y te hace «arreglar» una policy que
   estaba bien.

## Causa raíz

En PostgreSQL hay **dos** exenciones distintas de la RLS y `FORCE` solo levanta
una:

| Quién                           | ¿Se salta la RLS?                |
| ------------------------------- | -------------------------------- |
| Dueño de la tabla, sin `FORCE`  | Sí                               |
| Dueño de la tabla, con `FORCE`  | **No** — es lo que hace FORCE    |
| Rol con el atributo `BYPASSRLS` | **Sí, siempre** — FORCE da igual |
| Superusuario                    | Sí, siempre                      |

En esta plataforma las dos exenciones caen sobre el MISMO rol:
`migrations_user` es a la vez el **dueño** del esquema y **`BYPASSRLS`**
(`docker/postgres/init/02-roles.sh`). Resultado: sobre los cuatro roles actuales
—`postgres`, `migrations_user`, `service_user`, `app_user`— añadir `FORCE` es un
**no-op medible**. El único rol al que la RLS aplica es `app_user`
(`NOBYPASSRLS`), y a ése ya le aplicaba con el simple `ENABLE`.

Eso no quita valor a `FORCE`: es la postura que empieza a valer el día que el
dueño de la tabla deje de ser `BYPASSRLS` (la dirección que declara
`docker/postgres/init/04-service-role.sql`). Lo que hay que saber es que **hoy no
es observable por comportamiento**, solo por catálogo.

## Fix

Dos consecuencias prácticas:

1. **Al escribir la migración**: pon `ENABLE` + `FORCE` + policy igual, por
   consistencia con las ~65 tablas tenant-scoped que ya lo hacen, pero no cuentes
   con que `FORCE` proteja de nada hoy. Si lo que quieres es que un servicio deje
   de leer cross-tenant, `FORCE` no es la herramienta: quítale el `BYPASSRLS` al
   rol, y antes de hacerlo comprueba qué GUC fija cada camino.
2. **Al escribir el test**: afirma sobre `pg_class.relforcerowsecurity` (postura
   de catálogo) y sobre el comportamiento **con `app_user`**. Un test que
   pretenda medir `FORCE` conectando como `migrations_user` no puede fallar, y
   [un test que no puede fallar no vale nada](../verificar-antes-de-implementar.md).
   Si además el test depende de que el rol del servicio siga siendo `BYPASSRLS`,
   **escribe esa premisa como aserción** para que se rompa el día que cambie:

   ```python
   assert await conn.fetchval(
       "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user"
   ), "el rol ya no es BYPASSRLS: la policy le aplica y nadie fija el GUC"
   ```

## Cómo verificar el fix

Reproducción mínima, ~20 líneas, en una BD desechable. Es la que se usó para
decidir la migración 0125:

```sql
-- como migrations_user (dueño Y BYPASSRLS)
CREATE TABLE t (id serial primary key, tenant_id uuid not null);
GRANT SELECT, INSERT ON t TO app_user;
INSERT INTO t (tenant_id) VALUES (gen_random_uuid()), (gen_random_uuid());
ALTER TABLE t ENABLE ROW LEVEL SECURITY;
ALTER TABLE t FORCE  ROW LEVEL SECURITY;
CREATE POLICY t_iso ON t FOR ALL
  USING      (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
  WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);

SELECT count(*) FROM t;                  -- 2  ← FORCE puesto, GUC sin fijar
INSERT INTO t (tenant_id) VALUES (gen_random_uuid());  -- OK, sin WITH CHECK que valga
```

```sql
-- la MISMA tabla, como app_user (NOBYPASSRLS)
SELECT count(*) FROM t;                  -- 0  ← fail-closed, sin GUC
SELECT set_config('app.tenant_id', '<uno de los dos>', false);
SELECT count(*) FROM t;                  -- 1
```

Si la primera consulta te devuelve `0` en vez de `2`, tu `migrations_user` ha
perdido el `BYPASSRLS` y tienes un problema mayor que esta nota: Alembic y los
cuatro servicios dependen de él (ver
[`postgres-roles-bypassrls.md`](./postgres-roles-bypassrls.md)).

## Relacionado

- [`postgres-roles-bypassrls.md`](./postgres-roles-bypassrls.md) — qué rol
  necesita `BYPASSRLS` y por qué `app_user` no debe tenerlo.
- `apps/api-server/migrations/versions/20260730_0125_cortex_conv_rls.py` — la
  migración que salió de medir esto.
- `tests/integration/test_rls_invariant.py` — el invariante que exige
  `ENABLE` + `FORCE` + policy a toda tabla con columna de tenant.
