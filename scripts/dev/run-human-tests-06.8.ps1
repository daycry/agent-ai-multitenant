#!/usr/bin/env pwsh
# -----------------------------------------------------------------------------
# scripts/dev/run-human-tests-06.8.ps1
#
# Launcher informativo de los 4 tests humanos del Plan 06.8 (RBAC).
# Estos tests NO se pueden automatizar - requieren ojos humanos
# verificando botones / sidebar / badges en el admin-panel.
#
# Flujo:
#   1) Verifica que api-server :8001 y admin-panel :3000 responden.
#   2) Lee scripts/.demo_state_06_8.json (creado por
#      setup_demo_06_8.py) e imprime las credenciales de los 3 usuarios
#      + las URLs a abrir.
#   3) Lista qué hacer con cada cuenta - matchea los checklists del doc.
#
# Si nunca corriste el setup: ejecuta primero
#   .\.venv\Scripts\python.exe scripts\setup_demo_06_8.py
# -----------------------------------------------------------------------------

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $RepoRoot

function Write-Step($Msg) { Write-Host ""; Write-Host "==> $Msg" -ForegroundColor Cyan }
function Write-Ok($Msg)   { Write-Host "    [OK] $Msg" -ForegroundColor Green }
function Write-Fail($Msg) { Write-Host "    [FAIL] $Msg" -ForegroundColor Red }
function Write-Hint($Msg) { Write-Host "    $Msg" -ForegroundColor DarkGray }

# -----------------------------------------------------------------------------
# 1) Verificar servicios arriba
# -----------------------------------------------------------------------------
function Test-Url {
    param([string]$Url)
    try {
        $r = Invoke-WebRequest -Uri $Url -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
        return $r.StatusCode -in 200, 204, 304
    } catch { return $false }
}

Write-Step "Chequeo de servicios"
$apiOk = Test-Url "http://127.0.0.1:8001/healthz"
$webOk = Test-Url "http://127.0.0.1:3000"
if ($apiOk) { Write-Ok "api-server :8001" } else { Write-Fail "api-server :8001 no responde" }
if ($webOk) { Write-Ok "admin-panel :3000" } else { Write-Fail "admin-panel :3000 no responde" }
if (-not $apiOk) {
    Write-Hint "Lanza primero el stack: .\scripts\dev\up.ps1"
    exit 1
}
if (-not $webOk) {
    Write-Hint "El admin-panel no esta arriba. Arranca con: cd apps/admin-panel; npm run dev"
}

# -----------------------------------------------------------------------------
# 2) Leer state del seed
# -----------------------------------------------------------------------------
$StateFile = Join-Path $RepoRoot "scripts\.demo_state_06_8.json"
if (-not (Test-Path $StateFile)) {
    Write-Step "No existe $StateFile"
    Write-Hint "Corre primero: .\.venv\Scripts\python.exe scripts\setup_demo_06_8.py"
    exit 1
}

$state = Get-Content $StateFile -Raw | ConvertFrom-Json

Write-Step "Tenant demo: $($state.tenant_slug) ($($state.tenant_id))"
Write-Ok "Proyecto demo: Plan 06.8 demo ($($state.project_id))"

# -----------------------------------------------------------------------------
# 3) Credenciales + qué probar
# -----------------------------------------------------------------------------
Write-Host ""
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "  Credenciales de los 3 usuarios demo (login en :3000/login)"     -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan
$rows = foreach ($u in $state.users) {
    [PSCustomObject]@{
        Email    = $u.email
        Password = $u.password
        Rol      = if ($u.is_system_admin) { "system_admin" } else { $u.role }
    }
}
$rows | Format-Table -AutoSize | Out-Host

Write-Host "URLs utiles:" -ForegroundColor Cyan
Write-Host "  - http://localhost:3000/login                                (login)"
Write-Host "  - http://localhost:3000/admin/projects                       (lista)"
Write-Host "  - http://localhost:3000/admin/projects/$($state.project_id)  (hub demo)"
Write-Host "  - http://localhost:3000/admin/settings                       (admin only)"
Write-Host "  - http://localhost:3000/admin/knowledge-bases                (KBs)"
Write-Host ""

Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "  Que hacer con cada cuenta (resumen - detalle en el doc)"        -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "[human_06_8_01] Login como tenant_user (user-06-8@example.com):" -ForegroundColor Yellow
Write-Host "  - Sidebar SIN 'Settings' ni 'Validacion humana'"
Write-Host "  - Badge en header dice 'user' (gris)"
Write-Host "  - /admin/projects SIN boton 'Crear proyecto'"
Write-Host "  - Detail proyecto SIN botones Editar/Borrar"
Write-Host "  - curl POST /projects con su token -> 403"
Write-Host ""
Write-Host "[human_06_8_02] Login como tenant_admin (admin-06-8@example.com):" -ForegroundColor Yellow
Write-Host "  - Sidebar con 'Settings' visible"
Write-Host "  - Badge 'admin' (azul)"
Write-Host "  - Crear/editar/borrar proyecto OK desde la UI"
Write-Host "  - Cambiar tenant-setting (memoria threshold) persiste"
Write-Host "  - Tenant-picker NO visible (no es system_admin)"
Write-Host ""
Write-Host "[human_06_8_03] Login como system_admin (sysadmin-06-8@example.com):" -ForegroundColor Yellow
Write-Host "  - Badge 'system_admin' (ambar)"
Write-Host "  - Tenant-picker visible con todos los tenants"
Write-Host "  - Cambio de tenant refresca la UI"
Write-Host "  - GET /me -> is_system_admin: true"
Write-Host ""
Write-Host "[human_06_8_04] Login como tenant_user de nuevo:" -ForegroundColor Yellow
Write-Host "  - /admin/projects/{id}/tasks: boton 'Crear tarea' SI aparece"
Write-Host "  - Drag-drop entre columnas del kanban funciona"
Write-Host "  - Comentar en un plan funciona"
Write-Host ""

Write-Host "Detalle por test:" -ForegroundColor DarkGray
Write-Host "  docs\03-guides\human-tests\06.8-rbac-enforcement.md" -ForegroundColor DarkGray
Write-Host ""

exit 0
