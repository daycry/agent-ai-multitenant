<#
.SYNOPSIS
  Siembra una review-session DEMO end-to-end (ADR 0062) para los manuales:
  levanta la app "Hello World PHP" en un contenedor, persiste la review_session
  y pone el plan en `pending_human_validation`, de modo que el panel muestre el
  link clicable "Abrir app para probar" y el proxy del api-server la sirva.

  Reutilizable e idempotente (recrea el contenedor + UPSERT de la sesión).

.PARAMETER PlanId      Plan a poner en validación (def: phpPlanId de seed.json).
.PARAMETER SessionId   UUID de la review-session (def: fijo, reproducible).
#>
param(
  [string]$PlanId = "",
  [string]$SessionId = "019ed900-0000-7000-8000-0000000000aa",
  [string]$ContainerName = "agentic-review-demo"
)
$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$pg = "agentic-platform-postgres-1"

if (-not $PlanId) {
  $seed = Get-Content (Join-Path $RepoRoot "docs\manuals\assets\seed.json") -Raw | ConvertFrom-Json
  $PlanId = $seed.phpPlanId
}
$tenantId = (docker exec $pg psql -U postgres -d agentic_platform -t -A -c "SELECT tenant_id FROM plans WHERE id='$PlanId';").Trim()
if (-not $tenantId) { throw "no encuentro tenant_id del plan $PlanId" }
Write-Host "Plan=$PlanId  Tenant=$tenantId  Session=$SessionId" -ForegroundColor Cyan

# 1. App demo: nginx sirviendo docs/manuals/demo-app en el puerto 80, en la red
#    interna agentic-agents (la alcanza el api-server, nunca el host).
if (docker ps -aq -f "name=^$ContainerName$") { docker rm -f $ContainerName | Out-Null }
$appDir = (Join-Path $RepoRoot "docs\manuals\demo-app")
docker run -d --name $ContainerName --network agentic-agents `
  --label com.agentic-platform.component=review-runtime `
  --label com.agentic-platform.review-session-id=$SessionId `
  -v "${appDir}:/usr/share/nginx/html:ro" nginx:alpine | Out-Null
Write-Host "  app demo levantada ($ContainerName, nginx :80)" -ForegroundColor Green

# 2. review_sessions UPSERT + plan -> pending_human_validation.
$spec = @{
  plan_id   = $PlanId
  main_host = $ContainerName
  main_port = 80
  main_image = "nginx:alpine (demo)"
  human_checklist = @(
    @{ id = "human_01"; description = "GET /hello responde 200 con el JSON Hello World"; checklist = @("Status 200", "Cuerpo {message: Hello, World!}") },
    @{ id = "human_02"; description = "La documentación del endpoint existe en /docs"; checklist = @("Entrada en 04-reference") }
  )
} | ConvertTo-Json -Depth 6 -Compress
$specEsc = $spec -replace "'", "''"
$sql = @"
INSERT INTO review_sessions (id, tenant_id, plan_id, spec, status, container_ids, expires_at, created_at, last_activity_at)
VALUES ('$SessionId', '$tenantId', '$PlanId', '$specEsc'::jsonb, 'running', '[]'::jsonb, now() + interval '48 hours', now(), now())
ON CONFLICT (id) DO UPDATE SET spec = EXCLUDED.spec, status = 'running', expires_at = EXCLUDED.expires_at, last_activity_at = now(), deleted_at = NULL, verdict = NULL, rejection_reason = NULL;
UPDATE plans SET status = 'pending_human_validation' WHERE id = '$PlanId';
"@
$tmp = Join-Path $env:TEMP "seed-review.sql"
[System.IO.File]::WriteAllText($tmp, $sql)
Get-Content $tmp | docker exec -i $pg psql -U postgres -d agentic_platform | Out-Null
Write-Host "  review_session sembrada + plan en pending_human_validation" -ForegroundColor Green
Write-Host "==> Listo. El panel mostrará el link 'Abrir app para probar' en el plan $PlanId" -ForegroundColor Green
