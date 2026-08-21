#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Restauración COMPLETA desde un bundle de backup — plano de ejecución HOST-SIDE.
# prod-04 task_prod_04_03 (Decisión clave 1, opción b).
#
# POR QUÉ EN EL HOST Y NO DENTRO DE UN CONTENEDOR
# ------------------------------------------------
# El runbook anterior mandaba `docker compose exec -T worker python -c ...`. Eso
# no funcionaba por DOS razones independientes:
#
#   1. No existe ningún servicio llamado `worker` en ningún compose (es
#      `workers`), así que el comando fallaba antes de empezar.
#   2. Aunque existiera: el restore PARA el stack del que dependería ese
#      contenedor. `workers` está en WORKERS_RESTORE_APP_SERVICES — el proceso se
#      estaría matando a sí mismo en el paso 3, a mitad de una operación
#      destructiva. Ejecutar la restauración desde dentro de lo que se va a parar
#      es frágil por construcción.
#
# Por eso el motor corre AQUÍ, en el host, con acceso al socket de Docker y a los
# volúmenes/bind-mounts. El plano que ejecuta el restore NUNCA puede estar en la
# lista de servicios a parar.
#
# QUÉ HACE EL MOTOR (apps/workers/src/workers/restore.py)
# --------------------------------------------------------
#   localizar → (descifrar) → VERIFICAR (fail-closed, no toca nada si el bundle
#   está corrupto) → preflight de servicios → parar la aplicación → pg_restore
#   --exit-on-error → re-conceder GRANTs → restaurar volúmenes + repos de
#   proyectos + binds → arrancar el stack.
#
# Si algo falla en la fase destructiva el stack queda PARADO a propósito
# (task_prod_04_04): un stack sirviendo datos a medio restaurar es peor que uno
# apagado. El error dice hasta dónde se llegó y cuál es el siguiente paso.
#
# USO
# ---
#   ./scripts/restore.sh <backup_id>
#   ./scripts/restore.sh --list
#
# El motor exige un token de doble confirmación IGUAL al backup_id; el script lo
# pide por teclado (o lo toma de --confirm / RESTORE_CONFIRM para automatizarlo
# en el simulacro).
#
# ENTORNO (leído por el motor desde las WORKERS_*; nada hardcodeado aquí)
#   WORKERS_BACKUP_ROOT                 dónde viven los bundles
#   WORKERS_BACKUP_DATABASE_URL         URL libpq (¡NO la de SQLAlchemy!) del rol DDL
#   WORKERS_BACKUP_VOLUMES              lista JSON de volúmenes docker
#   WORKERS_BACKUP_VOLUMES_MOUNT_ROOT   directorio host con los volúmenes
#   WORKERS_BACKUP_PROJECTS_ROOT        raíz de los bare repos de los proyectos
#   WORKERS_BACKUP_BIND_PATHS           lista JSON de binds capturados
#   WORKERS_BACKUP_ENCRYPTION_ENABLED   true si el bundle está cifrado
#   WORKERS_BACKUP_ENCRYPTION_KEY       ← la clave AES. NO está en Vault: se
#                                          recupera de la CUSTODIA OFFSITE
#                                          (docs/06-runbooks/dr-drill.md)
#   WORKERS_RESTORE_COMPOSE_FILE        el compose que corre DE VERDAD
#   WORKERS_RESTORE_COMPOSE_PROJECT     nombre del proyecto compose
#
# Se carga automáticamente el fichero de entorno indicado en --env-file (por
# defecto el `.env` junto al compose), para no depender de que el operador tenga
# 15 variables exportadas a mano a las 4 de la mañana.
# -----------------------------------------------------------------------------
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python}"
ENV_FILE="${RESTORE_ENV_FILE:-}"
CONFIRM="${RESTORE_CONFIRM:-}"
BUNDLE=""
LIST_ONLY=0

usage() {
  sed -n '2,60p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --list) LIST_ONLY=1; shift ;;
    --env-file) ENV_FILE="${2:?--env-file necesita una ruta}"; shift 2 ;;
    --confirm) CONFIRM="${2:?--confirm necesita el backup_id}"; shift 2 ;;
    -h|--help) usage 0 ;;
    --*) echo "opción desconocida: $1" >&2; usage 2 ;;
    *) BUNDLE="$1"; shift ;;
  esac
done

