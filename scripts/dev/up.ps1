#!/usr/bin/env pwsh
# -----------------------------------------------------------------------------
# scripts/dev/up.ps1
#
# Start the dev stack (docker + api-server + admin-panel) DETACHED. After
# this returns you can close the terminal and the services keep running.
# PIDs go to .dev/*.pid; logs go to .dev/*.log; both gitignored.
#
# Use scripts/dev/down.ps1 to stop.
#
# Usage:
#   .\scripts\dev\up.ps1
#   .\scripts\dev\up.ps1 -ApiPort 8002 -AdminPort 3001
#   .\scripts\dev\up.ps1 -Monitoring     # + Prometheus/Alertmanager/Grafana/cAdvisor
# -----------------------------------------------------------------------------

[CmdletBinding()]
param(
    [int]$ApiPort = 8001,
    [int]$AdminPort = 3000,
    # Add the observability overlay (Prometheus + Alertmanager + Grafana +
    # cAdvisor + node-exporter). On Windows the node-exporter override is added
    # automatically so it actually boots (rslave mount). Off by default.
    [switch]$Monitoring,
    # Arranca ADEMAS el worker (celery) y el orchestrator, que es lo que hace
    # falta para que un run se ejecute de verdad: el orchestrator despacha las
    # tareas `ready` y el worker lanza el sandbox `agent-runtime`.
    #
    # Opt-in y no por defecto por dos razones medidas: suma ~600 MB de RAM en
    # una maquina donde el stack completo ya no cabia (16 GB), y relaja el
    # aislamiento de red del sandbox (ver el bloque del worker, mas abajo).
    # Sin esto el panel y la API funcionan igual, pero NINGUN run avanza:
    # las tareas se quedan en `ready` para siempre.
    [switch]$Runs,
    [int]$OrchestratorPort = 8002
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $RepoRoot

$DevDir = Join-Path $RepoRoot ".dev"
New-Item -ItemType Directory -Path $DevDir -Force | Out-Null

$ApiPidFile   = Join-Path $DevDir "api-server.pid"
$AdminPidFile = Join-Path $DevDir "admin-panel.pid"
$ApiLog       = Join-Path $DevDir "api-server.log"
$ApiErr       = Join-Path $DevDir "api-server.err.log"
$AdminLog     = Join-Path $DevDir "admin-panel.log"
$AdminErr     = Join-Path $DevDir "admin-panel.err.log"
$WorkerPidFile = Join-Path $DevDir "worker.pid"
$WorkerLog     = Join-Path $DevDir "worker.log"
$WorkerErr     = Join-Path $DevDir "worker.err.log"
$OrchPidFile   = Join-Path $DevDir "orchestrator.pid"
$OrchLog       = Join-Path $DevDir "orchestrator.log"
$OrchErr       = Join-Path $DevDir "orchestrator.err.log"
# Raiz de datos del worker: worktrees, bare repos y backups. En el compose es
# `/data/agent-platform`; en Windows no existe, asi que vive bajo `.dev/` (que
# esta gitignored). El sandbox lo recibe como bind a `/workspace`, no en la
# misma ruta, asi que una ruta Windows sirve.
$WorkerDataRoot = Join-Path $DevDir "data"

$ComposeArgs = @(
    "-f", "docker/docker-compose.yml",
    "-f", "docker/docker-compose.dev.yml"
)
if ($Monitoring) {
    # monitoring.yml = service defs; monitoring.dev.yml = host ports; windows.yml
    # = node-exporter override (needed on Docker Desktop/WSL2).
    $ComposeArgs += @(
        "-f", "docker/docker-compose.monitoring.yml",
        "-f", "docker/docker-compose.monitoring.dev.yml",
        "-f", "docker/docker-compose.windows.yml"
    )
}

function Test-PortBindable {
    param([int]$Port)
    try {
        $l = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
        $l.Start(); $l.Stop()
        return $true
    } catch { return $false }
}

# ---------------------------------------------------------------------------
# Reject "already running" scenarios up front. If a previous up.ps1 already
# launched stuff, the user should run down.ps1 first instead of stacking.
# ---------------------------------------------------------------------------
foreach ($pair in @(
        @($ApiPidFile, "api-server"),
        @($AdminPidFile, "admin-panel"),
        @($WorkerPidFile, "worker"),
        @($OrchPidFile, "orchestrator"))) {
    $file, $name = $pair
    if (Test-Path $file) {
        $oldPid = Get-Content $file -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($oldPid -and (Get-Process -Id $oldPid -ErrorAction SilentlyContinue)) {
            throw "$name already running (pid $oldPid). Run scripts/dev/down.ps1 first, or delete $file if it's stale."
        }
        Remove-Item $file -ErrorAction SilentlyContinue
    }
}

if (-not (Test-PortBindable -Port $ApiPort))   { throw "port $ApiPort is in use. Free it or pick another with -ApiPort." }
if (-not (Test-PortBindable -Port $AdminPort)) { throw "port $AdminPort is in use. Free it or pick another with -AdminPort." }

# ---------------------------------------------------------------------------
# Docker stack
# ---------------------------------------------------------------------------
Write-Host "==> Bringing docker stack up" -ForegroundColor Cyan
& docker compose @ComposeArgs up -d
if ($LASTEXITCODE -ne 0) { throw "docker compose up failed" }

Write-Host "==> Waiting for postgres to be healthy (max 60s)" -ForegroundColor Cyan
$pgDeadline = (Get-Date).AddSeconds(60)
$pgHealthy = $false
while ((Get-Date) -lt $pgDeadline) {
    $raw = & docker compose @ComposeArgs ps postgres --format json 2>$null
    if ($raw) {
        try {
            $entry = $raw | ConvertFrom-Json -ErrorAction Stop
            if ($entry -and $entry.Health -eq "healthy") { $pgHealthy = $true; break }
        } catch { }
    }
    Start-Sleep -Seconds 2
}
if (-not $pgHealthy) { throw "postgres did not become healthy within 60s" }

# ---------------------------------------------------------------------------
# Alembic migrations
# ---------------------------------------------------------------------------
$venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    throw ".venv missing. Run .\scripts\dev\bootstrap.ps1 first."
}

