#!/usr/bin/env bash
# Aplica el rol `service_user` a una base de datos que YA EXISTE.
#
# Los scripts de `docker/postgres/init/` solo se ejecutan cuando el volumen de
# PostgreSQL está vacío, así que un despliegue en marcha nunca vería
# `04-service-role.sql`. Este script es esa vía: ejecuta EL MISMO fichero (no
# una copia del DDL, que se desincronizaría) y opcionalmente fija la contraseña
# desde el entorno.
#
# Uso:
#   SERVICE_USER_PASSWORD=... ./20260730-service-user.sh            # dentro del contenedor
#   docker compose exec -T postgres bash -s < 20260730-service-user.sh
#
# Variables:
#   POSTGRES_USER / POSTGRES_DB   como en el compose (defaults del entorno del contenedor)
#   SERVICE_USER_PASSWORD         si se define, sustituye el placeholder de dev
#
# Idempotente: se puede re-ejecutar en cada despliegue. El test
# `tests/integration/test_db_roles_service_user.py::test_applying_the_sql_twice_is_idempotent`
# cubre precisamente eso.
#
# ORDEN DE DESPLIEGUE (riesgo nº 6 del plan prod-14): primero este script,
# DESPUÉS el cambio de `database_url` de los servicios. Al revés, los cuatro
# servicios arrancarían contra un rol inexistente a la vez. El rollback es
# trivial: devolver la variable de entorno a `migrations_user`.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROLE_SQL="${HERE}/../init/04-service-role.sql"

if [[ ! -f "${ROLE_SQL}" ]]; then
  echo "ERROR: no encuentro ${ROLE_SQL}" >&2
  exit 1
fi

DB="${POSTGRES_DB:-agentic_platform}"
SUPERUSER="${POSTGRES_USER:-postgres}"

echo "==> aplicando 04-service-role.sql sobre la base de datos '${DB}'"
psql -v ON_ERROR_STOP=1 --username "${SUPERUSER}" --dbname "${DB}" -f "${ROLE_SQL}"

if [[ -n "${SERVICE_USER_PASSWORD:-}" ]]; then
  echo "==> fijando la contraseña de service_user desde SERVICE_USER_PASSWORD"
  # Vía psql-variable para que la contraseña no aparezca en la línea de
  # comandos (y por tanto no acabe en `ps` ni en el historial del shell).
  psql -v ON_ERROR_STOP=1 --username "${SUPERUSER}" --dbname "${DB}" \
       -v pwd="${SERVICE_USER_PASSWORD}" \
       -c "ALTER ROLE service_user WITH PASSWORD :'pwd'"
else
  echo "==> AVISO: SERVICE_USER_PASSWORD no definida; service_user queda con el"
  echo "    placeholder de desarrollo. NO desplegar así en producción"
  echo "    (la gestión en Vault es del plan prod-10)."
fi

echo "==> comprobación posterior"
psql -v ON_ERROR_STOP=1 --username "${SUPERUSER}" --dbname "${DB}" -c "
  SELECT rolname, rolcanlogin, rolbypassrls, rolsuper, rolcreatedb, rolcreaterole
    FROM pg_roles WHERE rolname = 'service_user'"
psql -v ON_ERROR_STOP=1 --username "${SUPERUSER}" --dbname "${DB}" -c "
  SELECT has_schema_privilege('service_user','public','USAGE')  AS usage_ok,
         has_schema_privilege('service_user','public','CREATE') AS create_must_be_false,
         has_table_privilege('service_user','agents','SELECT')  AS dml_ok"

echo "OK: service_user listo. Recuerda: primero el rol, luego los servicios."
