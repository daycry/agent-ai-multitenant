#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# scripts/dev/run-e2e.sh
#
# All-in-one Playwright runner for task_00_13. Equivalent to
# run-e2e.ps1 but for Linux / macOS / WSL.
#
# Steps:
#   1. docker compose up -d (idempotent).
#   2. Wait for postgres to be healthy.
#   3. alembic upgrade head.
#   4. Launch uvicorn on $API_PORT in the background, log to files.
#   5. Wait for /healthz to respond.
#   6. Register the admin user (skip on 409) and UPDATE is_system_admin.
#   7. `npm run e2e` — Playwright auto-starts its own `npm run dev`
#      thanks to the webServer block in playwright.config.ts.
#   8. trap EXIT tears down the background uvicorn even on failure.
#
# Usage:
#   ./scripts/dev/run-e2e.sh
#   ./scripts/dev/run-e2e.sh --api-port 8002
#   ./scripts/dev/run-e2e.sh --admin-email other@x.test --admin-password s3cret
#
# Requires: bash 4+, docker, python3 (system or .venv), .venv created
# via scripts/dev/bootstrap.sh, apps/admin-panel npm install done,
# Playwright browsers installed (npm run e2e:install once).
# -----------------------------------------------------------------------------
set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults + arg parsing
# ---------------------------------------------------------------------------
ADMIN_EMAIL="${E2E_ADMIN_EMAIL:-root@example.com}"
ADMIN_PASSWORD="${E2E_ADMIN_PASSWORD:-longenoughpw}"
API_PORT="${API_PORT:-8001}"

usage() {
    cat <<EOF
Usage: $0 [options]
  --admin-email <email>      System Admin email (default: root@example.com)
  --admin-password <pw>      Password (default: longenoughpw)
  --api-port <port>          uvicorn port (default: 8001)
  -h, --help                 Show this help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --admin-email)    ADMIN_EMAIL="$2"; shift 2 ;;
        --admin-password) ADMIN_PASSWORD="$2"; shift 2 ;;
        --api-port)       API_PORT="$2"; shift 2 ;;
        -h|--help)        usage; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; usage >&2; exit 1 ;;
    esac
done

# ---------------------------------------------------------------------------
# Locate repo root + sanity checks
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"
echo "==> Repo root: $REPO_ROOT"

VENV_PY="$REPO_ROOT/.venv/bin/python"
if [[ ! -x "$VENV_PY" ]]; then
    echo "ERROR: .venv not found at $REPO_ROOT/.venv" >&2
    echo "       Run ./scripts/dev/bootstrap.sh first." >&2
    exit 1
fi

COMPOSE_ARGS=(-f docker/docker-compose.yml -f docker/docker-compose.dev.yml)
API_LOG="$REPO_ROOT/.e2e-api-server.log"
API_ERR="$REPO_ROOT/.e2e-api-server.err.log"
API_PID=""

# ---------------------------------------------------------------------------
# Cleanup on any exit (success, failure, or Ctrl-C)
# ---------------------------------------------------------------------------
cleanup() {
    local exit_code=$?
    if [[ -n "$API_PID" ]] && kill -0 "$API_PID" 2>/dev/null; then
        echo "==> Stopping api-server (pid $API_PID)"
        kill "$API_PID" 2>/dev/null || true
        # Give uvicorn 5s to wind down its async loop, then SIGKILL.
        for _ in $(seq 1 5); do
            kill -0 "$API_PID" 2>/dev/null || break
            sleep 1
        done
        kill -9 "$API_PID" 2>/dev/null || true
        wait "$API_PID" 2>/dev/null || true
    fi
    # The docker stack stays UP. Stop it manually with:
    #   docker compose -f docker/docker-compose.yml \
    #                  -f docker/docker-compose.dev.yml down
    exit "$exit_code"
}
trap cleanup EXIT INT TERM

# ---------------------------------------------------------------------------
# 1) Docker stack up (idempotent)
# ---------------------------------------------------------------------------
echo "==> Ensuring docker stack is up"
docker compose "${COMPOSE_ARGS[@]}" up -d