Write-Host "==> Applying Alembic migrations" -ForegroundColor Cyan
$env:DATABASE_URL = "postgresql+asyncpg://migrations_user:changeme-migrations-dev-only@localhost:15432/agentic_platform"
Push-Location (Join-Path $RepoRoot "apps\api-server")
try {
    & $venvPython -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw "alembic upgrade failed" }
} finally {
    Pop-Location
    Remove-Item Env:\DATABASE_URL -ErrorAction SilentlyContinue
}

# ---------------------------------------------------------------------------
# api-server (uvicorn) — detached
# ---------------------------------------------------------------------------
$env:API_SERVER_DATABASE_URL       = "postgresql+asyncpg://app_user:changeme-app-dev-only@localhost:15432/agentic_platform"
$env:API_SERVER_ADMIN_DATABASE_URL = "postgresql+asyncpg://migrations_user:changeme-migrations-dev-only@localhost:15432/agentic_platform"
# Con contraseña: el compose arranca Redis con `--requirepass ${REDIS_PASSWORD}`
# (prod-10) y el `.env.example` la fija en `changeme-redis-dev-only`. Sin ella el
# api-server arranca, `/healthz` da 200 y el primer login muere con
# `AuthenticationError: Authentication required` (gotcha
# redis-con-contrasena-rompe-la-integracion). Descubierto el 2026-09-08 en una
# máquina nueva: el script llevaba la URL sin credencial desde antes de prod-10.
$env:API_SERVER_REDIS_URL          = "redis://:changeme-redis-dev-only@localhost:6379/0"
$env:API_SERVER_JWT_SECRET         = "dev-only-jwt-secret-change-me"
# Wire the dev Vault (compose service, healthy on :8200) so secret-backed
# features work locally: LLM provider credentials (Plan 11.2), MCP server
# auth_ref (ADR 0025), etc. vault_url already defaults to localhost:8200.
$env:API_SERVER_VAULT_TOKEN        = "dev-root-token"
# El descubrimiento y la sonda de un MCP REMOTO salen por el egress-proxy
# (`task_mk_02`, ADR 0165): sin esta URL el api-server los intenta directos y
# el aviso `EGRESS_BLOCKED` no puede aparecer nunca. El contenedor
# `agentic-egress-proxy` publica 8888 en loopback.
$env:API_SERVER_EGRESS_PROXY_URL   = "http://127.0.0.1:8888"
# El api-server no solo consume: ENCOLA trabajo para el worker (import de tools
# de un MCP `task_mk_01`, sondas de destino de backup, despacho de eventos de
# ejecución). Su `broker_url` por defecto NO lleva la contraseña de Redis, así
# que sin estas dos cada encolado moría con «Authentication required» —logueado
# como `event_dispatch.enqueue_failed` y tragado, porque es best-effort— y el
# síntoma era una cola que nunca se drenaba sin ningún error a la vista.
# Medido el 2026-09-09 con el primer run de esta máquina.
$env:API_SERVER_BROKER_URL         = "redis://:changeme-redis-dev-only@localhost:6379/1"
$env:API_SERVER_RESULT_BACKEND     = "redis://:changeme-redis-dev-only@localhost:6379/2"

