#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Bootstrap an unsealed Vault — WITHOUT ever writing a secret in cleartext.
#
# Idempotent: if Vault is already initialized, the script exits 0 without
# doing anything destructive. Otherwise:
#
#   1. Initialize with Shamir sharing (5 shares, threshold 3 by default).
#   2. Hand the unseal keys + root token over in ONE of two ways:
#        * default  — encrypt the raw init output to the operator's public key
#                     (age or gpg) at $OUTPUT_DIR/vault-init.<age|gpg>.
#        * --print-once — print them to stdout ONLY, nothing touches the disk.
#   3. Unseal Vault using `threshold` keys held IN MEMORY (never read back
#      from a file).
#   4. Enable KV v2 at secret/.
#
# Why (plan prod-10 task_prod10_02, findings secrets-1 / deploy-11)
# ------------------------------------------------------------------
# This script used to write `unseal-keys.txt`, `root-token.txt` and
# `init-response.json` in cleartext (mode 600) and print a "CRITICAL next
# steps" banner telling the operator to custody them and `shred` the copies.
# Measured on 2026-06-10: the REAL unseal keys and root token of the running
# Vault had been sitting in `vault-init-output/` for three weeks, readable by
# every process on the machine — including the AI agents that work on this
# repo. A procedure whose security step depends on a human remembering is not
# a procedure. So the cleartext path is GONE: there is no flag, no env var and
# no fallback that writes a `.txt`.
#
# Fail-fast ordering matters: the recipient / encryptor check runs BEFORE
# `vault operator init`. If it failed after, the 5 shares and the root token
# would have been generated and lost in the same command — Vault initialized,
# sealed and unrecoverable. There is no second attempt.
#
# Designed to run from the repo root *after* `docker compose up -d vault`
# has the vault container healthy. The script talks to Vault through
# `docker compose exec`, so no host-side vault CLI is required. It needs no
# Python either (the previous version shelled out to python3 to strip a BOM).
#
# Usage:
#   VAULT_INIT_RECIPIENT=age1... ./scripts/init-vault.sh
#   VAULT_INIT_RECIPIENT=ops@example.com VAULT_INIT_ENCRYPT=gpg ./scripts/init-vault.sh
#   ./scripts/init-vault.sh --print-once          # reveal on stdout, write nothing
#
# Env vars:
#   COMPOSE_FILE              docker-compose.yml (default: docker/docker-compose.yml)
#   VAULT_KEY_SHARES          (default: 5)
#   VAULT_KEY_THRESHOLD       (default: 3)
#   VAULT_INIT_OUTPUT_DIR     where the ENCRYPTED blob goes
#                             (default: ./vault-init-output)
#   VAULT_INIT_RECIPIENT      age public key or gpg recipient. REQUIRED unless
#                             --print-once.
#   VAULT_INIT_ENCRYPT        auto | age | gpg (default: auto — age if present)
# -----------------------------------------------------------------------------
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker/docker-compose.yml}"
KEY_SHARES="${VAULT_KEY_SHARES:-5}"
KEY_THRESHOLD="${VAULT_KEY_THRESHOLD:-3}"
OUTPUT_DIR="${VAULT_INIT_OUTPUT_DIR:-./vault-init-output}"
RECIPIENT="${VAULT_INIT_RECIPIENT:-}"
ENCRYPT="${VAULT_INIT_ENCRYPT:-auto}"
MODE="encrypt"

die() {
  echo "error: $*" >&2
  exit 1
}

usage() {
  sed -n '2,55p' "$0" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
  case "$1" in
    --print-once) MODE="print-once" ;;
    -h | --help)
      usage
      exit 0
      ;;
    *) die "unknown argument: $1 (try --help)" ;;
  esac
  shift
done

# ---------------------------------------------------------------------------
# 0) PREFLIGHT — everything that can fail must fail HERE, before `operator
#    init` generates material that cannot be regenerated.
# ---------------------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  die "docker not found on PATH."
fi

dexec() {
  docker compose -f "$COMPOSE_FILE" exec -T vault "$@"
}

