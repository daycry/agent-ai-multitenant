# Cómo ejecutar los demos en vivo de los tests humanos

Los planes 02 y 04.5 vienen con **scripts demo** que reproducen los
tests humanos contra el stack de desarrollo, en estilo "ves cómo
el sistema lo hace en tu pantalla". El Plan 04 no trae scripts —
sus tests humanos se ejecutan por la UI del admin-panel. Esta guía
cubre los tres casos.

Si lo que buscas es **el modo Playwright** para ver los E2E de
frontend, esa es otra guía: [watching-e2e-tests.md](./watching-e2e-tests.md).

## Pre-requisitos comunes

Todos los demos asumen el stack de desarrollo arriba:

| Servicio           | Puerto | Cómo                                                                                          |
| ------------------ | ------ | --------------------------------------------------------------------------------------------- |
| Postgres           | 15432  | `docker compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml up -d postgres` |
| Redis              | 6379   | mismo `compose up`                                                                            |
| MinIO              | 9000   | sólo para Plan 04 (KB ingestion); puedes saltártelo en los demos                              |
| `agent-runtime:v1` | —      | `docker build -t agent-runtime:v1 docker/agent-runtimes/agent-runtime/`                       |
| migraciones        | —      | `cd apps/api-server && DATABASE_URL=postgresql+asyncpg://... alembic upgrade head`            |

Para los demos del Plan 04.5 además necesitas el **api-server local**
sirviendo `/internal/agent/*`:

```bash
cd apps/api-server
API_SERVER_DATABASE_URL="postgresql+asyncpg://app_user:changeme-app-dev-only@localhost:15432/agentic_platform" \
API_SERVER_ADMIN_DATABASE_URL="postgresql+asyncpg://migrations_user:changeme-migrations-dev-only@localhost:15432/agentic_platform" \
API_SERVER_REDIS_URL="redis://localhost:6379/0" \
API_SERVER_JWT_SECRET="test-secret" \
../../.venv/Scripts/uvicorn api_server.main:app --reload --port 8001
```

> **Importante:** el `API_SERVER_JWT_SECRET` debe ser el mismo en el
> api-server y en el shell que ejecuta los demos del Plan 04.5 — los
> scripts mintean tokens `kind=agent` con esa clave y el api-server
> los valida con la misma. Cualquier valor consistente vale.

Sólo necesitas el api-server para Plan 04.5; los demos de Plan 02
hablan directamente con el contenedor del agente.

## Variables de entorno comunes

| Variable            | Default                    | Para qué                                                            |
| ------------------- | -------------------------- | ------------------------------------------------------------------- |
| `DEMO_TENANT`       | `tenant-a`                 | Slug o UUID de un tenant que YA existe (para que aparezca en admin) |
| `DEMO_NO_PAUSE`     | (no set)                   | Si está, no espera entre fases. Útil para verificar el script       |
| `DEMO_PAUSE_S`      | `5`                        | Segundos entre fases si la pausa está activa                        |
| `DEMO_DATABASE_URL` | postgresql+asyncpg://…     | Sobrescribe la URL del Postgres dev                                 |
| `DEMO_REDIS_URL`    | `redis://localhost:6379/0` | Sobrescribe la URL del Redis dev                                    |
| `DEMO_API_URL`      | `http://localhost:8001`    | **Sólo Plan 04.5** — endpoint del api-server                        |
| `DEMO_MODEL_KIND`   | (scripted)                 | **Sólo `demo_human_02_01.py`** — usa un LLM real (ver abajo)        |

---

## Plan 02 — los 5 demos del agente ejecutando

Hay un **setup compartido** + cinco demos. El setup crea un proyecto
y un agente Writer una sola vez; cada demo añade su propia tarea a
ese proyecto. Si no corres el setup primero, cada demo crea su propio
proyecto suelto (también funciona, pero no se ven juntos en el board
del admin-panel).

### Orden recomendado

```bash
# 1. (Una vez por sesión) Setup del proyecto + agente compartidos
.venv/Scripts/python scripts/setup_demo_project.py

# 2. Los 5 demos, en el orden que prefieras (son independientes)
.venv/Scripts/python scripts/demo_human_02_01.py
.venv/Scripts/python scripts/demo_human_02_02.py
.venv/Scripts/python scripts/demo_human_02_03.py
.venv/Scripts/python scripts/demo_human_02_04.py
.venv/Scripts/python scripts/demo_human_02_05.py
```

### Qué hace cada uno

| Demo                    | Qué demuestra                                                                              |
| ----------------------- | ------------------------------------------------------------------------------------------ |
| `setup_demo_project.py` | Tenant + Project + Agent Writer compartidos. Guarda IDs en `scripts/.demo_state.json`      |
| `demo_human_02_01.py`   | Una ejecución end-to-end: lanza el contenedor agent-runtime, corre la loop, persiste filas |
| `demo_human_02_02.py`   | El sandbox **está aislado**: cap-drop, FS read-only, sin socket Docker, sin red de salida  |
| `demo_human_02_03.py`   | Las salvaguardas frenan al agente: max-iterations, loop-detection, timeout, max-cost       |
| `demo_human_02_04.py`   | La política de aprobación humana **aparca** la tarea en `awaiting_human_approval`          |
| `demo_human_02_05.py`   | Eventos de tiempo real: la tarea recorre el Kanban y el board se actualiza sin refrescar   |

