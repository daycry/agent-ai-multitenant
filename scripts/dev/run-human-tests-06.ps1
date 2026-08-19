# Run the four human-test demos of Plan 06 in order.
#
# Usage:  .\scripts\dev\run-human-tests-06.ps1
#
# Each demo prints its own [OK]/[FAIL] lines; this launcher just
# coordinates and reports the final pass/fail by demo.

$ErrorActionPreference = "Continue"
$repoRoot = (Get-Item -Path $PSScriptRoot).Parent.Parent.FullName
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Host "FAIL - .venv\Scripts\python.exe no encontrado en $repoRoot"
    Write-Host "       Activa el venv o instalalo primero."
    exit 2
}

function Invoke-Demo {
    param([string]$Label, [string]$ScriptRel)
    Write-Host ""
    Write-Host "========================================================================"
    Write-Host "  $Label"
    Write-Host "  $ScriptRel"
    Write-Host "========================================================================"
    & $python (Join-Path $repoRoot $ScriptRel) | Out-Host
    $rc = $LASTEXITCODE
    return $rc
}

$results = @{}
$results.setup = Invoke-Demo -Label "Setup compartido" -ScriptRel "scripts\demos\setup_demo_06.py"
if ($results.setup -ne 0) {
    Write-Host ""
    Write-Host "FAIL - setup termino con exit $($results.setup); abortando."
    exit 1
}

$results.a = Invoke-Demo -Label "Demo A - end-to-end pipeline (06_01 + 06_06 + 06_09)" -ScriptRel "scripts\demos\demo_human_06_a_endtoend.py"
$results.b = Invoke-Demo -Label "Demo B - cache + aux + multi-repo (06_02 + 06_03 + 06_05)" -ScriptRel "scripts\demos\demo_human_06_b_cache_aux.py"
$results.c = Invoke-Demo -Label "Demo C - pool + policies (06_07 + 06_08)" -ScriptRel "scripts\demos\demo_human_06_c_pool_policies.py"
$results.d = Invoke-Demo -Label "Demo D - review + escalado + audit (06_04 + 06_10 + 06_11 + 06_12)" -ScriptRel "scripts\demos\demo_human_06_d_review_audit.py"

Write-Host ""
Write-Host "========================================================================"
Write-Host "  Resumen"
Write-Host "========================================================================"
$allOk = $true
foreach ($key in @("setup", "a", "b", "c", "d")) {
    $rc = $results[$key]
    $mark = if ($rc -eq 0) { "[ OK ]" } else { "[FAIL]" }
    Write-Host "  $mark demo_$key  (exit $rc)"
    if ($rc -ne 0) { $allOk = $false }
}

if ($allOk) {
    Write-Host ""
    Write-Host "Todos los demos PASSED. Puedes marcar los 12 human_06_* como pass."
    exit 0
} else {
    Write-Host ""
    Write-Host "Algun demo FAILED. Revisa el output arriba antes de marcar como pass."
    exit 1
}
