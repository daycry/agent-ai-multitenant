#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Mint one PERIODIC Vault token per service, against the policies the installer
# already writes.
#
# Plan prod-10 task_prod10_08 (finding secrets-4).
#
# Why this exists
# ---------------
# `installer_backend.vault_bootstrap` writes four least-privilege ACL policies —
# api-server, workers, orchestrator, notification-dispatcher — each granting read
# on exactly the KV paths that service consumes. And then **nobody mints a token
# against them**: there is no `create_token` call anywhere in the repo, and
# `scripts/init-vault.sh` hands the operator a root token and wishes them luck.
# The practical result is the one the audit found: every service configured with
# the root token, which is the opposite of least privilege AND unrenewable.
#
# Periodic, not fixed-TTL: a periodic token never expires as long as it is
# renewed within its period, which is exactly what
# `api_server.vault_client.VaultTokenManager` now does in the background. A
# plain TTL token would need re-minting on a calendar — the operational burden
# that produced the 32-day time bomb in the first place.
#
# `-orphan`: the token must NOT die when the root token that created it is
# revoked. Revoking the exposed root token (task_prod10_01) is a step the
# operator has to be able to take without taking the platform down with it.
#
# Output
# ------
# By default the tokens go to STDOUT as `.env` lines and NOTHING is written to
# disk — same discipline as `scripts/init-vault.sh`. Pipe them where they belong:
#
#     ./scripts/vault-mint-service-tokens.sh >> docker/.env
#
# `--write FILE` appends to FILE (mode 600) for the same operation in one step.
#
# Usage:
#   VAULT_TOKEN=<root-or-admin> ./scripts/vault-mint-service-tokens.sh
#   ./scripts/vault-mint-service-tokens.sh --period 168h --dry-run
#
# Env vars:
#   COMPOSE_FILE   docker-compose.yml (default: docker/docker-compose.yml)
#   VAULT_TOKEN    a token allowed to create tokens (the root token, once)
#   VAULT_PERIOD   renewal period of the minted tokens (default: 72h)
# -----------------------------------------------------------------------------
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker/docker-compose.yml}"
PERIOD="${VAULT_PERIOD:-72h}"
WRITE_TO=""
DRY_RUN=0

die() {
  echo "error: $*" >&2
  exit 1
}

while [ $# -gt 0 ]; do
  case "$1" in
    --period)
      PERIOD="${2:?--period needs a value (e.g. 72h)}"
      shift
      ;;
    --write)
      WRITE_TO="${2:?--write needs a path}"
      shift
      ;;
    --dry-run) DRY_RUN=1 ;;
    -h | --help)
      sed -n '2,47p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) die "unknown argument: $1 (try --help)" ;;
  esac
  shift
done

# The policy names and the env var each service reads. Kept in lockstep with
# `installer_backend.vault_bootstrap.initial_policies()` by a test
# (tests/unit/test_vault_service_tokens.py) — a bash script cannot import the
# Python source, so the drift is caught in CI instead of at 3am.
#
# The prefixes are the ones each service's Settings actually uses: api-server
# reads API_SERVER_*, workers WORKERS_*, orchestrator ORCHESTRATOR_*, and the
# notification dispatcher NOTIFY_*.
SERVICES="api-server:API_SERVER_VAULT_TOKEN
workers:WORKERS_VAULT_TOKEN
orchestrator:ORCHESTRATOR_VAULT_TOKEN
notification-dispatcher:NOTIFY_VAULT_TOKEN"

if ! command -v docker >/dev/null 2>&1; then
  die "docker not found on PATH."
fi

if [ "$DRY_RUN" = "0" ] && [ -z "${VAULT_TOKEN:-}" ]; then
  die "$(
    cat <<'MSG'
VAULT_TOKEN is not set.

This script needs a token allowed to create tokens — the root token, used ONCE
for exactly this and then put back in your password manager (or revoked, if it
was the exposed one). Decrypt it from the blob scripts/init-vault.sh wrote:

    age -d -i <your-private-key> vault-init-output/vault-init.age

then:

    VAULT_TOKEN=hvs... ./scripts/vault-mint-service-tokens.sh >> docker/.env
MSG
  )"
fi

mint() {
  # `-orphan` so revoking the creating (root) token does NOT cascade-revoke the
  # service tokens; `-period` makes it renewable forever instead of expiring on
  # a calendar; `-field=token` keeps the secret off the parsing path.
  docker compose -f "$COMPOSE_FILE" exec -T \
    -e VAULT_TOKEN="${VAULT_TOKEN:-}" vault \
    vault token create \
    -policy="$1" \
    -period="$PERIOD" \
    -orphan \
    -field=token 2>/dev/null
}

OUTPUT=""
OUTPUT="$OUTPUT# Vault service tokens — minted $(date -u +%Y-%m-%dT%H:%M:%SZ), period=$PERIOD."
OUTPUT="$OUTPUT
# Periodic + orphan: they never expire while the services renew them
# (api_server.vault_client.VaultTokenManager), and revoking the root token does
# NOT revoke these. Rotation: docs/06-runbooks/restart-services.md."

while IFS= read -r entry; do
  service="${entry%%:*}"
  var="${entry##*:}"
  if [ "$DRY_RUN" = "1" ]; then
    echo "would mint: policy=$service period=$PERIOD -orphan -> $var" >&2
    OUTPUT="$OUTPUT
$var=<token for policy '$service'>"
    continue
  fi
  echo "==> minting token for policy '$service' (period=$PERIOD)" >&2
  token="$(mint "$service")" || die "vault token create failed for policy '$service'"
  token="$(printf '%s' "$token" | tr -d '\r\n')"
  [ -n "$token" ] || die "vault returned an empty token for policy '$service'"
  OUTPUT="$OUTPUT
$var=$token"
done <<EOF
$SERVICES
EOF

if [ -n "$WRITE_TO" ]; then
  printf '%s\n' "$OUTPUT" >>"$WRITE_TO"
  chmod 600 "$WRITE_TO" 2>/dev/null || true
  echo "==> appended ${#SERVICES} service tokens to $WRITE_TO (mode 600)" >&2
else
  printf '%s\n' "$OUTPUT"
fi

cat >&2 <<'MSG'

Next:
  1. Point each service at ITS token (the four variables above) and stop using
     the root token in any config.
  2. Put the root token back in your password manager, or revoke it if it was
     the exposed one:
       docker compose -f docker/docker-compose.yml exec vault vault token revoke <token>
     The service tokens survive: they are -orphan.
  3. Confirm nothing landed in the tree by accident:
       python scripts/check_no_secret_artifacts.py --root .
MSG