### Para usar un LLM real (sólo `demo_human_02_01.py`)

Por defecto los demos usan un `ScriptedModelClient` (sin
credenciales — el "poema" del mar es texto prefijado). Para que un
LLM real escriba el poema, define `DEMO_MODEL_KIND` y las
credenciales del proveedor (catálogo cerrado de ADR 0021):

```bash
# Azure AI Foundry vía APIM (gateway empresarial)
DEMO_MODEL_KIND=azure_foundry DEMO_MODEL=gpt-4o \
DEMO_APIM_BASE_URL=https://miempresa.azure-api.net/foundry \
DEMO_APIM_DEPLOYMENT=gpt-4o DEMO_APIM_SUBSCRIPTION_KEY=... \
.venv/Scripts/python scripts/demo_human_02_01.py

# Claude Agent SDK (suscripción Pro/Max — requiere claude-agent-sdk)
DEMO_MODEL_KIND=claude_sdk DEMO_MODEL=claude-opus-4-7 \
.venv/Scripts/python scripts/demo_human_02_01.py

# GitHub Copilot (OAuth token con acceso a Copilot)
DEMO_MODEL_KIND=copilot DEMO_MODEL=gpt-4o DEMO_GITHUB_TOKEN=gho_... \
.venv/Scripts/python scripts/demo_human_02_01.py

# Ollama local o cloud
DEMO_MODEL_KIND=ollama DEMO_MODEL=llama3.1 \
DEMO_OLLAMA_BASE_URL=http://localhost:11434/v1 \
.venv/Scripts/python scripts/demo_human_02_01.py
```

`litellm` no se soporta — se eliminó en ADR 0021.

### Dónde mirar en el admin-panel

Tras los demos:

- `http://localhost:3000/admin/projects` → el proyecto del demo.
- `http://localhost:3000/admin/projects/{id}/board` → las tareas creadas, su columna y el Timeline.
- `http://localhost:3000/admin/projects/{id}/approvals` → la solicitud que dejó `demo_human_02_04.py`.

### Volver a empezar

```bash
rm scripts/.demo_state.json
.venv/Scripts/python scripts/setup_demo_project.py
```

Crea un proyecto nuevo. Los antiguos quedan en la BD; bórralos a
mano si te molestan en el board.

---

## Plan 04 — sin scripts demo

Los 5 tests humanos de Plan 04 se ejecutan por la **UI del
admin-panel**, no con scripts Python. La descripción canónica está
en `docs/roadmap/04-memoria-rag-kbs.md` (sección Tests humanos).
Resumen:

| Test          | Cómo se ejecuta                                                                                                                                                                             |
| ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `human_04_01` | Ejecutar la misma tarea ("escribe un endpoint REST estándar") **dos veces** separadas en el tiempo. Validar que la 2ª referencia memorias de la 1ª.                                         |
| `human_04_02` | Subir 10 documentos de dominio (PDF, .docx, .md, audio) por `/admin/knowledge-bases/{id}/upload`. Esperar a que se indexen y comprobar los 4 caminos: BM25, vector, RRF, reranker.          |
| `human_04_03` | En el chat de un proyecto, pegar un PDF como adjunto y pedir un resumen — Docling lo procesa sin indexarlo. (**Bloqueado** por chat-file-upload, que vive en Plan 07.)                      |
| `human_04_04` | Crear 4 memorias con scopes distintos (private, team_shared, project_shared, global) y validar la visibilidad cruzando equipos / proyectos / agentes.                                       |
| `human_04_05` | Cambiar el modelo de embeddings desde el admin-panel; el sistema debe detectar el cambio y proponer reindexación asíncrona. (**Bloqueado** por la pieza de reindexación, fuera de Plan 04.) |

**Importante** — el changelog de Plan 04 dejó constancia honesta de
que **4 de los 5 tests no eran ejecutables end-to-end** cuando se
cerró el plan: faltaba el wire-up del agent-runtime con los nuevos
tools, que es exactamente lo que el Plan 04.5 ha cerrado. Los demos
en vivo de los puntos que sí se podían cerrar (memoria + RAG vistos
desde el sandbox) están en el Plan 04.5 (siguiente sección).

`human_04_03` (Docling en el chat) sigue parqueado hasta Plan 07.
`human_04_05` (reindexación) sigue fuera del alcance actual.

---

## Plan 04.5 — los 2 demos del agent-runtime integrado

Hay un **setup que extiende** el de Plan 02 (añade KB + Document +
4 chunks + ajusta el agente a `memory_scope=team_shared`) y dos
demos que dialogan con el api-server por `/internal/agent/*`.

