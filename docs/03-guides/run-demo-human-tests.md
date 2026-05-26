# Cómo ejecutar los tests humanos paso a paso

Esta guía es el **protocolo de validación humana** de los planes 02,
04 y 04.5. Cada test detalla qué prueba, el comando exacto, el
output esperado, qué mirar en la UI y qué considerar pass / fail.

> Si lo que buscas es el modo Playwright para ver los E2E del
> frontend, esa es otra guía:
> [watching-e2e-tests.md](./watching-e2e-tests.md).

## TL;DR — la versión automatizada

```powershell
.\scripts\dev\up.ps1                        # docker + api-server :8001 + admin-panel :3000
.\scripts\dev\run-human-tests.ps1           # corre los 7 demos en orden y resume
```

El launcher detecta si el stack está arriba, hace los dos setups
compartidos (idempotentes) y ejecuta los 7 demos en orden, imprimiendo
PASS / FAIL al final. Por defecto sin pausas; con `-Pause` deja 5 s
entre fases.

Opciones:

| Flag         | Para qué                                          |
| ------------ | ------------------------------------------------- |
| `-Only 02`   | Solo los 5 demos del Plan 02                      |
| `-Only 04_5` | Solo los 2 demos del Plan 04.5                    |
| `-Pause`     | Pausas de 5 s entre fases (para leer en vivo)     |
| `-SkipStack` | Asume el stack ya arrancado (no relanza `up.ps1`) |

> ⚠️ **Importante**: si arrancaste el api-server **a mano** con un
> `API_SERVER_JWT_SECRET` distinto del default, los demos del Plan
> 04.5 darán `401 Unauthorized`. La forma fácil de evitarlo es usar
> `up.ps1` (deja el default) o no setear la variable en ninguna
> terminal (ambos lados leen el mismo default del schema pydantic).

El resto de la guía es para cuando quieres **entender** qué hace
cada test o **ejecutarlo a mano** (no en bloque).

---

## Pre-requisitos comunes

`scripts/dev/up.ps1` se encarga de:

| Servicio      | Puerto | Cómo se levanta                                                                                            |
| ------------- | ------ | ---------------------------------------------------------------------------------------------------------- |
| Postgres      | 15432  | `docker compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml up -d postgres` (vía up.ps1) |
| Redis         | 6379   | mismo `compose up`                                                                                         |
| Vault         | 8200   | mismo                                                                                                      |
| MinIO         | 9000   | mismo                                                                                                      |
| ClamAV        | 3310   | mismo                                                                                                      |
| docling-serve | 5001   | mismo (Plan 04.5 expone el puerto al host)                                                                 |
| egress-proxy  | 8888   | mismo (idem)                                                                                               |
| Migraciones   | —      | `alembic upgrade head` (vía up.ps1)                                                                        |
| api-server    | 8001   | uvicorn detached (vía up.ps1), con `API_SERVER_JWT_SECRET=dev-only-jwt-secret-change-me`                   |
| admin-panel   | 3000   | next dev detached (vía up.ps1)                                                                             |

Únicas piezas que tienes que tocar tú:

- `agent-runtime:v1` construida (la primera vez):
  ```powershell
  docker build -t agent-runtime:v1 docker/agent-runtimes/agent-runtime/
  ```
- Un usuario en el sistema (la primera vez):
  ```powershell
  $body = '{"email":"root@example.com","password":"longenoughpw","full_name":"Root"}'
  Invoke-RestMethod -Method Post -Uri http://localhost:8001/auth/register -Body $body -ContentType "application/json"
  ```
  El primer usuario registrado es `system_admin` automáticamente.
  `up.ps1` te recuerda las credenciales sugeridas al final.

### Cómo lanzar scripts Python en PowerShell

