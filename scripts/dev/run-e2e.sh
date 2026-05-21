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
# 2b) Port preflight + stray-uvicorn cleanup.
# If a previous run crashed (or Ctrl-C beat the trap), a stray uvicorn may
# still own $API_PORT. Kill ours and proceed; anything that doesn't look
# like uvicorn is foreign and we bail rather than nuke it.
# ---------------------------------------------------------------------------
if command -v lsof >/dev/null 2>&1; then
    port_owner_pid="$(lsof -ti "tcp:$API_PORT" -sTCP:LISTEN 2>/dev/null | head -n1 || true)"
    if [[ -n "$port_owner_pid" ]]; then
        port_cmd="$(ps -p "$port_owner_pid" -o command= 2>/dev/null || true)"
        # 'ours' if any of:
        #   - command line contains 'uvicorn' (parent process)
        #   - command line is a multiprocessing-spawn worker that uvicorn
        #     created on macOS / Python 3.8+ (on Linux uvicorn uses fork,
        #     so the worker keeps 'uvicorn' in argv -- spawn workers don't)
        #   - executable path is our repo's .venv python
        if [[ "$port_cmd" == *uvicorn* ]] \
           || [[ "$port_cmd" == *spawn_main* ]] \
           || [[ "$port_cmd" == *multiprocessing* ]] \
           || [[ "$port_cmd" == *"$REPO_ROOT/.venv/"* ]]; then
            echo "==> Port $API_PORT held by stray uvicorn (pid $port_owner_pid). Killing tree."
            # Kill the process group if we can detect it, else just the pid.
            owner_pgid="$(ps -o pgid= -p "$port_owner_pid" 2>/dev/null | tr -d ' ' || true)"
            if [[ -n "$owner_pgid" ]]; then
                kill -- "-$owner_pgid" 2>/dev/null || true
            fi
            kill "$port_owner_pid" 2>/dev/null || true
            freed=0
            for _ in $(seq 1 10); do
                sleep 0.5
                if ! lsof -ti "tcp:$API_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
                    freed=1
                    break
                fi
            done
            if [[ "$freed" -ne 1 ]]; then
                # Hardest case: kill -9 any remaining listener PIDs.
                for rpid in $(lsof -ti "tcp:$API_PORT" -sTCP:LISTEN 2>/dev/null || true); do
                    kill -9 "$rpid" 2>/dev/null || true
                done
                sleep 1
                if lsof -ti "tcp:$API_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
                    echo "ERROR: port $API_PORT still busy after kill -9" >&2
                    exit 1
                fi
            fi
        else
            echo "ERROR: port $API_PORT held by pid $port_owner_pid -- not uvicorn." >&2
            echo "       Command line: $port_cmd" >&2
            echo "       Refusing to kill an unknown process." >&2
            echo "       Stop it manually or rerun with --api-port <free port>." >&2
            exit 1
        fi
    fi
else
    echo "    (lsof not installed; skipping port preflight)" >&2
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
# Each Playwright spec performs a fresh login. The default 5 / 15 min
# limit trips 429 once we have a handful of screen tests; loosen for E2E.
export API_SERVER_LOGIN_RATE_LIMIT_COUNT="1000"
export API_SERVER_LOGIN_RATE_LIMIT_WINDOW_SECONDS="60"

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
    if curl -sf "http://127.0.0.1:$API_PORT/healthz" >/dev/null 2>&1; then
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
#
# Login-first to handle the case where the email already exists in the DB
# with a DIFFERENT password. A naive 409-is-ok branch would let the script
# "succeed" and Playwright would later fail with 401.
#
# We also clear Redis rate-limit keys for the login endpoint before probing:
# repeated dev runs accumulate failures and trip the 429 brownout, which
# would then mask actual password mismatches.
# ---------------------------------------------------------------------------
echo "==> Ensuring admin user '$ADMIN_EMAIL' is registered + promoted"

# Wipe per-email + per-IP rate limit counters. Safe in the dev stack.
docker compose "${COMPOSE_ARGS[@]}" exec -T redis redis-cli \
    DEL "rl:login:email:$ADMIN_EMAIL" "rl:login:ip:127.0.0.1" >/dev/null 2>&1 || true

