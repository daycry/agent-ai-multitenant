# Plan 05 — tests humanos

Esta guía cubre los tres tests humanos del Plan 05 (MCP y Tools
Avanzadas). Cada demo siembra datos en la BD, hace su smoke test
y te dice **qué URL abrir del admin-panel + qué tienes que ver**.

> **Estado del plan**: `pending_human_validation`. Las 16 tareas + el
> cableado de `task_05_17` están en `done` con sus tests automáticos
> en verde (420 pytest + 11 Playwright). Quedan estos tres tests
> humanos antes de pasar a `completed`.

## TL;DR

```powershell
.\scripts\dev\up.ps1                          # primera vez: arranca el stack
.\scripts\dev\run-human-tests-05.ps1
```

El launcher:

1. Verifica que `api-server :8001` está vivo (si no, te lo dice).
2. Lanza `setup_demo_05.py` que siembra un proyecto + 2 agentes +
   1 entrada `mcp_servers` + 2 Tool rows (`http_endpoint` y
   `docker_command`). Persiste los ids en
   `scripts/.demo_state_05.json`.
3. Corre los tres demos en orden. Cada uno **imprime las URLs
   concretas que tienes que abrir** y qué buscar en cada una.
4. Termina con un resumen `PASS/FAIL/SKIP` + las dos URLs principales
   del proyecto sembrado.

## Opciones del launcher

| Flag          | Para qué                                                      |
| ------------- | ------------------------------------------------------------- |
| `-Only 01`    | Solo el primer demo                                           |
| `-Only 02`    | Solo el demo de docker_command (necesita Docker daemon)       |
| `-Only 03`    | Solo el demo de http_endpoint allowlist                       |
| `-SkipDocker` | Salta el 05_02 sin chequear el daemon                         |
| `-SkipSetup`  | Reusa el `scripts/.demo_state_05.json` previo (no re-siembra) |

## Pre-requisitos

- Stack dev levantado vía `.\scripts\dev\up.ps1` (postgres + redis +
  vault + api-server :8001 + admin-panel :3000).
- `.venv` con `shared-mcp` y `agent-runtime` instalados editable
  (lo deja hecho `scripts\dev\bootstrap.ps1`).
- Para el demo 05_02: Docker Desktop corriendo (la imagen
  `python:3.12-alpine` se pulla al vuelo ≈50 MB la primera vez).
- Para el step 2 del demo 05_03 (opcional): conectividad a
  `httpbin.org`. Sin ella, el step 1 sigue corriendo y el demo es PASS.

---

## Qué siembra `setup_demo_05.py`

Ejecutarlo crea, dentro del tenant `tenant-a` (override con
`DEMO_TENANT`):

| Recurso                       | Detalle                                                                                                                       |
| ----------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| **Project**                   | "Plan 05 demo - `<8 chars>`", con un `mcp_servers` JSONB conteniendo una entrada `toy-mcp` apuntando al toy MCP server local. |
| **Agent A** "HTTP Lookup Bot" | Project-scoped, role=`researcher`.                                                                                            |
| **Agent B** "Sandbox Runner"  | Project-scoped, role=`executor`.                                                                                              |
| **Tool `example-weather`**    | `implementation_type=http_endpoint`, `security_level=safe`. Wired al agente A vía `agent_tools`.                              |
| **Tool `alpine-probe`**       | `implementation_type=docker_command`, `security_level=privileged`. Wired al agente B.                                         |

Los ids van a `scripts/.demo_state_05.json` para que los tres demos
los lean en lugar de re-sembrar.

---

## `human_05_01` — MCP funciona con un servidor real

**Qué prueba**:

1. La entrada MCP del proyecto está persistida en `Project.mcp_servers`.
2. El endpoint `/test-connection` descubre los tools del toy server
   (`echo`, `add`, `secret_echo`).
3. La inyección Vault funciona: un secreto del resolver llega al
   subprocess sin filtrarse al `os.environ` del padre.

**Cómo ejecutarlo**:

```powershell
.\.venv\Scripts\python scripts\demo_human_05_01.py
```

**Qué tienes que ver en el admin-panel** (lo imprime el demo al final):

1. **`/admin/projects/<id>/mcp-servers`**
   - Una **card** con título `toy-mcp` y badge azul `stdio (subprocess)`.
   - El comando que se ve debajo es el `python.exe` del venv +
     `tests\integration\_toy_mcp_server.py`.
   - Pulsa el **icono lápiz** → se abre el dialog de edición → pulsa
     **"Probar conexión"** → debajo del botón aparece un panel con:
     - `Conectado a toy-mcp-server v1.x.x — 3 tools`
     - Lista `echo`, `add`, `secret_echo` con sus descriptions.

