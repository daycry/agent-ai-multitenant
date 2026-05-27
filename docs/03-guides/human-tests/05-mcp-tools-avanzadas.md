# Plan 05 — tests humanos

Esta guía cubre los tres tests humanos del Plan 05 (MCP y Tools
Avanzadas) — qué prueban, cómo ejecutarlos, qué esperar, cómo
declararlos pass/fail.

> **Estado del plan**: `pending_human_validation`. Las 16 tareas + el
> cableado de `task_05_17` están en `done` con sus tests automáticos
> en verde (420 pytest + 11 Playwright + 3 demos manuales). Quedan
> estos tres tests humanos antes de pasar a `completed`.

## TL;DR

```powershell
.\scripts\dev\run-human-tests-05.ps1
```

Corre los tres demos en orden. Sin pausas, ~30 s en total (más el
pull de `python:3.12-alpine` la primera vez que corres el 05_02 ).

Imprime un resumen tipo `PASS/FAIL/SKIP` al final.

Opciones:

| Flag          | Para qué                                                |
| ------------- | ------------------------------------------------------- |
| `-Only 01`    | Solo el primer demo (no necesita Docker ni internet)    |
| `-Only 02`    | Solo el demo de docker_command (necesita Docker daemon) |
| `-Only 03`    | Solo el demo de http_endpoint allowlist                 |
| `-SkipDocker` | Salta el 02 sin chequear el daemon                      |

A diferencia del launcher de Planes 02/04.5, **los demos del Plan 05
son standalone**: NO requieren `docker compose`, postgres, redis ni
api-server arrancado. Las dependencias específicas están listadas al
inicio de cada sección.

---

## Pre-requisitos comunes

- `.venv` del proyecto creado (`scripts\dev\bootstrap.ps1` la primera
  vez).
- `pip install -e packages/shared-mcp` y
  `pip install -e docker/agent-runtimes/agent-runtime/` en ese venv —
  los demos importan `shared_mcp` y `agent_runtime` directamente.

Para el demo 05_02 adicionalmente:

- Docker Desktop o equivalente corriendo.
- La imagen `python:3.12-alpine` puede pullarse al vuelo o ya estar
  cacheada localmente. La primera ejecución descarga ~50 MB.

Para el demo 05_03 step 2 (opcional):

- Conectividad a `httpbin.org`. Sin ella, el step 2 se skipea con un
  mensaje claro y el test sigue siendo PASS (el step 1, lo
  security-critical, no necesita red).

---

## `human_05_01` — MCP funciona con un servidor real

**Qué prueba**: que el cliente MCP completa el handshake con un servidor
externo, lista tools, y que un secreto guardado en Vault aterriza en
el subprocess del servidor sin filtrarse a los logs del padre.

El demo usa el **toy MCP server** (`tests/integration/_toy_mcp_server.py`)
como sustituto del servidor real (github-mcp, slack-mcp, …) para no
depender de un PAT real o conectividad externa. La mecánica que valida
es la misma: cualquier `vault:secret/data/…` resuelto por el
`HvacVaultResolver` cruzaría igual al subprocess del servidor real.

### Cómo ejecutarlo

```powershell
.\.venv\Scripts\python scripts\demo_human_05_01.py
```

### Output esperado

```
────────────────────────────────────────────────────────────
  demo human_05_01 — MCP + Vault end-to-end (toy server)
────────────────────────────────────────────────────────────

→ discover_tools() — opens session, runs handshake, lists tools
  server: 'toy-mcp-server' v1.x.x
  tools : ['add', 'echo', 'secret_echo']
  ✓ tools[] visible

→ call_tool('secret_echo') — proves Vault secret reached the subprocess
  ✓ subprocess received TOY_SECRET = 'tok-from-vault-NEVER-LOG-ME'

→ env scrub check — secret must NOT be in parent process env
  ✓ parent os.environ is clean

────────────────────────────────────────────────────────────
  demo human_05_01 PASSED
────────────────────────────────────────────────────────────
Checklist roadmap:
  [✓] La UI muestra las tools descubiertas del servidor
  [partial] Un agente puede listar repos del usuario  (requires real PAT)
  [partial] Un agente puede crear un issue            (requires real PAT)
  [✓] Los tokens del Vault se inyectan sin aparecer en logs
```

### Checklist del roadmap

| Item                                                      | Estado vía demo                                                       | Verificación manual adicional                                                                                       |
| --------------------------------------------------------- | --------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| La UI muestra las tools descubiertas del servidor         | ✓ (lista `[add, echo, secret_echo]`)                                  | Opcional: `/admin/projects/<id>/mcp-servers` → "Probar conexión" contra el toy server                               |
| Un agente puede listar repos del usuario con `list_repos` | parcial (mecánica probada con `secret_echo` en lugar de `list_repos`) | Para validar con GitHub real: declara un MCP server `github-mcp` con un PAT en Vault, lanza un plan que use la tool |
| Un agente puede crear un issue en un repo de prueba       | parcial (idem)                                                        | Idem item anterior                                                                                                  |
| Los tokens del Vault se inyectan sin aparecer en logs     | ✓ (secret entra al subprocess, no al padre)                           | Opcional: `docker logs <container> 2>&1 \| Select-String "ghp_\|TOY_SECRET"` debe estar vacío                       |

### Pitfalls conocidos

- **Vault wiring real**: el demo usa `StaticVaultResolver` (sustituto en
  memoria del Protocol que implementa `HvacVaultResolver`). Para probar
  con Vault real, exporta `API_SERVER_VAULT_TOKEN=dev-root-token` antes
  de arrancar el api-server; el endpoint `/test-connection` empezará a
  resolver punteros `vault:secret/data/…` contra el Vault del compose.