# ---------------------------------------------------------------------------
# 2) Wait for postgres healthy
# ---------------------------------------------------------------------------
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
# 3) Alembic migrations
# ---------------------------------------------------------------------------
echo "==> Applying Alembic migrations"
(
    export DATABASE_URL="postgresql+asyncpg://migrations_user:changeme-migrations-dev-only@localhost:15432/agentic_platform"
    cd "$REPO_ROOT/apps/api-server"
    "$VENV_PY" -m alembic upgrade head
)

# ---------------------------------------------------------------------------
# 4) Launch uvicorn in background
# ---------------------------------------------------------------------------
echo "==> Starting api-server on http://localhost:$API_PORT (logs: $API_LOG)"

export API_SERVER_DATABASE_URL="postgresql+asyncpg://app_user:changeme-app-dev-only@localhost:15432/agentic_platform"
export API_SERVER_ADMIN_DATABASE_URL="postgresql+asyncpg://migrations_user:changeme-migrations-dev-only@localhost:15432/agentic_platform"
export API_SERVER_REDIS_URL="redis://localhost:6379/0"
export API_SERVER_JWT_SECRET="dev-only-jwt-secret-change-me"

rm -f "$API_LOG" "$API_ERR"

(
    cd "$REPO_ROOT/apps/api-server"
    exec "$VENV_PY" -m uvicorn api_server.main:app --port "$API_PORT"
) > "$API_LOG" 2> "$API_ERR" &
API_PID=$!

# ---------------------------------------------------------------------------
# 5) Wait for /healthz
# ---------------------------------------------------------------------------
echo "==> Waiting for /healthz (max 30s)"
hz_deadline=$(( $(date +%s) + 30 ))
api_up=0
while [[ "$(date +%s)" -lt "$hz_deadline" ]]; do
    if ! kill -0 "$API_PID" 2>/dev/null; then
        echo "ERROR: api-server exited prematurely. Last 30 lines of stderr:" >&2
        tail -n 30 "$API_ERR" >&2 || true
        exit 1
    fi
    if curl -sf "http://localhost:$API_PORT/healthz" >/dev/null 2>&1; then
        api_up=1
        break
    fi
    sleep 1
done
if [[ "$api_up" -ne 1 ]]; then
    echo "ERROR: api-server /healthz did not respond within 30s" >&2
    echo "    Last 30 lines of stdout:" >&2
    tail -n 30 "$API_LOG" >&2 || true
    echo "    Last 30 lines of stderr:" >&2
    tail -n 30 "$API_ERR" >&2 || true
    exit 1
fi

# ---------------------------------------------------------------------------
# 6) Register + promote admin
# ---------------------------------------------------------------------------
echo "==> Ensuring admin user '$ADMIN_EMAIL' is registered + promoted"
register_body=$(python3 -c "
import json
print(json.dumps({'email': '$ADMIN_EMAIL', 'password': '$ADMIN_PASSWORD', 'full_name': 'E2E Admin'}))
")
register_code=$(
    curl -sS -o /dev/null -w "%{http_code}" \
        -X POST \
        -H "Content-Type: application/json" \
        -d "$register_body" \
        "http://localhost:$API_PORT/auth/register" || echo "000"
)
case "$register_code" in
    201) echo "    Registered new user." ;;
    409) echo "    User already exists (409) -- continuing." ;;
    *)
        echo "ERROR: register failed with HTTP $register_code" >&2
        exit 1
        ;;
esac

# Promote (idempotent).
docker compose "${COMPOSE_ARGS[@]}" exec -T postgres \
    psql -U postgres -d agentic_platform \
    -c "UPDATE users SET is_system_admin = true WHERE email = '$ADMIN_EMAIL'" \
    >/dev/null

# ---------------------------------------------------------------------------
# 7) Run Playwright (which auto-starts npm run dev via webServer:)
# ---------------------------------------------------------------------------
echo "==> Running Playwright"
playwright_exit=0
(
    cd "$REPO_ROOT/apps/admin-panel"
    export E2E_ADMIN_EMAIL="$ADMIN_EMAIL"
    export E2E_ADMIN_PASSWORD="$ADMIN_PASSWORD"
    npm run e2e
) || playwright_exit=$?

if [[ "$playwright_exit" -ne 0 ]]; then
    echo ""
    echo "==> Playwright FAILED (exit $playwright_exit)."
    echo "    Last 30 lines of api-server stdout:"
    tail -n 30 "$API_LOG" || true
    exit "$playwright_exit"
fi

echo ""
echo "OK. Playwright passed."