> ⚠️ **Siempre** invoca el Python del venv explícitamente:
>
> ```powershell
> .\.venv\Scripts\python.exe .\scripts\<script>.py
> ```
>
> Si haces `.\scripts\<script>.py` directo, Windows asocia el `.py`
> al Python del sistema (no al del venv), faltan dependencias, y el
> script revienta nada más arrancar sin imprimir nada. Causa #1 de
> "no veo nada".

---

## Plan 02 — los 5 tests humanos

Cada uno tiene un script demo bajo `scripts/`. El launcher los corre
todos; aquí están los protocolos individuales por si quieres
ejecutar uno suelto.

### `human_02_01` — Un agente ejecuta una tarea de principio a fin

**Qué prueba**: pipeline completa orchestrator → worker → contenedor
`agent-runtime` → LangGraph loop → BD funciona end-to-end.

**Comando**:

```powershell
.\.venv\Scripts\python.exe .\scripts\demo_human_02_01.py
```

Para LLM real (no scripted), define `DEMO_MODEL_KIND` y credenciales
del proveedor (catálogo cerrado ADR 0021: `azure_foundry`,
`claude_sdk`, `copilot`, `ollama`). Sin esa variable, usa el
`ScriptedModelClient` con un poema prefijado.

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

**Pass**: imprime `estado: done` y un poema no vacío.

**UI**:

- `http://localhost:3000/admin/board` — la tarea «Escribe un poema sobre el mar» en columna **done**.
- `http://localhost:3000/admin/executions/<uuid>` — Timeline con los 8 nodos del loop.

**Falla típica**: `docker run agent-runtime:v1` falla → la imagen no
está construida.

---

### `human_02_02` — El aislamiento del contenedor es real

**Qué prueba**: el perfil endurecido del sandbox (cap-drop ALL, FS
raíz read-only, sin socket Docker, red interna sin salida, seccomp)
es contrato, no aspiración.

**Comando**:

```powershell
.\.venv\Scripts\python.exe .\scripts\demo_human_02_02.py
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

**Pass**: los 5 `[OK]`. **Cualquier `[FALLO]`** indica regresión real
en `apps/workers/src/workers/container.py`.

---

### `human_02_03` — Las salvaguardas frenan al agente

**Qué prueba**: los 4 cinturones del agent loop
(`max_iterations`, `repetitive_loop`, `max_cost`, `container_timeout`)
disparan cuando deben.

**Comando**:

```powershell
.\.venv\Scripts\python.exe .\scripts\demo_human_02_03.py
```

**Output esperado**:

```
==========  human_02_03 — las salvaguardas del agent loop  ==========
  [  OK  ]  max_iterations dispara aborted (max_iterations_exceeded)
  [  OK  ]  repetitive_loop dispara aborted (repetitive_loop_detected)
  [  OK  ]  max_cost dispara aborted (max_cost_exceeded)
  [  OK  ]  container_timeout mata el contenedor + persiste failed
```

**Pass**: los 4 `[OK]`.

---

### `human_02_04` — La validación humana pausa la ejecución

**Qué prueba**: la política `human_approval_policy` con
`code_execution: human_required` aparca la tarea en
`awaiting_human_approval` cuando el agente intenta `shell_exec`, y
crea solicitud en `/admin/approvals`.

**Comando**:

```powershell
.\.venv\Scripts\python.exe .\scripts\demo_human_02_04.py
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

**UI**:

- `http://localhost:3000/admin/approvals` — tarjeta nueva con
  botones Aprobar / Rechazar y la acción (`shell_exec` con
  `deploy --prod`).
- `http://localhost:3000/admin/board` — la tarea en columna
  **Pendiente de aprobación**.

**Pass**: `awaiting_human_approval` en Execution + Task, tarjeta visible
en `/admin/approvals`. Al pulsar Aprobar, la tarea vuelve a `backlog`.

---

### `human_02_05` — Tiempo real (WebSocket) sin refresco

**Qué prueba**: el bus de eventos (Redis Streams) + WebSocket → el
board del admin-panel ve transiciones del Kanban y pasos de ejecución
**sin refrescar**.

