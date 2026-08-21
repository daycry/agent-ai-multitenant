---
title: Gestión de capacity
docs_language: es
audience: operador, system admin, devops
updated: 2026-05-31
---

# Runbook — Gestión de capacity

Cómo **dimensionar y escalar** la plataforma cuando la carga crece: añadir
workers por cola, ajustar la concurrencia de Celery y los límites de tiempo de
ejecución, dimensionar PostgreSQL / Redis / MinIO, leer las señales de
monitorización del host que indican que toca escalar, y la capacity de GPU.

> Alcance: **Docker Compose en una sola máquina** (CLAUDE.md). Escalar aquí
> significa **scale-up** (más réplicas de un servicio y más recursos en el mismo
> host), NO scale-out multi-máquina ni Kubernetes — eso queda explícitamente
> fuera del plan. Cuando el host se queda sin margen, la decisión es mover el
> stack a una máquina mayor (un [upgrade](./03-system-upgrade.md) con re-deploy),
> no repartirlo entre nodos.

La regla de oro: **escala guiado por señales, no a ciegas**. Sube réplicas o
recursos solo cuando una métrica del host (cola de trabajo creciente, RAM/CPU
sostenidas, disco) lo justifique, y deja siempre margen para que el host no entre
en OOM (ver [Señales de monitorización](#señales-de-monitorización-cuándo-escalar)).

## Cuándo

- Los planes tardan en arrancar: hay tareas encoladas esperando un worker libre.
- Un tipo de carga concreto (runs pesados, ingestión, tests) satura su carril y
  frena al resto.
- PostgreSQL / Redis / MinIO se acercan a sus límites (conexiones, memoria,
  disco).
- Vas a habilitar inferencia local en GPU o a aumentar su uso.
- Una alerta de presión de recursos del host (RAM/disco/swap/OOM) está firing.

Para un fallo puntual de un servicio (no de capacity) usa
[02-troubleshooting.md](./02-troubleshooting.md); para verificar salud antes y
después de escalar, [health-check.md](./health-check.md).

## Comprobación previa

1. **Stack sano** — no escales sobre un stack ya roto. Ejecuta
   [health-check.md](./health-check.md): `docker compose ps` todos
   `Up (healthy)`, `GET /healthz` → 200.
2. **Mira las métricas primero** — abre el dashboard Grafana `host-overview`
   (overlay de monitorización, Plan 12) y confirma **qué** recurso está
   apretado. Escalar el recurso equivocado no arregla nada y consume host.
3. **Margen en el host** — antes de subir réplicas/recursos comprueba que el
   host tiene RAM y CPU libres para el incremento. La suma de los
   `deploy.resources.limits` no debe superar la capacidad física, o provocarás
   OOM kills (alerta `HostOOMKills` / `ContainerOOMKilled`).

## Topología de colas (el modelo de escalado)

El trabajo se reparte en **6 colas Celery** (`apps/workers/src/workers/celery_app.py`),
de modo que la carga de runtime / privilegiada queda aislada del carril común y
**escala por separado**:

| Cola          | Qué drena                                                    |
| ------------- | ------------------------------------------------------------ |
| `default`     | Tareas de agente ordinarias — el carril común.               |
| `ingestion`   | Pipelines de ingestión documental (Docling — Plan 04).       |
| `test`        | Ejecución del test-runtime (Plan 06).                        |
| `review`      | Ejecución del review-runtime (Plan 06).                      |
| `privileged`  | Tareas que tocan secretos / infra (rotación de credenciales, |
|               | backup) — la drena un worker con el perfil de seguridad      |
|               | más estricto.                                                |
| `marketplace` | Las puertas de seguridad de una instalación del marketplace  |
|               | (análisis estático + prueba de humo en sandbox, prod-13      |
|               | `task_prod13_01`). La drena `workers-marketplace`, con       |
|               | `--concurrency=1`.                                           |

> **Por qué `marketplace` es lane propia y no un `--queues=...,marketplace` en un
> pool existente.** El trabajo dura minutos (bandit y semgrep con 120 s de plazo
> cada uno, más el contenedor de prueba de humo) y los tres pools que ya había
> tienen su forma documentada de romperse con eso: `workers` y `workers-aux` van a
> `--concurrency=2` y drenan `test`, la cola por la que un agent-run BLOQUEADO
> espera su `stack_exec` —la auto-inanición que motivó `workers-aux`—, y
> `workers-backup` va a `--concurrency=1` detrás del backup nocturno. Subir la
> capacidad de análisis es subir la concurrencia de ESA lane, sin tocar las otras:
> es justamente lo que el modelo de una-cola-por-carril compra.

> **Colas `heavy`/`gpu` retiradas (ADR 0083 / prod-06).** Estaban declaradas pero
> ningún productor enrutaba hacia ellas y, en mono-máquina, no había un worker
> dedicado que las drenara — colas muertas. Si algún día hace falta aislar runs
> pesados o añadir un host GPU, reintroducir el lane es un cambio de config + un
> ADR, no una migración.

El instalador genera **un** servicio `workers` (compose generator,
`task_15_07`), escalado por `deploy.replicas` a partir de la elección del wizard
(`resources.worker_replicas`). Por defecto ese pool consume **todas** las colas.
Para dar a un carril su propia capacity (que los runtimes de `test`/`review` no
ahoguen a `default`, que `privileged` corra siempre en el worker endurecido) se
despliega **un servicio de worker por cola** apuntándolo con `--queues`.

## Escalar workers

### Opción A — subir réplicas del pool único (lo más simple)

El camino por defecto: más procesos worker drenando las mismas colas. Si el
stack ya está desplegado, escala en caliente sin tocar el compose:

```bash
docker compose -f docker/docker-compose.yml up -d --scale workers=4
```

O, de forma persistente, sube `worker_replicas` en el `install.yaml` y
[reinstala en modo preservación](./03-system-upgrade.md#alternativa--reinstalación-con-preservación-de-datos)
(`./scripts/reinstall.sh`), que regenera el compose con el nuevo
`deploy.replicas` sin tocar los datos. Los perfiles del instalador ya traen un
punto de partida razonable: **minimal** 1 worker / 2 GiB, **recommended** 4
workers / 8 GiB, **gpu** 6 workers / 16 GiB
([scripts/install-profiles/](../../scripts/install-profiles/)).

### Opción B — un worker por cola (aislar carriles)

Cuando un tipo de carga debe escalar independientemente, define servicios de
worker dedicados con su `--queues`. Cada uno reutiliza la imagen `workers` y la
misma config; solo cambian las colas que consume y, opcionalmente, su
concurrencia y sus `deploy.resources.limits`:

```yaml
# fragmento del docker-compose generado / un override propio
services:
  workers-default:
    image: ${PLATFORM_REGISTRY}/workers:${PLATFORM_IMAGE_TAG}
    command:
      [
        "celery",
        "-A",
        "workers.celery_app",
        "worker",
        "--queues",
        "default,ingestion",
        "--concurrency",
        "4",
      ]
    deploy:
      replicas: 3
      resources: { limits: { cpus: "4.0", memory: "8g" } }

  workers-runtimes:
    image: ${PLATFORM_REGISTRY}/workers:${PLATFORM_IMAGE_TAG}
    command:
      [
        "celery",
        "-A",
        "workers.celery_app",
        "worker",
        "--queues",
        "test,review",
        "--concurrency",
        "2",
      ]
    deploy:
      replicas: 1
      resources: { limits: { cpus: "4.0", memory: "16g" } }

  workers-privileged:
    image: ${PLATFORM_REGISTRY}/workers:${PLATFORM_IMAGE_TAG}
    command:
      [
        "celery",
        "-A",
        "workers.celery_app",
        "worker",
        "--queues",
        "privileged",
        "--concurrency",
        "1",
      ]
    # El perfil de seguridad MÁS estricto: este worker toca Vault/secretos.
    deploy:
      replicas: 1
```

Reglas al separar carriles:

- **No dejes ninguna cola huérfana.** Si repartes las 5 colas entre varios
  servicios, asegúrate de que **cada** cola figura en el `--queues` de algún
  worker, o sus tareas quedan encoladas para siempre. La cola `privileged`
  **debe** drenarla el worker con el perfil de seguridad más estricto, nunca el
  pool genérico.
- **El proceso `beat` es singleton.** Hay un único Celery beat para todo el
  stack (las cadencias de backup, price-sync, rotación de credenciales). NO lo
  escales: dos beats duplicarían los disparos periódicos. Solo se escalan los
  workers, no beat.
- **El orchestrator también es singleton** en el modelo mono-máquina: asigna
  tareas y encola los runs (`apps/orchestrator`). No se replica.

## Concurrencia de Celery + límites de tiempo

### Concurrencia por worker

Cuántas tareas ejecuta **en paralelo** un proceso worker lo fija
`--concurrency` (o el env `CELERY_WORKER_CONCURRENCY` / `--autoscale`). Dos
invariantes ya cableadas en `celery_app.py` condicionan el tuning:

- `worker_prefetch_multiplier=1` — cada worker reserva **una** tarea a la vez. Los
  runs de agente no son baratos; un prefetch mayor estancaría la cola detrás de
  un job lento. **Déjalo en 1.**
- `task_acks_late=True` — el ack solo tras completar, para que un crash
  re-encole el job en vez de perderlo. Implica que un worker que muere con una
  tarea a medias **la repetirá** otro worker: las tareas deben ser idempotentes
  (lo son por diseño).

Capacity total de runs concurrentes ≈ `réplicas × concurrencia`. Súbela con
cuidado: cada run lanza un contenedor agent-runtime con su propio envelope de
memoria (`WORKERS_CONTAINER_MEM_LIMIT`, 512m por defecto), así que la concurrencia
real está acotada por la RAM del host, no solo por la CPU. Una concurrencia alta
con poca RAM dispara OOM kills (ver
[Señales](#señales-de-monitorización-cuándo-escalar)).

### Límites de tiempo de ejecución (platform settings, en caliente)

El **backstop de tiempo** de un `run_execution` NO está hardcodeado en los
workers: son **platform settings** que el orchestrator aplica **por tarea** al
encolar (`apply_async(soft_time_limit=…, time_limit=…)`), de modo que un cambio
desde el panel admin afecta a los **runs nuevos sin reiniciar** los workers
(Plan 06.14 `task_06_14_04`):

| Platform setting              | Por defecto     | Qué controla                                                                       |
| ----------------------------- | --------------- | ---------------------------------------------------------------------------------- |
| `execution_soft_time_limit_s` | `1800` (30 min) | Soft limit: lanza `SoftTimeLimitExceeded`, que la tarea captura y finaliza limpia. |
| `execution_hard_time_limit_s` | `2100` (35 min) | Hard limit: SIGKILL del proceso hijo del worker.                                   |

Notas de tuning:

- Son **generosos a propósito**: el agent-runtime impone su propio
  `container_run_timeout_s` (más estricto, 600 s por defecto); estos límites de
  Celery solo cazan una tarea realmente colgada.
- Se garantiza `soft < hard` (si se configura mal, `get_execution_time_limits`
  sube el hard); Celery nunca rechaza los límites.
- Solo un **System Admin** puede escribir un platform setting; un tenant no
  puede aflojarlos. Súbelos si tus runs legítimos son largos (modelos lentos,
  tareas grandes) y ves SIGKILLs espurios; bájalos para cortar antes runs
  desbocados.

## Dimensionar PostgreSQL / Redis / MinIO

Los servicios de datos comparten host con todo lo demás; dimensionarlos es
repartir RAM/CPU/disco del único host con cabeza. Defaults que genera el
instalador (`deploy.resources.limits` en el compose generator):

| Servicio   | Límite por defecto | Palancas de capacity                                                                                                                                                                                                                                                                                  |
| ---------- | ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| PostgreSQL | 2 CPU / 2 GiB      | Sube el límite de memoria y afina `shared_buffers`, `work_mem`, `max_connections`. PostgreSQL aloja también pgvector (RAG/embeddings): el índice vectorial pide RAM. Vigila el nº de conexiones (api-server + workers + orchestrator).                                                                |
| Redis      | 1 CPU / 1 GiB      | `--maxmemory ${REDIS_MAX_MEM:-512mb}` con política `allkeys-lru`. Redis sirve broker (DB 1), result backend (DB 2) y los event-streams (DB 0). Si la cola de Celery crece mucho, sube `REDIS_MAX_MEM` **y** el límite de memoria del contenedor a la par (si maxmemory ≥ límite del contenedor, OOM). |
| MinIO      | 2 CPU / 2 GiB      | El cuello de botella de MinIO es **disco**, no RAM: el almacenamiento de objetos (KBs, artefactos, bundles de backup) crece con el uso. Dimensiona el volumen bajo `data_root/minio` y vigila el disco.                                                                                               |

Reglas:

- **La suma de límites ≤ host.** Sumar los `limits.memory` de todos los servicios
  no debe superar la RAM física menos un margen para el kernel/SO. Si la suma se
  acerca al 100 %, ya estás sobre-comprometido y un pico provocará OOM.
- **El disco es el límite duro.** Todo el estado vive bajo `data_root`
  (`/data/agent-platform`): Postgres, MinIO, Redis, Vault, repos/worktrees,
  **y los backups** (`data_root/backups`, retención local 7 días por defecto).
  Un disco lleno tira el stack y rompe el backup. Mira la alerta
  `HostDiskUsageHigh` (>80 %).
- Cambiar un límite de un servicio de datos exige re-deploy (regenerar el compose
  vía reinstalación en modo preservación, o editar el override y `up -d`); no es
  un setting en caliente.

## Señales de monitorización: cuándo escalar

El overlay de monitorización (Plan 12, `docker-compose.monitoring.yml`) expone
las señales que disparan una decisión de capacity:

- **node-exporter** — métricas del host: CPU (`node_load1`), RAM
  (`node_memory_MemAvailable_bytes` / `…_MemTotal_bytes`), disco
  (`node_filesystem_avail_bytes`), swap.
- **cAdvisor** — métricas **por contenedor**: CPU/RAM/red por servicio, y
  `container_oom_events_total` (qué contenedor fue OOM-killed).
- **Grafana** — dashboard `host-overview` (provisionado, sin clics): CPU / RAM /
  disco / red + métricas por contenedor.

Las reglas de alerta ya definidas (`monitoring/prometheus/rules/host_alerts.yml`)
son el disparador objetivo de una acción de capacity:

| Alerta                | Umbral                  | Lectura de capacity → acción                                                               |
| --------------------- | ----------------------- | ------------------------------------------------------------------------------------------ |
| `HostMemoryUsageHigh` | RAM > 90 % sostenida 5m | El host va justo de RAM. **No** subas réplicas/concurrencia: bájalas o mueve a host mayor. |
| `HostSwapActive`      | swap en uso 5m          | Presión de memoria temprana — mismo veredicto que arriba; el swap thrash degrada todo.     |
| `HostOOMKills`        | OOM-killer del kernel   | Ya estás sobre-comprometido: reduce concurrencia / límites de algún servicio **ya**.       |
| `ContainerOOMKilled`  | OOM de un contenedor    | Sube el `limits.memory` de **ese** contenedor (lo nombra la alerta) o corrige la fuga.     |
| `HostDiskUsageHigh`   | disco > 80 %            | Poda bundles de backup viejos / sube `data_root` a un volumen mayor / expande el disco.    |

Señal de **falta de workers** (la que pide escalar el pool, no recortar): tareas
encoladas que no arrancan. Inspecciónala en Redis con la longitud de cada cola:

```bash
docker compose -f docker/docker-compose.yml exec redis \
  redis-cli -n 1 LLEN default      # nº de tareas esperando en la cola `default`
```

Si una cola crece de forma sostenida **y** el host tiene RAM/CPU libres, ese es
el caso legítimo para subir réplicas/concurrencia de esa cola (Opción A o B). Si
la cola crece pero el host **no** tiene margen, escalar empeora las cosas: toca
host mayor.

## Capacity de GPU

La GPU es **opcional** y se habilita en el wizard (`resources.gpu_enabled`, perfil
`gpu`). Cuando está activa, el generador de compose añade el servicio `ollama`
con una **reserva de dispositivo NVIDIA**
(`deploy.resources.reservations.devices`, `driver: nvidia`, `count: all`,
`capabilities: [gpu]`) bajo el profile `gpu`:

```bash
docker compose -f docker/docker-compose.yml --profile gpu up -d ollama
```

Consideraciones de capacity de GPU:

- **Requisito de host**: NVIDIA Container Toolkit instalado y una GPU visible
  (`nvidia-smi`). El paso 1 del wizard detecta la GPU
  ([01-installation-from-scratch.md](./01-installation-from-scratch.md)).
- **La GPU NO se reparte como CPU/RAM.** `count: all` reserva toda la GPU para
  Ollama; la VRAM la consume el **modelo cargado**, no las réplicas de worker. El
  límite de capacity de GPU es el tamaño del modelo frente a la VRAM disponible:
  un modelo que no cabe falla al cargar. Dimensiona el modelo (cuantización,
  tamaño) a la VRAM, no al revés.
- **Una sola GPU = un punto de serialización.** En el modelo mono-máquina hay una
  GPU; las peticiones de inferencia se serializan sobre ese recurso del servicio
  `ollama` (no hay una cola Celery `gpu` — la inferencia local va al servicio
  Ollama directamente). El límite es la VRAM frente al modelo cargado, no la
  concurrencia de los workers.
- El grueso del trabajo va a **proveedores gestionados** (Claude SDK, Copilot,
  Azure Foundry — ADR 0021); la GPU local (Ollama) es para inferencia local
  (p. ej. la distilación del Memorizer) o cuando se quiere mantener el dato en
  casa. No necesitas GPU para escalar la plataforma en general.

## Verificación

1. **Salud tras escalar** — [health-check.md](./health-check.md): todos los
   servicios `Up (healthy)`, `GET /healthz` → 200. Una réplica nueva que no pasa
   a healthy normalmente es falta de recursos (revisa la causa antes de seguir).
2. **Las réplicas drenan** — `docker compose ps workers` muestra el nº esperado
   de instancias; la longitud de la cola que estaba creciendo empieza a bajar
   (`redis-cli -n 1 LLEN <cola>`).
3. **Sin OOM nuevos** — ninguna alerta `HostOOMKills` / `ContainerOOMKilled`
   firing tras el cambio; si aparece una, te pasaste con réplicas/concurrencia:
   reduce.
4. **Límites de tiempo aplicados** — un cambio de `execution_*_time_limit_s` desde
   el panel admin afecta a los runs **nuevos** sin reinicio; verifica con un run
   de prueba que el budget esperado se respeta.

## A quién avisar

- **DevOps / operador**: ejecuta el escalado (réplicas, recursos), vigila el
  dashboard `host-overview` y decide cuándo el host se queda corto.
- **System Admin**: posee los platform settings (`execution_*_time_limit_s`),
  aprueba un re-deploy con más recursos y decide la migración a host mayor.

## Enlaces

- Salud del stack: [health-check.md](./health-check.md).
- Reiniciar/parar sin perder datos: [restart-services.md](./restart-services.md).
- Re-deploy con más recursos (reinstalación en preservación):
  [03-system-upgrade.md](./03-system-upgrade.md).
- Diagnóstico de fallos (no de capacity): [02-troubleshooting.md](./02-troubleshooting.md).
- Cola `privileged` y rotación de credenciales: [05-key-rotation.md](./05-key-rotation.md).
- Instalación + detección de GPU: [01-installation-from-scratch.md](./01-installation-from-scratch.md).
  </content>
  </invoke>