2. **`/admin/projects/<id>/agent-tools-diagnostic`**
   - Card "MCP servers del proyecto" lista `toy-mcp`.
   - Dos cards de agentes ("HTTP Lookup Bot" y "Sandbox Runner") con
     sus respectivos Tools wired (los siguientes dos tests cubren
     esas dos cards en detalle).

**Pitfalls**:

- **`HTTP 401 missing Authorization header`** en los pasos 1-2 del
  demo: normal. El demo no se autentica contra api-server; las URLs
  funcionan **en el navegador** donde tienes la sesión iniciada.
- **El paso 3 sigue siendo PASS aunque los 1-2 sean 401**: el smoke
  test de Vault corre en proceso, no depende del api-server.

---

## `human_05_02` — Aislamiento de tools `docker_command`

**Qué prueba**:

1. Un `DockerCommandTool` real lanza `python:3.12-alpine` con el
   envelope de hardening: uid 1000, root fs read-only, /tmp tmpfs,
   network=none.
2. El container se elimina al exit (`remove=True`); no quedan
   `python:3.12-alpine` en `docker ps -a` antes/después.

**Cómo ejecutarlo**:

```powershell
.\.venv\Scripts\python scripts\demo_human_05_02.py
```

**Qué tienes que ver en el admin-panel**:

**`/admin/projects/<id>/agent-tools-diagnostic`**

- Card del agente **"Sandbox Runner"** (badge `executor` + `project_local`).
- En su lista de tools, una fila para `alpine-probe`:
  - badge `docker_command` en **rojo**
  - badge `privileged` en **rojo**
  - `timeout 60s · category 'code'`
- Description: "Tool de demo: lanza python:3.12-alpine y reporta su
  entorno (uid, fs writability, network)."

**Pitfalls**:

- **`Docker daemon no responde`**: arranca Docker Desktop (o tu
  equivalente) y reintenta. El launcher lo detecta y salta el demo
  con `SKIP` en lugar de fallar.
- **Pull lento la primera vez**: ~50 MB. El demo tiene `timeout=60s`
  para el container; si tardas mucho con red lenta, la imagen ya
  cacheada acelera ejecuciones siguientes.

---

## `human_05_03` — Allowlist de `http_endpoint` se respeta

**Qué prueba**:

1. Step 1 (security-critical, sin red): un `HttpEndpointTool` con
   URL fuera del allowlist falla con error explícito `domain not
allowed: <host>` ANTES de hacer la llamada HTTP.
2. Step 2 (opcional, con internet): URL en el allowlist hace
   round-trip real a `httpbin.org/anything/{path}`.

**Cómo ejecutarlo**:

```powershell
.\.venv\Scripts\python scripts\demo_human_05_03.py
```

**Qué tienes que ver en el admin-panel**:

**`/admin/projects/<id>/agent-tools-diagnostic`**

- Card del agente **"HTTP Lookup Bot"** (badge `researcher`).
- En su lista de tools, fila para `example-weather`:
  - badge `http_endpoint` en **azul**
  - badge `safe` en **verde**
  - `timeout 10s · category 'data'`

**Pitfalls**:

- **`[SKIP] sin internet`** en el step 2: normal. El step 1 cubre el
  camino security-critical y el demo es PASS aunque el step 2 no
  corra.
- **El allowlist concreto no es un campo del Tool row**: vive en el
  agent-runtime al boot (lo configura el operador por proyecto).
  El demo lo simula in-process; la integración con el agent loop
  llegará cuando el `ToolRegistry` se hidrate desde Tool rows en BD.

---

## Después de los tres demos

Una vez los tres salgan `PASS` y hayas verificado en el admin-panel
que las dos pantallas (`/mcp-servers` y `/agent-tools-diagnostic`)
muestran lo descrito, el revisor humano puede:

1. Editar `docs/roadmap/05-mcp-tools-avanzadas.md` y cambiar
   `status: pending_human_validation` → `status: completed` con
   `completed_at: <fecha>`.
2. Verificar que `docs/07-changelog/05-mcp-tools-avanzadas.md` cuadra
   con lo entregado.
3. PR de `plan/05-mcp-tools-avanzadas` a `master`.
4. Activar el siguiente plan según el roadmap.

## Troubleshooting transversal

- **PowerShell: "TerminatorExpectedAtEndOfString" o
  "NativeCommandError"** → ver
  [`gotchas/powershell-utf8-em-dash-and-native-stderr.md`](../gotchas/powershell-utf8-em-dash-and-native-stderr.md).
- **El `.py` se abre en otro Python** → ver
  [`gotchas/powershell-ps1-vs-python-py.md`](../gotchas/powershell-ps1-vs-python-py.md).
- **Otros gotchas comunes** viven en
  [`docs/03-guides/gotchas/`](../gotchas/).
