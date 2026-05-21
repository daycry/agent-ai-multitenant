#!/usr/bin/env pwsh
# -----------------------------------------------------------------------------
# Bootstrap the local dev environment on Windows (PowerShell).
#
# Idempotent: safe to run repeatedly. Creates .venv/ at the repo root if
# missing, upgrades pip, installs everything in requirements-dev.txt, and
# registers the pre-commit git hook so future commits run the linters.
#
# Usage:
#   .\scripts\dev\bootstrap.ps1
#
# After bootstrap, activate the venv for interactive work:
#   .\.venv\Scripts\Activate.ps1
# Or invoke tools directly without activating:
#   .\.venv\Scripts\pre-commit run --all-files
# -----------------------------------------------------------------------------
$ErrorActionPreference = "Stop"

# Resolve repo root (this script lives at <root>/scripts/dev/).
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $RepoRoot

$VenvPath   = Join-Path $RepoRoot ".venv"
$VenvPython = Join-Path $VenvPath "Scripts\python.exe"

# 1) Ensure a usable system python.
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if ($null -eq $pythonCmd) {
    Write-Error "python not found on PATH. Install Python 3.12+ and retry."
    exit 1
}

# 2) Create venv if missing.
if (-not (Test-Path $VenvPython)) {
    Write-Host "==> Creating venv at .venv/" -ForegroundColor Cyan
    & python -m venv $VenvPath
} else {
    Write-Host "==> Reusing existing .venv/" -ForegroundColor DarkGray
}

# 3) Upgrade pip inside the venv.
Write-Host "==> Upgrading pip" -ForegroundColor Cyan
& $VenvPython -m pip install --upgrade pip --quiet

# 4) Install dev dependencies.
Write-Host "==> Installing requirements-dev.txt" -ForegroundColor Cyan
& $VenvPython -m pip install -r (Join-Path $RepoRoot "requirements-dev.txt")

# 5) Install each app/ package editable, so `from <pkg> import ...` works
#    and pytest can discover modules. New apps must be added here.
$AppPackages = @("apps/api-server", "apps/watchdog")
foreach ($pkg in $AppPackages) {
    $pkgPath = Join-Path $RepoRoot $pkg
    if (Test-Path (Join-Path $pkgPath "pyproject.toml")) {
        Write-Host "==> pip install -e $pkg`[dev`]" -ForegroundColor Cyan
        & $VenvPython -m pip install -e "$pkgPath[dev]"
    }
}

# 6) Register the pre-commit git hook.
Write-Host "==> Installing pre-commit git hook" -ForegroundColor Cyan
& $VenvPython -m pre_commit install

Write-Host ""
Write-Host "OK. Dev environment ready." -ForegroundColor Green
Write-Host "  Activate:        .\.venv\Scripts\Activate.ps1"
Write-Host "  Run all hooks:   .\.venv\Scripts\pre-commit run --all-files"
Write-Host "  Run a tool:      .\.venv\Scripts\ruff check ."
