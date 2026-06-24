#!/bin/sh
# -----------------------------------------------------------------------------
# Auto-init + auto-unseal companion for the PERSISTENT (file-backend) Vault of
# the manuals/dev stack (Camino B). Idempotent, long-running loop:
#
#   1. Wait for Vault to answer.
#   2. Initialise it ONCE (Shamir 5/3), storing the unseal keys + root token on
#      the `vault_init` docker volume.
#   3. Unseal it after EVERY (re)start — survives container restarts and host
#      reboots with zero manual steps.
#   4. Enable KV v2 at secret/ and mint a FIXED, non-expiring token whose id is
#      the same one the apps already carry (VAULT_FIXED_TOKEN, default
#      `dev-root-token`), so api-server/workers keep working with their static
#      VAULT_TOKEN env — unchanged.
#
# Secrets written by the apps then live on the `vault_data` volume and SURVIVE
# restarts (unlike the previous `-dev` in-memory Vault, which wiped them).
#
# SECURITY TRADE-OFF (intentional, dev/single-machine only): the unseal keys
# live on a local docker volume next to Vault, so unsealing is zero-touch. This
# is NOT the production posture (there the 5 keys go to 5 separate secure
# stores). See docs/03-guides/gotchas/ for the rationale.
# -----------------------------------------------------------------------------
set -u

VAULT_ADDR="${VAULT_ADDR:-http://vault:8200}"
export VAULT_ADDR
INIT_DIR=/vault/init
KEYS_FILE="$INIT_DIR/unseal-keys.txt"
ROOT_FILE="$INIT_DIR/root-token.txt"
FIXED_TOKEN="${VAULT_FIXED_TOKEN:-dev-root-token}"
SHARES="${VAULT_KEY_SHARES:-5}"
THRESHOLD="${VAULT_KEY_THRESHOLD:-3}"

mkdir -p "$INIT_DIR"
log() { echo "[vault-unsealer] $*"; }

# 'vault status' exits 0 (unsealed), 2 (sealed) or other (down/unreachable). Wait
# until it answers either 0 or 2 — both mean the server is up (capture the REAL
# exit code directly; negating it in the loop test would mask 2 as 0).
log "waiting for Vault at $VAULT_ADDR ..."
while true; do
  vault status >/dev/null 2>&1 && break
  code=$?
  [ "$code" = "2" ] && break
  sleep 2
done

# ---- Initialise once ----
if ! vault status 2>/dev/null | grep -qi 'Initialized.*true'; then
  log "initialising Vault (shares=$SHARES threshold=$THRESHOLD)"
  if vault operator init -key-shares="$SHARES" -key-threshold="$THRESHOLD" >"$INIT_DIR/init.txt" 2>&1; then
    grep '^Unseal Key' "$INIT_DIR/init.txt" | sed 's/.*: //' >"$KEYS_FILE"
    grep '^Initial Root Token' "$INIT_DIR/init.txt" | sed 's/.*: //' >"$ROOT_FILE"
    chmod 600 "$KEYS_FILE" "$ROOT_FILE" "$INIT_DIR/init.txt" 2>/dev/null || true
    log "initialised; keys stored on the vault_init volume"
  else
    log "init failed (already initialised by another run?) — continuing"
  fi
fi

unseal() {
  [ -f "$KEYS_FILE" ] || { log "no unseal keys on disk — cannot unseal"; return 1; }
  i=0
  while IFS= read -r key; do
    [ "$i" -ge "$THRESHOLD" ] && break
    [ -z "$key" ] && continue
    vault operator unseal "$key" >/dev/null 2>&1 || true
    i=$((i + 1))
  done <"$KEYS_FILE"
}

ensure_kv_and_token() {
  [ -f "$ROOT_FILE" ] || return 0
  VAULT_TOKEN="$(cat "$ROOT_FILE")"
  export VAULT_TOKEN
  vault secrets list 2>/dev/null | grep -q '^secret/' ||
    vault secrets enable -version=2 -path=secret kv >/dev/null 2>&1 || true
  # Fixed, effectively non-expiring token (periodic, 10y) with the id the apps use.
  vault token lookup "$FIXED_TOKEN" >/dev/null 2>&1 ||
    vault token create -id="$FIXED_TOKEN" -policy=root -orphan -period=87600h \
      >/dev/null 2>&1 || true
  unset VAULT_TOKEN
}

# ---- Ensure-unsealed loop (re-unseals after any Vault restart/reboot) ----
log "entering unseal loop"
while true; do
  status="$(vault status 2>/dev/null || true)"
  if echo "$status" | grep -qi 'Sealed.*true'; then
    log "Vault is sealed → unsealing"
    unseal
    sleep 1
    status="$(vault status 2>/dev/null || true)"
  fi
  if echo "$status" | grep -qi 'Sealed.*false'; then
    ensure_kv_and_token
  fi
  sleep 10
done