**Comando**:

```powershell
.\.venv\Scripts\python.exe .\scripts\demo_human_02_05.py
```

El script crea la tarea y te pide `Enter`. **Antes** de pulsar:

- Abre `http://localhost:3000/admin/board` (idealmente en VARIAS
  pestañas).
- Pulsa Enter y observa cómo las cartas se mueven solas.

Para automatizar (sin Enter): `$env:DEMO_NO_WAIT="1"`.

**Pass**: en cada pestaña del board ves la tarjeta moverse
`backlog → ready → in_progress → done` sin pulsar F5, y los pasos del
Timeline aparecen uno a uno mientras el agente corre.

---

## Plan 04 — los 5 tests humanos

Plan 04 no trae scripts. Los caminos críticos los cubren los demos
del Plan 04.5; el resto se prueba por la UI (o queda bloqueado).

### `human_04_01` — Memoria mejora tareas repetidas

✅ Cubierto por `demo_human_04_5_01.py` (ver siguiente sección). El
test del roadmap pide ejecutar la misma tarea dos veces y validar
que la 2ª referencia memorias de la 1ª; el demo lo simplifica
sembrando una Execution `done` + Memorizer + recall.

### `human_04_02` — RAG funciona con corpus realista

⚠️ **Parcial**. La parte "10 docs (PDF/.docx/.md/audio)" requiere
ingestión real por la UI; el demo `demo_human_04_5_02.py` cubre el
camino con un Document sembrado en BD.

**Protocolo manual completo** (10 docs reales):

1. `http://localhost:3000/admin/projects` → elige un proyecto.
2. Entra en **Knowledge Bases** del proyecto. Crea una KB si no la
   tienes; pulsa **Subir documento**.
3. Sube 10 archivos: 3 PDFs, 3 .docx, 3 .md, 1 audio (mp3/wav).
4. Espera 2-5 min a que pasen `pending → processing → indexed`. En
   `/admin/documents/<doc_id>/ingestion` ves el progreso por doc;
   si alguno cae a `failed`, Docling registra el motivo ahí.
5. Comprueba `/admin/dashboard`: `docling-serve` y `egress-proxy` en
   `ok`.

**Pass**: 10 docs en `indexed`, al menos una query RAG devuelve hits.

**Falla típica**: docling-serve `unhealthy` → confirma con `curl
http://localhost:5001/health`; reinicia con `docker compose restart
docling-serve`.

### `human_04_03` — Docling en el chat (pegar PDF)

❌ **Bloqueado** por chat-file-upload (Plan 07). La página
`/admin/projects/{id}/chat` no tiene `<input type="file">`. **Saltar**
hasta Plan 07.

### `human_04_04` — Scopes de memoria respetados

✅ **Ejecutable hoy** desde `/admin/memories`.

**Protocolo manual**:

1. `http://localhost:3000/admin/memories` → **Nueva memoria**.
2. Crea las 4 con scopes distintos:

   | scope            | content                               |
   | ---------------- | ------------------------------------- |
   | `private`        | "Mi nota privada"                     |
   | `team_shared`    | "Nota del equipo X"                   |
   | `project_shared` | "Nota del proyecto Y"                 |
   | `global`         | "Nota global" (requiere tenant_admin) |

3. Con el dropdown de **scope filter** confirma que ves las 4.
4. (Cross-team) Loguéate como usuario de otro team del mismo tenant.
   En `/admin/memories` ves `project_shared` y `global` pero NO la
   `team_shared` ni la `private` del primer usuario.

**Pass**: 4 memorias creadas con scope distinto + usuario de otro
team no ve las restringidas.

### `human_04_05` — Cambio modelo embeddings + reindexación

❌ **Bloqueado**. `embedding_model_id` se muestra como texto sin
selector ni endpoint para cambiarlo. **Saltar** hasta que llegue la
feature (probablemente Plan 12).