- **`shared_mcp` o `agent_runtime` no se importan**: re-ejecuta
  `pip install -e packages/shared-mcp` y
  `pip install -e docker/agent-runtimes/agent-runtime/`.

---

## `human_05_02` — Aislamiento de tools `docker_command`

**Qué prueba**: que un Tool de tipo `docker_command` lanza un contenedor
efímero con el envelope completo de seguridad (cap-drop ALL, no-new-privs,
read-only fs, network=none, uid 1000) y que el contenedor se elimina al
terminar.

El demo lanza `python:3.12-alpine` con un probe interno que reporta su
propio entorno (uid, writability del root y `/tmp`, conectividad de
red).

### Cómo ejecutarlo

```powershell
.\.venv\Scripts\python scripts\demo_human_05_02.py
```

### Output esperado

```
────────────────────────────────────────────────────────────
  demo human_05_02 — docker_command isolation
────────────────────────────────────────────────────────────
→ pre-run alpine containers (any state): 0

→ launching python:3.12-alpine with the platform's hardening envelope
  container output:
    {'uid': 1000, 'gid': 1000, 'cwd_writable': False, 'tmp_writable': True, 'net': 'blocked (URLError)'}

→ envelope checks:
  [✓] uid != root (1000:1000)
  [✓] root fs is read-only
  [✓] /tmp is writable (tmpfs)
  [✓] network=none blocks egress

→ ephemeral check — container deleted after exit
  post-run alpine containers (any state): 0
  [✓] no leftover container

────────────────────────────────────────────────────────────
  demo human_05_02 PASSED
────────────────────────────────────────────────────────────
```

### Checklist del roadmap

| Item                                                        | Verificación                                            |
| ----------------------------------------------------------- | ------------------------------------------------------- |
| La tool corre en un contenedor efímero separado             | ✓ (la salida del probe sale del container, no del host) |
| El contenedor tiene los mismos guardrails que agent-runtime | ✓ (4 checks ✓: uid + read-only + /tmp + network)        |
| Al terminar, el contenedor se destruye y no deja rastro     | ✓ (pre-run / post-run alpine containers count = 0)      |

### Pitfalls conocidos

- **`docker ps -a` no responde**: Docker Desktop no está arrancado. El
  launcher PS1 lo detecta y skipea el demo con un mensaje claro
  (`SKIP - Docker no disponible`).
- **`Error: image not found: python:3.12-alpine`**: se va a pullar al
  vuelo la primera vez (≈50 MB). Si la primera ejecución falla por
  pull lento, vuelve a correr el demo — la imagen queda cacheada.
- **El probe sale "net: open"** (en lugar de blocked): bug serio del
  envelope. El demo FAIL automáticamente — si lo ves, el problema es
  configuración de Docker (modo `linuxkit` vs `containerd`); abre un
  issue.

---

## `human_05_03` — Allowlist de `http_endpoint` se respeta

**Qué prueba**: que un Tool de tipo `http_endpoint` rechaza con error
explícito cualquier URL cuyo host no esté en el `allowed_domains` del
proyecto, ANTES de hacer la llamada HTTP.

### Cómo ejecutarlo

```powershell
.\.venv\Scripts\python scripts\demo_human_05_03.py
```

### Output esperado (con internet)

```
────────────────────────────────────────────────────────────
  demo human_05_03 — http_endpoint allowlist
────────────────────────────────────────────────────────────

→ Step 1: URL outside the project allowlist must fail
  ✓ ToolResult.ok=False
  ✓ error: domain not allowed: forbidden.example.com
  ✓ allowed list surfaced: ['api.allowed.example.com']

→ Step 2: URL on the allowlist round-trips a real HTTP call
  ✓ status_code: 200
  ✓ url echoed back: https://httpbin.org/anything/demo

────────────────────────────────────────────────────────────
  demo human_05_03 PASSED
────────────────────────────────────────────────────────────
```

### Checklist del roadmap

| Item                                                     | Estado vía demo                                                                                                            | Verificación adicional                                                                                                      |
| -------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| La invocación falla con error explícito sobre allowlist  | ✓ (Step 1: `domain not allowed: forbidden.example.com`)                                                                    | El `output.allowed` lista los dominios permitidos — el agente sabe qué pedir al operador                                    |
| El intento queda en `audit_log` con el dominio bloqueado | parcial (el demo verifica que el error es machine-readable; la entrada en `executions.steps_log` la genera un agente real) | Para validar end-to-end: lanza un plan con un Tool http_endpoint mal configurado, busca el step en `/admin/executions/<id>` |

### Pitfalls conocidos

- **Sin internet**: Step 2 se skipea con un mensaje claro. El Step 1
  (lo security-critical) no necesita red, así que el demo sigue siendo
  PASS.
- **`AttributeError: HttpEndpointTool ...`**: el venv no tiene
  `agent_runtime` instalado editable. Re-ejecuta
  `pip install -e docker/agent-runtimes/agent-runtime/`.

---

## Después de los tres demos

Una vez los tres salgan `PASS` en tu entorno, el revisor humano puede:

1. Editar `docs/roadmap/05-mcp-tools-avanzadas.md` y cambiar
   `status: pending_human_validation` → `status: completed` con
   `completed_at: <fecha>`.
2. Verificar que `docs/07-changelog/05-mcp-tools-avanzadas.md` lista
   los tres tests humanos como PASS.
3. Mergear `plan/05-mcp-tools-avanzadas` a `master` vía PR.
4. Activar el siguiente plan (status `in_progress`) según el roadmap.

## Troubleshooting transversal

Los problemas comunes a varios planes (PowerShell vs Python en `.py`,
`/admin/documents/<id>` 404, etc.) están en
[`docs/03-guides/gotchas/`](../gotchas/). Esta guía solo cubre lo
específico del Plan 05.
