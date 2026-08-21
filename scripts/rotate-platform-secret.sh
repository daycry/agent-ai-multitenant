#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Propagate a rotated platform secret: Vault KV -> .env -> coordinated restart.
#
# Plan prod-05 task_prod05_06 (finding gap2-2) · ADR 0144.
#
# Why this exists
# ---------------
# The weekly rotation job writes the NEW value into Vault KV and marks the entry
# `pending_apply=true`. Nothing else happens: api-server and workers keep reading
# the OLD value from their environment, because ADR 0144 chose "regenerate the
# env + restart in one window" over "read Vault at runtime" (Docker Compose on a
# single machine; the dual-accept JWT ring removes the cut-over).
#
# Until today that propagation was eight manual steps in the runbook, executed
# under pressure right after a credential leak. That is exactly where steps 2 and
# 3 of add-then-remove get swapped — and swapping them REVOKES the credential
# every service is still using, taking the platform's object storage down
# (risk 4 of the plan). This script makes the order un-swappable.
#
# What it does, in this order and no other
# ----------------------------------------
#   1. read the rotated value from Vault KV (`secret/platform/<name>`);
#   2. rewrite the `.env`:
#        jwt   -> PREPENDS the new key to API_SERVER_JWT_SECRETS, KEEPING the
#                 previous ring, so sessions and agent tokens in flight survive
#                 (task_prod05_04). Retiring the old key is a SEPARATE, later
#                 step — see the runbook.
#        minio -> writes both halves of the new credential.
#   3. restart the affected services in the same window;
#   4. minio only: NOW revoke the previous credential.
#
# The secret value never reaches stdout: it goes from KV to the `.env` and
# nowhere else. A rotation transcript ends up pasted into a ticket more often
# than anyone would like.
#
# Usage:
#   ./scripts/rotate-platform-secret.sh jwt   [options]
#   ./scripts/rotate-platform-secret.sh minio [options]
#
# Options:
#   --env-file FILE      .env to rewrite (default: docker/.env)
#   --compose-file FILE  repeatable; default: docker/docker-compose.yml plus
#                        docker/docker-compose.manuals.yml when it exists
#   --services "a b c"   services to restart (default depends on the secret)
#   --mount NAME         Vault KV mount (default: secret)
#   --dry-run            say what would change; touch nothing
#   --yes                do not ask for confirmation
#   --force              propagate even if the KV entry is not pending_apply
#
# NOTE for the dev/manuals stack: it hardcodes its secrets inline in the compose
# instead of reading them from `.env`, so this script has nothing to rewrite
# there. It is meant for a deployment whose compose references ${VARS}.
# -----------------------------------------------------------------------------
set -euo pipefail

SECRET_NAME="${1:-}"
shift || true

ENV_FILE="docker/.env"
MOUNT="secret"
DRY_RUN=0
ASSUME_YES=0
FORCE=0
SERVICES=""
COMPOSE_FILES=()

die() {
  echo "error: $*" >&2
  exit 1
}

while [ $# -gt 0 ]; do
  case "$1" in
    --env-file) ENV_FILE="${2:-}"; shift 2 ;;
    --compose-file) COMPOSE_FILES+=("${2:-}"); shift 2 ;;
    --services) SERVICES="${2:-}"; shift 2 ;;
    --mount) MOUNT="${2:-}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --yes|-y) ASSUME_YES=1; shift ;;
    --force) FORCE=1; shift ;;
    -h|--help) sed -n '2,55p' "$0"; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

case "$SECRET_NAME" in
  jwt|minio) ;;
  "") die "which secret? usage: $0 <jwt|minio> [options]" ;;
  *)
    die "unknown secret '$SECRET_NAME'. Only 'jwt' and 'minio' have a propagation
       path; everything else is in docs/06-runbooks/05-key-rotation.md and some
       of it has NO rotation path at all (Postgres, §12)."
    ;;
esac

[ -f "$ENV_FILE" ] || die "env file not found: $ENV_FILE (--env-file to point elsewhere)"
command -v docker >/dev/null 2>&1 || die "docker not found in PATH"

if [ ${#COMPOSE_FILES[@]} -eq 0 ]; then
  COMPOSE_FILES=("docker/docker-compose.yml")
  [ -f "docker/docker-compose.manuals.yml" ] && COMPOSE_FILES+=("docker/docker-compose.manuals.yml")
fi
COMPOSE_ARGS=()
for f in "${COMPOSE_FILES[@]}"; do
  COMPOSE_ARGS+=("-f" "$f")
done

# The services that carry each secret. JWT lives in the api-server (it signs and
# verifies human sessions); MinIO credentials live in every service that touches
# object storage — the workers write backup bundles with them.
if [ -z "$SERVICES" ]; then
  case "$SECRET_NAME" in
    jwt) SERVICES="api-server" ;;
    minio) SERVICES="api-server workers workers-aux workers-backup" ;;
  esac
fi

