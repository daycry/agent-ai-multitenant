<#
.SYNOPSIS
  Genera los manuales de usuario en PDF (docs/manuals/pdf) con Playwright, contra
  el STACK CONTENERIZADO completo servido por Caddy (single-origin).

.DESCRIPTION
  Reutilizable y reejecutable cuando la UI cambia. Flujo:
    1. (Opcional) construye las imágenes de app que usa el overlay de manuales:
       admin-panel:manuals (NEXT_PUBLIC_API_URL=/api) + api-server:manuals.
    2. Levanta el overlay docker/docker-compose.manuals.yml (api-server +
       admin-panel + Caddy) sobre la infra dev ya en marcha.
    3. Espera a que Caddy responda en http://localhost:<Port>.
    4. Siembra datos demo reales (proyectos + plan + tareas) — idempotente.
    5. Captura `docker compose ps` en docs/manuals/assets/dockers.json.
    6. Ejecuta los specs Playwright (navegan la app por Caddy y renderizan un PDF
       por manual) y combina todo en manual-completo.pdf.

  Prerrequisito: la infra dev arriba (scripts/dev/up.ps1 o docker compose up -d).
  El admin del tenant demo (por defecto demo@example.com) debe existir
  (apps/api-server/seeds/init_tenant.py).

.PARAMETER Port       Puerto host de Caddy (def 8080).
.PARAMETER Grep       Filtro de specs (-g de Playwright), p.ej. "01".
.PARAMETER SkipBuild  No reconstruir las imágenes de app.
.PARAMETER SkipSeed   No re-sembrar datos demo.
.PARAMETER Email/Password/Tenant  Credenciales de login del tenant demo.
#>
param(
  [int]$Port = 8080,
  [string]$Grep = "",
  [switch]$SkipBuild,
  [switch]$SkipSeed,
  [string]$Email = "demo@example.com",
  [string]$Password = "demo-manuales-pw-2026",
  [string]$Tenant = "Demo Manuales"
)
$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$ManualsDir = Join-Path $RepoRoot "docs\manuals"
$Base = "http://localhost:$Port"
$Compose = @(
  "-f", "docker/docker-compose.yml",
  "-f", "docker/docker-compose.dev.yml",
  "-f", "docker/docker-compose.manuals.yml"
)

Push-Location $RepoRoot
try {
  if (-not $SkipBuild) {
    Write-Host "==> Construyendo imágenes de app (admin-panel:/api + api-server)" -ForegroundColor Cyan
    docker build -t agentic-platform/admin-panel:manuals --build-arg NEXT_PUBLIC_API_URL=/api -f apps/admin-panel/Dockerfile apps/admin-panel | Out-Null
    docker build -t agentic-platform/api-server:manuals -f apps/api-server/Dockerfile . | Out-Null
  }

  Write-Host "==> Levantando overlay contenerizado (api-server + admin-panel + caddy)" -ForegroundColor Cyan
  & docker compose @Compose up -d api-server admin-panel caddy

  Write-Host "==> Esperando a Caddy en $Base (max 90s)" -ForegroundColor Cyan
  $deadline = (Get-Date).AddSeconds(90); $up = $false
  while ((Get-Date) -lt $deadline) {
    try { Invoke-RestMethod -Uri "$Base/healthz" -TimeoutSec 3 -ErrorAction Stop | Out-Null; $up = $true; break }
    catch { Start-Sleep -Seconds 3 }
  }
  if (-not $up) { throw "Caddy no respondió en $Base/healthz" }
  Write-Host "    Caddy OK" -ForegroundColor Green

  Push-Location $ManualsDir
  try {
    $env:MANUALS_NO_WEBSERVER = "1"
    $env:MANUALS_BASE_URL = $Base
    $env:MANUALS_EMAIL = $Email
    $env:MANUALS_PASSWORD = $Password
    $env:MANUALS_TENANT = $Tenant

    if (-not (Test-Path "node_modules")) {
      Write-Host "==> npm install (docs/manuals)" -ForegroundColor Cyan
      npm install --no-audit --no-fund | Out-Null
    }
    npx playwright install chromium | Out-Null

    if (-not $SkipSeed) {
      Write-Host "==> Sembrando datos demo (idempotente)" -ForegroundColor Cyan
      node lib/seed-demo-data.mjs
      # Demo de validación humana: app levantada + review-session (ADR 0062) para
      # el manual 12. Best-effort (no aborta la generación si falla).
      Write-Host "==> Sembrando demo de review-runtime (app levantada)" -ForegroundColor Cyan
      try { & (Join-Path $RepoRoot "scripts\dev\seed-review-demo.ps1") } catch { Write-Warning "seed-review-demo: $_" }
    }

    Write-Host "==> Capturando docker compose ps -> assets/dockers.json" -ForegroundColor Cyan
    $rows = docker ps --format '{{.Names}}|{{.Image}}|{{.Status}}|{{.Ports}}' | Where-Object { $_ -match "agentic" }
    $list = @(); foreach ($r in $rows) { $p = $r -split '\|'; $list += [pscustomobject]@{ name = $p[0]; image = $p[1]; status = $p[2]; ports = $p[3] } }
    $imgs = docker images --format '{{.Repository}}:{{.Tag}}|{{.Size}}' | Where-Object { $_ -match "agent-runtime|api-server|admin-panel|workers|orchestrator|caddy|egress" }
    $ri = @(); foreach ($i in $imgs) { $p = $i -split '\|'; $ri += [pscustomobject]@{ image = $p[0]; size = $p[1] } }
    $json = [pscustomobject]@{ containers = $list; images = $ri; capturedAt = (Get-Date -Format "yyyy-MM-dd") } | ConvertTo-Json -Depth 4
    [System.IO.File]::WriteAllText((Join-Path $ManualsDir "assets\dockers.json"), $json)  # sin BOM

    Write-Host "==> Generando manuales (Playwright)" -ForegroundColor Cyan
    if ($Grep) { npx playwright test -g $Grep } else { npx playwright test }
    $code = $LASTEXITCODE
    Write-Host "==> Playwright exit code: $code"

    if (-not $Grep) {
      Write-Host "==> Combinando en manual-completo.pdf" -ForegroundColor Cyan
      node lib/combine-pdfs.mjs
    }
  } finally { Pop-Location }
} finally { Pop-Location }

Write-Host "==> Manuales en docs/manuals/pdf/" -ForegroundColor Green
Get-ChildItem (Join-Path $ManualsDir "pdf") -ErrorAction SilentlyContinue | Select-Object Name, @{N = "KB"; E = { [int]($_.Length / 1KB) } }
