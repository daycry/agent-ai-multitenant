#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# scripts/dev/down.sh
#
# Stop the dev stack started by scripts/dev/up.sh. Reads .dev/*.pid and
# kills each process group (negative PID arg = process-group kill) so
# uvicorn workers and node children die with their parents.
#
# Usage:
#   ./scripts/dev/down.sh             # stops api-server + admin-panel
#   ./scripts/dev/down.sh --docker    # also `docker compose down`
# -----------------------------------------------------------------------------
set -u  # not -e: we want to keep going on partial failures

DOCKER_DOWN=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --docker) DOCKER_DOWN=1; shift ;;
        -h|--help) echo "Usage: $0 [--docker]"; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

DEV_DIR="$REPO_ROOT/.dev"
API_PID_FILE="$DEV_DIR/api-server.pid"
ADMIN_PID_FILE="$DEV_DIR/admin-panel.pid"

stop_from_pidfile() {
    local file="$1" name="$2"
    if [[ ! -f "$file" ]]; then
        echo "    no $file -- $name not tracked"
        return
    fi
    local pid
    pid="$(cat "$file" 2>/dev/null || true)"
    if [[ -z "$pid" ]]; then
        rm -f "$file"
        echo "    $file was empty"
        return
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
        echo "    $name (pid $pid) already gone"
        rm -f "$file"
        return
    fi
    echo "==> Stopping $name (pid $pid, pg $pid)"
    # Negative arg = process group. up.sh launched with setsid, so the
    # pid IS the pgid, and -- separates flag-looking args from kill's TERM.
    kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
    # Give it 5s, then SIGKILL if still alive.
    for _ in $(seq 1 5); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 1
    done
    if kill -0 "$pid" 2>/dev/null; then
        kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
    fi
    rm -f "$file"
}

stop_from_pidfile "$ADMIN_PID_FILE" "admin-panel"
stop_from_pidfile "$API_PID_FILE"   "api-server"

if [[ "$DOCKER_DOWN" -eq 1 ]]; then
    echo "==> docker compose down"
    # --remove-orphans also tears down the monitoring overlay (up.sh --monitoring)
    # and the one-shot ollama-bootstrap: same project, not in these two files.
    docker compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml down --remove-orphans
fi

echo ""
echo "Done."