# --- entorno -----------------------------------------------------------------
if [ -z "${ENV_FILE}" ]; then
  # Junto al compose que se va a manejar, que es donde el instalador lo escribe.
  compose_file="${WORKERS_RESTORE_COMPOSE_FILE:-/data/agent-platform/docker-compose.yml}"
  candidate="$(dirname "${compose_file}")/.env"
  [ -f "${candidate}" ] && ENV_FILE="${candidate}"
fi
if [ -n "${ENV_FILE}" ]; then
  if [ ! -f "${ENV_FILE}" ]; then
    echo "no existe el fichero de entorno: ${ENV_FILE}" >&2
    exit 2
  fi
  echo "cargando entorno de ${ENV_FILE}"
  set -a
  # shellcheck disable=SC1090
  . "${ENV_FILE}"
  set +a
fi

# --- comprobaciones que evitan un fallo a mitad ------------------------------
command -v docker >/dev/null 2>&1 || { echo "docker no está en el PATH" >&2; exit 2; }
docker info >/dev/null 2>&1 || {
  echo "no se puede hablar con el daemon de Docker (¿permisos? ¿está arrancado?)" >&2
  exit 2
}
command -v pg_restore >/dev/null 2>&1 || {
  echo "pg_restore no está en el PATH del host: instala el cliente de PostgreSQL" >&2
  exit 2
}
command -v psql >/dev/null 2>&1 || {
  echo "psql no está en el PATH del host: hace falta para re-conceder los GRANTs" >&2
  exit 2
}

if [ "${LIST_ONLY}" = "1" ]; then
  exec "${PYTHON_BIN}" -c '
from pathlib import Path

from workers.config import get_settings

root = Path(get_settings().backup_root)
if not root.is_dir():
    raise SystemExit(f"no existe el directorio de backups: {root}")
for entry in sorted(p for p in root.iterdir() if p.is_dir()):
    manifest = entry / "manifest.json"
    mark = "ok " if manifest.is_file() else "SIN MANIFEST "
    print(f"{mark}{entry.name}")
'
fi

if [ -z "${BUNDLE}" ]; then
  echo "falta el <backup_id>. Usa --list para verlos." >&2
  exit 2
fi

# --- doble confirmación -------------------------------------------------------
if [ -z "${CONFIRM}" ]; then
  echo
  echo "AVISO: esta operación es DESTRUCTIVA y GLOBAL: reemplaza la base de datos"
  echo "y los volúmenes de TODOS los tenants por el contenido de ${BUNDLE}."
  echo "Para restaurar un solo tenant usa docs/06-runbooks/dr-tenant-restore.md."
  echo
  printf 'Escribe el backup_id para confirmar: '
  read -r CONFIRM
fi

BUNDLE="${BUNDLE}" CONFIRM="${CONFIRM}" "${PYTHON_BIN}" -c '
import os
import sys

from workers.restore import RestoreError, RestorePartialError, run_full_restore

bundle = os.environ["BUNDLE"]
confirm = os.environ["CONFIRM"]
try:
    result = run_full_restore(bundle, confirm=confirm)
except RestorePartialError as exc:
    print(f"RESTORE INCOMPLETO: {exc}", file=sys.stderr)
    raise SystemExit(3)
except RestoreError as exc:
    print(f"restore abortado (no se ha tocado nada destructivo): {exc}", file=sys.stderr)
    raise SystemExit(1)

print(f"restore ok: {result.backup_id}")
print(f"  cifrado:   {result.encrypted}")
print(f"  volúmenes: {list(result.restored_volumes)}")
print(f"  rutas:     {list(result.restored_paths)}")
'

# --- reconciliación: el restore NO se da por bueno sin ella -------------------
# Se ejecuta AQUÍ, no como una sugerencia impresa: un paso que hay que acordarse
# de lanzar es un paso que no se lanza. Devuelve != 0 si hay divergencias
# críticas entre la base de datos, MinIO, Vault y los repos git.
echo
echo "Reconciliando los cuatro almacenes (BD / MinIO / Vault / git)..."
if "${PYTHON_BIN}" -m workers.restore_reconcile; then
  echo
  echo "SIGUIENTE: desella Vault y valida el login de un usuario de tenant."
  echo "  Guion completo del simulacro: docs/06-runbooks/dr-drill.md"
else
  rc=$?
  echo >&2
  echo "LA RECONCILIACIÓN ENCONTRÓ DIVERGENCIAS CRÍTICAS (rc=${rc})." >&2
  echo "Los datos están restaurados pero NO cuadran entre sí: revisa el informe" >&2
  echo "de arriba antes de dar el servicio por recuperado." >&2
  exit "${rc}"
fi