kv_field() {
  # Reads ONE field of the KV entry. `-field` prints the bare value with no JSON
  # around it, which keeps this script free of a jq dependency (not installable
  # on every operator's Git Bash).
  docker compose "${COMPOSE_ARGS[@]}" exec -T vault \
    vault kv get -mount="$MOUNT" -field="$1" "platform/$SECRET_NAME" 2>/dev/null || true
}

env_value() {
  # Last occurrence wins, same as Compose reads a .env.
  grep -E "^$1=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- || true
}

upsert() {
  # Replace the value of KEY, or append the line when the key is absent. Writes
  # through a temp file and moves it into place: an interrupted rewrite must not
  # leave the deployment without a .env.
  local key="$1" value="$2" tmp
  tmp="$(mktemp)"
  chmod 600 "$tmp"
  if grep -qE "^$key=" "$ENV_FILE"; then
    awk -v k="$key" -v v="$value" \
      'index($0, k "=") == 1 { print k "=" v; next } { print }' "$ENV_FILE" >"$tmp"
  else
    cat "$ENV_FILE" >"$tmp"
    printf '%s=%s\n' "$key" "$value" >>"$tmp"
  fi
  mv "$tmp" "$ENV_FILE"
}

confirm() {
  [ "$ASSUME_YES" = "1" ] && return 0
  printf 'Propagate the rotated %s secret and restart [%s]? [y/N] ' "$SECRET_NAME" "$SERVICES"
  read -r reply </dev/tty || reply=""
  case "$reply" in y|Y|yes|YES) return 0 ;; *) die "aborted" ;; esac
}

pending="$(kv_field pending_apply)"
if [ "$pending" != "true" ] && [ "$FORCE" != "1" ]; then
  echo "note: secret/platform/$SECRET_NAME is not marked pending_apply" >&2
  echo "      (nothing rotated, or already propagated). Use --force to go ahead." >&2
  [ "$DRY_RUN" = "1" ] || exit 0
fi

# --- 1. read the rotated value ----------------------------------------------
declare -A NEW_VALUES=()
if [ "$SECRET_NAME" = "jwt" ]; then
  value="$(kv_field value)"
  [ -n "$value" ] || die "secret/platform/jwt has no 'value' field — has the rotation job run?"

  previous="$(env_value API_SERVER_JWT_SECRETS)"
  [ -n "$previous" ] || previous="$(env_value API_SERVER_JWT_SECRET)"

  if [ "$previous" = "$value" ] || [ "${previous%%,*}" = "$value" ]; then
    # Idempotent: re-running after a half-finished window is normal, and a ring
    # with the same key twice is rubbish the operator has to clean by hand at the
    # worst possible moment.
    ring="$previous"
  elif [ -n "$previous" ]; then
    ring="$value,$previous"
  else
    ring="$value"
  fi
  NEW_VALUES[API_SERVER_JWT_SECRETS]="$ring"
else
  access="$(kv_field access_key)"
  secret="$(kv_field secret_key)"
  [ -n "$access" ] && [ -n "$secret" ] || die \
    "secret/platform/minio is missing access_key/secret_key — writing only one
     half would leave the .env describing a credential that does not exist"
  NEW_VALUES[API_SERVER_MINIO_ACCESS_KEY]="$access"
  NEW_VALUES[API_SERVER_MINIO_SECRET_KEY]="$secret"
fi

if [ "$DRY_RUN" = "1" ]; then
  echo "dry-run: would rewrite $ENV_FILE"
  for key in "${!NEW_VALUES[@]}"; do
    echo "  $key = (${#NEW_VALUES[$key]} chars, hidden)"
  done
  echo "dry-run: would restart: $SERVICES"
  [ "$SECRET_NAME" = "minio" ] && echo "dry-run: would then revoke the previous MinIO credential"
  exit 0
fi

confirm

# --- 2. rewrite the .env -----------------------------------------------------
for key in "${!NEW_VALUES[@]}"; do
  upsert "$key" "${NEW_VALUES[$key]}"
  echo "wrote $key to $ENV_FILE (value hidden)"
done

# --- 3. restart, in the same window -----------------------------------------
# shellcheck disable=SC2086 - SERVICES is a deliberate word list
docker compose "${COMPOSE_ARGS[@]}" up -d $SERVICES
echo "restarted: $SERVICES"

# --- 4. and ONLY NOW revoke the previous credential -------------------------
if [ "$SECRET_NAME" = "minio" ]; then
  docker compose "${COMPOSE_ARGS[@]}" exec -T workers \
    python -m workers.rotation_apply --revoke-previous-minio
  echo "revoked the previous MinIO credential; pending_apply cleared"
else
  echo
  echo "The previous JWT key is STILL in the ring on purpose: sessions and agent"
  echo "tokens minted with it are still in flight. Retire it in a SECOND pass,"
  echo "after the maximum token TTL — docs/06-runbooks/05-key-rotation.md §1."
fi