login_body=$(python3 -c "
import json
print(json.dumps({'email': '$ADMIN_EMAIL', 'password': '$ADMIN_PASSWORD'}))
")
register_body=$(python3 -c "
import json
print(json.dumps({'email': '$ADMIN_EMAIL', 'password': '$ADMIN_PASSWORD', 'full_name': 'E2E Admin'}))
")

# Echoes one of: ok | bad-password | rate-limited | no-user | other:<code>
get_admin_login_status() {
    local code
    code=$(
        curl -sS -o /dev/null -w "%{http_code}" \
            -X POST -H "Content-Type: application/json" \
            -d "$login_body" \
            "http://127.0.0.1:$API_PORT/auth/login" || echo "000"
    )
    case "$code" in
        200) echo "ok" ;;
        401) echo "bad-password" ;;
        429) echo "rate-limited" ;;
        404) echo "no-user" ;;
        *)   echo "other:$code" ;;
    esac
}

login_status=$(get_admin_login_status)
case "$login_status" in
    ok)
        echo "    User already exists with the expected password."
        ;;
    rate-limited)
        echo "ERROR: /auth/login is rate-limited even after clearing Redis keys. Wait a minute and rerun." >&2
        exit 1
        ;;
    *)
        register_code=$(
            curl -sS -o /dev/null -w "%{http_code}" \
                -X POST -H "Content-Type: application/json" \
                -d "$register_body" \
                "http://127.0.0.1:$API_PORT/auth/register" || echo "000"
        )
        case "$register_code" in
            201) echo "    Registered new user." ;;
            409)
                echo "ERROR: user '$ADMIN_EMAIL' exists in the DB with a DIFFERENT password than '$ADMIN_PASSWORD'." >&2
                echo "       Either:" >&2
                echo "         - rerun with --admin-password <the password it actually has>, or" >&2
                echo "         - delete it and re-run:" >&2
                echo "             docker compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml exec postgres \\" >&2
                echo "               psql -U postgres -d agentic_platform -c \"DELETE FROM users WHERE email = '$ADMIN_EMAIL'\"" >&2
                exit 1
                ;;
            *)
                echo "ERROR: register failed with HTTP $register_code" >&2
                exit 1
                ;;
        esac
        # Clear rate limits again (the failed login above counted) and re-verify.
        docker compose "${COMPOSE_ARGS[@]}" exec -T redis redis-cli \
            DEL "rl:login:email:$ADMIN_EMAIL" "rl:login:ip:127.0.0.1" >/dev/null 2>&1 || true
        post_status=$(get_admin_login_status)
        if [[ "$post_status" != "ok" ]]; then
            echo "ERROR: registered '$ADMIN_EMAIL' but /auth/login returns '$post_status' -- aborting." >&2
            exit 1
        fi
        ;;
esac

# Promote (idempotent).
docker compose "${COMPOSE_ARGS[@]}" exec -T postgres \
    psql -U postgres -d agentic_platform \
    -c "UPDATE users SET is_system_admin = true WHERE email = '$ADMIN_EMAIL'" \
    >/dev/null

# ---------------------------------------------------------------------------
# 6b) Apply built-in seeds (idempotent). Plan-01 Playwright tests
# assume the 11 built-in agents exist.
# ---------------------------------------------------------------------------
echo "==> Applying built-in seeds"
(
    export API_SERVER_ADMIN_DATABASE_URL="postgresql+asyncpg://migrations_user:changeme-migrations-dev-only@localhost:15432/agentic_platform"
    cd "$REPO_ROOT/apps/api-server"
    "$VENV_PY" -m api_server.seeds
)

# ---------------------------------------------------------------------------
# 7) Run Playwright (which auto-starts npm run dev via webServer:)
# ---------------------------------------------------------------------------
echo "==> Running Playwright"
playwright_exit=0
(
    cd "$REPO_ROOT/apps/admin-panel"
    export E2E_ADMIN_EMAIL="$ADMIN_EMAIL"
    export E2E_ADMIN_PASSWORD="$ADMIN_PASSWORD"
    # Next dev server bakes lib/api.ts's API_URL from NEXT_PUBLIC_API_URL.
    # Without this the browser tests would call the default
    # http://localhost:8001 even when we run uvicorn on a different port.
    export NEXT_PUBLIC_API_URL="http://127.0.0.1:$API_PORT"
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
