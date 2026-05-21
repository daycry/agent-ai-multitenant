#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# scripts/dev/up.sh
#
# Linux/macOS port of up.ps1. Starts the dev stack (docker + api-server +
# admin-panel) DETACHED. Logs to .dev/*.log, PIDs to .dev/*.pid (both
# gitignored). Use scripts/dev/down.sh to stop.
#
# Usage:
#   ./scripts/dev/up.sh
#   ./scripts/dev/up.sh --api-port 8002 --admin-port 3001
# -----------------------------------------------------------------------------
set -euo pipefail

API_PORT="${API_PORT:-8001}"
ADMIN_PORT="${ADMIN_PORT:-3000}"

usage() {
    cat <<EOF
Usage: $0 [options]
  --api-port <port>     uvicorn port (default: 8001)
  --admin-port <port>   Next dev port (default: 3000)
  -h, --help            Show this help
EOF
}
while [[ $# -gt 0 ]]; do
    case "$1" in
        --api-port)   API_PORT="$2"; shift 2 ;;
        --admin-port) ADMIN_PORT="$2"; shift 2 ;;
        -h|--help)    usage; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; usage >&2; exit 1 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

DEV_DIR="$REPO_ROOT/.dev"
mkdir -p "$DEV_DIR"
API_PID_FILE="$DEV_DIR/api-server.pid"
ADMIN_PID_FILE="$DEV_DIR/admin-panel.pid"
API_LOG="$DEV_DIR/api-server.log"
API_ERR="$DEV_DIR/api-server.err.log"
ADMIN_LOG="$DEV_DIR/admin-panel.log"
ADMIN_ERR="$DEV_DIR/admin-panel.err.log"

COMPOSE_ARGS=(-f docker/docker-compose.yml -f docker/docker-compose.dev.yml)
VENV_PY="$REPO_ROOT/.venv/bin/python"
if [[ ! -x "$VENV_PY" ]]; then
    echo "ERROR: .venv missing. Run ./scripts/dev/bootstrap.sh first." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Reject "already running" -- ask the user to run down.sh first.
# ---------------------------------------------------------------------------
check_already() {
    local file="$1" name="$2"
    if [[ -f "$file" ]]; then
        local old_pid
        old_pid="$(cat "$file" 2>/dev/null || true)"
        if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null; then
            echo "ERROR: $name already running (pid $old_pid)." >&2
            echo "       Run ./scripts/dev/down.sh first, or delete $file if stale." >&2
            exit 1
        fi
        rm -f "$file"
    fi
}
check_already "$API_PID_FILE" "api-server"
check_already "$ADMIN_PID_FILE" "admin-panel"

port_free() {
    # Try to bind via python; if it succeeds, the port is free.
    "$VENV_PY" - "$1" <<'PY'
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    s.bind(("127.0.0.1", int(sys.argv[1])))
    sys.exit(0)
except OSError:
    sys.exit(1)
finally:
    s.close()
PY
}
port_free "$API_PORT"   || { echo "ERROR: port $API_PORT is in use." >&2; exit 1; }
port_free "$ADMIN_PORT" || { echo "ERROR: port $ADMIN_PORT is in use." >&2; exit 1; }

# ---------------------------------------------------------------------------
# Docker stack
# ---------------------------------------------------------------------------
echo "==> Bringing docker stack up"
docker compose "${COMPOSE_ARGS[@]}" up -d

echo "==> Waiting for postgres to be healthy (max 60s)"
pg_deadline=$(( $(date +%s) + 60 ))
pg_health=""
while [[ "$(date +%s)" -lt "$pg_deadline" ]]; do
    pg_health="$(
        docker compose "${COMPOSE_ARGS[@]}" ps postgres --format json 2>/dev/null \
        | python3 -c "
import json, sys
try:
    d = json.loads(sys.stdin.read() or '{}')
    print(d.get('Health', ''))
except Exception:
    pass
" || true
    )"
    [[ "$pg_health" == "healthy" ]] && break
    sleep 2
done
if [[ "$pg_health" != "healthy" ]]; then
    echo "ERROR: postgres did not become healthy within 60s" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Alembic migrations
# ---------------------------------------------------------------------------
echo "==> Applying Alembic migrations"
(
    export DATABASE_URL="postgresql+asyncpg://migrations_user:changeme-migrations-dev-only@localhost:15432/agentic_platform"
    cd "$REPO_ROOT/apps/api-server"
    "$VENV_PY" -m alembic upgrade head
)