### Pre-requisito extra

El api-server local en :8001 (ver "Pre-requisitos comunes" más
arriba). Comprueba con `curl http://localhost:8001/healthz` antes
de empezar.

### Orden recomendado

```bash
# 1. Setup compartido del Plan 02 (si no lo has hecho ya)
.venv/Scripts/python scripts/setup_demo_project.py

# 2. Extensión Plan 04.5 (KB + Document + team_id + scope)
.venv/Scripts/python scripts/setup_demo_04_5.py

# 3. (En otra terminal: arranca el api-server — ver arriba)

# 4. Los 2 demos (con el mismo JWT secret que el api-server)
API_SERVER_JWT_SECRET="test-secret" \
.venv/Scripts/python scripts/demo_human_04_5_01.py
API_SERVER_JWT_SECRET="test-secret" \
.venv/Scripts/python scripts/demo_human_04_5_02.py
```

### Qué hace cada uno

| Demo                    | Qué demuestra                                                                                                                                                                                    |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `setup_demo_04_5.py`    | Crea Team del proyecto, ajusta agente a `team_shared`, crea KB + Document + 4 chunks. Idempotente.                                                                                               |
| `demo_human_04_5_01.py` | **Memory replay**: Execution `done` → Memorizer destila con LLM fake → `memory-recall` HTTP encuentra → `memory-store` HTTP graba → re-recall comprueba la nueva memoria.                        |
| `demo_human_04_5_02.py` | **RAG con citas**: `rag-search` × 3 keywords (`tenant_id RLS`, `sandbox agent-runtime`, `asyncpg`) → `document-convert` lista los 4 chunks → `promote-to-kb` copia el Document a una KB destino. |

Los demos **no** abren un contenedor sandbox — disparan las
llamadas directamente desde el script con un agent token minteado
en sitio. La lógica del servidor que se ejerce es exactamente la
que vería un sandbox real; cambia sólo quién dispara la llamada.
Cuando se cierre el wire-up worker→sandbox (mint del token +
register de tools dentro del contenedor), los mismos endpoints
sirven al agente desde la LangGraph loop sin tocar los demos.

### Dónde mirar en el admin-panel

- `http://localhost:3000/admin/memories` → las memorias destiladas por el Memorizer + la que graba `memory-store`.
- `http://localhost:3000/admin/knowledge-bases` → la KB origen y la destino (creada por `demo_human_04_5_02.py`).
- `http://localhost:3000/admin/knowledge-bases/{kb_id}/documents/{doc_id}` → el documento promovido en la KB destino.

### Volver a empezar el escenario Plan 04.5

Las memorias y las KBs se acumulan en cada ejecución (es la
intención — quieres verlas crecer). Para limpiarlas:

```sql
-- desde psql contra agentic_platform:
TRUNCATE memory_entries CASCADE;
TRUNCATE chunks, documents, kb_projects, knowledge_bases RESTART IDENTITY CASCADE;
```

Después relanza `setup_demo_04_5.py` y los dos demos.

---

## Troubleshooting

### "password authentication failed for user migrations_user"

El default de `workers.config.Settings.database_url` apunta a
**:5432** (producción), no a **:15432** (dev). Los demos del Plan
04.5 fuerzan la URL correcta al construir `Settings`; los del
Plan 02 leen `DEMO_DATABASE_URL`. Si tocas algo y rompes esto,
exporta:

```bash
export DEMO_DATABASE_URL="postgresql+asyncpg://migrations_user:changeme-migrations-dev-only@localhost:15432/agentic_platform"
```

### "relation knowledge_bases does not exist"

Tu BD dev está por debajo de la migración 0022. Arregla con:

```bash
cd apps/api-server
DATABASE_URL=postgresql+asyncpg://migrations_user:changeme-migrations-dev-only@localhost:15432/agentic_platform \
../../.venv/Scripts/python -m alembic upgrade head
```

### "Foreign key associated with column ... could not find table 'users'"

Aparece cuando un script abre su propia `AsyncEngine` y SQLAlchemy
no tiene `User` en el metadata aún. Los demos lo arreglan con
`import api_server.db.models` antes del primer `session.add`. Si
escribes un script nuevo, replica ese patrón.

### "No alcanzo el api-server en http://localhost:8001"

El api-server no está corriendo. Mira la sección "Pre-requisitos
comunes" para arrancarlo. Comprueba con `curl http://localhost:8001/healthz`.

### El JWT minteado en el demo no lo acepta el api-server

Ambos lados deben leer el **mismo** `API_SERVER_JWT_SECRET`. El
default del api-server es `change-me-please`, el demo no asume
ninguno — exporta el mismo valor en las dos terminales.

### "Agent token validation failed: agent not found or revoked"

El demo y el api-server no comparten la misma BD: el agente
existe en una, no en la otra. Verifica con `\dt` en psql contra el
puerto al que apunta cada lado.
