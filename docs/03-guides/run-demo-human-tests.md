# Tests humanos paso a paso — índice + troubleshooting compartido

> ⚠️ **Esta página fue dividida**. Originalmente acumulaba los Plans
> 02 / 04 / 04.5 en un único documento. La guía detallada de cada
> plan vive ahora en su propio archivo:
>
> - [`human-tests/02-ejecucion-agentes.md`](./human-tests/02-ejecucion-agentes.md)
>   — los 5 tests del Plan 02 (`human_02_01..05`).
> - [`human-tests/04-memoria-rag-kbs.md`](./human-tests/04-memoria-rag-kbs.md)
>   — los 5 tests del Plan 04 (`human_04_01..05`).
> - [`human-tests/04.5-agent-runtime-integration.md`](./human-tests/04.5-agent-runtime-integration.md)
>   — los 2 demos del Plan 04.5 (`human_04_5_01..02`).
>
> El índice completo de todos los planes con tests humanos vive en
> [`human-tests/README.md`](./human-tests/README.md).

Este archivo se mantiene por dos razones:

1. **Compatibilidad** con enlaces antiguos que apuntan a esta página.
2. **Troubleshooting compartido** (abajo) — las tres guías nuevas
   redirigen aquí para los errores transversales del stack dev
   (asyncpg, Docker, JWT secret mismatch, etc.) en lugar de
   duplicarlos.

## TL;DR — el launcher que las recorre todas

```powershell
.\scripts\dev\up.ps1                        # docker + api-server :8001 + admin-panel :3000
.\scripts\dev\run-human-tests.ps1           # corre los 7 demos (02 + 04.5) en orden
```

Opciones del launcher:

| Flag         | Para qué                                          |
| ------------ | ------------------------------------------------- |
| `-Only 02`   | Solo los 5 demos del Plan 02                      |
| `-Only 04_5` | Solo los 2 demos del Plan 04.5                    |
| `-Pause`     | Pausas de 5 s entre fases (para leer en vivo)     |
| `-SkipStack` | Asume el stack ya arrancado (no relanza `up.ps1`) |

## Pre-requisitos comunes (cualquier plan)

`scripts/dev/up.ps1` se encarga de:

| Servicio      | Puerto | Cómo se levanta                                                                                            |
| ------------- | ------ | ---------------------------------------------------------------------------------------------------------- |
| Postgres      | 15432  | `docker compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml up -d postgres` (vía up.ps1) |
| Redis         | 6379   | mismo `compose up`                                                                                         |
| Vault         | 8200   | mismo                                                                                                      |
| MinIO         | 9000   | mismo                                                                                                      |
| ClamAV        | 3310   | mismo                                                                                                      |
| docling-serve | 5001   | mismo (Plan 04.5 expone el puerto al host)                                                                 |
| egress-proxy  | 8888   | mismo                                                                                                      |
| Migraciones   | —      | `alembic upgrade head` (vía up.ps1)                                                                        |
| api-server    | 8001   | uvicorn detached, con `API_SERVER_JWT_SECRET=dev-only-jwt-secret-change-me`                                |
| admin-panel   | 3000   | next dev detached                                                                                          |

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

## Troubleshooting

Errores transversales que aparecen en cualquiera de las guías
per-plan. Las nuevas guías enlazan a esta sección en lugar de
duplicar el contenido.

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
limpia con TRUNCATE (ver "Volver a empezar" en cada guía per-plan).

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
..\..\.venv\Scripts\python.exe -m alembic upgrade head
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