Remove-Item $ApiLog, $ApiErr -ErrorAction SilentlyContinue
# Con `-Runs`, uvicorn escucha en TODAS las interfaces y no sólo en loopback.
# No es un capricho: el sandbox del run llama a la API interna del agente
# (`/internal/agent/*`) por `host.docker.internal`, que resuelve a la IP del host
# vista desde el contenedor — un uvicorn atado a 127.0.0.1 le da un puerto
# cerrado, el runtime aborta en `ensure_reachable()` y el run muere en segundos
# sin un paso y sin `abort_code`. Es exactamente la cadena de
# `docs/03-guides/gotchas/agent-run-failed-si-el-sandbox-no-alcanza-la-api-interna.md`,
# reencontrada en dev el 2026-09-09. Sin `-Runs` se queda en loopback, que es lo
# que quieres cuando sólo abres el panel.
$apiArgs = @("-m", "uvicorn", "api_server.main:app", "--port", $ApiPort)
if ($Runs) { $apiArgs += @("--host", "0.0.0.0") }
Write-Host "==> Starting api-server on http://127.0.0.1:$ApiPort (logs: $ApiLog)" -ForegroundColor Cyan
$apiProc = Start-Process -PassThru -WindowStyle Hidden `
    -FilePath $venvPython `
    -ArgumentList $apiArgs `
    -WorkingDirectory (Join-Path $RepoRoot "apps\api-server") `
    -RedirectStandardOutput $ApiLog `
    -RedirectStandardError $ApiErr
$apiProc.Id | Out-File -Encoding ascii $ApiPidFile

Write-Host "==> Waiting for /healthz (max 30s)" -ForegroundColor Cyan
$hzDeadline = (Get-Date).AddSeconds(30)
$apiUp = $false
while ((Get-Date) -lt $hzDeadline) {
    if ($apiProc.HasExited) {
        Start-Sleep -Milliseconds 300
        Get-Content $ApiErr -Tail 30 -ErrorAction SilentlyContinue | Out-Host
        Remove-Item $ApiPidFile -ErrorAction SilentlyContinue
        throw "api-server exited prematurely. See $ApiErr"
    }
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:$ApiPort/healthz" -TimeoutSec 2 -ErrorAction Stop | Out-Null
        $apiUp = $true; break
    } catch { Start-Sleep -Seconds 1 }
}
if (-not $apiUp) { throw "api-server /healthz did not respond within 30s. See $ApiLog / $ApiErr" }

# ---------------------------------------------------------------------------
# worker (celery) + orchestrator — detached, solo con -Runs
#
# Son los dos que faltaban para que un run OCURRA: el orchestrator vigila las
# tareas `ready` y las despacha por Celery; el worker las recoge, prepara el
# worktree y lanza el contenedor `agent-runtime`. Sin ellos una tarea se queda
# en `ready` indefinidamente y el panel no dice por que — no hay error, no hay
# nadie escuchando.
#
# Tres decisiones de este bloque, con su motivo, porque ninguna es obvia:
#
#  1. **`--pool=threads`.** El pool por defecto de Celery (prefork) no existe
#     en Windows: `billiard` necesita `fork(2)`. Con prefork el worker arranca,
#     acepta el mensaje y muere al ejecutarlo.
#
#  2. **`WORKERS_AGENT_NETWORK_INTERNAL=false`, y esto SI es una concesion.**
#     Por defecto el sandbox nace en una red `internal` sin salida (principio
#     rector 2) y alcanza a los proveedores LLM SOLO por el egress-proxy, que
#     el worker engancha al bridge del run. Ese cableado es del compose; con el
#     worker en el host, el sandbox se queda sin ruta a NINGUN modelo — ni al
#     Ollama de esta maquina— y todo run muere sin llegar a pensar. Se abre la
#     red del sandbox para que dev pueda ejecutar, y se dice aqui en vez de
#     esconderlo: **en un despliegue esto no se toca**. El sandbox sigue con
#     cap-drop ALL, FS de solo lectura y sin socket Docker.
#
#  3. **`WORKERS_SECCOMP_PROFILE_PATH` se queda vacio.** El perfil endurecido
#     (`docker/seccomp/agent-runtime.json`) lo resuelve el DAEMON por ruta, y
#     el daemon de Docker Desktop vive en la VM: una ruta Windows no existe
#     ahi. En dev corre con el `docker-default`. Consecuencia honesta: **el
#     primer run bajo el perfil estricto sigue siendo una verificacion
#     pendiente**, y no se acredita aqui.
# ---------------------------------------------------------------------------
if ($Runs) {
    if (-not (Test-PortBindable -Port $OrchestratorPort)) {
        throw "port $OrchestratorPort is in use. Free it or pick another with -OrchestratorPort."
    }
    $runtimeImage = (docker images -q "agent-runtime:v1" 2>$null)
    if (-not $runtimeImage) {
        throw @"
La imagen 'agent-runtime:v1' no existe y sin ella el worker no puede lanzar
ningun run. Construyela (~5 min, contexto = raiz del repo):

  docker build -t agent-runtime:v1 -f docker/agent-runtimes/agent-runtime/Dockerfile .
"@
    }

    New-Item -ItemType Directory -Path $WorkerDataRoot -Force | Out-Null

    # `service_user` es el rol BYPASSRLS sin DDL con el que corren worker y
    # orchestrator (el compose hace lo mismo); el puerto es el 15432 del dev.
    $serviceDsn = "postgresql+asyncpg://service_user:changeme-service-dev-only@localhost:15432/agentic_platform"
    # Redis con contrasena, por lo mismo que el api-server (prod-10): sin ella
    # el worker arranca y muere en el primer mensaje con `NOAUTH`.
    $redisAuth = "redis://:changeme-redis-dev-only@localhost:6379"

    $env:WORKERS_DATABASE_URL             = $serviceDsn
    $env:WORKERS_BROKER_URL               = "$redisAuth/1"
    $env:WORKERS_RESULT_BACKEND           = "$redisAuth/2"
    $env:WORKERS_EVENTS_REDIS_URL         = "$redisAuth/0"
    $env:WORKERS_DATA_ROOT                = $WorkerDataRoot
    $env:WORKERS_VAULT_URL                = "http://127.0.0.1:8200"
    $env:WORKERS_VAULT_TOKEN              = "dev-root-token"
    $env:WORKERS_AGENT_NETWORK_INTERNAL   = "false"
    $env:WORKERS_SECCOMP_PROFILE_PATH     = ""
    # El sandbox llama a la API interna del agente (`/internal/agent/*`: recall,
    # rag-search, document-convert). Su default es `http://api-server:8000`, el
    # hostname del servicio EN EL COMPOSE: aqui el api-server vive en el HOST,
    # asi que ese nombre no resuelve dentro del contenedor y el worker avisa con
    # `run_bridge_peer_missing` — un WARNING, no un error, y el run seguia hasta
    # morir sin pasos. `host.docker.internal` es el nombre que Docker Desktop da
    # al host desde un contenedor.
    $env:WORKERS_AGENT_INTERNAL_API_URL   = "http://host.docker.internal:$ApiPort"
    # `127.0.0.1` y no `localhost`: en Windows `localhost` resuelve primero a
    # `::1` y los tres clientes de Ollama (memorizer, embedder, cortex) mueren
    # con «All connection attempts failed» aunque el servicio este publicado.
    # Visto el 2026-09-09 en el primer run: `memorizer.llm_call_failed`.
    $env:WORKERS_MEMORIZER_LLM_BASE_URL     = "http://127.0.0.1:11434/v1"
    $env:WORKERS_MEMORY_EMBEDDER_BASE_URL   = "http://127.0.0.1:11434"
    $env:WORKERS_CORTEX_AFFECT_LLM_BASE_URL = "http://127.0.0.1:11434/v1"
    # El worker IMPORTA codigo del api-server para publicar eventos de ejecucion
    # (`api_server.celery_client`), y ese cliente lee su propia config con
    # prefijo `API_SERVER_`. Sin estas tres, cada evento (`execution_failed`,
    # `task_blocked`) muere con «Authentication required» contra la Redis con
    # contrasena y el panel no se entera de nada en vivo.
    $env:API_SERVER_REDIS_URL      = "$redisAuth/0"
    $env:API_SERVER_BROKER_URL     = "$redisAuth/1"
    $env:API_SERVER_RESULT_BACKEND = "$redisAuth/2"
    $env:API_SERVER_DATABASE_URL   = "postgresql+asyncpg://app_user:changeme-app-dev-only@localhost:15432/agentic_platform"
    $env:API_SERVER_JWT_SECRET     = "dev-only-jwt-secret-change-me"

    Remove-Item $WorkerLog, $WorkerErr -ErrorAction SilentlyContinue
    Write-Host "==> Starting worker (celery, todas las lanes; logs: $WorkerLog)" -ForegroundColor Cyan
    $workerProc = Start-Process -PassThru -WindowStyle Hidden `
        -FilePath $venvPython `
        -ArgumentList "-m", "celery", "-A", "workers.celery_app:app", "worker",
                      "-Q", "default,ingestion,test,review,privileged,marketplace",
                      "--pool=threads", "-c", "4", "--loglevel=info" `
        -WorkingDirectory $RepoRoot `
        -RedirectStandardOutput $WorkerLog `
        -RedirectStandardError $WorkerErr
    $workerProc.Id | Out-File -Encoding ascii $WorkerPidFile

    $env:ORCHESTRATOR_DATABASE_URL = $serviceDsn
    $env:ORCHESTRATOR_BROKER_URL   = "$redisAuth/1"
    $env:ORCHESTRATOR_REDIS_URL    = "$redisAuth/0"
    $env:ORCHESTRATOR_PORT         = "$OrchestratorPort"

    Remove-Item $OrchLog, $OrchErr -ErrorAction SilentlyContinue
    Write-Host "==> Starting orchestrator on http://127.0.0.1:$OrchestratorPort (logs: $OrchLog)" -ForegroundColor Cyan
    $orchProc = Start-Process -PassThru -WindowStyle Hidden `
        -FilePath $venvPython `
        -ArgumentList "-m", "orchestrator" `
        -WorkingDirectory $RepoRoot `
        -RedirectStandardOutput $OrchLog `
        -RedirectStandardError $OrchErr
    $orchProc.Id | Out-File -Encoding ascii $OrchPidFile

    Write-Host "==> Waiting for the orchestrator /healthz (max 30s)" -ForegroundColor Cyan
    $orchDeadline = (Get-Date).AddSeconds(30)
    $orchUp = $false
    while ((Get-Date) -lt $orchDeadline) {
        if ($orchProc.HasExited) {
            Start-Sleep -Milliseconds 300
            Get-Content $OrchErr -Tail 30 -ErrorAction SilentlyContinue | Out-Host
            Remove-Item $OrchPidFile -ErrorAction SilentlyContinue
            throw "orchestrator exited prematurely. See $OrchErr"
        }
        try {
            Invoke-RestMethod -Uri "http://127.0.0.1:$OrchestratorPort/healthz" -TimeoutSec 2 -ErrorAction Stop | Out-Null
            $orchUp = $true; break
        } catch { Start-Sleep -Seconds 1 }
    }
    if (-not $orchUp) { throw "orchestrator /healthz did not respond within 30s. See $OrchLog / $OrchErr" }

    # El worker no publica puerto: se comprueba preguntandole por Celery.
    #
    # 120s y no 30: medido el 2026-09-09, en Windows tarda ~40s desde el
    # `Start-Process` hasta el `celery@host ready` (arranque del pool de hilos +
    # los ~7s de `mingle: searching for neighbors`), y cada `inspect ping` gasta
    # ~8s mas en su propio arranque. Con 30s el bucle se agotaba con el worker
    # perfectamente sano y el script moria ANTES de arrancar el panel.
    Write-Host "==> Pinging the worker over Celery (max 120s; en Windows tarda ~40s)" -ForegroundColor Cyan
    # `$ErrorActionPreference = "Continue"` DENTRO del bucle, y no es cosmetico:
    # con el "Stop" de la cabecera del script, el stderr de un EJECUTABLE NATIVO
    # se convierte en error TERMINANTE (`NativeCommandError`). Mientras el worker
    # arranca, `inspect ping` escribe «No nodes replied» en stderr — o sea que el
    # primer intento fallido, que es el caso NORMAL, mataba el script entero y el
    # panel no llegaba a arrancar. Diagnosticado el 2026-09-09 tras dos pasadas
    # en las que el sintoma parecia un cuelgue.
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $pingDeadline = (Get-Date).AddSeconds(120)
    $workerUp = $false
    while ((Get-Date) -lt $pingDeadline) {
        if ($workerProc.HasExited) {
            Start-Sleep -Milliseconds 300
            Get-Content $WorkerErr -Tail 30 -ErrorAction SilentlyContinue | Out-Host
            Remove-Item $WorkerPidFile -ErrorAction SilentlyContinue
            throw "worker exited prematurely. See $WorkerErr"
        }
        # `--timeout 5` NO es decorativo: sin el, `inspect ping` espera la
        # respuesta con el default de kombu y puede quedarse colgado; y como el
        # `while` solo se evalua ENTRE iteraciones, un comando que no vuelve
        # convierte este bucle acotado en una espera infinita. Pasado el
        # 2026-09-09: el script se quedo aqui y ni arranco el panel.
        # `2>$null`: mientras el worker arranca, `inspect ping` escribe «No nodes
        # replied» en stderr, y PowerShell convierte el stderr de un ejecutable
        # nativo en un ErrorRecord que ensucia la consola con un stack por
        # intento. El veredicto es el codigo de salida, no el texto.
        & $venvPython -m celery -A workers.celery_app:app inspect --timeout 5 ping 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { $workerUp = $true; break }
        Start-Sleep -Seconds 2
    }
    $ErrorActionPreference = $prevEap
    if (-not $workerUp) { throw "the worker did not answer 'celery inspect ping' within 120s. See $WorkerLog / $WorkerErr" }
}

# ---------------------------------------------------------------------------
# admin-panel (npm run dev) — detached
# Launched via cmd.exe so taskkill /T can walk the tree (cmd -> npm.cmd -> node).
# ---------------------------------------------------------------------------
if (-not (Test-Path (Join-Path $RepoRoot "apps\admin-panel\node_modules\next"))) {
    throw "admin-panel deps missing. Run: cd apps\admin-panel; npm install"
}

Remove-Item $AdminLog, $AdminErr -ErrorAction SilentlyContinue
$env:NEXT_PUBLIC_API_URL = "http://127.0.0.1:$ApiPort"
Write-Host "==> Starting admin-panel on http://localhost:$AdminPort (logs: $AdminLog)" -ForegroundColor Cyan
$adminProc = Start-Process -PassThru -WindowStyle Hidden `
    -FilePath "cmd.exe" `
    -ArgumentList "/c", "npm", "run", "dev", "--", "-p", $AdminPort `
    -WorkingDirectory (Join-Path $RepoRoot "apps\admin-panel") `
    -RedirectStandardOutput $AdminLog `
    -RedirectStandardError $AdminErr
$adminProc.Id | Out-File -Encoding ascii $AdminPidFile

Write-Host "==> Waiting for admin-panel to compile (max 60s)" -ForegroundColor Cyan
$adminDeadline = (Get-Date).AddSeconds(60)
$adminUp = $false
while ((Get-Date) -lt $adminDeadline) {
    if ($adminProc.HasExited) {
        Start-Sleep -Milliseconds 300
        Get-Content $AdminErr -Tail 30 -ErrorAction SilentlyContinue | Out-Host
        Remove-Item $AdminPidFile -ErrorAction SilentlyContinue
        throw "admin-panel exited prematurely. See $AdminErr"
    }
    try {
        Invoke-WebRequest -Uri "http://127.0.0.1:$AdminPort/" -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop | Out-Null
        $adminUp = $true; break
    } catch { Start-Sleep -Seconds 2 }
}
if (-not $adminUp) { throw "admin-panel did not start within 60s. See $AdminLog / $AdminErr" }

# ---------------------------------------------------------------------------
# Print URLs + handoff
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "================================================================" -ForegroundColor Green
Write-Host "Dev stack is up. You can close this terminal."                   -ForegroundColor Green
Write-Host "================================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Admin panel:   http://localhost:$AdminPort/login"
Write-Host "                 (root@example.com / longenoughpw)"
Write-Host "  API docs:      http://127.0.0.1:$ApiPort/docs"
Write-Host "  API healthz:   http://127.0.0.1:$ApiPort/healthz"
Write-Host "  MinIO:         http://localhost:9001 (minioadmin / changeme-dev-only)"
Write-Host "  Vault UI:      http://localhost:8200/ui (token: dev-root-token)"
Write-Host "  Ollama:        http://localhost:11434 (embeddings; sin auth)"
if ($Runs) {
    Write-Host "  Orchestrator:  http://127.0.0.1:$OrchestratorPort/healthz  (despacha las tareas ready)"
    Write-Host "  Worker:        celery, lanes default/ingestion/test/review/privileged/marketplace"
    Write-Host "                 sandbox con red ABIERTA (dev) y sin perfil seccomp estricto" -ForegroundColor Yellow
} else {
    # OJO: sin backticks dentro de la cadena. En PowerShell el backtick es el
    # carácter de escape, así que "`ready`" se convierte en un retorno de carro
    # y el fichero deja de parsear (roto y arreglado el 2026-09-09).
    Write-Host "  Runs:          APAGADOS - sin worker ni orchestrator, una tarea ready no avanza." -ForegroundColor Yellow
    Write-Host "                 Relanza con  -Runs  (necesita la imagen agent-runtime:v1)."
}
if ($Monitoring) {
    Write-Host "  Grafana:       http://localhost:3001 (admin / changeme-dev-only)"
    Write-Host "  Prometheus:    http://localhost:9090"
    Write-Host "  Alertmanager:  http://localhost:9093"
} else {
    Write-Host "  Monitoring:    (opcional) re-lanza con  -Monitoring"
}
Write-Host ""
Write-Host "  Logs:   $DevDir\*.log"
Write-Host "  Stop:   .\scripts\dev\down.ps1"
Write-Host ""