---

## Plan 04.5 — los 2 demos del agent-runtime integrado

Cubren `human_04_5_01` (memoria end-to-end) y `human_04_5_02` (RAG
con citas). Los setups (`setup_demo_project.py` y `setup_demo_04_5.py`)
son idempotentes — el launcher los re-ejecuta.

### `human_04_5_01` — Memory replay end-to-end

**Qué prueba**: ciclo completo de la memoria del agente. Memorizer
destila tras Execution `done` → otra ejecución recupera vía
`memory_recall` → el agente escribe a mano con `memory_store`.

**Comando**:

```powershell
.\.venv\Scripts\python.exe .\scripts\demo_human_04_5_01.py
```

Tiempo: ~5 s sin pausas.

**Output esperado** (N = memorias del agente al empezar):

```
========================================================================
  demo human_04_5_01 — Memory replay end-to-end
========================================================================
  Tenant   : tenant-a
  Proyecto : <uuid>
  Agente   : <uuid>
  api-server: http://localhost:8001

──────────────────────────────────────────────────────────────────
  Paso 1/4 — Sembrar una Execution `done`
──────────────────────────────────────────────────────────────────
  [  OK  ]  Execution + Task creadas  — execution_id=<uuid>

──────────────────────────────────────────────────────────────────
  Paso 2/4 — Memorizer destila con LLM fake (2 candidatos)
──────────────────────────────────────────────────────────────────
  [  OK  ]  Memorizer persistio 2 entradas  — reason=ok
  [  OK  ]  memory_entries del agente N → N+2

──────────────────────────────────────────────────────────────────
  Paso 3/4 — `memory_recall` HTTP (lado lectura del agente)
──────────────────────────────────────────────────────────────────
  Token minteado (kind=agent), llamando .../memory-recall
  [  OK  ]  hits devueltos: >=1
    · score=0.0164  bm25=1  vec=None  scope=team_shared
      "El proyecto usa asyncpg como único driver de Postgres."

──────────────────────────────────────────────────────────────────
  Paso 4/4 — `memory_store` HTTP (lado escritura del agente)
──────────────────────────────────────────────────────────────────
  [  OK  ]  memory_store 201  — memory_id=<uuid>
  [  OK  ]  recall encuentra la nueva memoria
```

**Qué significa cada `[OK]`**:

| `[OK]`                           | Demuestra                                                                                                       |
| -------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| Paso 1 Execution+Task            | Conexión SQLAlchemy a Postgres funciona                                                                         |
| Paso 2 Memorizer 2 entradas      | `should_memorize` autoriza + `distil_execution` parsea + `persist_memory_candidates` guarda con `agent_id` + FK |
| Paso 2 N → N+2                   | Count contra BD coincide (verificación independiente del Memorizer)                                             |
| Paso 3 hits >=1                  | JWT `kind=agent` valida + endpoint resuelve `team_id` del agente + BM25 encuentra                               |
| Paso 4 memory_store 201          | El agente puede escribir a mano (no solo destilar)                                                              |
| Paso 4 recall encuentra la nueva | Read-after-write inmediato (no hay sink que drenar)                                                             |

**UI**:

- `http://localhost:3000/admin/memories` — verás 3 entradas nuevas
  (2 destiladas por el Memorizer con `agent_id` + `source_execution_id`,
  1 manual del paso 4 con `source: agent_runtime` en metadata). Filtra
  por `scope=team_shared` para aislarlas.
- `http://localhost:3000/admin/executions/<execution_id>` — el demo
  imprime esta URL al final. Verás la **Timeline con 5 pasos**:
  `perceive` → `plan` (model_call, 148 tokens, $0.0015) → `act` (tool_call
  shell_exec con args + result) → `observe` → `finalize`. Badge verde
  `done`, iteraciones 1, total tokens 148.

  > El demo siembra los `steps_log` con el **shape canónico** que la
  > UI espera (`index` + `node` + `kind` + `status` + `summary` por
  > paso). Si vinieras de una Execution antigua que se sembró antes
  > de ese fix, la página aparecería vacía con "Esta ejecución
  > todavía no tiene pasos registrados" — eso es la fila vieja en BD,
  > no un bug actual.

