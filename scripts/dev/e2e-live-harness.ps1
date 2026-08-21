#!/usr/bin/env pwsh
# -----------------------------------------------------------------------------
# scripts/dev/e2e-live-harness.ps1
#
# El arnes de los 12 specs Playwright que NO mockean el backend.
#
# SOLO ASCII EN ESTE FICHERO, a proposito. Windows PowerShell 5.1 lee un .ps1
# sin BOM con la pagina de codigos ANSI, y hay caracteres cuyos bytes UTF-8
# contienen 0x93/0x94 -- que en cp1252 son las comillas tipograficas, y
# PowerShell las acepta como delimitador de cadena. Una sola raya larga en un
# comentario deja media docena de cadenas abiertas y el error aparece 200 lineas
# mas abajo, senalando una linea intacta. Ver
# docs/03-guides/gotchas/powershell-utf8-em-dash-and-native-stderr.md
#
# POR QUE ES UN HERMANO DE run-e2e.ps1 Y NO UN FLAG SUYO
# ------------------------------------------------------
# `run-e2e.ps1` corre la suite entera contra la base del stack vivo. Para el
# subconjunto mockeado eso es inofensivo: esos specs no tocan el backend. Para
# estos doce NO lo es -- crean y borran proyectos, agentes, equipos y tenants --,
# asi que el arnes necesita una base DESECHABLE. Y necesita lo contrario que un
# runner: dejar el api-server EN PIE con las credenciales impresas, para que la
# siguiente iteracion cueste un `npx playwright test` y no una hora de prueba y
# error.
#
# Meter las dos cosas en `run-e2e.ps1` habria convertido su ruta feliz -- la que
# usan CI y el flujo manual -- en una rama de un condicional sobre a que base
# apuntar. Un guion cuya ruta peligrosa comparte codigo con la segura es el
# guion que un dia apunta a la base equivocada.
#
# QUE HACE, EN ORDEN (todo idempotente)
#   1. Lee `docker/.env` -- la UNICA fuente de credenciales de este guion.
#   2. Comprueba que la base objetivo NO es la del stack vivo, y aborta si lo es.
#   3. Crea la base desechable si no esta (+ extensiones + privilegios base).
#   4. `alembic upgrade head`.
#   5. Los GRANT que la migracion no trae, con su REVOKE detras.
#   6. Los seeds del catalogo (`python -m api_server.seeds`).
#   7. Los cuatro usuarios y el tenant que los specs declaran en su cabecera.
#   8. Arranca el api-server con LAS DOS urls de base de datos.
#   9. Imprime credenciales, URL y los comandos que faltan.
#
# USO
#   .\scripts\dev\e2e-live-harness.ps1                 # levanta el arnes
#   .\scripts\dev\e2e-live-harness.ps1 -Recreate       # desde cero (borra la BD)
#   .\scripts\dev\e2e-live-harness.ps1 -SkipSeeds      # sin catalogo (mas rapido)
#   .\scripts\dev\e2e-live-harness.ps1 -BuildPanel     # + build del admin-panel
#   .\scripts\dev\e2e-live-harness.ps1 -Down           # para el api-server
#
# REQUISITOS: el stack de docker levantado (`.\scripts\dev\up.ps1`), `.venv`
# (`.\scripts\dev\bootstrap.ps1`) y `npm install` hecho en apps/admin-panel.
#
# La guia esta en docs/03-guides/e2e-con-backend-vivo.md.
# -----------------------------------------------------------------------------

