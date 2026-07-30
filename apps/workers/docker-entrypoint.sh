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

if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DATA_ROOT" 2>/dev/null || true
    if [ -d "$DATA_ROOT" ]; then
        owner="$(stat -c '%u' "$DATA_ROOT" 2>/dev/null || echo unknown)"
        if [ "$owner" != "1000" ]; then
            echo "workers-entrypoint: reparando propiedad de $DATA_ROOT (uid $owner -> 1000)" >&2
            chown -R 1000:1000 "$DATA_ROOT" || true
        fi
    fi
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
