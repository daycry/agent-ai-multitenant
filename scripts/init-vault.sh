#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Bootstrap an unsealed Vault.
#
# Idempotent: if Vault is already initialized, the script exits 0 without
# doing anything destructive. Otherwise:
#
#   1. Initialize with Shamir 5-of-5 sharing, threshold 3.
#   2. Persist the 5 unseal keys and the root token to $OUTPUT_DIR with
#      mode 600. The operator is expected to move them to secure storage
#      and `shred -u` the local copies.
#   3. Unseal Vault using 3 of the 5 keys.
#   4. Enable KV v2 at secret/.
#
# Designed to run from the repo root *after* `docker compose up -d vault`
# has the vault container healthy. The script talks to Vault through
# `docker compose exec`, so no host-side vault CLI is required.
#
# Usage:
#   ./scripts/init-vault.sh
#
# Env vars (all optional):
#   COMPOSE_FILE              docker-compose.yml (default: docker/docker-compose.yml)
#   VAULT_KEY_SHARES          (default: 5)
#   VAULT_KEY_THRESHOLD       (default: 3)
#   VAULT_INIT_OUTPUT_DIR     where to write unseal keys + root token
#                             (default: ./vault-init-output)
# -----------------------------------------------------------------------------
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker/docker-compose.yml}"
KEY_SHARES="${VAULT_KEY_SHARES:-5}"
KEY_THRESHOLD="${VAULT_KEY_THRESHOLD:-3}"
OUTPUT_DIR="${VAULT_INIT_OUTPUT_DIR:-./vault-init-output}"

if ! command -v docker >/dev/null 2>&1; then
  echo "error: docker not found on PATH." >&2
  exit 1
fi

dexec() {
  docker compose -f "$COMPOSE_FILE" exec -T vault "$@"
}

# ---------------------------------------------------------------------------
# 1) Already initialized?
# ---------------------------------------------------------------------------
if dexec vault status -format=json 2>/dev/null | grep -q '"initialized": true'; then
  echo "==> Vault is already initialized."
  echo "    To unseal an already-initialized but sealed Vault, run:"
  echo "      docker compose -f $COMPOSE_FILE exec vault vault operator unseal"
  exit 0
fi

# ---------------------------------------------------------------------------
# 2) Initialize — capture JSON to a file directly to avoid any subshell
#    quoting or CRLF surprises.
# ---------------------------------------------------------------------------
mkdir -p "$OUTPUT_DIR"
chmod 700 "$OUTPUT_DIR" 2>/dev/null || true

INIT_JSON_FILE="$OUTPUT_DIR/init-response.json"
UNSEAL_KEYS_FILE="$OUTPUT_DIR/unseal-keys.txt"
ROOT_TOKEN_FILE="$OUTPUT_DIR/root-token.txt"

echo "==> Initializing Vault (shares=$KEY_SHARES, threshold=$KEY_THRESHOLD)"
dexec vault operator init \
  -key-shares="$KEY_SHARES" \
  -key-threshold="$KEY_THRESHOLD" \
  -format=json >"$INIT_JSON_FILE"

# Strip BOM / carriage returns that some Windows pipelines inject.
python3 - "$INIT_JSON_FILE" "$UNSEAL_KEYS_FILE" "$ROOT_TOKEN_FILE" <<'PYEOF'
import json, sys
src, keys_path, token_path = sys.argv[1], sys.argv[2], sys.argv[3]
with open(src, "rb") as f:
    raw = f.read()
# Drop UTF-8 BOM if present, then decode.
if raw.startswith(b"\xef\xbb\xbf"):
    raw = raw[3:]
text = raw.decode("utf-8", errors="replace").replace("\r", "")
data = json.loads(text)
with open(keys_path, "w", encoding="utf-8", newline="\n") as f:
    for k in data["unseal_keys_b64"]:
        f.write(k + "\n")
with open(token_path, "w", encoding="utf-8", newline="\n") as f:
    f.write(data["root_token"] + "\n")
PYEOF

chmod 600 "$UNSEAL_KEYS_FILE" "$ROOT_TOKEN_FILE" "$INIT_JSON_FILE" 2>/dev/null || true

# ---------------------------------------------------------------------------
# 3) Unseal (3 of 5).
# ---------------------------------------------------------------------------
echo "==> Unsealing with $KEY_THRESHOLD of $KEY_SHARES keys"
i=0
# IMPORTANT: `docker compose exec` consumes the inherited stdin even with -T,
# which would drain the loop's redirected file after the first iteration.
# Redirect each exec's stdin to /dev/null so the file FD stays intact.
while IFS= read -r key; do
  if [ "$i" -ge "$KEY_THRESHOLD" ]; then break; fi
  dexec vault operator unseal "$key" </dev/null >/dev/null
  i=$((i + 1))
done <"$UNSEAL_KEYS_FILE"

ROOT_TOKEN="$(cat "$ROOT_TOKEN_FILE")"

# ---------------------------------------------------------------------------
# 4) Enable KV v2 at secret/.
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
# 5) Operator instructions.
# ---------------------------------------------------------------------------
cat <<MSG

==============================================================
Vault initialized and unsealed. KV v2 mounted at secret/.

  Unseal keys:  $UNSEAL_KEYS_FILE   (mode 600)
  Root token:   $ROOT_TOKEN_FILE    (mode 600)
  Raw response: $INIT_JSON_FILE     (mode 600 — delete after use)

CRITICAL next steps for the operator:

  1. Move the unseal keys to 5 separate secure locations
     (password managers, sealed envelopes, smartcards, ...).
     If you lose >= $((KEY_SHARES - KEY_THRESHOLD + 1)) of them you
     CANNOT recover the data inside Vault.

  2. Save the root token in your personal password manager. Use
     it only to issue per-service tokens via 'vault token create
     -policy=...'. Do NOT use the root token in service configs.

  3. Shred the local copies as soon as both items above are done:
       shred -u $OUTPUT_DIR/*.txt $INIT_JSON_FILE

This script is idempotent — re-running it is safe and a no-op if
Vault is already initialized.
==============================================================
MSG
