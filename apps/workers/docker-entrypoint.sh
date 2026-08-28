#!/bin/sh
# workers — entrypoint de self-heal de permisos (auditoría runs 2026-07-02).
#
# En Docker Desktop/WSL2 el bind-source /data/agent-platform puede reaparecer
# VACÍO y root:root tras un engine-restart (el daemon lo auto-crea al montar).
# El one-shot worktrees-init del compose solo corre con `docker compose up`,
# así que el worker (uid 1000) quedaba con EACCES permanente al provisionar
# worktrees. Este entrypoint arranca como root, repara la propiedad SOLO si
# hace falta y cae a uid 1000 antes de ejecutar el comando real. El one-shot
# del compose se mantiene como red de seguridad.
set -eu

DATA_ROOT="${WORKERS_DATA_ROOT:-/data/agent-platform}"

# Los subárboles de los que el worker es DUEÑO, y sólo ésos (2026-08-28).
#
# Hasta hoy esto hacía `chown -R 1000:1000 "$DATA_ROOT"`: la raíz ENTERA, que
# contiene también los datos de postgres, redis, minio, vault y caddy. Con
# `cap_drop: ALL` el chown fallaba en silencio —lleva `|| true`— así que nadie
# lo notó. Al devolverle CAP_CHOWN al worker para que pudiera hacer `setpriv`,
# el chown pasó a FUNCIONAR y se llevó por delante la PKI de Caddy (e2e run
# 33187158257):
#
#   provisioning CA 'local': loading root cert:
#     open /data/caddy/pki/authorities/local/root.crt: permission denied
#
# Un servicio que se apropia de los datos de los demás es un fallo por sí mismo;
# que llevara años tapado por un `|| true` sólo lo hacía invisible. Se acota a lo
# que el worker usa de verdad: bare repos, worktrees, caché de dependencias y
# los bundles de backup.
WORKER_OWNED="projects worktrees dep-cache backups marketplace"

if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DATA_ROOT" 2>/dev/null || true
    for sub in $WORKER_OWNED; do
        ruta="$DATA_ROOT/$sub"
        [ -d "$ruta" ] || continue
        owner="$(stat -c '%u' "$ruta" 2>/dev/null || echo unknown)"
        if [ "$owner" != "1000" ]; then
            echo "workers-entrypoint: reparando propiedad de $ruta (uid $owner -> 1000)" >&2
            chown -R 1000:1000 "$ruta" || true
        fi
    done
    # Pool de backup/restore (durabilidad 2026-07-03): el paso volume-tar lee
    # los _data de redis (uid 999) y vault (uid 100), ambos 0700 — ilegibles
    # para uid 1000 — y un restore además ESCRIBE en ellos. El servicio
    # dedicado `workers-backup` (cola privileged, sin runs de agentes) se queda
    # como root DENTRO de su contenedor confinado; el resto de pools siguen
    # cayendo a uid 1000.
    if [ "${WORKERS_RUN_AS_ROOT:-0}" = "1" ]; then
        exec "$@"
    fi
    # HOME heredado de root sería /root: ilegible para uid 1000, y asyncpg
    # revienta con EACCES al buscar ~/.postgresql/postgresql.key en CADA
    # conexión a BD (regresión detectada 2026-07-02). Un HOME escribible
    # propio del proceso degradado lo evita.
    export HOME=/tmp
    exec setpriv --reuid=1000 --regid=1000 --clear-groups "$@"
fi

exec "$@"
