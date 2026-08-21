#!/usr/bin/env pwsh
# -----------------------------------------------------------------------------
# scripts/init-vault.ps1
#
# PowerShell port of scripts/init-vault.sh. Bootstraps an unsealed Vault
# WITHOUT ever writing a secret in cleartext.
#
# Idempotent: if Vault is already initialized, the script exits 0 without
# doing anything destructive. Otherwise:
#
#   1. Initialize with Shamir sharing (5 shares, threshold 3 by default).
#   2. Hand the unseal keys + root token over in ONE of two ways:
#        * default    — encrypt the raw init output to the operator's public key
#                       (age or gpg) at $OutputDir\vault-init.<age|gpg>.
#        * -PrintOnce — write them to the console ONLY; nothing touches the disk.
#   3. Unseal Vault using `KeyThreshold` keys held IN MEMORY.
#   4. Enable KV v2 at secret/.
#
# Why (plan prod-10 task_prod10_02, findings secrets-1 / deploy-11)
# ------------------------------------------------------------------
# THIS script is the one that produced the incident: the repo is developed on
# Windows, and the 5 real unseal keys + root token of the running Vault sat in
# vault-init-output\ in cleartext from 2026-05-20 to 2026-06-10 (and beyond),
# readable by every process on the machine, because the previous version wrote
# them to disk and merely PRINTED a banner asking the operator to shred them.
# Fixing only the bash twin would have left the actual Windows path unchanged.
#
# Fail-fast ordering matters: the recipient / encryptor check runs BEFORE
# `vault operator init`. Failing after would generate the 5 shares and the root
# token and lose them in the same command — Vault initialized, sealed and
# unrecoverable. There is no second attempt.
#
# Designed to run from the repo root *after* `docker compose up -d vault`
# has the vault container healthy. The script talks to Vault through
# `docker compose exec`, so no host-side vault CLI is required.
#
# Usage:
#   $env:VAULT_INIT_RECIPIENT = "age1..."; .\scripts\init-vault.ps1
#   .\scripts\init-vault.ps1 -Recipient ops@example.com -Encrypt gpg
#   .\scripts\init-vault.ps1 -PrintOnce          # reveal on console, write nothing
#
# Parameters (all optional, env vars override defaults, params win over env):
#   -ComposeFile       (default: docker/docker-compose.yml)
#   -KeyShares         (default: 5)
#   -KeyThreshold      (default: 3)
#   -OutputDir         where the ENCRYPTED blob goes (default: .\vault-init-output)
#   -Recipient         age public key or gpg recipient (env VAULT_INIT_RECIPIENT).
#                      REQUIRED unless -PrintOnce.
#   -Encrypt           auto | age | gpg (env VAULT_INIT_ENCRYPT, default auto)
#   -PrintOnce         reveal on the console and write nothing to disk
# -----------------------------------------------------------------------------