[CmdletBinding()]
param(
    # Base de datos DESECHABLE del arnes. Nunca la del stack vivo: la guarda
    # `Assert-DisposableDatabase` aborta si alguien lo intenta.
    [string]$DbName = "e2e_vivo",
    [int]$ApiPort = 8001,
    # Primera de las TRES bases logicas de Redis que consume el api-server
    # (result_backend / sessions / broker). Ver `Assert-RedisDbRange`.
    [int]$RedisDbBase = 12,
    # Borra la base desechable antes de empezar. Es lo que hay que usar para
    # comprobar que este guion funciona de cero.
    [switch]$Recreate,
    # Salta `python -m api_server.seeds`. Los seeds tardan minutos (ingieren el
    # catalogo documental y piden embeddings a Ollama) y son idempotentes, asi
    # que en la segunda vuelta sobran.
    [switch]$SkipSeeds,
    # Construye el admin-panel contra este arnes. Sin esto el guion imprime el
    # comando y no lo ejecuta.
    [switch]$BuildPanel,
    # Para el api-server del arnes y sale. No toca la base de datos.
    [switch]$Down
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$HarnessPy = Join-Path $PSScriptRoot "e2e_live_harness.py"
$ApiLog = Join-Path $RepoRoot ".e2e-live-harness.log"
$ApiErrLog = Join-Path $RepoRoot ".e2e-live-harness.err.log"
$PidFile = Join-Path $RepoRoot ".e2e-live-harness.pid"

function Stop-WithMessage {
    param([string]$Message)
    # `Write-Host` + `exit 1` y no `throw`: estos mensajes estan escritos para
    # LEERSE, y `throw` los envuelve en un volcado de PowerShell que los repite
    # dos veces entre trazas de pila. Para lo que falla por dentro (un exit code
    # de alembic, de npm) si se usa `throw`, porque ahi la traza informa.
    Write-Host ""
    Write-Host $Message -ForegroundColor Red
    Write-Host ""
    exit 1
}

# ---------------------------------------------------------------------------
# Llamar a un ejecutable nativo sin que su stderr mate el guion
#
# PowerShell 5.1 convierte CADA linea de stderr de un exe nativo en un
# `NativeCommandError`, y con `$ErrorActionPreference = "Stop"` eso detiene el
# guion aunque el exe haya devuelto 0. Alembic escribe sus `INFO [alembic...]`
# por stderr, asi que `& python -m alembic upgrade head` revienta el guion en
# cuanto la primera migracion dice algo -- con el upgrade a medias y un mensaje
# que habla de PowerShell, no de la base de datos.
#
# El veredicto real sale de `$LASTEXITCODE`, no de como PowerShell interprete el
# stream de error. `Out-Host` es la otra mitad: sin el, la salida del exe se
# anade al pipeline de retorno de la funcion y el llamante recibe un array en
# vez del entero. Las tres trampas estan en
# docs/03-guides/gotchas/powershell-utf8-em-dash-and-native-stderr.md
# ---------------------------------------------------------------------------
function Invoke-Native {
    param(
        [string]$Exe,
        [string[]]$Arguments,
        [string]$FailMessage
    )
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Exe @Arguments 2>&1 | Out-Host
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
    if ($code -ne 0) { throw "$FailMessage (exit $code)" }
}

# ---------------------------------------------------------------------------
# docker/.env -- la unica fuente de credenciales
#
# NO hay ni una contrasena escrita en este fichero, y no es una formalidad: el
# repositorio es publico y `docker/.env` esta en .gitignore. Se lee el MISMO
# fichero que lee `docker compose`, asi que el arnes y el stack no pueden
# divergir -- el mismo criterio que aplica `tests/integration/_redis_url.py`.
# ---------------------------------------------------------------------------
function Read-DockerEnv {
    $path = Join-Path $RepoRoot "docker\.env"
    if (-not (Test-Path $path)) {
        Stop-WithMessage @"
No existe docker/.env, que es de donde salen TODAS las credenciales del arnes.
Crealo copiando el ejemplo y ajustando lo que haga falta:
    Copy-Item docker\.env.example docker\.env
(El compose tampoco arranca sin el: cada credencial se declara con :? y sin
default, para que un despliegue al que le falte una no arranque.)
"@
    }
    $valores = @{}
    foreach ($linea in Get-Content $path -Encoding UTF8) {
        $texto = $linea.Trim()
        if ($texto -eq "" -or $texto.StartsWith("#")) { continue }
        $corte = $texto.IndexOf("=")
        if ($corte -lt 1) { continue }
        $valores[$texto.Substring(0, $corte).Trim()] = $texto.Substring($corte + 1).Trim()
    }
    return $valores
}

function Get-RequiredEnv {
    param([hashtable]$Values, [string]$Key)
    if (-not $Values.ContainsKey($Key) -or [string]::IsNullOrWhiteSpace($Values[$Key])) {
        Stop-WithMessage "docker/.env no define $Key, y el arnes lo necesita. Mira docker/.env.example."
    }
    return $Values[$Key]
}

function Get-OptionalEnv {
    param([hashtable]$Values, [string]$Key, [string]$Fallback)
    if ($Values.ContainsKey($Key) -and -not [string]::IsNullOrWhiteSpace($Values[$Key])) {
        return $Values[$Key]
    }
    return $Fallback
}

# El userinfo de una URL va percent-encoded: una contrasena con `@`, `/` o `#`
# rompe el parseo del DSN de formas que no se parecen a su causa (asyncpg dice
# "invalid connection URI", SQLAlchemy dice que falta el host).
function Protect-UrlPart {
    param([string]$Raw)
    return [System.Uri]::EscapeDataString($Raw)
}

# ---------------------------------------------------------------------------
# LAS GUARDAS. Son el motivo por el que este guion existe como guion.
# ---------------------------------------------------------------------------
function Assert-DisposableDatabase {
    param([string]$Target, [string]$LiveDb)

    # La base del stack va por DESCUBRIMIENTO (el POSTGRES_DB del .env, o sea el
    # mismo dato que lee el compose) y ademas por nombre en la lista de abajo.
    # Lo primero protege del despiste de hoy; lo segundo, del dia que alguien
    # renombre la base del stack y no toque nada mas.
    $protegidas = @("agentic_platform", "postgres", "template0", "template1")
    if (-not [string]::IsNullOrWhiteSpace($LiveDb)) { $protegidas += $LiveDb }
    $protegidas = $protegidas | Sort-Object -Unique

    if ($protegidas -contains $Target) {
        Stop-WithMessage @"
ABORTA: '$Target' es una base PROTEGIDA, no la desechable del arnes.

Los 12 specs que no mockean el backend no se limitan a leer: CREAN y BORRAN
proyectos, agentes, equipos y tenants. Correrlos contra la base del stack vivo
no la ensucia -- se lleva trabajo real por delante.

Bases protegidas: $($protegidas -join ', ')
    ('$LiveDb' sale del POSTGRES_DB de docker/.env, o sea del propio compose.)

Pasa -DbName con una base desechable. El default, e2e_vivo, ya lo es.
"@
    }
}

# La OTRA guarda, y la que mas cerca estuvo de hacer dano: `admin_database_url`
# tiene un DEFAULT en `api_server/config.py`, y ese default apunta a la base del
# stack vivo. Un arnes que exporte solo `API_SERVER_DATABASE_URL` deja que la
# mitad /admin/* del api-server -- System Admin, o sea BYPASSRLS y sin RLS que
# la pare -- escriba en la base del operador. No da ningun error: da un arnes
# que parece funcionar.
function Assert-BothDatabaseUrls {
    param([string]$Target)

    $pares = [ordered]@{
        "API_SERVER_DATABASE_URL"       = $env:API_SERVER_DATABASE_URL
        "API_SERVER_ADMIN_DATABASE_URL" = $env:API_SERVER_ADMIN_DATABASE_URL
    }
    foreach ($nombre in $pares.Keys) {
        $valor = $pares[$nombre]
        if ([string]::IsNullOrWhiteSpace($valor)) {
            Stop-WithMessage @"
ABORTA: $nombre esta vacia justo antes de arrancar el api-server.

Sin ella el api-server NO falla: cae al default de `api_server/config.py`, que
apunta a la base del stack vivo en localhost:15432. Con
API_SERVER_ADMIN_DATABASE_URL eso significa que las rutas /admin/* escriben en
la base del operador con un rol BYPASSRLS.
"@
        }
        if (-not $valor.EndsWith("/$Target")) {
            $censurado = $valor -replace '://[^@]*@', '://***@'
            Stop-WithMessage @"
ABORTA: $nombre no apunta a la base del arnes ('$Target').

Valor (sin credenciales): $censurado

Las DOS urls tienen que acabar en /$Target. Si una apunta a otro sitio, el
api-server lee de una base y escribe en la otra.
"@
        }
    }
}

# Redis: el api-server consume TRES bases logicas, no dos -- `redis_url`
# (sesiones y rate limit), `broker_url` y `result_backend`. Exportar solo las dos
# primeras deja `result_backend` en su default, que es la base 2: el result
# backend de Celery DEL STACK VIVO. Y las 0/1/2 son justo las que el arnes no
# puede usar (`tests/integration/_redis_url.PLATFORM_REDIS_DATABASES` explica por
# que: el worker vivo drena la cola antes de que el test la lea). La 15 se deja
# libre porque es la del arnes de pytest.
function Assert-RedisDbRange {
    param([int]$Base)
    if ($Base -lt 3 -or ($Base + 2) -gt 14) {
        Stop-WithMessage @"
ABORTA: -RedisDbBase $Base usaria las bases de Redis $Base/$($Base+1)/$($Base+2).

Las bases 0, 1 y 2 son del STACK VIVO (event streams, broker de Celery, result
backend): el orquestador y los workers las consumen en caliente, asi que se
llevarian los mensajes del arnes -- y un DEL del arnes tiraria trabajo real.
La 15 es la del arnes de pytest (tests/integration/_redis_url.py).

Usa un valor entre 3 y 12 (el default es 12 -> bases 12/13/14).
"@
    }
}

# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
function Test-PortBindable {
    param([int]$Port)
    try {
        $l = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
        $l.Start()
        $l.Stop()
        return $true
    } catch {
        return $false
    }
}

function Test-PortAnswers {
    param([int]$Port, [string]$What)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        # 127.0.0.1 y no localhost: en Windows el resolver devuelve ::1 primero y
        # los puertos que publica Docker Desktop escuchan solo en IPv4, asi que
        # cada intento regala ~2 s sin dar ningun error. Ver
        # docs/03-guides/gotchas/localhost-ipv6-primero-cuesta-dos-segundos.md
        $ok = $client.ConnectAsync("127.0.0.1", $Port).Wait(3000)
        if (-not $ok -or -not $client.Connected) {
            Stop-WithMessage "Nadie contesta en 127.0.0.1:$Port ($What). Levanta el stack: .\scripts\dev\up.ps1"
        }
    } finally {
        $client.Close()
    }
}

# Para el api-server del arnes, y SOLO el del arnes: se identifica por el pid que
# dejo escrito este guion, o por ser el python de nuestro .venv el que ocupa el
# puerto. Nunca se mata un proceso desconocido.
function Stop-HarnessApi {
    param([int]$Port)

    if (Test-Path $PidFile) {
        $anterior = (Get-Content $PidFile -Raw).Trim()
        if ($anterior -match '^\d+$') {
            $proc = Get-Process -Id ([int]$anterior) -ErrorAction SilentlyContinue
            if ($null -ne $proc) {
                Write-Host "==> Parando el api-server anterior del arnes (pid $anterior)" -ForegroundColor Yellow
                & taskkill /F /T /PID ([int]$anterior) 2>$null | Out-Null
            }
        }
        Remove-Item $PidFile -ErrorAction SilentlyContinue
    }

    if (Test-PortBindable -Port $Port) { return }

    $owner = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -eq $owner) {
        Stop-WithMessage "127.0.0.1:$Port no se puede abrir y nadie declara escuchar ahi. Prueba con -ApiPort <otro>."
    }
    $ownerPid = $owner.OwningProcess
    $cim = Get-CimInstance Win32_Process -Filter "ProcessId = $ownerPid" -ErrorAction SilentlyContinue
    $exe = ""
    if ($cim) { $exe = $cim.ExecutablePath }
    $cmd = ""
    if ($cim) { $cmd = $cim.CommandLine }

    $esNuestro = $false
    if ($exe -and ($exe.ToLower() -eq $VenvPython.ToLower())) { $esNuestro = $true }
    if ($cmd -match 'uvicorn|spawn_main|multiprocessing') { $esNuestro = $true }

    if (-not $esNuestro) {
        Stop-WithMessage @"
El puerto $Port lo ocupa el pid $ownerPid, que NO parece nuestro uvicorn.
    Ejecutable: $exe
    Comando:    $cmd
No se mata un proceso desconocido. Paralo tu, o usa -ApiPort <puerto libre>.
"@
    }
    Write-Host "==> Puerto $Port ocupado por un uvicorn huerfano (pid $ownerPid). Se cierra." -ForegroundColor Yellow
    & taskkill /F /T /PID $ownerPid 2>$null | Out-Null
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Milliseconds 500
        if (Test-PortBindable -Port $Port) { return }
    }
    Stop-WithMessage "El puerto $Port sigue ocupado 10 s despues de cerrar el pid $ownerPid."
}

function Invoke-HarnessStep {
    param([string]$Action, [string]$Title)
    Write-Host "==> $Title" -ForegroundColor Cyan
    Invoke-Native -Exe $VenvPython -Arguments @($HarnessPy, $Action) `
        -FailMessage "e2e_live_harness.py $Action fallo"
}

# Los specs que necesitan este arnes se descubren con el MISMO criterio, negado,
# con el que CI selecciona los que NO lo necesitan (.github/workflows/ci.yml:
# `grep -rlE "page\.route|\.route\("`). Se descubren y no se listan a mano para
# que un spec nuevo aparezca aqui solo.
function Get-LiveBackendSpecs {
    $dir = Join-Path $RepoRoot "apps\admin-panel\e2e"
    $todos = Get-ChildItem -Path $dir -Filter "*.spec.ts" -File
    if ($todos.Count -eq 0) {
        Stop-WithMessage "No hay ningun .spec.ts en $dir. Ruta equivocada?"
    }
    $vivos = @()
    foreach ($spec in $todos) {
        $mockea = Select-String -Path $spec.FullName -Pattern 'page\.route|\.route\(' -Quiet
        if (-not $mockea) { $vivos += ("e2e/" + $spec.Name) }
    }
    # Guarda con dientes: si el descubrimiento deja de encontrar specs sin mocks,
    # el resumen imprimiria un `npx playwright test` SIN argumentos, que corre la
    # suite ENTERA (los ~100 mockeados incluidos) contra el arnes. Pasar en vacio
    # aqui no es inocuo: es un comando distinto del que se cree copiar.
    if ($vivos.Count -eq 0) {
        Stop-WithMessage @"
El descubrimiento no encuentra NINGUN spec sin mocks entre $($todos.Count).

O todos los specs mockean ya (y este arnes no hace falta), o el patron
'page\.route|\.route\(' dejo de casar. Compruebalo antes de seguir: el resumen
imprimiria un `npx playwright test` sin argumentos, que corre la suite entera
contra este arnes.
"@
    }
    return $vivos
}

# ---------------------------------------------------------------------------
# 0) Preflight
# ---------------------------------------------------------------------------
Set-Location $RepoRoot
Write-Host "==> Repo: $RepoRoot" -ForegroundColor Cyan

if (-not (Test-Path $VenvPython)) {
    Stop-WithMessage ".venv no existe. Corre .\scripts\dev\bootstrap.ps1 primero."
}

$DockerEnv = Read-DockerEnv
$PgPort = [int](Get-OptionalEnv $DockerEnv "POSTGRES_PORT" "15432")
$RedisPort = [int](Get-OptionalEnv $DockerEnv "REDIS_PORT" "6379")
$LiveDb = Get-OptionalEnv $DockerEnv "POSTGRES_DB" ""

$SuperUser = Get-OptionalEnv $DockerEnv "POSTGRES_USER" "postgres"
$SuperPass = Get-RequiredEnv $DockerEnv "POSTGRES_PASSWORD"
$MigPass = Get-RequiredEnv $DockerEnv "MIGRATIONS_USER_PASSWORD"
$AppPass = Get-RequiredEnv $DockerEnv "APP_USER_PASSWORD"
$ServicePass = Get-RequiredEnv $DockerEnv "SERVICE_USER_PASSWORD"
$RedisPass = Get-RequiredEnv $DockerEnv "REDIS_PASSWORD"

# Los roles los crea docker/postgres/init/. Sus nombres son fijos, no secretos.
$MigRole = "migrations_user"
$AppRole = "app_user"
$ServiceRole = "service_user"

Assert-DisposableDatabase -Target $DbName -LiveDb $LiveDb
Assert-RedisDbRange -Base $RedisDbBase

if ($Down) {
    Stop-HarnessApi -Port $ApiPort
    Write-Host ""
    Write-Host "Arnes parado. La base '$DbName' sigue en pie (se reutiliza al siguiente arranque)." -ForegroundColor Green
    Write-Host "Para empezar de cero:  .\scripts\dev\e2e-live-harness.ps1 -Recreate" -ForegroundColor DarkGray
    exit 0
}

Test-PortAnswers -Port $PgPort -What "PostgreSQL del compose"
Test-PortAnswers -Port $RedisPort -What "Redis del compose"

# Los DSN se construyen aqui y viajan a Python por ENTORNO, nunca por argv: los
# argumentos de un proceso los lee cualquiera con Get-CimInstance Win32_Process.
$SuperEnc = Protect-UrlPart $SuperPass
$MigEnc = Protect-UrlPart $MigPass
$AppEnc = Protect-UrlPart $AppPass
$ServiceEnc = Protect-UrlPart $ServicePass
$RedisEnc = Protect-UrlPart $RedisPass

$env:HARNESS_DB = $DbName
$env:HARNESS_FORBIDDEN_DBS = $LiveDb
$env:HARNESS_MIGRATIONS_ROLE = $MigRole
$env:HARNESS_APP_ROLE = $AppRole
$env:HARNESS_SERVICE_ROLE = $ServiceRole
$env:HARNESS_SUPERUSER_DSN = "postgresql://${SuperUser}:${SuperEnc}@127.0.0.1:${PgPort}/postgres"
$env:HARNESS_TARGET_ADMIN_DSN = "postgresql://${SuperUser}:${SuperEnc}@127.0.0.1:${PgPort}/${DbName}"
$env:HARNESS_SEED_DSN = "postgresql://${MigRole}:${MigEnc}@127.0.0.1:${PgPort}/${DbName}"

$apiProc = $null
try {
    # -----------------------------------------------------------------------
    # 1) Base desechable
    # -----------------------------------------------------------------------
    if ($Recreate) {
        Invoke-HarnessStep -Action "drop" -Title "Borrando la base desechable '$DbName' (-Recreate)"
    }
    Invoke-HarnessStep -Action "create" -Title "Base desechable '$DbName' + extensiones + privilegios base"

    # -----------------------------------------------------------------------
    # 2) Migraciones
    # -----------------------------------------------------------------------
    Write-Host "==> alembic upgrade head" -ForegroundColor Cyan
    $env:DATABASE_URL = "postgresql+asyncpg://${MigRole}:${MigEnc}@127.0.0.1:${PgPort}/${DbName}"
    Push-Location (Join-Path $RepoRoot "apps\api-server")
    try {
        Invoke-Native -Exe $VenvPython -Arguments @("-m", "alembic", "upgrade", "head") `
            -FailMessage "alembic upgrade head fallo"
    } finally {
        Pop-Location
        Remove-Item Env:\DATABASE_URL -ErrorAction SilentlyContinue
    }

    # -----------------------------------------------------------------------
    # 3) Los GRANT que la migracion NO trae, con su REVOKE
    # -----------------------------------------------------------------------
    Invoke-HarnessStep -Action "grant" `
        -Title "GRANT de la aplicacion (+ REVOKE de las tablas que una migracion retira)"

    # -----------------------------------------------------------------------
    # 4) El entorno del api-server. LAS DOS urls, y las TRES bases de Redis.
    # -----------------------------------------------------------------------
    $env:API_SERVER_ENVIRONMENT = "dev"
    $env:API_SERVER_DATABASE_URL = "postgresql+asyncpg://${AppRole}:${AppEnc}@127.0.0.1:${PgPort}/${DbName}"
    $env:API_SERVER_ADMIN_DATABASE_URL = "postgresql+asyncpg://${ServiceRole}:${ServiceEnc}@127.0.0.1:${PgPort}/${DbName}"
    $env:API_SERVER_REDIS_URL = "redis://:${RedisEnc}@127.0.0.1:${RedisPort}/$($RedisDbBase + 1)"
    $env:API_SERVER_BROKER_URL = "redis://:${RedisEnc}@127.0.0.1:${RedisPort}/$($RedisDbBase + 2)"
    $env:API_SERVER_RESULT_BACKEND = "redis://:${RedisEnc}@127.0.0.1:${RedisPort}/${RedisDbBase}"

    # Secretos de firma FIJOS y con el nombre puesto: este arnes es local, su
    # puerto no sale de loopback y su base es desechable. Que se llamen `arnes`
    # es la mitad importante -- compartirlos con el stack haria que una sesion
    # del arnes valiese en la instalacion de verdad.
    $env:API_SERVER_JWT_SECRET = "arnes-e2e-jwt-secret-local-desechable"
    $env:API_SERVER_INTERNAL_TOKEN_SECRET = "arnes-e2e-internal-token-secret-local-desechable"

    # El limitador de logins. Los 12 specs hacen decenas de logins REALES y el
    # limite de produccion son 5 cada 15 min. El sintoma de tropezarlo no es un
    # 429 visible: es un `toHaveURL` que nunca llega, porque el login devuelve
    # 429 y la pagina no navega. Se sube SOLO aqui; el limite de verdad lo
    # ejercita tests/integration/test_api_rate_limit.py, que si lo mide.
    $env:API_SERVER_LOGIN_RATE_LIMIT_COUNT = "1000"
    $env:API_SERVER_LOGIN_RATE_LIMIT_WINDOW_SECONDS = "60"

    # El api-server del arnes corre EN EL HOST, asi que los nombres de servicio
    # de docker (`vault`, `minio`, `ollama`...) no resuelven. Sin esto,
    # /admin/system-health espera a que cada resolucion DNS falle por timeout, y
    # el services-grid del dashboard -- que es la ultima llamada de la carga --
    # tarda todavia mas de los ~12 s medidos.
    $env:API_SERVER_VAULT_URL = "http://127.0.0.1:$(Get-OptionalEnv $DockerEnv 'VAULT_PORT' '8200')"
    $env:API_SERVER_VAULT_TOKEN = Get-OptionalEnv $DockerEnv "VAULT_DEV_ROOT_TOKEN" ""
    $env:API_SERVER_MINIO_URL = "http://127.0.0.1:$(Get-OptionalEnv $DockerEnv 'MINIO_API_PORT' '9000')"
    $env:API_SERVER_MINIO_ACCESS_KEY = Get-OptionalEnv $DockerEnv "MINIO_ROOT_USER" "minioadmin"
    $env:API_SERVER_MINIO_SECRET_KEY = Get-RequiredEnv $DockerEnv "MINIO_ROOT_PASSWORD"
    $env:API_SERVER_DOCLING_SERVE_URL = "http://127.0.0.1:$(Get-OptionalEnv $DockerEnv 'DOCLING_SERVE_PORT' '5001')"
    $env:API_SERVER_OLLAMA_URL = "http://127.0.0.1:$(Get-OptionalEnv $DockerEnv 'OLLAMA_PORT' '11434')"
    $env:API_SERVER_CLAMAV_HOST = "127.0.0.1"
    $env:API_SERVER_CLAMAV_PORT = Get-OptionalEnv $DockerEnv "CLAMAV_PORT" "3310"
    $env:API_SERVER_EGRESS_PROXY_HOST = "127.0.0.1"
    $env:API_SERVER_EGRESS_PROXY_PORT = Get-OptionalEnv $DockerEnv "EGRESS_PROXY_PORT" "8888"
    $env:API_SERVER_ALERTS_INGEST_TOKEN = Get-OptionalEnv $DockerEnv "API_SERVER_ALERTS_INGEST_TOKEN" ""

    Assert-BothDatabaseUrls -Target $DbName

    # -----------------------------------------------------------------------
    # 5) Seeds del catalogo
    # -----------------------------------------------------------------------
    if ($SkipSeeds) {
        Write-Host "==> Seeds del catalogo SALTADOS (-SkipSeeds)" -ForegroundColor Yellow
    } else {
        Write-Host "==> Seeds del catalogo (agentes, equipos, plantillas, skills, tools, KBs)" -ForegroundColor Cyan
        Write-Host "    (tarda minutos: ingiere el catalogo documental y pide embeddings a Ollama)" -ForegroundColor DarkGray
        Push-Location (Join-Path $RepoRoot "apps\api-server")
        try {
            Invoke-Native -Exe $VenvPython -Arguments @("-m", "api_server.seeds") `
                -FailMessage "python -m api_server.seeds fallo"
        } finally {
            Pop-Location
        }
    }

    # -----------------------------------------------------------------------
    # 6) Los usuarios que los specs declaran y ningun seed crea
    # -----------------------------------------------------------------------
    Invoke-HarnessStep -Action "seed-users" -Title "Usuarios y tenant del arnes"

    # -----------------------------------------------------------------------
    # 7) api-server
    # -----------------------------------------------------------------------
    Stop-HarnessApi -Port $ApiPort
    Remove-Item $ApiLog, $ApiErrLog -ErrorAction SilentlyContinue
    Write-Host "==> Arrancando el api-server en http://127.0.0.1:$ApiPort" -ForegroundColor Cyan
    $apiProc = Start-Process -PassThru -NoNewWindow `
        -FilePath $VenvPython `
        -ArgumentList "-m", "uvicorn", "api_server.main:app", "--host", "127.0.0.1", "--port", $ApiPort `
        -WorkingDirectory (Join-Path $RepoRoot "apps\api-server") `
        -RedirectStandardOutput $ApiLog `
        -RedirectStandardError $ApiErrLog
    Set-Content -Path $PidFile -Value $apiProc.Id -Encoding utf8

    Write-Host "    esperando /healthz (max 60 s)" -ForegroundColor DarkGray
    $arriba = $false
    $limite = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $limite) {
        if ($apiProc.HasExited) {
            Start-Sleep -Milliseconds 300
            Write-Host "    api-server stderr:" -ForegroundColor Red
            Get-Content $ApiErrLog -Tail 40 -ErrorAction SilentlyContinue | Out-Host
            throw "el api-server salio antes de responder. Log: $ApiErrLog"
        }
        try {
            Invoke-RestMethod -Uri "http://127.0.0.1:$ApiPort/healthz" -TimeoutSec 2 -ErrorAction Stop | Out-Null
            $arriba = $true
            break
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    if (-not $arriba) {
        Get-Content $ApiLog, $ApiErrLog -Tail 40 -ErrorAction SilentlyContinue | Out-Host
        throw "/healthz no contesto en 60 s. Logs: $ApiLog / $ApiErrLog"
    }
    Write-Host "    /healthz OK (pid $($apiProc.Id))" -ForegroundColor Green

    # -----------------------------------------------------------------------
    # 8) admin-panel (opcional)
    # -----------------------------------------------------------------------
    if ($BuildPanel) {
        Write-Host "==> next build del admin-panel contra http://127.0.0.1:$ApiPort" -ForegroundColor Cyan
        Push-Location (Join-Path $RepoRoot "apps\admin-panel")
        try {
            # Sin NEXT_PUBLIC_API_URL el build de produccion ABORTA a proposito
            # (assertPublicApiUrl): un panel construido sin ella apunta al
            # default y llama a un backend que no es este arnes.
            $env:NEXT_PUBLIC_API_URL = "http://127.0.0.1:$ApiPort"
            Invoke-Native -Exe "npm" -Arguments @("run", "build") -FailMessage "npm run build fallo"
        } finally {
            Pop-Location
        }
    }
} catch {
    # Si algo falla DESPUES de arrancar el api-server, no se deja un proceso
    # suelto escuchando en el puerto con la base a medio preparar.
    if ($null -ne $apiProc -and -not $apiProc.HasExited) {
        & taskkill /F /T /PID $apiProc.Id 2>$null | Out-Null
        Remove-Item $PidFile -ErrorAction SilentlyContinue
    }
    throw
}

# ---------------------------------------------------------------------------
# 9) El resumen. Existe para que el siguiente no tenga que leer el codigo.
# ---------------------------------------------------------------------------
$LiveSpecs = Get-LiveBackendSpecs
$ExpectTimeout = 25000

Write-Host ""
Write-Host "======================================================================" -ForegroundColor Green
Write-Host " ARNES E2E CON BACKEND VIVO -- LISTO" -ForegroundColor Green
Write-Host "======================================================================" -ForegroundColor Green
Write-Host ""
Write-Host " api-server     http://127.0.0.1:$ApiPort   (pid $($apiProc.Id))"
Write-Host " base de datos  '$DbName' en 127.0.0.1:$PgPort   [DESECHABLE]"
Write-Host " Redis          bases $RedisDbBase/$($RedisDbBase + 1)/$($RedisDbBase + 2) en 127.0.0.1:$RedisPort"
Write-Host " logs           $ApiLog"
Write-Host "                $ApiErrLog"
Write-Host ""
Write-Host " Credenciales (las cuatro con la misma contrasena: longenoughpw)" -ForegroundColor Cyan
Write-Host "   root\@example.com          system admin + tenant_admin del tenant e2e"
Write-Host "   sys\@platform.example.com  system admin, SIN tenant"
Write-Host "   admin\@tenant.example.com  tenant_admin"
Write-Host "   member\@tenant.example.com tenant_user"
Write-Host "   tenant 'E2E backend vivo' (slug e2e-backend-vivo), asistente personal ON"
Write-Host ""
if (-not $BuildPanel) {
    Write-Host " FALTA construir el panel contra este arnes:" -ForegroundColor Yellow
    Write-Host "   cd apps\admin-panel"
    Write-Host ('   $env:NEXT_PUBLIC_API_URL = ''http://127.0.0.1:{0}''; npm run build' -f $ApiPort)
    Write-Host "   (o relanza este guion con -BuildPanel)"
    Write-Host ""
}
Write-Host " Correr los $($LiveSpecs.Count) specs de backend vivo:" -ForegroundColor Cyan
Write-Host "   cd apps\admin-panel"
Write-Host ('   $env:NEXT_PUBLIC_API_URL = ''http://127.0.0.1:{0}''' -f $ApiPort)
Write-Host '   $env:E2E_WEBSERVER_CMD   = ''npm run start'''
Write-Host ('   $env:E2E_EXPECT_TIMEOUT  = ''{0}''' -f $ExpectTimeout)
# El acento grave se construye por codigo de caracter: escrito literal, el
# tokenizador de PowerShell lo trata como escape incluso entre comillas simples.
$Continuacion = [char]0x60
for ($i = 0; $i -lt $LiveSpecs.Count; $i++) {
    $prefijo = "                      "
    if ($i -eq 0) { $prefijo = "   npx playwright test " }
    $cola = " $Continuacion"
    if ($i -eq $LiveSpecs.Count - 1) { $cola = "" }
    Write-Host ($prefijo + $LiveSpecs[$i] + $cola)
}
Write-Host ""
Write-Host " E2E_EXPECT_TIMEOUT existe porque /admin/system-health abre una peticion a cada" -ForegroundColor DarkGray
Write-Host " servicio y el services-grid del dashboard aparece a los ~12 s. Con los 5 s por" -ForegroundColor DarkGray
Write-Host " defecto, 21 de 41 casos fallan por el reloj y ninguno dice nada del producto. El" -ForegroundColor DarkGray
Write-Host " default de playwright.config.ts sigue en 5 s, que es lo correcto para el" -ForegroundColor DarkGray
Write-Host " subconjunto mockeado que corre CI." -ForegroundColor DarkGray
Write-Host ""
Write-Host " Parar el arnes:  .\scripts\dev\e2e-live-harness.ps1 -Down" -ForegroundColor DarkGray
Write-Host " Empezar de cero: .\scripts\dev\e2e-live-harness.ps1 -Recreate" -ForegroundColor DarkGray
Write-Host " Guia completa:   docs\03-guides\e2e-con-backend-vivo.md" -ForegroundColor DarkGray
Write-Host ""
