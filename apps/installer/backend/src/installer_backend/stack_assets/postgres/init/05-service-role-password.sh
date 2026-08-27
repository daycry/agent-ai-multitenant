#!/usr/bin/env bash
# Fija la contraseña de `service_user` desde el entorno, en un arranque LIMPIO
# (plan prod-14, task_prod14_04).
#
# POR QUÉ ESTO ES UN FICHERO APARTE
# ---------------------------------
# `04-service-role.sql` crea el rol y reparte sus privilegios, y tiene que
# seguir siendo **SQL plano**: es el mismo artefacto que se aplica a una base de
# datos VIVA (`docker/postgres/upgrade/20260730-service-user.sh`) y el que lee y
# ejecuta `tests/integration/test_db_roles_service_user.py`. El SQL plano no
# puede leer variables de entorno, así que su `CREATE ROLE` lleva forzosamente un
# literal — y ese literal está en el repositorio.
#
# Dejarlo así en un despliegue nuevo sería publicar la contraseña de un rol
# BYPASSRLS: la llave que se salta la RLS de TODOS los tenants. El script de
# upgrade ya aceptaba `SERVICE_USER_PASSWORD`; lo que faltaba era el arranque
# limpio, que es precisamente el caso de una instalación nueva.
#
# ORDEN: el entrypoint de la imagen de PostgreSQL ejecuta `/docker-entrypoint-
# initdb.d/` en orden alfabético, así que el `05-` corre después del `04-` que
# crea el rol. El test lo comprueba, porque si alguien renombra los ficheros el
# fallo sería silencioso (un ALTER sobre un rol inexistente, con `set -e`, sí
# rompe el init; pero el orden inverso es fácil de introducir sin darse cuenta).
#
# Idempotente: un `ALTER ROLE ... PASSWORD` se puede repetir sin efectos.
#
# El default de dev es el MISMO literal que trae el `.sql`, para que un
# `docker compose up` sin `.env` siga funcionando y para que la suite de
# integración pueda conectar. La eliminación de defaults conocidos y la gestión
# de este secreto en Vault son de prod-10.
set -euo pipefail

SERVICE_PASS="${SERVICE_USER_PASSWORD:-changeme-service-dev-only}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    ALTER ROLE service_user WITH PASSWORD '${SERVICE_PASS}';
EOSQL

if [ -n "${SERVICE_USER_PASSWORD:-}" ]; then
  echo "agentic-platform: contraseña de service_user tomada de SERVICE_USER_PASSWORD."
else
  echo "agentic-platform: service_user con la contraseña PLACEHOLDER de desarrollo." >&2
  echo "                  Define SERVICE_USER_PASSWORD antes de exponer esto." >&2
fi
