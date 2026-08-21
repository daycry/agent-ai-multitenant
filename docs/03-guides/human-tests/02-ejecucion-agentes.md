# Plan 02 — tests humanos

Esta guía cubre los **5 tests humanos** del Plan 02 (Ejecución de
agentes). Cada `human_02_NN` tiene un script demo dedicado bajo
`scripts/`; el launcher transversal `run-human-tests.ps1` los corre
todos en orden.

> **Estado del plan**: `completed` (mergeado a master hace tiempo).
> Esta guía queda para regresión cuando se toque el agent-runtime,
> el sandbox, las salvaguardas o el bus WebSocket del board.

## TL;DR

```powershell
.\scripts\dev\up.ps1                                  # stack arriba (1ª vez)
.\.venv\Scripts\python.exe scripts\demos\setup_demo_project.py   # proyecto + agente compartidos
.\scripts\dev\run-human-tests.ps1 -Only 02            # corre los 5 demos del Plan 02
```

Sin pausas por defecto; añade `-Pause` para 5 s entre fases. El
launcher imprime PASS/FAIL al final y deja state en
`scripts/demos/.demo_state.json` para que cada demo reuse el proyecto +
agente.

## Pre-requisitos

| Requisito                                     | Por qué                                                                           |
| --------------------------------------------- | --------------------------------------------------------------------------------- |
| Stack dev (`.\scripts\dev\up.ps1`)            | Postgres + Redis + api-server :8001 + admin-panel :3000 + workers.                |
| `agent-runtime:v1` construido                 | `docker build -t agent-runtime:v1 docker/agent-runtimes/agent-runtime/` (1ª vez). |
| Usuario `system_admin` (1ª vez)               | El primer `POST /auth/register` se promueve auto.                                 |
| `setup_demo_project.py` ejecutado al menos 1× | Crea el proyecto + agente Writer compartidos por los 5 demos.                     |

> ⚠️ Lanza los scripts Python **siempre** con
> `.\.venv\Scripts\python.exe`. Si invocas `.py` directo, Windows usa
> el Python del sistema (sin deps) y revienta sin imprimir nada.

## Qué siembra `setup_demo_project.py`

Bajo el tenant `tenant-a` (override con `DEMO_TENANT`):

| Recurso            | Detalle                                                                                           |
| ------------------ | ------------------------------------------------------------------------------------------------- |
| **Project**        | "Demo proyecto compartido — `<8 chars>`" con `human_approval_policy` que aparca `code_execution`. |
| **Agent** "Writer" | scope=`project_local`, role=`worker`. Sujeto de los 5 demos.                                      |

ids persistidos en `scripts/demos/.demo_state.json` — borrar el archivo
fuerza re-crear (no destructivo del tenant; sólo del demo).

---

## `human_02_01` — Un agente ejecuta una tarea de principio a fin

**Qué prueba**: pipeline completo orchestrator → worker → contenedor
`agent-runtime` → LangGraph loop → BD funciona end-to-end.

**Cómo ejecutarlo**:

```powershell
.\.venv\Scripts\python.exe .\scripts\demos\demo_human_02_01.py
```

Para LLM real (no scripted): define `DEMO_MODEL_KIND` + credenciales
del proveedor (catálogo cerrado ADR 0021: `azure_foundry`,
`claude_sdk`, `copilot`, `ollama`). Sin esa variable usa el
`ScriptedModelClient` con un poema pre-fijado.

**Output esperado** (modo scripted):

```
==========  Demo human_02_01 — un agente ejecuta una tarea  ==========
  Modelo del agente: determinista (sin credenciales)
  Escenario creado:
    tenant    Tenant A (tenant-a)
    proyecto  <uuid>
    agente    <uuid>  «Writer»
    tarea     <uuid>  «Escribe un poema sobre el mar»
  Ejecutando — lanzando el contenedor agent-runtime...
  Ejecución <uuid>  ·  estado: done
  Lo que hizo el agente, paso a paso:
    [ 0] node        perceive     Perceived task: ...
    [ 1] node        recall       Recalled 0 memory item(s) — ...
    [ 2] model_call  plan         decision: act
    [ 3] tool_call   act          Tool 'echo' → ok
    ...
  Resultado — el poema:
    | El mar repite su nombre en la orilla, ...
  Iteraciones: 2  Tokens: 335  Coste: $0.0044
  Tarea <uuid>  ->  estado: done
```

**Checklist**:

- [ ] `estado: done` final + poema no vacío.
- [ ] `/admin/board` muestra la tarea en columna **done**.
- [ ] `/admin/executions/<uuid>` muestra Timeline con los 8 nodos del loop.

**Pitfalls conocidos**:

- `docker run agent-runtime:v1` falla → imagen no construida (ver
  pre-requisitos).
- `password authentication failed for migrations_user` → la BD apunta
  a `:5432` en lugar de `:15432`. Setea
  `$env:DEMO_DATABASE_URL = "postgresql+asyncpg://migrations_user:changeme-migrations-dev-only@localhost:15432/agentic_platform"`.

