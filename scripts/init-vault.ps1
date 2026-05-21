#!/usr/bin/env pwsh
# -----------------------------------------------------------------------------
# scripts/init-vault.ps1
#
# PowerShell port of scripts/init-vault.sh. Bootstraps an unsealed Vault.
#
# Idempotent: if Vault is already initialized, the script exits 0 without
# doing anything destructive. Otherwise:
#
#   1. Initialize with Shamir 5-of-5 sharing, threshold 3.
#   2. Persist the 5 unseal keys and the root token to $OutputDir.
#      Windows ACL is best-effort tightened to current user only.
#   3. Unseal Vault using 3 of the 5 keys.
#   4. Enable KV v2 at secret/.
#
# Designed to run from the repo root *after* `docker compose up -d vault`
# has the vault container healthy. The script talks to Vault through
# `docker compose exec`, so no host-side vault CLI is required.
#
# Usage:
#   .\scripts\init-vault.ps1
#   .\scripts\init-vault.ps1 -ComposeFile docker/docker-compose.yml
#
# Parameters (all optional, env vars override defaults):
#   -ComposeFile       (default: docker/docker-compose.yml)
#   -KeyShares         (default: 5)
#   -KeyThreshold      (default: 3)
#   -OutputDir         where to write unseal keys + root token
#                      (default: .\vault-init-output)
# -----------------------------------------------------------------------------

[CmdletBinding()]
param(
    [string]$ComposeFile = "",
    [int]$KeyShares = 0,
    [int]$KeyThreshold = 0,
    [string]$OutputDir = ""
)

$ErrorActionPreference = "Stop"

# Resolve defaults (env vars win over hardcoded ones, params win over env).
if (-not $ComposeFile) {
    $ComposeFile = if ($env:COMPOSE_FILE) { $env:COMPOSE_FILE } else { "docker/docker-compose.yml" }
}
if ($KeyShares -le 0) {
    $KeyShares = if ($env:VAULT_KEY_SHARES) { [int]$env:VAULT_KEY_SHARES } else { 5 }
}
if ($KeyThreshold -le 0) {
    $KeyThreshold = if ($env:VAULT_KEY_THRESHOLD) { [int]$env:VAULT_KEY_THRESHOLD } else { 3 }
}
if (-not $OutputDir) {
    $OutputDir = if ($env:VAULT_INIT_OUTPUT_DIR) { $env:VAULT_INIT_OUTPUT_DIR } else { ".\vault-init-output" }
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "docker not found on PATH."
}

function Invoke-VaultExec {
    [CmdletBinding()]
    param([Parameter(ValueFromRemainingArguments = $true)] [string[]]$Args)
    & docker compose -f $ComposeFile exec -T vault @Args
}

# ---------------------------------------------------------------------------
# 1) Already initialized?
# ---------------------------------------------------------------------------
$statusJson = $null
try {
    $statusJson = & docker compose -f $ComposeFile exec -T vault vault status -format=json 2>$null
} catch {
    # `vault status` exits 2 when sealed; PowerShell may surface that as an
    # error. We still want to read the JSON, so swallow and inspect below.
}
if ($statusJson -and ($statusJson -match '"initialized"\s*:\s*true')) {
    Write-Host "==> Vault is already initialized." -ForegroundColor Yellow
    Write-Host "    To unseal an already-initialized but sealed Vault, run:"
    Write-Host "      docker compose -f $ComposeFile exec vault vault operator unseal"
    exit 0
}

# ---------------------------------------------------------------------------
# 2) Initialize — capture JSON to a file directly to avoid CRLF surprises.
# ---------------------------------------------------------------------------
if (-not (Test-Path $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir | Out-Null
}

$initJsonFile   = Join-Path $OutputDir "init-response.json"
$unsealKeysFile = Join-Path $OutputDir "unseal-keys.txt"
$rootTokenFile  = Join-Path $OutputDir "root-token.txt"

Write-Host "==> Initializing Vault (shares=$KeyShares, threshold=$KeyThreshold)" -ForegroundColor Cyan

# Note: we pipe the output of `docker compose exec` directly to a file via
# cmd.exe redirection-by-call. Out-File would re-encode as UTF-16-LE; we
# want raw UTF-8 bytes from the container.
$initRaw = & docker compose -f $ComposeFile exec -T vault `
    vault operator init `
    -key-shares=$KeyShares `
    -key-threshold=$KeyThreshold `
    -format=json
if ($LASTEXITCODE -ne 0) {
    throw "vault operator init failed (exit $LASTEXITCODE)"
}
# $initRaw is an array of strings; rejoin with LF and write UTF-8 (no BOM).
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($initJsonFile, ($initRaw -join "`n"), $utf8NoBom)

# Parse with PowerShell's ConvertFrom-Json (handles BOM/CRLF cleanly).
$initData = Get-Content $initJsonFile -Raw -Encoding UTF8 | ConvertFrom-Json

# Write unseal keys (LF line endings, UTF-8 no BOM).
[System.IO.File]::WriteAllText(
    $unsealKeysFile,
    (($initData.unseal_keys_b64 -join "`n") + "`n"),
    $utf8NoBom
)
# Write root token (LF, UTF-8 no BOM).
[System.IO.File]::WriteAllText(
    $rootTokenFile,
    ($initData.root_token + "`n"),
    $utf8NoBom
)

# Best-effort ACL tightening: grant only the current user full access.
# This is the closest analog to chmod 600 on Windows.
function Restrict-Acl {
    param([string]$Path)
    try {
        $acl = Get-Acl $Path
        $acl.SetAccessRuleProtection($true, $false)  # disable inheritance, drop inherited
        $acl.Access | ForEach-Object { [void]$acl.RemoveAccessRule($_) }
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
            [System.Security.Principal.WindowsIdentity]::GetCurrent().User,
            "FullControl",
            "None",
            "None",
            "Allow"
        )
        $acl.SetAccessRule($rule)
        Set-Acl -Path $Path -AclObject $acl
    } catch {
        Write-Host "    warning: could not tighten ACL on $Path ($_)" -ForegroundColor Yellow
    }
}
foreach ($f in @($unsealKeysFile, $rootTokenFile, $initJsonFile)) {
    Restrict-Acl -Path $f
}