**Pass**: los **6 `[OK]`** del demo + Timeline con 5 pasos visibles.

### `human_04_5_02` — RAG con citas end-to-end

**Qué prueba**: el agente busca en una KB granted a su proyecto y
recibe hits con `chunk_id`/`document_id`/`kb_id` (citas reales) +
puede abrir el documento + puede copiarlo a otra KB.

**Comando**:

```powershell
.\.venv\Scripts\python.exe .\scripts\demo_human_04_5_02.py
```

Tiempo: ~5 s.

**Output esperado**:

```
========================================================================
  demo human_04_5_02 — RAG con citas end-to-end
========================================================================
  Paso 1/3 — `rag_search` HTTP x 3 queries
  Query: «tenant_id RLS»
    1. rrf=0.0164  bm25=1  vec=None  rerank=1.000
       "Multi-tenancy desde el día uno: ..."
       cita → kb_id=<uuid>  document_id=<uuid>  ordinal=0
  Query: «sandbox agent-runtime»  → 1 hit
  Query: «asyncpg»                → 1 hit
  [  OK  ]  Al menos una query devolvió hits

  Paso 2/3 — `document_convert` (vista «open document»)
  [  OK  ]  Document devuelto: «Notas de arquitectura»  — 4 chunks
    ord=0  "Multi-tenancy desde el día uno: ..."
    ord=1  "Los workers nunca ejecutan código del usuario: ..."
    ord=2  "El acceso a BD desde dev usa asyncpg + SQLAlchemy ..."
    ord=3  "El sandbox habla con el api-server por /internal/agent/* ..."

  Paso 3/3 — `promote_to_kb` (copiar doc a una KB destino)
  [  OK  ]  promote_to_kb 201  — new doc <uuid> · chunks=4
```

**UI**:

- `http://localhost:3000/admin/projects/<project_id>/knowledge-bases` —
  KB origen ("Arquitectura del sistema (demo 04.5)") y KB destino
  ("KB destino del demo (04.5)"), ambas concedidas al proyecto.