---

## `human_02_02` — El aislamiento del contenedor es real

**Qué prueba**: el perfil endurecido del sandbox (cap-drop ALL, FS
raíz read-only, sin socket Docker, red interna sin salida, seccomp)
es contrato, no aspiración.

**Cómo ejecutarlo**:

```powershell
.\.venv\Scripts\python.exe .\scripts\demos\demo_human_02_02.py
```

**Output esperado**:

```
==========  human_02_02 — el aislamiento del contenedor  ==========
  [  OK  ]  /var/run/docker.sock NO montado en el contenedor
  [  OK  ]  FS raíz read-only — no se puede escribir fuera de /workspace
  [  OK  ]  /workspace SÍ escribible (tmpfs efímero)
  [  OK  ]  Red interna: el contenedor NO alcanza 1.1.1.1
  [  OK  ]  cap_net_admin (y los 38 caps restantes) NO disponibles
```

**Checklist**:

- [ ] Los 5 `[OK]` impresos.
- [ ] **Cualquier `[FALLO]`** indica regresión real en
      `apps/workers/src/workers/container.py` o `isolation.py`.

---

## `human_02_03` — Las salvaguardas frenan al agente

**Qué prueba**: los 4 cinturones del agent loop (`max_iterations`,
`repetitive_loop`, `max_cost`, `container_timeout`) disparan cuando
deben.

**Cómo ejecutarlo**:

```powershell
.\.venv\Scripts\python.exe .\scripts\demos\demo_human_02_03.py
```

**Output esperado**:

```
==========  human_02_03 — las salvaguardas del agent loop  ==========
  [  OK  ]  max_iterations dispara aborted (max_iterations_exceeded)
  [  OK  ]  repetitive_loop dispara aborted (repetitive_loop_detected)
  [  OK  ]  max_cost dispara aborted (max_cost_exceeded)
  [  OK  ]  container_timeout mata el contenedor + persiste failed
```

**Checklist**:

- [ ] Los 4 `[OK]`.
- [ ] BD tiene 4 Executions con `aborted_reason` esperado en cada una.

---

## `human_02_04` — La validación humana pausa la ejecución

**Qué prueba**: política `human_approval_policy` con
`code_execution: human_required` aparca la tarea en
`awaiting_human_approval` cuando el agente intenta `shell_exec`, y
crea solicitud en `/admin/approvals`.

**Cómo ejecutarlo**:

```powershell
.\.venv\Scripts\python.exe .\scripts\demos\demo_human_02_04.py
```

**Output esperado**:

```
==========  human_02_04 — validación humana aparca la ejecución ==========
  Tarea <uuid> creada en proyecto <uuid> con política
    code_execution: human_required
  Ejecutando — el agente intentará `shell_exec`...
  Resultado:
    execution.status     awaiting_human_approval
    tarea.status         awaiting_human_approval
    approval_request     <approval-uuid>  (pending)
```

**Checklist**:

- [ ] `awaiting_human_approval` en Execution + Task.
- [ ] `/admin/approvals` muestra tarjeta con botones Aprobar / Rechazar
      y la acción (`shell_exec` con `deploy --prod`).
- [ ] `/admin/board` muestra la tarea en columna **Pendiente de
      aprobación**.
- [ ] Al pulsar Aprobar, la tarea vuelve a `backlog`.

---

## `human_02_05` — Tiempo real (WebSocket) sin refresco

**Qué prueba**: el bus de eventos (Redis Streams) + WebSocket → el
board ve transiciones del Kanban y pasos de ejecución **sin
refrescar**.

**Cómo ejecutarlo**:

```powershell
.\.venv\Scripts\python.exe .\scripts\demos\demo_human_02_05.py
```

El script crea la tarea y te pide `Enter`. **Antes de pulsar**:

1. Abre `/admin/board` (idealmente en VARIAS pestañas).
2. Pulsa Enter y observa.

Para automatizar (sin Enter): `$env:DEMO_NO_WAIT = "1"`.

**Checklist**:

- [ ] En cada pestaña del board ves la tarjeta moverse
      `backlog → ready → in_progress → done` sin pulsar F5.
- [ ] Los pasos del Timeline aparecen uno a uno mientras el agente corre.

---

## Volver a empezar

```powershell
.\scripts\dev\down.ps1 -Docker
Remove-Item scripts\demos\.demo_state.json -ErrorAction SilentlyContinue
docker exec agentic-platform-postgres-1 psql -U migrations_user -d agentic_platform -c `
  "TRUNCATE memory_entries CASCADE; TRUNCATE projects RESTART IDENTITY CASCADE;"
.\scripts\dev\up.ps1
.\scripts\dev\run-human-tests.ps1 -Only 02
```

## Troubleshooting

Para errores transversales (Docker, asyncpg, OTel, JWT secret
mismatch, favicon 404, …) ver
[`run-demo-human-tests.md`](../run-demo-human-tests.md#troubleshooting)
— la sección de troubleshooting compartida con Plans 04 / 04.5.