# ---------------------------------------------------------------------------
# 1) Already initialized? Then this is a no-op (idempotent by contract), and it
#    must stay a no-op WITHOUT demanding an encryption recipient: an operator
#    re-running the script to check state should not need an age key for a path
#    that generates no secret.
# ---------------------------------------------------------------------------
if dexec vault status 2>/dev/null | grep -qi 'Initialized.*true'; then
  echo "==> Vault is already initialized."
  echo "    To unseal an already-initialized but sealed Vault, run:"
  echo "      docker compose -f $COMPOSE_FILE exec vault vault operator unseal"
  echo "    Or follow docs/06-runbooks/restart-services.md (unseal is step 1"
  echo "    after any host reboot)."
  exit 0
fi

ENCRYPT_CMD=""
if [ "$MODE" = "encrypt" ]; then
  if [ -z "$RECIPIENT" ]; then
    die "$(
      cat <<'MSG'
no place to put the unseal keys.

This script no longer writes secrets in cleartext (plan prod-10, finding
secrets-1). Pick one:

  * Encrypt to your public key (recommended):
      VAULT_INIT_RECIPIENT=age1...          ./scripts/init-vault.sh
      VAULT_INIT_RECIPIENT=ops@example.com VAULT_INIT_ENCRYPT=gpg ./scripts/init-vault.sh

  * Reveal once on stdout and store them yourself, right now:
      ./scripts/init-vault.sh --print-once

Generate an age key with `age-keygen -o ~/.age/vault.key` (the PUBLIC line it
prints is the recipient). Keep the private key OUT of this repo.
MSG
    )"
  fi

  case "$ENCRYPT" in
    auto)
      if command -v age >/dev/null 2>&1; then
        ENCRYPT_CMD="age"
      elif command -v gpg >/dev/null 2>&1; then
        ENCRYPT_CMD="gpg"
      else
        die "neither 'age' nor 'gpg' on PATH; install one, or use --print-once."
      fi
      ;;
    age)
      command -v age >/dev/null 2>&1 || die "VAULT_INIT_ENCRYPT=age but 'age' is not on PATH."
      ENCRYPT_CMD="age"
      ;;
    gpg)
      command -v gpg >/dev/null 2>&1 || die "VAULT_INIT_ENCRYPT=gpg but 'gpg' is not on PATH."
      ENCRYPT_CMD="gpg"
      ;;
    *) die "VAULT_INIT_ENCRYPT must be one of: auto, age, gpg (got '$ENCRYPT')." ;;
  esac
fi

# ---------------------------------------------------------------------------
# 2) Initialize. The output is captured into a SHELL VARIABLE — it never
#    reaches the filesystem in cleartext, not even briefly.
# ---------------------------------------------------------------------------
echo "==> Initializing Vault (shares=$KEY_SHARES, threshold=$KEY_THRESHOLD)"
INIT_OUTPUT="$(dexec vault operator init \
  -key-shares="$KEY_SHARES" \
  -key-threshold="$KEY_THRESHOLD" 2>&1)" || die "vault operator init failed: $INIT_OUTPUT"

# Text output (not -format=json) on purpose: parsing it needs only grep/sed, so
# the script has no Python dependency and behaves the same on the Windows Git
# Bash of the dev machine as on the CI runner. Same parse as
# docker/vault/auto-unseal.sh, so there is ONE shape to keep working.
UNSEAL_KEYS="$(printf '%s\n' "$INIT_OUTPUT" | grep '^Unseal Key' | sed 's/.*: //' || true)"
ROOT_TOKEN="$(printf '%s\n' "$INIT_OUTPUT" | grep '^Initial Root Token' | sed 's/.*: //' || true)"

if [ -z "$UNSEAL_KEYS" ] || [ -z "$ROOT_TOKEN" ]; then
  # LAST RESORT. Vault is now initialized and the material exists only in this
  # variable: losing it is unrecoverable, so printing it beats discarding it.
  echo "" >&2
  echo "!!! could not parse the init output. Vault IS INITIALIZED and this is" >&2
  echo "!!! the ONLY copy of its unseal keys. SAVE IT NOW, then re-run the" >&2
  echo "!!! unseal manually:" >&2
  echo "" >&2
  printf '%s\n' "$INIT_OUTPUT" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# 3) Hand over the material: encrypted blob, or one-shot reveal.
# ---------------------------------------------------------------------------
OUT_FILE=""
if [ "$MODE" = "print-once" ]; then
  cat <<MSG

==============================================================
SHOWN ONCE — NOT WRITTEN ANYWHERE. Store these NOW.

Unseal keys (${KEY_SHARES} shares, ${KEY_THRESHOLD} needed to unseal):
$UNSEAL_KEYS