- `http://localhost:3000/admin/documents/<doc_id>/citations` (original
  o promovido) — la vista de citas con `Notas de arquitectura` como
  título, badge `application/pdf`, **2 páginas A4 placeholder** apiladas
  verticalmente y **4 rectángulos azules** posicionados:
  - Página 1: top half (chunk #0 "Multi-tenancy…") y bottom half
    (chunk #1 "Los workers…").
  - Página 2: top half (chunk #2 "asyncpg + SQLAlchemy…") y bottom
    half (chunk #3 "/internal/agent/\*…").

  El **texto completo de cada chunk** vive en el panel lateral derecho
  (col `lg:grid-cols-[1fr_360px]`); en pantalla estrecha ese panel cae
  debajo. Hover sobre un rectángulo azul = tooltip con texto truncado.
  Click en un chunk del panel = scroll automático + resaltado verde.

  > El demo siembra el Document como `application/pdf` con bboxes
  > simulados para que esta vista funcione completa. El renderizado
  > del PDF real con PDF.js es task posterior (la página fija la
  > **superficie** citación → página → bbox; el contenido visual
  > viene después).

- `http://localhost:3000/admin/documents/<doc_id>/ingestion` (original
  o promovido) — badge verde **Indexado** y card de Eventos con el
  mensaje _"Documento ya indexado. No hay pipeline corriendo, así
  que no llegarán eventos nuevos por WebSocket."_ Cuando un worker
  está procesando un documento real (no nuestro caso), aquí aparecen
  los eventos `document.status` / `document.progress` en vivo.

> **Nota**: la ruta `/admin/documents/<id>` (sin sufijo `/citations` o
> `/ingestion`) devuelve **404** — el admin-panel no tiene una vista
> raíz del documento, sólo las dos subvistas. Si te llega un enlace
> sin sufijo, añade `/citations` para ver los chunks.

**Pass**: los **3 `[OK]`** del demo + el Document promovido aparece en
la KB destino + `/citations` pinta los 4 rectángulos azules sobre 2
páginas + `/ingestion` muestra estado **Indexado**.

---

## Volver a empezar

```powershell
# Parar todo
.\scripts\dev\down.ps1 -Docker

# Limpiar el estado del demo (proyecto + agente compartidos)
Remove-Item scripts\.demo_state.json -ErrorAction SilentlyContinue

# Limpiar memorias / KBs acumuladas (destructivo)
docker exec agentic-platform-postgres-1 psql -U migrations_user -d agentic_platform -c `
  "TRUNCATE memory_entries CASCADE; TRUNCATE chunks, documents, kb_projects, knowledge_bases RESTART IDENTITY CASCADE;"

# Re-arrancar
.\scripts\dev\up.ps1
.\scripts\dev\run-human-tests.ps1
```

---

## Troubleshooting

### "No veo nada al ejecutar el script"

Causa #1: lanzaste el `.py` directo (`.\scripts\xxx.py`) en PowerShell.
Windows usa el Python del sistema, faltan dependencias, revienta sin
imprimir. **Usa siempre** `.\.venv\Scripts\python.exe`.

### "SyntaxError: invalid syntax" al ejecutar un `.ps1`

Lanzaste el `.ps1` con Python (`.venv\Scripts\python scripts\dev\xxx.ps1`).
PowerShell scripts NO son scripts de Python — Python intenta parsear
`[CmdletBinding()]` o `[string]$Only = "all"` y revienta.

Regla rápida:

| Extensión | Cómo se invoca                                   |
| --------- | ------------------------------------------------ |
| `.py`     | `.\.venv\Scripts\python.exe .\scripts\<demo>.py` |
| `.ps1`    | `.\scripts\dev\<script>.ps1`                     |

### `/admin/documents/<uuid>` devuelve 404

El admin-panel **no tiene una vista raíz del Document**, sólo dos
subvistas. La URL correcta es:

- `/admin/documents/<uuid>/citations` — chunks con bounding boxes
- `/admin/documents/<uuid>/ingestion` — estado del pipeline

Los demos imprimen las URLs con sufijo en su footer. Si te llega un
enlace sin sufijo (un demo antiguo, una nota copiada), añade
`/citations`.

### Timeline de Execution vacía: "Esta ejecución todavía no tiene pasos registrados"

La fila `executions` en BD tiene `steps_log` con un shape que la UI
no entiende (le faltan campos `index` / `node` / `kind`). Pasa con
Executions sembradas por demos antiguos antes del fix de
`demo_human_04_5_01.py`. El demo actual siembra **5 pasos** con shape
canónico y la Timeline se pinta correctamente.

Para "reparar" una Execution vieja sin re-lanzar nada, ejecuta el
demo otra vez: te dará un `execution_id` nuevo cuyo URL pinta bien.
Las viejas filas siguen en BD (no hay migración retroactiva); o
limpia con TRUNCATE (ver "Volver a empezar" arriba).

### `/admin/documents/<uuid>/ingestion` dice "Pendiente" cuando el doc está indexado

Bug arreglado: la página ahora hace fetch del estado real del
documento al cargar (antes sólo escuchaba el WebSocket, que está
mudo cuando no hay un worker procesando). Si lo ves así, tu
admin-panel está sirviendo código antiguo. Recarga la página con
Ctrl+F5; si persiste, reinicia el stack:

```powershell
.\scripts\dev\down.ps1
.\scripts\dev\up.ps1
```

### Consola del navegador: `Extra attributes from the server: cz-shortcut-listen`

No es nuestro bug. ColorZilla (extensión de Chrome) inyecta el atributo
`cz-shortcut-listen` en el `<body>` después del SSR y React detecta
mismatch. El admin-panel pone `suppressHydrationWarning` en `<body>`
para silenciar este aviso (cubre también Grammarly, LastPass, etc.).
Si lo ves, recarga con Ctrl+F5 — el código antiguo aún está cacheado.

### `GET /favicon.ico 404` en la consola del navegador

Arreglado: el admin-panel ahora sirve `app/favicon.ico` (16×16) +
`app/icon.svg` (vectorial). Si lo ves, Ctrl+F5 para forzar recarga.

### `401 Unauthorized` en demos del Plan 04.5

Tu api-server y tu shell del demo leen `API_SERVER_JWT_SECRET`
distintos. Más fácil de evitar:

- **No setees** `API_SERVER_JWT_SECRET` en NINGUNA terminal — ambos
  lados leen el default pydantic `dev-only-jwt-secret-change-me`.
- O usa `.\scripts\dev\up.ps1`, que pinea el default explícitamente.

Si tu shell tiene la variable seteada de antes:

```powershell
Remove-Item Env:\API_SERVER_JWT_SECRET -ErrorAction SilentlyContinue
```

### "password authentication failed for user migrations_user"

El default de `workers.config.Settings.database_url` apunta a `:5432`
(producción), no a `:15432` (dev). Los demos del Plan 04.5 lo
fuerzan al construir `Settings(database_url=DB_URL)`; los del Plan
02 leen `DEMO_DATABASE_URL`. Si tocas algo y rompes esto:

```powershell
$env:DEMO_DATABASE_URL = "postgresql+asyncpg://migrations_user:changeme-migrations-dev-only@localhost:15432/agentic_platform"
```

### "relation knowledge_bases does not exist"

Tu BD dev está por debajo de la migración 0022. `up.ps1` ya hace
`alembic upgrade head`; si arrancaste el stack manualmente sin
migrar:

```powershell
cd apps\api-server
$env:DATABASE_URL = "postgresql+asyncpg://migrations_user:changeme-migrations-dev-only@localhost:15432/agentic_platform"
..\..\.venv\Scripts\python -m alembic upgrade head
```

### "Foreign key associated with column ... could not find table 'users'"

Sólo si modificas un script demo y abres tu propio `AsyncEngine`.
Los demos lo arreglan con `import api_server.db.models` antes del
primer `session.add`. Si añades scripts nuevos, replica ese patrón.

### "No alcanzo el api-server en http://localhost:8001"

El api-server no está corriendo. Lánzalo con `.\scripts\dev\up.ps1`
y comprueba `curl http://localhost:8001/healthz`.

### `Agent token validation failed: agent not found or revoked`

El demo y el api-server no comparten la misma BD: el agente existe
en una, no en la otra. Verifica con `docker exec agentic-platform-postgres-1
psql -U migrations_user -d agentic_platform -c "\dt"` que apuntas a
la misma BD que el api-server.

### Dashboard `/admin/dashboard` muestra postgres `down`

Conocido en dev cuando el pool asyncpg está frío. El timeout del
probe es 5 s; si no responde a tiempo (típicamente sólo en el primer
request tras arrancar uvicorn), se ve `down`. Re-carga en 30 s
(el dashboard auto-refresca).

### Dashboard `/admin/dashboard` muestra ollama `down`

Esperado si no tienes Ollama corriendo en el host (`ollama serve`).
El dashboard monitoriza los **8 servicios** del stack desde Plan 04.5
(postgres, redis, vault, minio, clamav, docling-serve, ollama,
egress-proxy). Ollama es opcional — si no lo usas no instales nada;
si lo usas, arráncalo con `ollama serve` en otra terminal y el probe
pasará a `ok` en el siguiente refresh.