# ---------------------------------------------------------------------------
# api-server (uvicorn) — detached via nohup + & + disown
# ---------------------------------------------------------------------------
export API_SERVER_DATABASE_URL="postgresql+asyncpg://app_user:changeme-app-dev-only@localhost:15432/agentic_platform"
export API_SERVER_ADMIN_DATABASE_URL="postgresql+asyncpg://migrations_user:changeme-migrations-dev-only@localhost:15432/agentic_platform"
export API_SERVER_REDIS_URL="redis://localhost:6379/0"
export API_SERVER_JWT_SECRET="dev-only-jwt-secret-change-me"

rm -f "$API_LOG" "$API_ERR"
echo "==> Starting api-server on http://127.0.0.1:$API_PORT (logs: $API_LOG)"
(
    cd "$REPO_ROOT/apps/api-server"
    setsid nohup "$VENV_PY" -m uvicorn api_server.main:app --port "$API_PORT" \
        > "$API_LOG" 2> "$API_ERR" < /dev/null &
    echo $! > "$API_PID_FILE"
)
API_PID="$(cat "$API_PID_FILE")"

echo "==> Waiting for /healthz (max 30s)"
hz_deadline=$(( $(date +%s) + 30 ))
api_up=0
while [[ "$(date +%s)" -lt "$hz_deadline" ]]; do
    if ! kill -0 "$API_PID" 2>/dev/null; then
        echo "ERROR: api-server exited prematurely. See $API_ERR" >&2
        tail -n 30 "$API_ERR" >&2 || true
        rm -f "$API_PID_FILE"
        exit 1
    fi
    if curl -sf "http://127.0.0.1:$API_PORT/healthz" >/dev/null 2>&1; then
        api_up=1
        break
    fi
    sleep 1
done
if [[ "$api_up" -ne 1 ]]; then
    echo "ERROR: api-server /healthz did not respond within 30s. See $API_LOG / $API_ERR" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# admin-panel (next dev) — detached
# ---------------------------------------------------------------------------
if [[ ! -d "$REPO_ROOT/apps/admin-panel/node_modules/next" ]]; then
    echo "ERROR: admin-panel deps missing. Run: (cd apps/admin-panel && npm install)" >&2
    exit 1
fi

export NEXT_PUBLIC_API_URL="http://127.0.0.1:$API_PORT"
rm -f "$ADMIN_LOG" "$ADMIN_ERR"
echo "==> Starting admin-panel on http://localhost:$ADMIN_PORT (logs: $ADMIN_LOG)"
(
    cd "$REPO_ROOT/apps/admin-panel"
    setsid nohup npm run dev -- -p "$ADMIN_PORT" \
        > "$ADMIN_LOG" 2> "$ADMIN_ERR" < /dev/null &
    echo $! > "$ADMIN_PID_FILE"
)
ADMIN_PID="$(cat "$ADMIN_PID_FILE")"

echo "==> Waiting for admin-panel to compile (max 60s)"
ap_deadline=$(( $(date +%s) + 60 ))
admin_up=0
while [[ "$(date +%s)" -lt "$ap_deadline" ]]; do
    if ! kill -0 "$ADMIN_PID" 2>/dev/null; then
        echo "ERROR: admin-panel exited prematurely. See $ADMIN_ERR" >&2
        tail -n 30 "$ADMIN_ERR" >&2 || true
        rm -f "$ADMIN_PID_FILE"
        exit 1
    fi
    if curl -sf "http://127.0.0.1:$ADMIN_PORT/" >/dev/null 2>&1; then
        admin_up=1
        break
    fi
    sleep 2
done
if [[ "$admin_up" -ne 1 ]]; then
    echo "ERROR: admin-panel did not start within 60s. See $ADMIN_LOG / $ADMIN_ERR" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# URLs + handoff
# ---------------------------------------------------------------------------
cat <<EOF

================================================================
Dev stack is up. You can close this terminal.
================================================================

  Admin panel:   http://localhost:$ADMIN_PORT/login
                 (root@example.com / longenoughpw)
  API docs:      http://127.0.0.1:$API_PORT/docs
  API healthz:   http://127.0.0.1:$API_PORT/healthz
  MinIO:         http://localhost:9001 (minioadmin / changeme-dev-only)
  Vault UI:      http://localhost:8200/ui (token: dev-root-token)

  Logs:   $DEV_DIR/*.log
  Stop:   ./scripts/dev/down.sh
EOF