[CmdletBinding()]
param(
    [string]$ComposeFile = "",
    [int]$KeyShares = 0,
    [int]$KeyThreshold = 0,
    [string]$OutputDir = "",
    [string]$Recipient = "",
    [ValidateSet("", "auto", "age", "gpg")]
    [string]$Encrypt = "",
    [switch]$PrintOnce
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
if (-not $Recipient) {
    $Recipient = if ($env:VAULT_INIT_RECIPIENT) { $env:VAULT_INIT_RECIPIENT } else { "" }
}
if (-not $Encrypt) {
    $Encrypt = if ($env:VAULT_INIT_ENCRYPT) { $env:VAULT_INIT_ENCRYPT } else { "auto" }
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "docker not found on PATH."
}

# ---------------------------------------------------------------------------
# 1) Already initialized? Then this is a no-op (idempotent by contract), and it
#    stays a no-op WITHOUT demanding an encryption recipient: re-running the
#    script to check state should not need an age key for a path that generates
#    no secret.
# ---------------------------------------------------------------------------
$statusRaw = $null
try {
    $statusRaw = & docker compose -f $ComposeFile exec -T vault vault status
} catch {
    # `vault status` exits 2 when sealed; PowerShell may surface that as an
    # error. We still want to read the text, so swallow and inspect below.
}
if ($statusRaw -and (($statusRaw -join "`n") -match '(?i)Initialized\s+true')) {
    Write-Host "==> Vault is already initialized." -ForegroundColor Yellow
    Write-Host "    To unseal an already-initialized but sealed Vault, run:"
    Write-Host "      docker compose -f $ComposeFile exec vault vault operator unseal"
    Write-Host "    Or follow docs/06-runbooks/restart-services.md (unseal is step 1"
    Write-Host "    after any host reboot)."
    exit 0
}

# ---------------------------------------------------------------------------
# 2) PREFLIGHT — everything that can fail must fail HERE, before the init call.
# ---------------------------------------------------------------------------
$encryptCmd = ""
if (-not $PrintOnce) {
    if (-not $Recipient) {
        throw @"
no place to put the unseal keys.

This script no longer writes secrets in cleartext (plan prod-10, finding
secrets-1). Pick one:

  * Encrypt to your public key (recommended):
      .\scripts\init-vault.ps1 -Recipient age1...
      .\scripts\init-vault.ps1 -Recipient ops@example.com -Encrypt gpg

  * Reveal once on the console and store them yourself, right now:
      .\scripts\init-vault.ps1 -PrintOnce

Generate an age key with ``age-keygen -o `$HOME\.age\vault.key`` (the PUBLIC
line it prints is the recipient). Keep the private key OUT of this repo.
"@
    }

    $hasAge = [bool](Get-Command age -ErrorAction SilentlyContinue)
    $hasGpg = [bool](Get-Command gpg -ErrorAction SilentlyContinue)
    switch ($Encrypt) {
        "auto" {
            if ($hasAge) { $encryptCmd = "age" }
            elseif ($hasGpg) { $encryptCmd = "gpg" }
            else { throw "neither 'age' nor 'gpg' on PATH; install one, or use -PrintOnce." }
        }
        "age" {
            if (-not $hasAge) { throw "-Encrypt age but 'age' is not on PATH." }
            $encryptCmd = "age"
        }
        "gpg" {
            if (-not $hasGpg) { throw "-Encrypt gpg but 'gpg' is not on PATH." }
            $encryptCmd = "gpg"
        }
    }
}

# ---------------------------------------------------------------------------
# 3) Initialize. The output stays in a VARIABLE — it never reaches the
#    filesystem in cleartext, not even briefly.
# ---------------------------------------------------------------------------
Write-Host "==> Initializing Vault (shares=$KeyShares, threshold=$KeyThreshold)" -ForegroundColor Cyan

$initRaw = & docker compose -f $ComposeFile exec -T vault `
    vault operator init `
    -key-shares=$KeyShares `
    -key-threshold=$KeyThreshold
if ($LASTEXITCODE -ne 0) {
    throw "vault operator init failed (exit $LASTEXITCODE)"
}
$initText = ($initRaw -join "`n")

# Text output (not -format=json) on purpose: parsing it needs only a regex, and
# it is the SAME shape docker/vault/auto-unseal.sh and the bash twin parse, so
# there is one format to keep working instead of two.
$unsealKeys = @(
    $initText -split "`n" |
        Where-Object { $_ -match '^Unseal Key' } |
        ForEach-Object { ($_ -replace '^.*:\s*', '').Trim() }
)
$rootTokenLine = ($initText -split "`n" | Where-Object { $_ -match '^Initial Root Token' } | Select-Object -First 1)
$rootToken = if ($rootTokenLine) { ($rootTokenLine -replace '^.*:\s*', '').Trim() } else { "" }

if ($unsealKeys.Count -lt $KeyThreshold -or -not $rootToken) {
    # LAST RESORT. Vault is now initialized and the material exists only in this
    # variable: losing it is unrecoverable, so showing it beats discarding it.
    Write-Host ""
    Write-Host "!!! could not parse the init output. Vault IS INITIALIZED and this" -ForegroundColor Red
    Write-Host "!!! is the ONLY copy of its unseal keys. SAVE IT NOW, then unseal"   -ForegroundColor Red
    Write-Host "!!! manually."                                                       -ForegroundColor Red
    Write-Host ""
    Write-Host $initText
    exit 1
}

# ---------------------------------------------------------------------------
# 4) Hand over the material: encrypted blob, or one-shot reveal.
# ---------------------------------------------------------------------------
$outFile = ""
if ($PrintOnce) {
    Write-Host ""
    Write-Host "==============================================================" -ForegroundColor Green
    Write-Host "SHOWN ONCE -- NOT WRITTEN ANYWHERE. Store these NOW."           -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Unseal keys ($KeyShares shares, $KeyThreshold needed to unseal):"
    $unsealKeys | ForEach-Object { Write-Host "  $_" }
    Write-Host ""
    Write-Host "Initial root token:"
    Write-Host "  $rootToken"
    Write-Host "==============================================================" -ForegroundColor Green
} else {
    if (-not (Test-Path $OutputDir)) {
        New-Item -ItemType Directory -Path $OutputDir | Out-Null
    }
    $suffix = if ($encryptCmd -eq "age") { "age" } else { "gpg" }
    $outFile = Join-Path $OutputDir "vault-init.$suffix"

    $encryptOk = $false
    try {
        if ($encryptCmd -eq "age") {
            $initText | & age -r $Recipient -o $outFile
        } else {
            $initText | & gpg --batch --yes --trust-model always -e -r $Recipient -o $outFile
        }
        $encryptOk = ($LASTEXITCODE -eq 0) -and (Test-Path $outFile)
    } catch {
        $encryptOk = $false
    }

    if (-not $encryptOk) {
        if (Test-Path $outFile) { Remove-Item $outFile -Force -ErrorAction SilentlyContinue }
        Write-Host ""
        Write-Host "!!! encryption FAILED and Vault IS ALREADY INITIALIZED. What"   -ForegroundColor Red
        Write-Host "!!! follows is the only copy of its unseal keys -- save it NOW:" -ForegroundColor Red
        Write-Host ""
        Write-Host $initText
        exit 1
    }

    # Best-effort ACL tightening on the CIPHERTEXT: grant only the current user.
    # Defence in depth, not the primary control — the primary control is that the
    # bytes on disk are encrypted.
    try {
        $acl = Get-Acl $outFile
        $acl.SetAccessRuleProtection($true, $false)  # disable inheritance, drop inherited
        $acl.Access | ForEach-Object { [void]$acl.RemoveAccessRule($_) }
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
            [System.Security.Principal.WindowsIdentity]::GetCurrent().User,
            "FullControl", "None", "None", "Allow"
        )
        $acl.SetAccessRule($rule)
        Set-Acl -Path $outFile -AclObject $acl
    } catch {
        Write-Host "    warning: could not tighten ACL on $outFile ($_)" -ForegroundColor Yellow
    }
}

# ---------------------------------------------------------------------------
# 5) Unseal, reading the keys FROM MEMORY.
# ---------------------------------------------------------------------------
Write-Host "==> Unsealing with $KeyThreshold of $KeyShares keys" -ForegroundColor Cyan
for ($i = 0; $i -lt $KeyThreshold; $i++) {
    $key = $unsealKeys[$i]
    & docker compose -f $ComposeFile exec -T vault vault operator unseal $key | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "vault operator unseal failed on key $($i + 1) (exit $LASTEXITCODE)"
    }
}

# ---------------------------------------------------------------------------
# 6) Enable KV v2 at secret/.
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
# 7) Operator instructions.
# ---------------------------------------------------------------------------
$maxLost = $KeyShares - $KeyThreshold + 1
Write-Host ""
Write-Host "==============================================================" -ForegroundColor Green
Write-Host "Vault initialized and unsealed. KV v2 mounted at secret/."     -ForegroundColor Green
if ($outFile) {
    Write-Host ""
    Write-Host "  Encrypted init material: $outFile  ($encryptCmd, user-only ACL)"
    Write-Host "  Decrypt with:"
    Write-Host "    age -d -i <your-private-key> $outFile"
    Write-Host "    gpg -d $outFile"
}
Write-Host ""
Write-Host "CRITICAL next steps for the operator:" -ForegroundColor Yellow
Write-Host ""
Write-Host "  1. Split the $KeyShares unseal keys into $KeyShares SEPARATE secure"
Write-Host "     stores (password managers, sealed envelopes, smartcards, ...)."
Write-Host "     Losing >= $maxLost of them means the data inside Vault is GONE."
Write-Host "     Decrypt the blob, distribute, then delete the blob."
Write-Host ""
Write-Host "  2. Save the root token in your personal password manager and do NOT"
Write-Host "     put it in a service config. Mint per-service tokens instead:"
Write-Host "       bash scripts/vault-mint-service-tokens.sh"
Write-Host ""
Write-Host "  3. Nothing here is in cleartext, so there is nothing to shred."
Write-Host "     Verify with:"
Write-Host "       .venv\Scripts\python.exe scripts\check_no_secret_artifacts.py --root ."
Write-Host ""
Write-Host "  4. Unseal after a host reboot is step 1 of"
Write-Host "     docs/06-runbooks/restart-services.md."
Write-Host ""
Write-Host "This script is idempotent -- re-running it is safe and a no-op"
Write-Host "if Vault is already initialized."
Write-Host "==============================================================" -ForegroundColor Green
