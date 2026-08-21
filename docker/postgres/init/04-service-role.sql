-- =====================================================================
-- service_user — el rol con el que deben conectarse los SERVICIOS
-- (workers, orchestrator, notification-dispatcher y el engine admin de la
-- api-server), plan prod-14 / hallazgo tenancy-2.
--
-- Hoy esos cuatro servicios conectan como `migrations_user`, que es el
-- PROPIETARIO del esquema con `GRANT ALL`: un servicio comprometido puede
-- ejecutar `ALTER TABLE agents DISABLE ROW LEVEL SECURITY` y desactivar el
-- aislamiento multi-tenant de toda la plataforma. Ese privilegio no lo
-- necesita ningún servicio: solo Alembic.
--
-- Reparto de roles resultante:
--
--   migrations_user  DDL + ownership + BYPASSRLS   → SOLO Alembic
--   service_user     DML  + BYPASSRLS, SIN DDL     → los 4 servicios
--   app_user         DML  + NOBYPASSRLS            → api-server (RLS aplicada)
--
-- Por qué `service_user` SÍ es BYPASSRLS: es su razón de ser. Un worker
-- procesa la ejecución del tenant que le toque sin que haya un
-- `app.tenant_id` de request al que atarse, y el dispatcher tiene que ver
-- las colas de todos los tenants. El riesgo residual (un servicio
-- comprometido sigue leyendo cross-tenant) es inherente a esa función; lo
-- que esta separación quita es la capacidad de DESMONTAR la protección para
-- todos los demás, que es un salto de gravedad distinto.
--
-- POR QUÉ ESTE FICHERO ES SQL Y NO ESTÁ EN 02-roles.sh
-- ----------------------------------------------------
-- Porque los scripts de `init/` solo corren en un contenedor con el volumen
-- VACÍO, y necesitamos poder aplicar esto a las bases de datos que YA
-- existen. Al ser SQL plano e idempotente, el mismo fichero sirve para las
-- tres vías:
--
--   * arranque limpio       → docker lo ejecuta solo (init/, orden alfabético);
--   * base de datos viva    → `psql -f 04-service-role.sql` (ver el runbook
--                             docker/postgres/upgrade/README.md);
--   * suite de integración  → `tests/integration/test_db_roles_service_user.py`
--                             lo ejecuta y comprueba lo que produce.
--
-- Esa tercera vía es la que importa: el test no re-escribe el DDL, LEE ESTE
-- FICHERO. Si alguien le añade `GRANT ALL` o le quita el `NOSUPERUSER`, el
-- test se pone rojo.
--
-- El `GRANT ... ON ALL TABLES` de abajo es no-op en un arranque limpio (aún
-- no hay tablas) y es justo lo que hace falta en una base de datos viva; el
-- `ALTER DEFAULT PRIVILEGES` cubre las tablas que Alembic cree después.
--
-- Contraseña: placeholder SOLO-DEV, igual que las de `02-roles.sh`. La
-- eliminación de defaults conocidos y su gestión en Vault son de prod-10; el
-- script de upgrade acepta `SERVICE_USER_PASSWORD` por entorno.
-- =====================================================================

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'service_user') THEN
    CREATE ROLE service_user WITH
      LOGIN
      BYPASSRLS
      NOSUPERUSER
      NOCREATEDB
      NOCREATEROLE
      NOINHERIT
      PASSWORD 'changeme-service-dev-only';
  ELSE
    -- Idempotente Y correctivo: si alguien tocó los atributos a mano, los
    -- devuelve a la postura declarada.
    ALTER ROLE service_user WITH
      LOGIN BYPASSRLS NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
  END IF;
END
$$;

-- `current_database()` en vez de una variable de psql: así el fichero es SQL
-- plano ejecutable por psql, por docker-entrypoint y por asyncpg desde el test.
DO $$
BEGIN
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO service_user', current_database());
END
$$;

-- USAGE, y deliberadamente NO CREATE: sin CREATE en el esquema no puede
-- crear tablas, ni funciones, ni tipos donde esconder nada. (PostgreSQL 15+
-- ya no concede CREATE en `public` a PUBLIC; el REVOKE explícito deja la
-- postura escrita y protege de un GRANT manual anterior.)
GRANT USAGE ON SCHEMA public TO service_user;
REVOKE CREATE ON SCHEMA public FROM service_user;

-- DML sobre lo que ya existe (base de datos viva).
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO service_user;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO service_user;

-- DML sobre lo que Alembic cree en el futuro.
ALTER DEFAULT PRIVILEGES FOR ROLE migrations_user IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO service_user;
ALTER DEFAULT PRIVILEGES FOR ROLE migrations_user IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO service_user;