Initial root token:
$ROOT_TOKEN
==============================================================
MSG
else
  mkdir -p "$OUTPUT_DIR"
  chmod 700 "$OUTPUT_DIR" 2>/dev/null || true
  case "$ENCRYPT_CMD" in
    age) OUT_FILE="$OUTPUT_DIR/vault-init.age" ;;
    gpg) OUT_FILE="$OUTPUT_DIR/vault-init.gpg" ;;
  esac

  encrypt_ok=0
  if [ "$ENCRYPT_CMD" = "age" ]; then
    printf '%s\n' "$INIT_OUTPUT" | age -r "$RECIPIENT" -o "$OUT_FILE" && encrypt_ok=1
  else
    printf '%s\n' "$INIT_OUTPUT" |
      gpg --batch --yes --trust-model always -e -r "$RECIPIENT" -o "$OUT_FILE" && encrypt_ok=1
  fi

  if [ "$encrypt_ok" != "1" ]; then
    rm -f "$OUT_FILE" 2>/dev/null || true
    echo "" >&2
    echo "!!! encryption FAILED and Vault IS ALREADY INITIALIZED. What follows" >&2
    echo "!!! is the only copy of its unseal keys — save it NOW:" >&2
    echo "" >&2
    printf '%s\n' "$INIT_OUTPUT" >&2
    exit 1
  fi
  chmod 600 "$OUT_FILE" 2>/dev/null || true
fi

# ---------------------------------------------------------------------------
# 4) Unseal (threshold of shares) reading the keys FROM MEMORY.
# ---------------------------------------------------------------------------
echo "==> Unsealing with $KEY_THRESHOLD of $KEY_SHARES keys"
i=0
# IMPORTANT: `docker compose exec` consumes the inherited stdin even with -T,
# which would drain the here-doc after the first iteration. Redirect each
# exec's stdin to /dev/null so the loop keeps reading.
# `if`, not `&& break`: with `set -e` a false `[ ] && break` as the last command
# of the body would abort the script.
while IFS= read -r key; do
  if [ "$i" -ge "$KEY_THRESHOLD" ]; then
    break
  fi
  if [ -z "$key" ]; then
    continue
  fi
  dexec vault operator unseal "$key" </dev/null >/dev/null
  i=$((i + 1))
done <<EOF
$UNSEAL_KEYS
EOF

# ---------------------------------------------------------------------------
# 5) Enable KV v2 at secret/.
# ---------------------------------------------------------------------------
echo "==> Enabling KV v2 at secret/"
if docker compose -f "$COMPOSE_FILE" exec -T \
  -e VAULT_TOKEN="$ROOT_TOKEN" vault \
  vault secrets list -format=json 2>/dev/null | grep -q '"secret/"'; then
  echo "    secret/ already mounted, skipping."
else
  docker compose -f "$COMPOSE_FILE" exec -T \
    -e VAULT_TOKEN="$ROOT_TOKEN" vault \
    vault secrets enable -version=2 -path=secret kv >/dev/null
fi

# ---------------------------------------------------------------------------
# 6) Operator instructions.
# ---------------------------------------------------------------------------
cat <<MSG

==============================================================
Vault initialized and unsealed. KV v2 mounted at secret/.
MSG

if [ -n "$OUT_FILE" ]; then
  cat <<MSG

  Encrypted init material: $OUT_FILE  (mode 600, $ENCRYPT_CMD)
  Decrypt with:
    age    -d -i <your-private-key> $OUT_FILE
    gpg    -d $OUT_FILE
MSG
fi

cat <<MSG

CRITICAL next steps for the operator:

  1. Split the $KEY_SHARES unseal keys into $KEY_SHARES SEPARATE secure stores
     (password managers, sealed envelopes, smartcards, ...). Losing
     $((KEY_SHARES - KEY_THRESHOLD + 1)) of them means the data inside Vault is
     GONE. Decrypt the blob, distribute, and delete the blob.

  2. Save the root token in your personal password manager and DO NOT put it in
     a service config. Mint per-service tokens instead:
       ./scripts/vault-mint-service-tokens.sh

  3. Nothing here is in cleartext, so there is nothing to shred. Verify with:
       python scripts/check_no_secret_artifacts.py --root .

  4. Unseal after a host reboot is step 1 of
     docs/06-runbooks/restart-services.md.

This script is idempotent — re-running it is safe and a no-op if
Vault is already initialized.
==============================================================
MSG
