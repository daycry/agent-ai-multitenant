#!/usr/bin/env bash
# Create the two application roles used by the platform:
#   migrations_user — has DDL rights, used by Alembic.
#   app_user        — has DML rights on existing + future tables, used
#                     by the runtime services.
#
# Idempotent. Reads passwords from env vars; if either is missing,
# safe placeholder defaults are used (dev only — phase 15 installer
# replaces them with Vault-managed secrets).
set -euo pipefail

MIG_PASS="${MIGRATIONS_USER_PASSWORD:-changeme-migrations-dev-only}"
APP_PASS="${APP_USER_PASSWORD:-changeme-app-dev-only}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    -- migrations_user: full DDL rights AND BYPASSRLS so it can run
    -- Alembic migrations (which create the RLS policies themselves)
    -- without tripping over them.
    DO \$\$
    BEGIN
      IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'migrations_user') THEN
        CREATE ROLE migrations_user WITH LOGIN BYPASSRLS PASSWORD '${MIG_PASS}';
      ELSE
        ALTER ROLE migrations_user WITH LOGIN BYPASSRLS;
      END IF;
    END
    \$\$;

    GRANT CONNECT ON DATABASE "${POSTGRES_DB}" TO migrations_user;
    GRANT ALL PRIVILEGES ON DATABASE "${POSTGRES_DB}" TO migrations_user;
    GRANT ALL ON SCHEMA public TO migrations_user;

    -- app_user: DML only. NOBYPASSRLS — every query MUST respect the
    -- row-level security policies. Cross-tenant leaks would otherwise
    -- be invisible to the platform.
    DO \$\$
    BEGIN
      IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'app_user') THEN
        CREATE ROLE app_user WITH LOGIN NOBYPASSRLS PASSWORD '${APP_PASS}';
      ELSE
        ALTER ROLE app_user WITH LOGIN NOBYPASSRLS;
      END IF;
    END
    \$\$;

    GRANT CONNECT ON DATABASE "${POSTGRES_DB}" TO app_user;
    GRANT USAGE ON SCHEMA public TO app_user;

    -- Default privileges for tables created LATER by migrations_user:
    -- app_user automatically gets SELECT/INSERT/UPDATE/DELETE.
    ALTER DEFAULT PRIVILEGES FOR ROLE migrations_user IN SCHEMA public
      GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_user;
    ALTER DEFAULT PRIVILEGES FOR ROLE migrations_user IN SCHEMA public
      GRANT USAGE, SELECT ON SEQUENCES TO app_user;
EOSQL

echo "agentic-platform: roles migrations_user and app_user ready."