# ---------------------------------------------------------------------------
# 3) Unseal (3 of 5).
# ---------------------------------------------------------------------------
Write-Host "==> Unsealing with $KeyThreshold of $KeyShares keys" -ForegroundColor Cyan
$keys = Get-Content $unsealKeysFile | Where-Object { $_ -ne "" }
for ($i = 0; $i -lt $KeyThreshold; $i++) {
    $key = $keys[$i]
    # `docker compose exec -T` does not need stdin here because we pass the
    # key as an argument; this mirrors `</dev/null` in the bash version
    # (PowerShell does not inherit a file descriptor across the call).
    & docker compose -f $ComposeFile exec -T vault vault operator unseal $key | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "vault operator unseal failed on key $($i + 1) (exit $LASTEXITCODE)"
    }
}

$rootToken = (Get-Content $rootTokenFile -Raw).Trim()

# ---------------------------------------------------------------------------
# 4) Enable KV v2 at secret/.
# ---------------------------------------------------------------------------
Write-Host "==> Enabling KV v2 at secret/" -ForegroundColor Cyan
$secretsRaw = & docker compose -f $ComposeFile exec -T `
    -e VAULT_TOKEN=$rootToken vault `
    vault secrets list -format=json 2>$null
if ($secretsRaw -and (($secretsRaw -join "`n") -match '"secret/"')) {
    Write-Host "    secret/ already mounted, skipping." -ForegroundColor DarkGray
} else {
    & docker compose -f $ComposeFile exec -T `
        -e VAULT_TOKEN=$rootToken vault `
        vault secrets enable -version=2 -path=secret kv | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "vault secrets enable kv v2 failed (exit $LASTEXITCODE)"
    }
}

# ---------------------------------------------------------------------------
# 5) Operator instructions.
# ---------------------------------------------------------------------------
$maxLost = $KeyShares - $KeyThreshold + 1
Write-Host ""
Write-Host "==============================================================" -ForegroundColor Green
Write-Host "Vault initialized and unsealed. KV v2 mounted at secret/."     -ForegroundColor Green
Write-Host ""
Write-Host "  Unseal keys:  $unsealKeysFile   (user-only ACL)"
Write-Host "  Root token:   $rootTokenFile    (user-only ACL)"
Write-Host "  Raw response: $initJsonFile     (user-only ACL -- delete after use)"
Write-Host ""
Write-Host "CRITICAL next steps for the operator:" -ForegroundColor Yellow
Write-Host ""
Write-Host "  1. Move the unseal keys to 5 separate secure locations"
Write-Host "     (password managers, sealed envelopes, smartcards, ...)."
Write-Host "     If you lose >= $maxLost of them you CANNOT recover"
Write-Host "     the data inside Vault."
Write-Host ""
Write-Host "  2. Save the root token in your personal password manager. Use"
Write-Host "     it only to issue per-service tokens via 'vault token create"
Write-Host "     -policy=...'. Do NOT use the root token in service configs."
Write-Host ""
Write-Host "  3. Securely delete the local copies as soon as both items above"
Write-Host "     are done. Windows has no 'shred -u'; use sdelete or"
Write-Host "     Remove-Item after copying to your password manager:"
Write-Host "       Remove-Item $OutputDir\*.txt, $initJsonFile -Force"
Write-Host ""
Write-Host "This script is idempotent -- re-running it is safe and a no-op"
Write-Host "if Vault is already initialized."
Write-Host "==============================================================" -ForegroundColor Green
