---
plan_id: prod-13-rendimiento-y-datos
title: Rendimiento y gestión de datos — event loop, pool, retención e índices
status: pending_approval
blocking_plan: null
started_at: null
completed_at: null
estimated_duration_calendar: 3-4 semanas
estimated_effort_person_days: 21
estimated_cost_human_eur: 9.500 € – 12.600 €
estimated_cost_ai_eur: 60 € – 120 €
created_by: auditoria-claude-2026-06
spec_sections_referenced: []
docs_language: es
priority: P1
---

# Plan prod-13 — Rendimiento y gestión de datos: event loop, pool, retención e índices

## Cabecera

| Campo                              | Valor                                                            |
| ---------------------------------- | ---------------------------------------------------------------- |
| **ID del Plan**                    | `prod-13-rendimiento-y-datos`                                    |
| **Prioridad**                      | P1                                                               |
| **Bloqueado por**                  | — (independiente; coordinación con prod-04/05/06/10, ver tareas) |
| **Tiempo estimado (calendario)**   | 3-4 semanas                                                      |
| **Tiempo estimado (persona-días)** | 21                                                               |
| **Rama git sugerida**              | `plan/prod-13-rendimiento-y-datos`                               |

> **Estado**: la fuente de verdad es el frontmatter YAML de este fichero (`status:`). El campo duplicado que había en esta tabla se retiró en prod-15 (hallazgo docsroadmap-6): se había desincronizado en 22 de 51 planes.

---

## Resumen

La auditoría de producción (2026-06-10) confirmó que los hot paths están bien
construidos (paginación con límites duros, stats en SQL, sin N+1, streams por
entidad), pero detectó tres frentes que en producción degradan o tumban la
plataforma entera:

1. **Event loop bloqueado**: la actualización de marketplace ejecuta bandit/semgrep
   por `subprocess.run` síncrono (hasta 2×120 s) y un sandbox Docker síncrono dentro
   de handlers async (perf-1); los endpoints de backup llaman boto3/paramiko/rclone
   síncronos (api-3); las lecturas hvac/Vault son síncronas y sin timeout en el path
   del chat del asistente (perf-7); el upload de documentos materializa el fichero
   entero en RAM antes del check de 50 MiB (api-2). Cualquiera de ellos congela
   TODAS las requests y WebSockets del api-server.
2. **BD sin tuning ni retención**: el pool asyncpg va con defaults (15 conexiones)
   y la transacción por request se retiene durante el turno LLM completo —
   ~15 chats concurrentes agotan el pool y toda la API falla (perf-2/db-2). El
   índice FTS de chunks no casa con la query `es_unaccent` (seq scan, perf-3),
   no existe purga de soft-deleted ni retención de append-only (db-4), faltan
   índices ((tenant_id, created_at) en executions, unicidad por tenant en
   teams/skills/agents) y el HNSW global degrada el recall multi-tenant (db-6).
3. **Endpoints sin válvulas**: listados sin paginar que arrastran MBs (api-6,
   perf-6, perf-8), `/ws/kanban` re-lee el stream global de 10k eventos por socket
   (perf-5), `/assistant/chat` sin rate limit (api-4), transiciones de estado sin
   `FOR UPDATE` (api-10) y errores crudos de PostgreSQL expuestos al cliente (api-5).

Este plan cierra los 23 hallazgos asignados (perf-1..11, db-2/4/6/7/8/9,
api-2/3/4/5/6/10) en cinco fases: A (event loop), B (pool y transacciones),
C (índices y búsqueda), D (retención y backfill), E (endpoints).

## Alcance

**Entra**:

- Desbloquear el event loop del api-server: marketplace a Celery/`to_thread`,
  backup y Vault con `to_thread` + timeouts, upload en streaming con rechazo
  temprano, clientes httpx/embedder compartidos.
- Tuning del pool async (settings explícitos) y rediseño de la transacción por
  request para NO abarcar llamadas LLM/embeds.
- Migraciones: índice FTS de chunks a `public.es_unaccent`, índice
  `(tenant_id, created_at)` en executions, unicidad parcial por tenant en
  teams/skills/agents, `BigInteger` en `source_size_bytes`.
- Jobs beat de purga de soft-deleted (ventana de gracia configurable), retención
  de append-only (`steps_log`, `audit_log`, `guardrail_events`, `notifications`)
  y backfill de chunks sin embedding.
- Paginación de conversaciones/documentos/citas, `deferred()` de columnas
  pesadas, `/ws/kanban` por canal de proyecto, rate limit en `/assistant/chat`,
  caché Redis de catálogos calientes, `FOR UPDATE` en transiciones críticas y
  saneamiento de respuestas 409.

**Queda fuera** (cubierto por otros planes de la serie o pospuesto):

- Cableado de auto-pausa/alertas de presupuesto (db-1) → **prod-06**. Aquí solo
  se prepara el índice que ese sweep necesitará (db-7).
- Orden blob/commit en `delete_document` (db-3) y dispatch de proyectos
  soft-borrados (db-5) → **prod-06**; la purga de este plan (task_prod13_14)
  asume que ese fix llega antes o en paralelo.
- Antivirus fail-open (api-1) → **prod-12** (hardening de ingesta).
- Operabilidad de Vault, AppRole y defaults (→ **prod-10**); aquí solo timeout +
  `to_thread` + caché del secreto (perf-7).
- Restore/backup funcional de verdad (→ **prod-04**); aquí solo el no-bloqueo
  del event loop en sus endpoints (api-3).
- Particionado físico de `chunks` por tenant o índices HNSW por tenant — se
  documenta como ADR propuesto, no se implementa (db-6 se mitiga con
  `iterative_scan`/`ef_search`).

## Decisiones clave

1. **Marketplace: Celery vs `to_thread`** (perf-1). Opciones: (a) mitigación
   mínima con `asyncio.to_thread` alrededor de `_run_static_analysis`/`_run_sandbox`;
   (b) mover las puertas a una task Celery en cola dedicada con endpoint 202 +
   estado consultable. **Recomendación: (b)**, con (a) como paso intermedio si se
   necesita hotfix; 4 minutos de análisis no pertenecen a un request HTTP aunque
   no bloquee el loop (timeouts de proxy, reintentos del cliente).
2. **Transacción vs LLM** (perf-2/db-2). Opciones: (a) cerrar la transacción antes
   del turno LLM y reabrir sesión para persistir; (b) pasar un _session-factory_
   a las tools del asistente en vez de la sesión viva. **Recomendación: (b)** —
   cada tool abre/cierra su sesión corta (con `set_config` de tenant), el request
   no retiene conexión durante el I/O externo y el patrón sirve igual para el
   embed de `/knowledge-bases/{id}/search`.
3. **Retención de `audit_log`** (db-4). Cuánto tiempo retener auditoría y si los
   runs antiguos se archivan a MinIO antes de compactar `steps_log` es una
   decisión de producto/cumplimiento: **se redacta ADR propuesto**
   (`docs/05-architecture-decisions/`) con opciones (borrado puro a N meses /
   archivado a MinIO + borrado / retención infinita con particionado) y el humano
   decide. La purga de soft-deleted (ventana 30 días configurable) NO requiere
   ADR: la semántica "recuperable durante la gracia" ya está prometida en docstrings.
4. **Valores de pool por defecto** (db-2). Propuesta para single-host:
   `pool_size=10`, `max_overflow=20`, `pool_timeout=10`, `pool_recycle=1800`,
   expuestos como settings de entorno (api-server y admin engine). Se valida con
   la métrica de pool de SQLAlchemy antes de cerrar la fase.
5. **HNSW multi-tenant** (db-6). Ahora: `SET hnsw.iterative_scan = relaxed_order`
   (pgvector ≥ 0.8) en la sesión de búsqueda + `ef_search` configurable + test de
   recall con tenants desbalanceados. Índices parciales/particionado → ADR
   propuesto, no se implementa en este plan.
6. **Caché con TTL corto, invalidación explícita** (perf-10). Membership y
   platform_settings se cachean en Redis con TTL ≤ 60 s e invalidación al
   escribir. Ante la duda entre frescura y rendimiento, gana la frescura: un rol
   revocado no puede sobrevivir más que el TTL.

## Tareas

### Fase A — Event loop sin bloqueos

#### `task_prod13_01` — Marketplace: puertas de análisis y sandbox fuera del event loop

- [x] **Título**: Mover `_run_static_analysis` (subprocess bandit/semgrep,
      `static_analysis.py:480-486`) y `_run_sandbox` (SDK Docker síncrono,
      `sandbox.py:419,433-435`) fuera del loop: task Celery en cola dedicada,
      endpoint de instalación/actualización devuelve 202 + recurso de estado
      consultable; `asyncio.to_thread` como mitigación intermedia en
      `marketplace/install.py:514-517,559`.
  - ⏳ **Pendiente (2026-07-31):** solo está la mitigación intermedia — las dos
    puertas ya corren fuera del bucle (verificado en
    `tests/unit/test_no_blocking_calls_in_event_loop.py`); falta la task Celery en
    cola dedicada y el endpoint que devuelve 202 + recurso de estado consultable.
  - ⏳ **Re-verificado (2026-08-01):** sigue igual —`marketplace/install.py:655,687`
    son `asyncio.to_thread` y no hay task Celery ni 202 en ninguna parte. **No se
    aborda desde este carril a propósito**: `marketplace/` lo está reescribiendo
    [`marketplace-v2-despliegue`](./marketplace-v2-despliegue.md) (ADR 0142), y
    meter aquí un endpoint asíncrono nuevo sobre el flujo de instalación es
    pedirle un conflicto a quien está cambiando ese mismo flujo. El sitio natural
    de esta tarea es ese plan, que ya tiene el concepto de despliegue como
    entidad con estado consultable — justo el recurso que el 202 necesita.
  - ⏳ **Re-verificado (2026-08-10): la mitad del BUCLE está hecha y protegida; lo
    que queda NO es un problema de event loop.** `_run_static_analysis` corre
    fuera del bucle (`marketplace/install.py:656`, `asyncio.to_thread`) y
    `tests/unit/test_no_blocking_calls_in_event_loop.py` lo mide **por hilo**
    (compara `threading.get_ident()` dentro del analizador con el del bucle), no
    leyendo el código: no puede pasar en vacío. Lo que falta —Celery + 202— es un
    problema de **latencia de request**, no de congelación: 4 minutos de análisis
    no pertenecen a un HTTP aunque no bloqueen a nadie (timeouts de proxy,
    reintentos del cliente). Sigue sin abordarse desde aquí por la razón ya
    escrita: `marketplace/` lo está reescribiendo marketplace-v2, y meterle un
    endpoint asíncrono nuevo al flujo de instalación es pedirle un conflicto a
    quien está cambiando ese mismo flujo.
  - ⏳ **Re-verificado (2026-08-12): la premisa sigue en pie, y lo que falta es
    una DECISIÓN DE PLAN, no código.** Medido contra el árbol de hoy:
    `marketplace/install.py:655` y `:687` siguen siendo `asyncio.to_thread`, y
    en todo el repo no hay ninguna task Celery de análisis estático ni ningún
    202 en la instalación. O sea: **la mitad de event loop está hecha y
    protegida por test; la mitad de latencia de request no**.
    Lo que esta tanda añade es el planteamiento explícito para que el operador
    lo cierre en una línea: la tarea pide «un recurso de estado consultable»
    detrás de un 202, y **marketplace-v2 ya tiene esa entidad** (el despliegue,
    ADR 0142, migración 0128). Construir aquí un segundo recurso de estado sobre
    el flujo de instalación que ese plan está reescribiendo produciría dos
    máquinas de estado para lo mismo, y luego un conflicto al mezclar.
    **Recomendación: mover esta media casilla a
    [`marketplace-v2-despliegue`](./marketplace-v2-despliegue.md)** y cerrar
    aquí lo que de verdad pertenece a prod-13 (el no-bloqueo del bucle, ya
    hecho). Es una decisión de reparto de alcance entre planes: no la tomo yo.
  - ✅ **Cerrada (2026-08-19). La premisa del aplazamiento ya no aplica, y la
    entidad que el plan proponía como recurso de estado NO servía — su hermana
    sí.** Por orden:

    **La premisa, comprobada.** `marketplace-v2-despliegue` está entregado con
    sus casillas cerradas (cero `[ ]` en el fichero) y con `marketplace_deployments`
    en la migración `0128` (ADR 0142). O sea que el motivo escrito tres veces
    aquí —«no se toca porque otro plan está reescribiendo este flujo»— caducó.

    **Y la mitad que el plan daba por buena, no lo era.** `MarketplaceDeployment`
    no puede ser el recurso de estado del 202: se crea **después** de que exista
    la instalación, es por proyecto, y su `status` es `active`/`disabled`/`retired`
    —el ciclo de vida de algo vivo, no el progreso de un trabajo—. Cuando el 202
    responde no hay ningún despliegue al que apuntar. **La hermana un nivel más
    arriba sí sirve y ya existía**: `marketplace_installations`, acotada por tenant
    con RLS y con columna `status`. Es lo que la instalación crea, así que es lo
    que el 202 devuelve. Ni tabla nueva ni segunda máquina de estados (la regla de
    «NO política paralela» del ADR 0142), y **sin migración**: `status` es
    `varchar(16)` **sin CHECK** (migración `0041`, líneas 256-260), así que los dos
    valores transitorios nuevos —`analyzing`, `blocked`— se aplican en Python, y la
    guarda es `tests/unit/test_marketplace_models.py::test_installation_status_enum_values`.

    **Lo entregado.** `POST /marketplace/installations` acepta `async_gates: true`
    y entonces responde **202** con `Location`, con la fila en `analyzing`
    (`routers/marketplace/installations.py`); el productor es
    `celery_client.enqueue_marketplace_install_gates` (cola `marketplace`) y el
    consumidor `workers/marketplace_gates.py`, que corre las puertas y cierra la
    instalación llamando al MISMO `marketplace/finalize.py` que el camino
    síncrono. Ese fichero es nuevo y existe por una razón: el cierre
    (consentimiento → estado → materialización → auditoría) estaba inline en el
    router, y copiarlo al worker habría creado dos políticas de instalación
    divergentes. `GET /marketplace/installations/{id}` es el recurso de estado, y
    **no existía**: sólo había el listado, o sea que un cliente con un 202 tendría
    que sondear `?limit=100` y buscarse dentro.

    **La cola nace con consumidor, porque el ADR 0083 lo exige.** Retiró `heavy` y
    `gpu` por ser lanes declaradas sin quien las drenase; `marketplace` entra con
    su pool en los DOS composes (`workers-marketplace`, `--concurrency=1`, en
    `docker-compose.manuals.yml` y en `compose_generator.py`). No se metió en un
    pool existente porque los tres tienen su forma documentada de romperse con un
    trabajo de 4 minutos: `workers`/`workers-aux` van a `--concurrency=2` y drenan
    `test`, la cola por la que un agent-run BLOQUEADO espera su `stack_exec` (la
    auto-inanición que motivó `workers-aux`), y `workers-backup` va a
    `--concurrency=1` detrás del backup nocturno.

    **Dos cosas que se encontraron por el camino y que no eran esta tarea:**

    1. **Un fallo de fetch abortaba sin escribir su fila de auditoría.**
       `_gate_fetch` tenía un `except InstallError: raise` DELANTE del `_abort`,
       así que el fallo de fetch más común —el `ArtifactFetchError` que lanza
       `LocalArtifactFetcher` cuando no hay artefacto— abortaba **mudo**, al
       contrario de lo que promete el docstring de `_abort` y de lo que hacen las
       otras cuatro puertas. La rama parecía evitar una doble auditoría, pero
       ningún fetcher puede auditar: su `Protocol` no recibe sesión. Arreglado.
    2. **El root de artefactos no está compartido, y eso podía apagar la puerta en
       silencio.** Medido el 2026-08-19 en el stack vivo: el api-server **no monta
       ningún volumen** (`docker inspect` → `Mounts: []`) y
       `/data/agent-platform/marketplace/artifacts` no existe en el api-server ni
       en el worker; `seed_official_catalog` además no tiene ningún llamante fuera
       de los tests. Con las puertas en otro proceso, un fetch fallido es ambiguo
       —«no hay artefacto» (skip honesto del ADR 0081) vs. «existe y desde aquí no
       se alcanza»— y tragárselos como uno habría convertido el traslado en un
       apagado silencioso del análisis: verde en los tests, `skipped` en
       producción. Así que el productor observa el artefacto donde acepta la
       petición (`InstallOrchestrator.artifact_expected`) y el worker distingue:
       ausencia esperada → sigue; ausencia inesperada → `blocked`. Lo mide
       `test_un_artefacto_que_el_worker_no_alcanza_no_se_traga_como_skip`, y
       verificado rompiéndolo: con la guarda desactivada la instalación acaba
       `enabled` y el test se pone rojo. **Un root de artefactos compartido y
       durable sigue siendo la Fase B/C del ADR 0081** (registry + sandbox
       out-of-process), no esta casilla.

    **El tramo que queda, dicho en voz alta porque es el modo de fallo nº1 de esta
    base.** El 202 es **opt-in** (`async_gates: true`) y **hoy no lo pide nadie**:
    el admin-panel sigue mandando el POST sin el campo y recibiendo su 201. O sea
    que el mecanismo está entregado y sin llamante en producción — el patrón
    «mecanismo entregado, cero llamantes» del §5 de
    `verificar-antes-de-implementar`. Se dejó así a propósito y por dos razones:
    cambiar el 201 por un 202 por debajo convierte «instalado» en «aceptado» sin
    que el llamante se entere, y cablear la pantalla exige tocar
    `apps/admin-panel/lib/i18n/dictionary.ts`, que no es de este carril. Lo que
    falta es pequeño y concreto: que el botón de instalar mande `async_gates`,
    sondee `GET /marketplace/installations/{id}` y pinte los estados `analyzing` /
    `blocked`.

    - 🔎 **Corrección del 2026-08-19, comprobada antes de cablear nada: ese botón
      NO EXISTE.** Fui a añadirle `async_gates: true` y no hay ninguna acción de
      instalar en el panel. `grep` de `POST` contra `/marketplace/installations`
      en todo `apps/admin-panel/` no da un solo hit: la pantalla del marketplace
      **lista, revoca, desinstala y despliega**, pero instalar un listing sólo se
      puede hacer llamando a la API. O sea que el 202 no es «un mecanismo al que
      le falta su llamante»: es un mecanismo cuyo llamante no existiría tampoco en
      la rama síncrona, porque la instalación entera es API-only hoy.
      Eso convierte el último tramo en una casilla de producto —«el catálogo del
      panel puede instalar»— y no en el cableado de una línea. No la abro yo: es
      alcance nuevo y lo decide el operador. Lo que sí queda dicho es que la nota
      de arriba daba por hecho un botón que nadie ha escrito.

    **Lo que NO se afirma.** La puerta 5 (prueba de humo en sandbox) sólo corre
    para los niveles de confianza cuya política la exige, y en el api-server nunca
    podía correr (sin socket Docker, principio 2). La lane nueva SÍ lleva
    `DOCKER_HOST`, o sea que es el «sandbox out-of-process» que el ADR 0081 pedía,
    pero **eso no se ha ejercitado contra un Docker real**: el stack no está
    redesplegado con el compose nuevo, así que en el stack vivo de hoy la cola
    `marketplace` todavía no tiene quien la drene. Es un `docker compose up -d`
    del operador, y hasta entonces un 202 se queda en `analyzing`.
- **Tiempo**: 2 días · **Complejidad**: l
- **Tests automáticos**:
  ```yaml
  - id: auto_prod13_01_a
    runtime: python-pytest
    command: "pytest tests/integration/test_marketplace_async_gates.py -v"
  - id: auto_prod13_01_b
    runtime: python-pytest
    command: "pytest tests/unit/test_marketplace_no_sync_subprocess_in_async.py -v"
  ```

#### `task_prod13_02` — Backup: boto3/paramiko/rclone con `to_thread` y timeouts

- [x] **Título**: Envolver `destination.test_connectivity()` (`routers/backup.py:230`)
      y `destination.list_remote()` (`backup.py:361`, bucle de `_list_remote_backups`)
      en `asyncio.to_thread`, con timeouts de conexión explícitos y cortos en los
      adaptadores de `workers/backup_destinations.py` cuando se invocan desde el
      api-server. **Coordinación**: prod-04 reescribe el backup y api-9 (frontera
      apps) puede mover esto a Celery — este task garantiza solo el no-bloqueo.
  - ✅ **El plazo del lado api-server (2026-08-01):** además del `to_thread` que ya estaba, las dos
    llamadas van ahora con **plazo explícito** (`run_remote_probe`,
    `REMOTE_PROBE_TIMEOUT_S = 15 s` en `routers/backup.py`): la sonda de
    conectividad devuelve `ok=False` con motivo en vez de colgarse, y el listado
    remoto se salta el destino que no contesta en vez de esperar por él. El
    motivo de fondo, que el plan no escribía: `to_thread` usa el executor por
    defecto (`min(32, cpu+4)` hilos), así que suficientes sondas colgadas lo
    agotan y `to_thread` vuelve a hacer cola — el bloqueo entra por detrás.
    Y lo que ese plazo NO puede hacer, que es la mitad de abajo: acota la
    RESPUESTA, no el HILO, porque Python no puede matar un hilo.
  - ✅ **Cerrada (2026-08-01) — el timeout de socket ya está en los tres
    adaptadores** (`REMOTE_CONNECT_TIMEOUT_S = 10 s` en
    `workers/backup_destinations.py`, 5/5 en
    `tests/unit/test_backup_destination_connect_timeouts.py`). Uno por backend,
    porque cada uno se cuelga a su manera: **boto3** llevaba
    `connect_timeout=60` **y hasta 5 intentos** —o sea ~5 min de hilo por sonda
    contra un endpoint muerto—, así que va con `botocore.Config` de 10 s y 2
    intentos; **paramiko** necesita los TRES plazos y no solo `timeout`, porque
    `banner_timeout` y `auth_timeout` cubren al host que ABRE el socket y luego
    no se identifica o no contesta al auth (el connect ya está resuelto, el hilo
    sigue colgado); **rclone** lleva `--contimeout=10s` en todas las
    invocaciones, delante del subcomando.
    Lo que NO se toca, y conviene que esté escrito: el plazo de LECTURA. Una
    subida multiparte de varios GB por un enlace lento hace lecturas
    legítimamente largas y un `read_timeout` corto la mataría a mitad; el
    connect no tiene ese problema. Y el `config` explícito de un llamante gana
    sobre el nuestro — es un default, no una imposición (con test).
    Verificado en rojo quitando los tres usos y dejando la constante: 3 de 5.
- **Tiempo**: 0,5 días · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod13_02_a
    runtime: python-pytest
    # El nombre que el plan escribió (`tests/integration/test_backup_endpoints_nonblocking.py`)
    # nunca existió: los tests viven en unit porque lo que hay que fijar son
    # constantes y argv, no un endpoint. Se corrige aquí para que el comando
    # ejecute algo en vez de fallar con «file not found».
    command: "pytest tests/unit/test_backup_remote_probe_deadline.py tests/unit/test_backup_destination_connect_timeouts.py -v"
  ```

#### `task_prod13_03` — Vault: timeout explícito, `to_thread` y caché del secreto

- [x] **Título**: Pasar `timeout` corto al `hvac.Client` (`routers/llm_providers.py:116`),
      envolver `vault.read_secret` en `asyncio.to_thread` dentro de
      `build_llm_provider` (`llm_providers/factory.py:175-183`) y cachear la
      credencial por `provider_id` con TTL corto (30-60 s) para no ir a Vault en
      cada mensaje del chat. **Coordinación**: prod-05 (rotación) debe invalidar
      esta caché al rotar; prod-10 (Vault operable) hereda el timeout.
  - ✅ **Cerrada (2026-07-31):** la mitad del panel ya estaba (el `timeout=5 s` del
    `hvac.Client` y el `to_thread` de las cuatro llamadas al store); ahora también
    la del CHAT — `build_llm_provider` lee el secreto por `asyncio.to_thread` y lo
    cachea EN PROCESO por `provider_id` con TTL de 30 s (el extremo bajo del rango,
    porque lo rancio aquí es una credencial). Un fallo de Vault NO se cachea. El
    gancho que prod-05 debe llamar al rotar es
    `factory.invalidate_provider_secret_cache(provider_id)`; hasta que lo llame, el
    TTL acota la ventana. La credencial no va a Redis a propósito (ADR 0028: no se
    crea una segunda copia del secreto fuera del proceso que ya lo tiene).
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod13_03_a
    runtime: python-pytest
    command: "pytest tests/unit/test_vault_timeout_and_cache.py -v"
  ```

#### `task_prod13_04` — Upload de documentos en streaming con rechazo temprano

- [x] **Título**: En `POST /knowledge-bases/{kb_id}/documents`
      (`routers/knowledge_bases.py:579-598`): rechazar por header `Content-Length`
      antes de leer, leer en chunks acumulando hasta `MAX_UPLOAD_BYTES+1` (nunca
      `file.read()` completo), y validar content-type/extensión contra la lista
      de formatos soportados por Docling. Reutilizar el patrón ya existente en
      `incoming_webhooks.py:160-171`.
  - ⏳ **Pendiente (2026-08-01) — hecha la mitad de memoria, falta la de formatos:**
    la lectura ya es por trozos y para en `max_bytes + 1`
    (`routers/_uploads.py::read_capped_upload`, 7/7 en
    `tests/unit/test_capped_upload_read.py`), con rechazo temprano por
    `Content-Length` **con margen de multipart** — sin ese margen el tope real
    habría quedado silenciosamente por debajo del anunciado, porque el header
    mide el request entero y no el fichero. El header no se cree a ciegas: la
    lectura vuelve a comprobar el tamaño, y hay test de que un `Content-Length`
    mentiroso no cuela nada. **Falta** la validación de content-type/extensión
    contra los formatos de Docling: en el repo NO existe esa lista, e inventarla
    sería una restricción de producto que puede rechazar subidas legítimas hoy
    aceptadas. Necesita la lista canónica (o un ADR) antes de tocarse.
    Límite residual documentado en `_uploads.py`: con un parámetro `UploadFile`,
    Starlette ya ha parseado el multipart al `SpooledTemporaryFile` antes de que
    el handler corra; cortar antes pide un middleware ASGI, que es otra tarea.
  - ⏳ **Re-verificado (2026-08-01) — la premisa sigue en pie y ahora se sabe
    cuánto duele:** en `routers/knowledge_bases.py` no hay ni una comprobación de
    `content_type` ni de extensión, y en todo el repo sigue sin existir una lista
    canónica de formatos de Docling. Lo que sí se ha comprobado esta vez es **qué
    pasa hoy sin ella**: un formato que Docling no sabe parsear levanta
    `DoclingParseError` en el pipeline y el documento acaba en `failed` con el
    motivo (`ingestion/pipeline.py:151`). O sea, el hueco es de **UX** —el
    usuario se entera tarde y después de subir 50 MiB—, no de robustez: nada se
    cuelga y nada queda a medias. Eso rebaja la urgencia y **sube el coste del
    error**: una lista inventada rechazaría en la puerta subidas que hoy se
    aceptan y funcionan, y el fallo sería silencioso en el sentido contrario.
    **Sigue necesitando la lista canónica (o un ADR)** antes de tocarse; no es
    trabajo de código, es una decisión de producto.
  - ⏳ **Re-verificado (2026-08-10) — la lista NO se puede sacar del código, y
    ahora se sabe por qué:** Docling **no es una dependencia Python** de este
    repo. El api-server habla con `docling-serve` por HTTP
    (`ingestion/docling.py`), así que no existe ningún `InputFormat` importable
    del que derivar la lista sin inventarla. Las dos salidas posibles, para que
    el humano elija: (a) fijar la lista a mano en un ADR —barato, pero rechaza en
    la puerta formatos que hoy se aceptan y funcionan—; (b) preguntarle sus
    formatos a `docling-serve` y cachearlos —sin invención, pero mete una
    dependencia de red en la ruta de subida y hay que decidir qué se hace cuando
    no contesta—. **Lo que SÍ se ha hecho hoy es blindar la mitad terminada**:
    `tests/unit/test_no_blocking_calls_in_event_loop.py` gana dos guardas —una
    estática, que el `await file.read()` sin tope no vuelva a
    `knowledge_bases.py`, y otra **medida**, que contando bytes servidos
    comprueba que un fichero de 10 MiB con tope de 1 KiB se rechaza tras leer un
    solo trozo en vez de drenarse entero. Las dos verificadas en rojo. (La
    estática nació con un falso positivo propio: el comentario que explica por
    qué se retiró el `file.read()` lo cita literalmente, y la guarda se ponía roja
    por su propia documentación. Ahora ignora las líneas de comentario.)
  - ✅ **Cerrada del todo (2026-08-12) — la lista se le PREGUNTA a
    `docling-serve`, con caché y respaldo fijo.** Era la mitad que faltaba, y
    llevaba dos meses abierta porque las dos salidas del bloque anterior parecían
    malas: inventar la lista envejece en silencio, y preguntarla en cada petición
    mete la red dentro de una validación de entrada. La decisión firmada las
    esquiva: se pregunta **una vez, al arrancar**, y se cachea en proceso.
    - `api_server/ingestion/formats.py` lee el enum `InputFormat` del
      `/openapi.json` del propio servicio —el mismo que usa su validador—, lo
      mapea a extensiones y tipos MIME y lo guarda. El arranque lo primea desde
      el lifespan (`main.py::_prime_supported_formats`, best-effort: si reventara,
      el api-server no arrancaría por no poder validar extensiones). El camino de
      la subida lee **la caché y nunca la red**.
    - **Qué pasa si docling-serve está caído**: `FALLBACK_INPUT_FORMATS`, que es
      deliberadamente el listado ANCHO conocido de Docling. Esa asimetría es la
      decisión de diseño que hace segura la degradación: un respaldo más corto
      convertiría una caída del servicio en rechazos de subidas legítimas, que es
      el modo de fallo que esta tarea llevaba dos meses evitando. Hay test de que
      el respaldo cubre los formatos troncales. La única degradación real es que
      un formato AÑADIDO por un Docling posterior a esa lista se rechace hasta el
      siguiente arranque con el servicio vivo.
    - **La regla de admisión, escrita en el módulo: en la duda, ACEPTA.** Basta
      con que la extensión O el tipo MIME sean reconocibles, y
      `application/octet-stream` se trata como «no sé» (es lo que manda el
      navegador que no reconoce la extensión), así que decide la extensión. `.txt`
      cuelga de `md` a propósito: el texto plano es markdown válido y es lo que
      sube la suite de KBs de hoy — un allowlist ingenuo lo habría roto en
      silencio, y hay un test que lo fija.
    - **Verificar contra el servicio VIVO destapó el defecto que la tarea temía,
      y en el sentido malo.** El `docling-serve` del stack declara hoy 17
      formatos, y tres —`vtt`, `latex`, `xml_xbrl`— no estaban en el mapa
      formato→extensión, así que un `.vtt` o un `.tex` se habrían rechazado en la
      puerta **pese a que Docling los parsea**: exactamente el falso negativo que
      este bloque llevaba dos meses evitando, colado por la puerta de atrás. Ya
      están mapeados, y hay un test que exige que TODO formato del respaldo tenga
      al menos una extensión o un MIME, para que ampliar la lista sin ampliar el
      mapa no vuelva a poder pasar en silencio. El respaldo está copiado del
      enum vivo, no escrito de memoria — la lección es que un mapa incompleto
      rechaza igual que una lista corta.
    - **El rechazo es 415 y llega antes de almacenar nada**: ni blob en MinIO ni
      fila en `documents` (`tests/integration/test_document_upload_limits.py`,
      5/5). Rojo verificado neutralizando el `raise` del router: 2 fallos, uno de
      ellos con el documento ya persistido en la respuesta. En unit,
      `tests/unit/test_ingestion_supported_formats.py` 24/24, con rojo verificado
      dos veces (dejando pasar todo: 6 fallos; estrechando el `except` del
      sondeo: 2).
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod13_04_a
    runtime: python-pytest
    command: "pytest tests/integration/test_document_upload_limits.py -v"
  - id: auto_prod13_04_b
    runtime: python-pytest
    command: "pytest tests/unit/test_ingestion_supported_formats.py -v"
  ```

#### `task_prod13_05` — Cliente httpx/embedder compartido en los hot paths internos

- [x] **Título**: Sustituir el `OllamaEmbedder()` nuevo por request
      (`docs_viewer.py:125-138`, `internal_agent.py:198,295`,
      `ingestion/embeddings.py:86-89`) por un `httpx.AsyncClient` singleton de
      proceso (mismo patrón `lru_cache` que `get_redis`), con keep-alive hacia Ollama.
  - ✅ **Cerrada (2026-08-01):** los dos llamantes que faltaban
    (`chat/responder.py`, `docs_structure/kb_sync.py` ×2) van ya sobre el cliente
    compartido. Lo que lo desbloqueó fue **mudar el singleton de sitio**: vivía
    en `routers/docs_viewer.py` —su primer usuario— y desde un router ningún
    servicio lo podía importar sin invertir la dependencia, así que la vía
    cómoda era seguir construyendo uno propio. Ahora vive en
    `ingestion/embed_client.py` con el helper `shared_ollama_embedder()`, y NO
    se re-exporta desde el router: dos nombres serían dos `lru_cache`, o sea dos
    clientes, y el singleton dejaría de serlo justo cuando alguien crea que lo
    tiene. Un test lo fija buscando la definición por todo el árbol.
    Detalle que hace segura la mudanza: como el cliente se INYECTA,
    `_owns_client` es `False` y `aclose()` es un no-op — sin eso, el
    `if own_embedder: await …aclose()` que `kb_sync` ya tenía habría matado el
    pool de todos los demás en la primera sincronización, un fallo peor que el
    churn que se venía a arreglar. Hay test de eso.
    `seeds/catalog_ingestion.py` **queda fuera a propósito** y con el motivo
    escrito en la guarda: crea el embedder una vez por PASADA del seed y lo
    cierra al terminar, así que no hay churn por request.
    La guarda de código fuente (AST) enumera los módulos de camino caliente y
    falla si alguno vuelve a `OllamaEmbedder()` a pelo; sobre el árbol anterior
    señalaba las tres líneas exactas (`responder.py:976`,
    `kb_sync.py:222,337`), que es su verificación en rojo.
- **Tiempo**: 0,5 días · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod13_05_a
    runtime: python-pytest
    command: "pytest tests/unit/test_shared_embedder_client.py -v"
  ```

### Fase B — Pool y transacciones

#### `task_prod13_06` — Tuning explícito del pool async como settings

- [x] **Título**: Exponer `pool_size`/`max_overflow`/`pool_timeout`/`pool_recycle`
      como settings de entorno y aplicarlos en `get_engine` y `get_admin_engine`
      (`db/session.py:21-58`), con los defaults de la decisión clave 4 y métrica
      de saturación del pool expuesta (coordinación con prod-08 para la alerta).
  - ✅ **Cerrada (2026-08-01):** los cuatro settings ya estaban; faltaba la
    métrica, y la premisa que la bloqueaba dejó de ser cierta — **el api-server
    sí publica métricas** desde prod-08 Fase B (`api_server/metrics.py`, la nota
    anterior es del día antes). El colector vive en `db/pool_metrics.py` y
    publica `agentic_db_pool_connections{engine,state}` +
    `agentic_db_pool_capacity{engine}` para los DOS engines (app y admin).
    Tres decisiones que conviene tener escritas: se mide **en el scrape**
    leyendo el pool vivo de SQLAlchemy, no instrumentando el checkout, así que
    en régimen normal cuesta cero; se publica `capacity` = `pool_size +
max_overflow` porque sin denominador la alerta de saturación de prod-08 no
    es escribible; y se registra desde los **sessionmakers** en vez de desde
    `install_metrics`, para que la serie exista en cualquier proceso que abra
    sesiones (CLI, seeds), no solo en el que monta FastAPI. El test que manda es
    el del cableado: quitando la llamada de `get_sessionmaker` se pone rojo — un
    colector que nadie registra publica exactamente nada.
- **Tiempo**: 0,5 días · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod13_06_a
    runtime: python-pytest
    command: "pytest tests/unit/test_engine_pool_settings.py -v"
  ```

#### `task_prod13_07` — La transacción por request no abarca llamadas LLM ni embeds

- [x] **Título**: Implementar la decisión clave 2: `POST /assistant/chat`
      (`routers/assistant.py:171-199`) resuelve datos, cierra la transacción de
      `open_tenant_session` (`auth/deps.py:242-252`) y ejecuta `run_assistant_turn`
      sin conexión retenida — las tools reciben un session-factory tenant-aware en
      `tool_ctx` y abren sesiones cortas; `GET /knowledge-bases/{id}/search`
      (`knowledge_bases.py:294-301`) embebe contra Ollama fuera de la sesión.
      Persistencia del resultado en sesión nueva.
  - ⏳ **Pendiente (2026-07-31):** sin empezar — la sesión de
    `open_tenant_session` sigue viva durante `run_assistant_turn` y las tools no
    reciben ningún session-factory tenant-aware en `tool_ctx`.
  - ⏳ **Re-verificado (2026-08-01), sigue sin empezar y sigue siendo la tarea
    más cara del plan:** `routers/assistant.py:550,753` awaitan
    `run_assistant_turn` con la sesión del request abierta. Es 2,5 días, cambia
    el contrato de `tool_ctx.session` para TODAS las tools del asistente y su
    riesgo nº 1 está escrito en este mismo plan: si el session-factory no replica
    el `set_config` de tenant en cada sesión corta, se abre un agujero de RLS.
    No es trabajo de una tanda paralela con la propiedad repartida — pide su
    rama, la suite de integración del asistente entera antes y después, y quien
    la haga tiene que poder tocar `auth/deps.py`, que aquí es de otro carril.
  - ⏳ **Sigue abierta (2026-08-10), pero con el diseño ya derivado y con una
    corrección: NO hace falta tocar `auth/deps.py`.** Verificado leyendo el
    código: la costura del despacho de tools es ÚNICA —
    `assistant/tools.py::run_assistant_tool`, por donde pasa toda llamada— y
    `ctx.session` sólo se usa en 11 sitios, todos dentro de `tools.py`. El diseño
    que cierra el riesgo nº 1 sin escribir una sola línea de RLS nueva: el
    session-factory es `lambda: open_tenant_session(principal)` **tal cual**, o
    sea el mismo código que ya hace los dos `set_config` de `app.user_id` /
    `app.tenant_id`; al no haber una segunda implementación, no hay una segunda
    que se olvide del `set_config`. `run_assistant_tool` abre la sesión corta y
    entrega al tool un ctx con ella ya enlazada, así que los 11 sitios no cambian.
    Los endpoints quedan en tres fases: sesión corta para resolver (identidad,
    memorias, conversación, historial, rate limit) → `run_assistant_turn` **sin
    conexión retenida** → sesión corta para persistir.
    **Por qué no se ha implementado hoy y no por falta de tiempo:** es la única
    tarea del carril cuyo fallo se manifiesta como fuga entre tenants, la suite
    que la protege son 7 ficheros de integración del asistente, y durante esta
    tanda hay cuatro carriles más escribiendo en el árbol — de hecho uno rompió
    `routers/sso` a mitad de sesión y dejó el api-server sin importar durante
    minutos. Entregar un refactor de RLS que no he podido correr entero contra un
    árbol estable es exactamente lo que este plan no puede permitirse. Pide su
    rama, como ya decía la nota anterior; lo que cambia es que **ya no pide la
    propiedad de `auth/deps.py`**.
  - ⏳ **Sigue abierta (2026-08-12), y ahora se sabe qué le falta EXACTAMENTE.**
    Re-verificado: `routers/assistant.py:717` y `:501` siguen recibiendo
    `session: AsyncSession = Depends(get_tenant_session)`, y `AssistantToolContext`
    (`assistant/tools.py:67-76`) sigue llevando una `session` viva.
    El diseño de la nota anterior se confirma y se le añade el obstáculo que no
    estaba escrito, que es el que la hace cara: **no basta con no usar la sesión
    dentro del turno; hay que dejar de PEDIRLA**. `Depends(get_tenant_session)`
    abre la sesión y su transacción antes de que el handler corra, así que
    mientras el endpoint la declare como dependencia la conexión sigue retenida
    durante el turno LLM aunque nadie la toque. Y no es sólo el parámetro del
    handler: `require_assistant_access` (`:246`) y
    `enforce_assistant_chat_rate_limit` (`:178`) dependen de ella también, así
    que la cadena entera tiene que pasar a abrir sesiones cortas con
    `open_tenant_session(principal)`.
    **Por qué no se entrega en esta tanda, y no es por tiempo:** es la única
    tarea del plan cuyo fallo se manifiesta como fuga entre tenants, la protegen
    7 ficheros de integración del asistente, y esta tanda tiene cuatro carriles
    más escribiendo en el árbol —con un redespliegue real al final—. Entregar un
    cambio de la cadena de autenticación del asistente que no se ha podido correr
    entero contra un árbol estable es exactamente lo que no se puede permitir el
    día que el operador levanta el stack. **Pide su rama y su tanda.**
  - ✅ **Cerrada (2026-08-19), con la suite de integración del asistente corrida
    entera ANTES y DESPUÉS del cambio: 57 verdes → 57 verdes, más los 11 tests
    nuevos de los dos ficheros que declara la casilla.** El diseño de
    las notas de arriba se confirmó al completo, incluido el obstáculo del
    2026-08-12: lo que retenía la conexión durante el turno no era el handler,
    era la **cadena de dependencias**. `require_assistant_access` pedía
    `Depends(get_tenant_session)`, y una dependencia con `yield` abre su sesión
    antes de que el endpoint corra y la cierra después de enviar la respuesta —
    o sea que quitar el parámetro del handler no habría cambiado nada medible.
    Está comprobado en rojo: reponiendo SÓLO esa dependencia en la puerta, con el
    handler ya limpio, el test vuelve a observar `[1, 1]` conexiones retenidas
    durante el turno y el de concurrencia recae en el `TimeoutError` de
    producción. Ése es el hallazgo que un test de firma del endpoint no habría
    visto nunca.

    Lo que cambió respecto al diseño escrito, y por qué: el contexto de tools se
    partió en **dos tipos** en vez de hacer `session` opcional.
    `AssistantToolContext` sigue siendo el contexto ENLAZADO que recibe cada tool
    —sus once implementaciones y toda la suite previa quedan intactas— y el nuevo
    `AssistantToolScope` es lo que el endpoint entrega al grafo: tenant, usuario y
    la fábrica. Con una `session: AsyncSession | None` compartida, mypy strict
    marcaba los once cuerpos de tool (`Item "None" ... has no attribute
"execute"`), y la única salida barata habría sido un `type: ignore` por tool,
    o sea trece renuncias a la comprobación de tipos para ahorrar una clase.

    Lo que NO cambió, porque es el cierre del riesgo nº 1: la fábrica es
    `lambda: open_tenant_session(principal)` tal cual. Los dos tests de fuga
    fallan en las DOS direcciones, comprobado rompiendo el despacho: con una
    sesión BYPASSRLS el tenant A ve el proyecto de B (`total: 2`), y con una
    sesión de `app_user` sin los `set_config` el tenant deja de ver lo suyo
    (`total: 0`) y la tool de escritura muere con «new row violates row-level
    security policy». Una guarda que sólo comprobara la ausencia de fuga habría
    pasado en verde vacío ante el segundo caso.

    Tres decisiones de comportamiento que conviene tener escritas:

    1. **El hilo se crea al persistir, no al resolver.** Con una transacción
       única daba igual: si el proveedor fallaba, el rollback se llevaba también
       el hilo vacío. Troceada, crearlo antes del turno dejaría un hilo huérfano
       en la lista del usuario por cada error de LLM.
    2. **`_persist_turns` toma ids, no la instancia ORM del hilo.** Cargada en la
       sesión de resolución, en la de persistencia está desligada y asignarle
       `updated_at` no escribe nada — un fallo que no da error.
    3. **La tool de escritura commitea al volver de la tool**, no al final del
       turno. Es más honesto (el asistente ya dijo «lo he recordado») y el dedup
       sigue funcionando entre sesiones cortas.

    Alcance real entregado, que va más allá de los dos endpoints del enunciado
    porque comparten la misma costura: `/assistant/chat`, `/assistant/chat/stream`
    —donde era peor: la sesión vivía todo el SSE—, el WebSocket de voz
    (`_respond` retenía una conexión durante STT+cerebro+TTS, decenas de
    segundos) y `GET /knowledge-bases/{id}/search`, que embebe la query contra
    Ollama y ahora lo hace fuera de toda transacción. En las DOS puertas que
    había que dejar sin sesión retenida —`require_assistant_access` y la del
    search de KB— se llama al `require_tenant_admin` / `require_tenant_member`
    originales como función normal con la sesión corta ya abierta: sus
    `Depends(...)` sólo los interpreta FastAPI, y así no aparece un segundo
    predicado de autorización que pueda divergir. **`auth/deps.py` no se tocó**,
    como decía la nota del 2026-08-10.

    Y el test que vale más que las aserciones de pool, porque traduce la
    propiedad a su consecuencia: con `pool_size=1, max_overflow=0` dos chats
    simultáneos —solapados de verdad con una `asyncio.Barrier`— salen los dos.
    Antes, el segundo no llegaba ni a pasar la puerta.

    Arista lateral encontrada y NO arreglada (no es de esta tarea): los dos
    turnos de un mensaje se insertan en el mismo flush y comparten `created_at`
    al microsegundo, así que el orden entre el `user` y el `assistant` de un
    mismo turno no es determinista — `_conversation_history` ordena DESC y
    revierte, `list_assistant_turns` ordena ASC, y con el empate pueden dar
    órdenes distintos. Se descubrió porque un assert por índice del test nuevo
    salió rojo por eso y no por el cambio. El test se hizo insensible al orden;
    el empate sigue ahí.
- **Tiempo**: 2,5 días · **Complejidad**: l
- **Depende de**: `task_prod13_06`
- **Tests automáticos**:
  ```yaml
  - id: auto_prod13_07_a
    runtime: python-pytest
    command: "pytest tests/integration/test_assistant_no_tx_during_llm.py -v"
  - id: auto_prod13_07_b
    runtime: python-pytest
    command: "pytest tests/integration/test_assistant_tools_session_factory.py -v"
  ```

#### `task_prod13_08` — `NullPool` en los engines por tarea Celery

- [x] **Título**: Pasar `poolclass=NullPool` a los `create_async_engine` de las
      tareas Celery (`workers/ingestion.py:127,192`, `workers/maintenance.py:287`)
      para que cada tarea cueste exactamente 1 conexión sin pool ocioso (perf-11).
- **Tiempo**: 0,25 días · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod13_08_a
    runtime: python-pytest
    command: "pytest tests/unit/test_worker_engines_nullpool.py -v"
  ```

#### `task_prod13_09` — Seed runner: una transacción por seed

- [x] **Título**: Trocear `python -m api_server.seeds` (`seeds/__main__.py:46-112`)
      en una transacción por seed, separando `seed_catalog_ingestion`
      (`catalog_ingestion.py:55`, embeds por red) a su propia transacción/lote por
      documento. La idempotencia existente (uuid5, hash de corpus) hace el cambio seguro.
  - ✅ **Cerrada (2026-08-01):** los ~20 seeds son ahora una tabla `SEED_STEPS` y
    `run_seeds` abre **una transacción por paso**; el catálogo va aparte con
    `seed_catalog_ingestion_per_document`, que commitea **por documento** (es el
    único seed que habla por red). Lo que se gana no es rendimiento sino que un
    arranque con Ollama caído deje de tirar los 17 seeds anteriores: antes, un
    fallo en la ingesta del corpus —la parte prescindible— dejaba la instalación
    sin agentes, sin equipos y sin tools. El test que muerde es ése: con una
    transacción global, `organizations` y `agents` quedan a 0 tras el fallo
    (comprobado en rojo). Y hay un tercer test que fija el ORDEN de los pasos
    contra las FKs, que es lo que un refactor así rompe sin que nada más se
    entere hasta el siguiente arranque en limpio.
- **Tiempo**: 0,5 días · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod13_09_a
    runtime: python-pytest
    command: "pytest tests/integration/test_seeds_transaction_per_seed.py -v"
  ```

### Fase C — Índices y búsqueda

#### `task_prod13_10` — Índice FTS de chunks coherente con la query `es_unaccent`

- [x] **Título**: Migración Alembic que reconstruya `ix_chunks_content_fts` con
      `to_tsvector('public.es_unaccent', content)` (réplica de lo que 0079 hizo
      con memory_entries) y unificar `bm25_chunks` (`rag/search.py:103-113`) con
      la misma configuración que usa el preview (`search.py:349-361`), para que
      agente y operador vean los mismos resultados. Downgrade real.
  - ✅ **Ya estaba hecha antes de este plan (verificado 2026-07-31):** la
    migración `0107_chunks_fts_es_unaccent` reconstruyó el índice con
    `public.es_unaccent` y su downgrade restaura el `'simple'` de la 0022;
    `rag/search.py` unifica ambas rutas sobre `_TS_CONFIG`.
- **Tiempo**: 0,5 días · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  # CORREGIDO el 2026-08-19: `tests/integration/test_chunks_fts_index_es_unaccent.py` no
  # ha existido nunca. Las dos mitades de la casilla están probadas dentro de
  # `test_bm25_search.py`, junto al resto de la recuperación por texto en vez de en un
  # fichero suelto: `test_chunks_fts_index_uses_es_unaccent` lee el `indexdef` real de
  # `ix_chunks_content_fts` en `pg_indexes` (la migración `0107`), y
  # `test_spanish_accents_and_stemming_match` prueba la unificación de `bm25_chunks` con
  # una consulta SIN acentos y con otra inflexión, que sólo casa con unaccent+spanish_stem.
  # Comprobado que muerde: `_TS_CONFIG = "public.es_unaccent"` → `"simple"` en
  # `rag/search.py` y saltó `test_spanish_accents_and_stemming_match` — la divergencia
  # índice/consulta que la casilla existe para cerrar. Restaurado con
  # `git show HEAD:… > …`; 6 verdes.
  - id: auto_prod13_10_a
    runtime: python-pytest
    command: "pytest tests/integration/test_bm25_search.py -v"
  ```

#### `task_prod13_11` — Índice `(tenant_id, created_at)` en executions + predicado sargable

- [x] **Título**: Migración con índice compuesto `(tenant_id, created_at)` sobre
      executions y reescritura de `_spend_usd_in_window`
      (`budgets/consumption.py:216-224`) como rango sargable sobre TIMESTAMPTZ
      (sin `func.date()`), definiendo la zona horaria del corte. **Coordinación**:
      prod-06 cablea el sweep de presupuestos (db-1) que depende de este índice.
  - ✅ **Cerrada (2026-08-01):** el índice ya estaba (0126); ahora lo usa alguien.
    `spend_in_window_stmt` construye el rango semiabierto sobre TIMESTAMPTZ y el
    corte se fija **en UTC explícito**, que era la parte no escrita: `date()` se
    evalúa en la zona horaria de la SESIÓN, así que en un PostgreSQL que no
    estuviera en UTC un gasto de las 02:00 del día 1 se contabilizaba en el
    período anterior. El test lo demuestra poniendo la sesión en
    `America/New_York` — con el código viejo se pierden 7,00 USD; con el nuevo,
    no. El `EXPLAIN` lleva su propio CONTROL: explica también la forma con
    `date()` y exige que ESA no llegue al `Index Cond`, porque si no, la
    aserción de que la nueva sí llega no estaría midiendo nada.
- **Tiempo**: 0,5 días · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod13_11_a
    runtime: python-pytest
    command: "pytest tests/integration/test_budget_window_sargable.py -v"
  ```

#### `task_prod13_12` — Recall vectorial multi-tenant: iterative scan + test de recall

- [x] **Título**: Mitigar el post-filtrado HNSW (db-6): activar
      `SET hnsw.iterative_scan = relaxed_order` y `hnsw.ef_search` configurable en
      la sesión de `vector_chunks` (`rag/search.py:142-149`), y añadir test de
      recall con dos tenants desbalanceados (corpus 95/5) que falle si el tenant
      pequeño recibe 0 resultados. Redactar ADR propuesto para índices parciales/
      particionado por tenant (decisión futura, no se implementa).
  - ✅ **La mitigación (2026-08-01):** `api_server/rag/hnsw.py` fija `hnsw.iterative_scan =
relaxed_order` y un `hnsw.ef_search` configurable (100) en la transacción de
    `vector_chunks`. Dos decisiones que conviene tener escritas: **`SET LOCAL`** y
    no `SET`, porque las conexiones del pool se reutilizan entre tenants y un
    `SET` a secas dejaría el parámetro pegado a la conexión; y un **SAVEPOINT**
    alrededor, porque con pgvector < 0.8 ese GUC no existe y el error abortaría
    la transacción entera — sin el savepoint, arrancar contra una pgvector
    antigua no degradaría el recall: tumbaría la búsqueda.
    `tests/integration/test_vector_recall_multitenant.py` fija el CABLEADO (2/2;
    quitando la llamada de `vector_chunks` se pone rojo, comprobado).
  - ✅ **El test de recall (2026-08-01):**
    `tests/integration/test_vector_recall_desbalanceado.py`. La nota anterior
    daba el caso por irreproducible a escala de test —«PostgreSQL no elige el
    índice HNSW con mil filas, el control devolvía 10 donde debía devolver
    0»—. **El diagnóstico era correcto y la conclusión no**: faltaba una
    línea, `SET LOCAL enable_seqscan = off`. Quitándole al planificador la
    alternativa que el tamaño de juguete le regalaba, el defecto se reproduce
    con **2.030 filas** de 768 dimensiones y el test tarda un minuto. Forzarlo
    no es hacer trampa: en producción, con corpus real, el planificador elige
    el índice por su cuenta — que es justo el escenario de db-6.
    Medido antes de escribir el test (pgvector 0.8.2, 2.000 chunks del tenant
    grande contra 30 del pequeño, todos más cerca del query los del grande):
    `ef_search=40` sin iterative → **0** · `ef_search=100` sin iterative →
    **0** · `ef_search=100` con iterative → **10**. La fila del medio es la
    que da sentido al test: descarta que lo que arregla el recall sea el
    `ef_search` más alto. El test lleva su CONTROL dentro (el arco sin
    mitigación tiene que dar cero, o su mitad verde no demuestra nada) y se
    verificó en rojo desactivando `iterative_scan`.
  - ✅ **Cerrada del todo (2026-08-01):** faltaba el ADR de la decisión clave 5 y
    ya está escrito —
    [ADR 0152](../05-architecture-decisions/0152-recall-vectorial-multitenant-hnsw.md),
    en `proposed`, que es lo que esta tarea pedía («redactar ADR propuesto …
    decisión futura, no se implementa»). Lleva las tres opciones costeadas
    (statu quo · índices HNSW parciales por tenant 3-5 d · particionar `chunks`
    por `tenant_id` 5-8 d) y la medición de arriba como evidencia.
    Dos cosas que salieron al escribirlo y no estaban en el plan: **(1)** la
    mitigación no es gratis a largo plazo — `iterative_scan` paga el
    desequilibrio en LATENCIA, y esa curva crece con el corpus del tenant MÁS
    GRANDE, sobre el que el tenant pequeño no tiene ni control ni visibilidad;
    **(2)** se recomienda **A ahora, C con un disparador medible escrito** (el
    tenant mayor por encima del 60 % del corpus Y más de ~200.000 chunks), y se
    desaconseja B pese a ser más barata, porque mete DDL en el alta de tenant y
    rompe la propiedad «esquema compartido + RLS» del ADR 0028. El ADR nombra
    además **el único trabajo que pide hacer ya**: sin una métrica del reparto
    del corpus por tenant, el disparador no es comprobable y la decisión no se
    puede tomar nunca.
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod13_12_a
    runtime: python-pytest
    command: "pytest tests/integration/test_vector_recall_multitenant.py -v"
  ```

#### `task_prod13_13` — Unicidad por tenant en teams/skills/agents + tipos coherentes

- [x] **Título**: Replicar el patrón 0077 (`uq_tools_tenant_name`): índice único
      parcial `WHERE deleted_at IS NULL` sobre `(tenant_id, name)` para teams
      (`domain.py:634-640`), skills y agents, con dedup "latest wins" previo en la
      migración. En la misma pasada: `source_size_bytes` a `BigInteger` y
      `Plan.created_by` a `UUID | None` (db-9, hallazgos fusionados).
  - ℹ️ **Desviación aceptada (2026-07-31):** `agents` NO lleva un
    `(tenant_id, name)` sino DOS índices parciales partidos por `project_id`
    (`uq_agents_tenant_project_name_live` y `uq_agents_tenant_name_global_live`):
    un fork `project_local` conserva a propósito el nombre de su plantilla
    `global_tenant_template` y el índice ingenuo lo habría prohibido. Migración
    0126, con round-trip de downgrade probado.
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod13_13_a
    runtime: python-pytest
    command: "pytest tests/integration/test_tenant_name_uniqueness.py -v"
  ```

### Fase D — Retención y backfill

#### `task_prod13_14` — Job beat de purga de filas soft-deleted

- [x] **Título**: Nueva task en `workers/maintenance.py` + entrada en
      `beat_schedule.py`: purga física de filas con `deleted_at` anterior a la
      ventana de gracia (platform setting, default 30 días), cascada vía las FKs
      `ON DELETE` existentes (KBs→documents→chunks con embeddings, proyectos→
      plans/tasks/executions), con modo dry-run y log de recuento por tabla.
      **Coordinación**: prod-06 corrige antes el orden blob/commit (db-3) — la
      purga es quien borra los blobs de MinIO de documentos soft-deleted.
  - ✅ **Cerrada (2026-08-01):** `workers/maintenance/purge.py` +
    `PURGE_SOFT_DELETED_BEAT_ENTRY` (diario 04:30, cola `ingestion`) + export en
    el façade `workers.maintenance` —los tres, porque una task sin el tercero es
    la trampa de `gotchas/beat-entry-whose-task-nobody-imports.md`: beat la
    encola y el worker la rechaza con `NotRegistered`, en silencio.
    Cuatro decisiones que el plan no fijaba y valen más que el código:
    **(1) Arranca en DRY-RUN y ese es el default de la función**, no solo de la
    configuración. El riesgo 3 del plan es irreversible; encender el borrado real
    es del operador (`purge.soft_deleted_enabled`, o
    `celery call workers.purge_soft_deleted --kwargs '{"dry_run": false}'` para
    una primera pasada vigilada).
    **(2) El alcance es una allowlist de DOS raíces, no un barrido.** Hay 35
    tablas con `deleted_at`; purgar por tener la columna habría incluido
    `organizations` (dar de baja un tenant) y `users` (global desde el ADR 0137).
    Las 33 exclusiones llevan **el motivo escrito**, y un test exige que
    allowlist ∪ exclusiones cubran el universo: una tabla nueva con `deleted_at`
    obliga a decidir en vez de colarse en cualquiera de los dos sentidos.
    **(3) Las claves de los blobs se leen ANTES del DELETE.** Después de la
    cascada las filas `documents` ya no existen y no habría forma de saber qué
    borrar en MinIO — es el mismo orden que el hallazgo db-3 arregla en el
    borrado interactivo.
    **(4) Cota de 500 raíces por pasada**, para que la primera ejecución sobre
    un histórico grande no monopolice la cola.
    El test que manda es el de la ventana de gracia, escrito primero: ignorando
    el corte, se pone rojo (comprobado).
- **Tiempo**: 1,5 días · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod13_14_a
    runtime: python-pytest
    command: "pytest tests/integration/test_purge_soft_deleted.py -v"
  ```

#### `task_prod13_15` — Retención de tablas append-only (steps_log, audit_log, guardrail_events)

- [x] **Título**: Redactar el ADR de retención (decisión clave 3) y, una vez
      decidido por humano, implementar la task beat de retención: compactar/
      archivar `executions.steps_log` (`db/domain.py:1060`) de runs antiguos y
      aplicar la retención decidida a `audit_log`, `guardrail_events` y
      `notifications`. La task entra detrás del flag/setting que el ADR defina.
  - ✅ **Cerrada EN NEGATIVO (2026-08-01) — el ADR se firmó y descartó la task
    que esta casilla pedía.** El
    [ADR 0151](../05-architecture-decisions/0151-retencion-de-tablas-append-only.md)
    pasó a `accepted` el 2026-08-01 con la **opción C: particionado nativo por
    rango en las cinco tablas, sin borrar nada**. La firma se apartó de la
    recomendación del propio ADR (un híbrido A+C) a sabiendas del coste, y con
    ella **deja de existir el objeto de esta tarea**: no hay «retención decidida»
    que aplicar ni plazo que acertar, porque lo que se compró es justamente la
    opción que no obliga a fijar ninguno. Una task beat que borre filas por
    antigüedad hoy contradiría la decisión firmada.
    La mitad que SÍ sigue viva —dejar de arrastrar la historia— es el
    particionado, y tiene plan propio:
    [`part-01-particionado-append-only`](./part-01-particionado-append-only.md)
    (8 días-persona, fase 1 ya entregada con la migración 0131 de
    `guardrail_events`). Lo entregado por esta casilla es **el ADR**, que era su
    primera mitad literal («redactar el ADR de retención»), con la medición que
    dio la escala: `steps_log` es el **76 %** de la tabla `executions` (1.672 KiB
    de 2.208), 9,5 KiB de media por run. La conclusión que conviene no perder:
    hoy el problema no es el disco, es el **tiempo de restauración** — el bundle
    de backup crece con la historia, no con el estado.
  - ℹ️ **Nota histórica**: entre el momento de redactarlo y la firma, esta
    casilla estuvo bloqueada a propósito — cuánto se retiene `audit_log` es
    política de cumplimiento, no una decisión técnica disfrazada, y el ADR se
    dejó en `proposed` con sus tres opciones costeadas hasta que un humano
    eligiera. Firmó opción C.
- **Tiempo**: 1,5 días · **Complejidad**: m
- **Depende de**: `task_prod13_14` (comparte infraestructura de purga)
- **Tests automáticos**:
  ```yaml
  - id: auto_prod13_15_a
    runtime: python-pytest
    command: "pytest tests/integration/test_append_only_retention.py -v"
  ```

#### `task_prod13_16` — Embeds por lotes + backfill de chunks sin vector

- [x] **Título**: Trocear el embed de ingesta en lotes (p. ej. 64 chunks/request,
      `ingestion/pipeline.py:123-147`) y añadir task beat `backfill_chunk_embeddings`
      gemela de `backfill_memory_embeddings` (`workers/maintenance.py:249-368`)
      sobre `chunks WHERE embedding IS NULL`, reutilizando el patrón
      `FOR UPDATE SKIP LOCKED` + throttle + platform settings ya existente. Cierra
      el hueco "documento verde en la UI pero invisible para el RAG vectorial".
  - ✅ **Cerrada (2026-07-31):** el backfill YA EXISTÍA (P1-11b) pero sin un solo
    test propio; ahora lo tiene, y muerde: quitarle el `documents.deleted_at IS NULL`
    pone 4 de sus 6 tests en rojo. La mitad que faltaba de verdad —el troceo de la
    ingesta— está hecha con `EMBED_BATCH_SIZE = 64`. Dos decisiones que el plan no
    fijaba y conviene tener escritas: un lote que falla se pierde **solo a sí mismo**
    (antes un `EmbeddingError` dejaba el documento entero sin vector), y un embedder
    que devuelve menos vectores de los pedidos descarta ese lote en vez de emparejar
    por posición — eso último además arregla un `ValueError` que escapaba a Celery
    desde el `zip(strict=True)`, pese a que el pipeline promete no levantar nunca.
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod13_16_a
    runtime: python-pytest
    command: "pytest tests/integration/test_backfill_chunk_embeddings.py -v"
  - id: auto_prod13_16_b
    runtime: python-pytest
    command: "pytest tests/unit/test_ingestion_embed_batching.py -v"
  ```

### Fase E — Endpoints: paginación, caché y concurrencia

#### `task_prod13_17` — Paginar conversaciones, documentos de KB y citas (sin vector)

- [x] **Título**: Aplicar `limit_query()/offset_query()` (`routers/_pagination.py`)
      a `GET /projects/{id}/conversations` (`conversations.py:171`),
      `GET /knowledge-bases/{kb_id}/documents` (`knowledge_bases.py:637`) y
      `GET /documents/{id}/citations` (`knowledge_bases.py:733-746`, paginado por
      `ordinal`). En citations, seleccionar columnas explícitas o declarar
      `Chunk.embedding` como `deferred()` para no arrastrar el vector(768) por
      fila (perf-8).
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  # CORREGIDO el 2026-08-19: `tests/integration/test_pagination_conversations_docs_citations.py`
  # no ha existido nunca. El test real es `tests/unit/test_row_lock_and_pagination.py`,
  # que nombra `task_prod13_17` en su primera línea, y es UNIT a propósito y con razón:
  # lo que hay que verificar es **la firma que FastAPI publica** (que `limit`/`offset`
  # existen y llevan los bounds compartidos `ge=1` / `le=MAX_PAGE_SIZE`, que es lo que
  # impide un `?limit=1000000`) y **el SQL que se emite**, y ninguna de las dos necesita
  # Postgres. Cubre los tres endpoints parametrizados —`list_conversations`,
  # `list_documents`, `get_document_citations`— más los dos contornos del visor de citas:
  # default = `MAX_PAGE_SIZE` (con el blando, un PDF de 2.000 chunks se truncaría en
  # silencio) y `total`/`has_more` en el payload.
  # Comprobado que muerde: `limit: int = limit_query(default=MAX_PAGE_SIZE)` →
  # `limit: int = 500` en `get_document_citations` y saltaron dos, entre ellas la
  # parametrización de `get_document_citations` en
  # `test_listing_endpoints_accept_bounded_limit_and_offset`.
  # ⚠️ HALLAZGO, sin arreglar aquí porque no es de este carril:
  # `test_citations_query_does_not_select_the_embedding_vector` NO compila el SELECT del
  # router — reconstruye uno equivalente y luego hace `grep` del texto
  # `select(Chunk).where(Chunk.document_id == document_id)` sobre el fuente. Coge la
  # regresión que nombra (volver a la entidad entera), pero se le escapa añadir
  # `Chunk.embedding` a la lista explícita de columnas: se probó y siguió verde. Es
  # `verificar-antes-de-implementar` §4 con otro disfraz.
  - id: auto_prod13_17_a
    runtime: python-pytest
    command: "pytest tests/unit/test_row_lock_and_pagination.py -v"
  ```

#### `task_prod13_18` — No materializar `steps_log` en listados/exports de runs

- [x] **Título**: En `tenant_stats.py` (`_fetch_runs:557-592`, export `:461`,
      `_last_model_expr`, `_token_split:774-801`): seleccionar solo columnas
      escalares (o `deferred()` en `Execution.steps_log`, `domain.py:1058-1062`)
      y materializar `last_model`/`tokens_in`/`tokens_out` como columnas
      denormalizadas al cerrar el run (patrón ya existente con
      `total_tokens`/`total_cost_usd`), con migración + backfill.
  - ⏳ **Pendiente (2026-08-01) — hecha la mitad que NO pide migración, y es la
    que quemaba memoria:** `runs_select` (antes `_fetch_runs`) selecciona ahora
    **columnas escalares explícitas** en vez de la entidad `Execution` entera, así
    que el listado y el export dejaron de materializar `steps_log` en el proceso
    del api-server. La escala, medida el 2026-08-01: `steps_log` es el **76 %** de
    la tabla `executions`, 9,5 KiB de media por run; con `MAX_EXPORT_ROWS = 5000`
    eso eran del orden de **50 MiB de JSONB** cruzando la red y materializándose en
    Python para producir un CSV que no publica ni un byte de esa traza. Los 20
    tests de integración de stats (export, toggle de divisa, dashboard) siguen
    verdes: son la red contra el desplazamiento-de-uno que este refactor invita.
    **De regalo, la paginación por keyset**: `?cursor=` opaco + cabecera
    `X-Next-Cursor`, sobre `(created_at, id)` con comparación de FILA para que no
    se salte ni repita filas del mismo instante. El `offset` sigue funcionando —
    quitarlo rompería a los clientes de hoy.
    **Falta** lo que exige migración y este carril no puede crear: las columnas
    denormalizadas `last_model` / `tokens_in` / `tokens_out` con su backfill.
    Hasta entonces `_last_model_expr` y `_token_split` siguen expandiendo
    `steps_log` con `jsonb_array_elements` — pero eso lo recorre PostgreSQL y
    devuelve un `text`, que es una factura muy distinta.
  - ⏳ **Sigue abierta (2026-08-01), y el ADR 0151 no la desbloquea:** el
    particionado que el operador firmó reparte `executions` por rango de
    `created_at`; las columnas denormalizadas siguen siendo columnas nuevas, o
    sea una migración. Este carril tiene prohibido crearlas (la propiedad de las
    migraciones es de otro), así que la mitad que falta se queda pendiente **a
    propósito y no por olvido**. Ojo al orden cuando se retome: con `executions`
    ya particionada, añadir columnas y backfillear es más caro que hacerlo antes
    — conviene coordinarlo con
    [`part-01`](./part-01-particionado-append-only.md), no después.
  - ✅ **La paginación por keyset queda COMPLETA (2026-08-10): faltaba el export.**
    El explorador ya la tenía; el export no, y ahí el agujero no era de
    rendimiento sino de **pérdida silenciosa de datos**: todo lo que pasara de
    `MAX_EXPORT_ROWS` (5.000) se quedaba fuera sin que la respuesta lo dijera, y
    el docstring mandaba al usuario a «paginar con el explorador (con su
    offset)». Ahora `GET /tenant-stats/runs/export` acepta el MISMO `cursor`
    opaco y devuelve `X-Next-Cursor` cuando llenó la página, así que 20.000 runs
    se bajan en cuatro ficheros. Test:
    `tests/integration/test_stats_export.py::test_export_resumes_past_the_row_cap_with_the_keyset_cursor`
    —que además comprueba que la segunda página **ni repite ni se salta** filas,
    que es lo que rompe un keyset mal hecho— y
    `::test_a_corrupt_export_cursor_is_a_400_not_a_500`. Rojo verificado quitando
    el `cursor=` del `_fetch_runs` del export; los 9 del fichero, verdes.
    **Lo que deliberadamente NO se hizo, para que nadie lo pida como olvido:**
    trocear el export en lotes internos. No bajaría el pico de memoria
    —`build_runs_export` construye el cuerpo entero igual— y cambiaría una
    consulta por N. Bajar ese pico pide streaming del cuerpo, que es otro
    contrato y otra tarea. Y en `runs_select` el `offset` NO degrada nada en el
    export: siempre vale 0.
  - ✅ **Cerrada (2026-08-12): las columnas denormalizadas y su backfill son la
    migración `0139_executions_steps_rollup`.** Este carril sí es dueño de las
    migraciones, que era el bloqueo escrito arriba.
    - `executions` gana `last_model` (TEXT), `tokens_in` y `tokens_out`
      (BIGINT NOT NULL DEFAULT 0). Con eso, `_last_model_expr()` es **una
      columna** en vez de una subconsulta correlacionada por fila, y
      `_token_split()` suma dos columnas en vez de desenrollar el `steps_log` de
      todos los runs del período — el 76 % del peso de la tabla. La función
      `_last_model_expr` se conserva **a propósito** aunque ahora devuelva la
      columna: es el punto único por el que volver atrás, y donde vive la
      explicación.
    - **Se escriben donde se escribe `steps_log`** (`db/execution_repo.py`:
      `record_execution`, `finalize_execution` y la rama de la guarda de
      idempotencia que refunde un log más rico). Ésa es la única propiedad que
      impide que la proyección y su fuente se separen; un backfill sin escritor
      sería correcto para el histórico y mentiroso desde el primer run nuevo, y
      hay test de eso. En la guarda de idempotencia SÍ se recalculan, y conviene
      tener escrito por qué no contradice su propósito: esa guarda protege los
      roll-ups de `usage` (`total_tokens`, `total_cost_usd`), que no se tocan;
      estas tres son una proyección de la columna que la propia guarda acaba de
      reemplazar, y dejarlas rancias sería hacerlas mentir sobre lo que resumen.
    - **El riesgo real no era fallar, era cambiar las cifras en silencio**, así
      que el test que manda es el de EQUIVALENCIA: el backfill SQL contra
      `steps_rollup()` en Python, sobre cuatro formas de `steps_log` cuyo
      resultado no es obvio (índices desordenados, pasos que no son `model_call`
      con tokens propios, un `model_call` sin modelo detrás del último con
      modelo, y un run que nunca llamó a un modelo → NULL y ceros, no 0 y "").
      El SQL se **carga del fichero de la migración**, no se copia al test.
    - Verificado: `tests/integration/test_runs_listing_no_steps_log.py` 5/5 y
      `tests/unit/test_execution_steps_rollup.py` 8/8. Rojo comprobado con dos
      roturas a la vez —invertir el `ORDER BY … DESC` del backfill y quitar el
      `apply_steps_rollup` de `record_execution`—: 2 fallos, uno por mitad.
      El test de reversibilidad va **down → siembra → up**, así que prueba el
      `downgrade` con datos dentro y no sobre una tabla vacía.
    - **Lo que cuesta aplicarla, dicho aquí y no sólo en el docstring**: el
      `ADD COLUMN` es de catálogo (PostgreSQL ≥ 11, default constante) y se
      propaga a las particiones de la 0137; el **backfill es un UPDATE de toda
      la tabla**, así que reescribe cada tupla con pasos y `executions` puede
      llegar a ocupar el doble en disco hasta el siguiente `VACUUM`. No bloquea
      lecturas. Medir antes con
      `SELECT pg_size_pretty(pg_total_relation_size('executions'));` — en la
      instancia de desarrollo son ~2 MiB y es instantáneo.
    - **Queda fuera a propósito, y no es olvido**: el `LEFT JOIN LATERAL` gemelo
      del leaderboard (`routers/runs.py:143-150`) hace lo mismo con el mismo
      `steps_log`. Ahora es una sustitución de dos líneas por `e.last_model`,
      pero `runs.py` es de otro carril y cambiarlo desde aquí es pedirle un
      conflicto a quien lo esté tocando.
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod13_18_a
    runtime: python-pytest
    command: "pytest tests/integration/test_runs_listing_no_steps_log.py -v"
  ```

#### `task_prod13_19` — `/ws/kanban` por canal de proyecto, sin replay global

- [x] **Título**: Publicar los eventos de tareas también en un stream por proyecto
      (`events:tasks:{project_id}`, dual-write transitorio desde `events.py:28-35`)
      y que `/ws/kanban` (`routers/ws.py:153,211-234`) consuma ese stream
      arrancando en `$` (solo eventos nuevos; el backlog lo da la carga REST
      inicial del tablero), eliminando el filtrado por tenant/proyecto en Python
      y el replay de 10k entradas por socket.
  - ✅ **Cerrada (2026-08-01):** `events.project_task_events_stream` +
    dual-write en el MISMO pipeline de `_publish`, y `/ws/kanban` leyendo ya el
    stream de su proyecto. El dual-write **no es transitorio y no debe
    retirarse**: el stream global lo consume el orchestrator con un grupo de
    consumidores, así que es el bus de despacho, no un residuo de la migración.
    Los seis publicadores (routers, `dag_promotion`, `task_lifecycle`,
    reconciler, `run_cycle` y el propio orchestrator) pasan todos por
    `api_server.events`, así que el dual-write los cubre sin tocarlos.
    Cuatro decisiones que el plan no fijaba: **(1)** se conserva la ventana de
    re-reproducción de 15 s en vez del `$` que pedía el plan — arrancar en `$`
    pierde los eventos del hueco entre el fetch REST y el handshake, que es
    justo lo que esa ventana se puso a cubrir el 2026-07-03. **(2)** El filtro
    por `tenant_id` se mantiene aunque el stream ya sea de un proyecto:
    defensa en profundidad barata sobre la única propiedad que importa.
    **(3)** `maxlen` del stream por proyecto = 500 y no 10.000: su único lector
    mira los últimos 15 s. **(4)** TTL de 24 h deslizante, por lo mismo que el
    stream de ejecución — `maxlen` acota lo que pesa cada stream, no cuántos
    hay, y sin caducidad quedaría una clave por proyecto borrado para siempre.
    El test que manda es el del cruce, y lleva un **señuelo en el stream
    global**: sin él la regresión no falla, se CUELGA (el `receive_json` de
    `TestClient` no tiene plazo) y una suite colgada no se lee como un rojo.
    Verificado en rojo devolviendo el pump al stream global.
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod13_19_a
    runtime: python-pytest
    command: "pytest tests/integration/test_ws_kanban_per_project_stream.py -v"
  ```

#### `task_prod13_20` — Rate limit en `POST /assistant/chat`

- [x] **Título**: Añadir dependencia de rate limit por `user_id` (y cap por tenant)
      al endpoint (`routers/assistant.py:167`), reutilizando
      `RateLimiter.check_with_headers` (`auth/rate_limit.py:51`) con budget
      configurable en platform_settings. **Coordinación**: prod-07 añade los
      budgets/contabilidad LLM — este límite es la válvula de QPS, no de coste.
- **Tiempo**: 0,5 días · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod13_20_a
    runtime: python-pytest
    command: "pytest tests/integration/test_assistant_chat_rate_limit.py -v"
  ```

#### `task_prod13_21` — Caché Redis para membership y platform_settings

- [x] **Título**: Cachear en Redis (TTL ≤ 60 s + invalidación al escribir) el
      lookup de membership por request (`auth/deps.py:308-352`) y
      `get_platform_setting` (`db/platform_settings.py:32`), con invalidación en
      los endpoints de escritura correspondientes. Medir antes/después con la
      métrica de QPS por query (decisión clave 6: la frescura gana).
  - ✅ **Cerrada (2026-07-31):** la de `platform_settings` ya estaba; la de
    membership vive ahora en `api_server/cache/membership.py` (TTL 30 s, la mitad
    del máximo, porque lo que se cachea es un permiso). **La invalidación NO se
    sembró por los endpoints de escritura**, y a propósito: las membresías se
    escriben desde cuatro sitios (panel admin, SCIM, mapeo de grupos SSO, siembra
    de tenant) y el quinto llegará sin que nadie se acuerde de la caché — el patrón
    "mecanismo entregado, cero llamantes" del apartado 5 de
    `verificar-antes-de-implementar.md`. Cuelga de eventos de mapper del ORM y se
    ejecuta DESPUÉS del commit. Precio dicho en voz alta: un UPDATE en SQL crudo
    fuera del ORM no invalida (ninguna vía de aplicación escribe así; el TTL acota
    el resto). Los tres tests que mandan son los de revocación —retirar acceso,
    degradar rol y CONCEDER acceso—: desactivando la invalidación, los tres pasan a
    rojo.
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod13_21_a
    runtime: python-pytest
    command: "pytest tests/integration/test_redis_cache_membership_settings.py -v"
  ```

#### `task_prod13_22` — `FOR UPDATE` en transiciones de estado críticas

- [x] **Título**: Añadir `for_update=True` a `get_writable_or_404`
      (`routers/_helpers.py:77`) y usarlo en `approve_plan`/`apply_human_action`
      (`plans.py:471,495`) y `task_lifecycle.py:140`, cerrando la carrera de doble
      firma simultánea (api-10). Test de concurrencia con dos firmas en paralelo.
  - ✅ **Cerrada (2026-08-01):** el `for_update=True` ya estaba; faltaba lo único
    que demuestra que sirve, y son **dos tests distintos** porque prueban cosas
    distintas (`tests/integration/test_state_transitions_row_lock.py`, 3/3).
    El que manda es el de **sesiones**: dos transacciones vivas, interleave
    controlado a mano, y comprueba las dos mitades de la propiedad — que el
    segundo `SELECT … FOR UPDATE` **se queda esperando** mientras el primero no
    commitea, y que al desbloquearse lee la fila **actualizada** (READ COMMITTED
    re-evalúa la versión viva), o sea que decide sobre el estado real y no sobre
    el que vio el primero. Lleva su CONTROL dentro: el mismo guion sin candado
    devuelve al instante el estado RANCIO, que es literalmente la carrera de la
    doble firma. Verificado en rojo neutralizando el `if for_update:` del helper.
    El segundo test son las **dos firmas en paralelo por HTTP** que pedía el
    plan, y su límite queda escrito en su propio docstring: el interleave no
    está forzado, así que puede pasar por casualidad con el candado quitado —
    comprueba el cableado de punta a punta, no la serialización.
    De paso, una trampa cazada en caliente: con el umbral de doble firma por
    debajo del coste del plan el endpoint aprueba a la PRIMERA firma y la
    segunda recibe un 409 correcto; el test lo asegura comprobando el coste
    contra el umbral antes de firmar, o sería verde por el motivo equivocado.
- **Tiempo**: 0,5 días · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod13_22_a
    runtime: python-pytest
    command: "pytest tests/integration/test_state_transitions_row_lock.py -v"
  ```

#### `task_prod13_23` — No exponer `str(exc.orig)` en respuestas 409

- [x] **Título**: Sustituir el patrón `detail=str(exc.orig)` de los seis routers
      (`conversations.py:159,311`, `plans.py:158`, `projects.py:212`,
      `tasks.py:108`, `kb_categories.py:140`) por un exception handler global de
      `IntegrityError` que mapee constraint→mensaje de dominio estable (el patrón
      correcto ya existe en `api_v1/router.py:137`), y sanear el `{exc}` del
      proveedor LLM en `assistant.py:209-220`.
  - ℹ️ **Desviación aceptada (2026-07-31):** el mapeo constraint→mensaje de
    dominio vive en el helper compartido `routers/_integrity.py`
    (`integrity_conflict`) en vez de en un exception handler global; el efecto es
    el mismo y una guarda recorre los routers para que ninguno vuelva a
    `detail=str(exc.orig)`. El saneado del error del proveedor LLM
    (`_provider_error_detail`) está hecho pero NO tiene test propio.
- **Tiempo**: 0,5 días · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod13_23_a
    runtime: python-pytest
    command: "pytest tests/integration/test_integrity_error_sanitized.py -v"
  ```

## Hallazgos de auditoría cubiertos

| fid     | Severidad | Tarea(s) que lo cierran                        |
| ------- | --------- | ---------------------------------------------- |
| perf-1  | high      | task_prod13_01                                 |
| perf-2  | high      | task_prod13_06, task_prod13_07                 |
| perf-3  | medium    | task_prod13_10                                 |
| perf-4  | medium    | task_prod13_16                                 |
| perf-5  | medium    | task_prod13_19                                 |
| perf-6  | medium    | task_prod13_18                                 |
| perf-7  | medium    | task_prod13_03                                 |
| perf-8  | low       | task_prod13_17                                 |
| perf-9  | low       | task_prod13_05                                 |
| perf-10 | low       | task_prod13_21                                 |
| perf-11 | low       | task_prod13_08                                 |
| db-2    | high      | task_prod13_06, task_prod13_07, task_prod13_08 |
| db-4    | medium    | task_prod13_14, task_prod13_15                 |
| db-6    | medium    | task_prod13_12                                 |
| db-7    | low       | task_prod13_11                                 |
| db-8    | low       | task_prod13_09                                 |
| db-9    | low       | task_prod13_13                                 |
| api-2   | medium    | task_prod13_04                                 |
| api-3   | medium    | task_prod13_02                                 |
| api-4   | medium    | task_prod13_20                                 |
| api-5   | low       | task_prod13_23                                 |
| api-6   | low       | task_prod13_17                                 |
| api-10  | low       | task_prod13_22                                 |

## Riesgos

1. **Regresión funcional en el asistente** (task_prod13_07): sacar la sesión viva
   del turno LLM cambia el contrato de las tools (`tool_ctx.session`). Mitigación:
   suite de integración del asistente completa antes/después + el session-factory
   replica `set_config` de tenant en cada sesión corta (riesgo RLS si se omite).
2. **Reconstrucción del índice GIN/FTS bloquea escrituras** (task_prod13_10): con
   corpus grande la migración puede tardar. Mitigación: `CREATE INDEX CONCURRENTLY`
   fuera de la transacción de Alembic (autocommit) o ventana de mantenimiento
   documentada en el runbook.
3. **La purga borra datos que un tenant quería recuperar** (task_prod13_14):
   pérdida irreversible si la ventana de gracia o la cascada están mal. Mitigación:
   dry-run por defecto la primera semana, log de recuentos, test que verifica que
   filas dentro de la gracia NUNCA se tocan.
4. **Dedup "latest wins" rompe referencias por nombre** (task_prod13_13): seeds o
   plantillas que resuelven por nombre pueden apuntar al duplicado eliminado.
   Mitigación: la migración loguea los renombrados/fusionados y se revisa en staging.
5. **Caché de membership sirve un rol revocado** (task_prod13_21): un admin
   degradado conserva privilegios hasta el TTL. Mitigación: TTL ≤ 60 s +
   invalidación explícita en los writes de membership; test de revocación.
6. **Dual-write de eventos del kanban** (task_prod13_19): consumidores del stream
   global existentes deben seguir funcionando durante la transición. Mitigación:
   mantener el stream global hasta verificar los consumidores y retirar después.

## Tests humanos del Plan

```yaml
- id: human_prod13_01
  description: "El api-server no se congela bajo operaciones pesadas"
  hint: "Dos pestañas: una operando, otra disparando la operación pesada"
  checklist:
    - "Lanzar una actualización de paquete del marketplace → el endpoint devuelve 202 y el kanban en otra pestaña sigue fluido (WS vivo, sin spinner)"
    - "Probar un destino de backup SFTP inalcanzable → la UI espera, pero /healthz y el resto de la API responden < 1 s"
    - "Subir un fichero de 2 GB a una KB → rechazo rápido con 413, sin OOM ni reinicio del contenedor"
    - "Parar Vault y mandar un mensaje al asistente → error acotado en segundos, la API no se cuelga"

- id: human_prod13_02
  description: "Concurrencia de chats sin agotar el pool"
  hint: "Script con 20 chats de asistente en paralelo contra un modelo lento"
  checklist:
    - "Con 20 chats concurrentes, GET /plans y el kanban responden < 2 s"
    - "Sin TimeoutError de pool en los logs del api-server"
    - "La métrica de pool no llega a saturación sostenida"

- id: human_prod13_03
  description: "Retención y backfill operativos"
  hint: "Usar un tenant de pruebas con datos sintéticos"
  checklist:
    - "Soft-borrar una KB, adelantar el reloj/ventana → la purga elimina documents+chunks y libera disco; dentro de la gracia NO se toca nada"
    - "Tirar Ollama durante una ingesta → documento con chunks sin vector; al volver Ollama, el backfill los rellena solo (< 10 min) y el RAG los encuentra"
    - "El ADR de retención de audit_log está decidido y firmado por un humano antes de activar la task de retención"

- id: human_prod13_04
  description: "Endpoints con válvulas"
  checklist:
    - "Bucle de POST /assistant/chat → a partir del límite devuelve 429 con headers X-RateLimit-*"
    - "Doble click simultáneo en 'Aprobar plan' por dos admins → solo una primera firma; la otra recibe 409"
    - "Provocar un nombre duplicado → el 409 muestra un mensaje de dominio, nunca 'duplicate key value violates...'"
    - "Abrir el visor de citas de un PDF grande → respuesta paginada, carga fluida"
```

## Criterios de cierre

1. Todas las tareas con `[x]` y sus tests automáticos en verde.
2. Tabla «Hallazgos de auditoría cubiertos» verificada: los 23 fids tienen su
   tarea cerrada (re-chequeo contra la evidencia original de la auditoría).
3. Los 4 tests humanos pass, validados por un humano.
4. ADR de retención append-only (decisión clave 3) y ADR de particionado HNSW
   (decisión clave 5) redactados; el primero decidido antes de activar la task
   de retención.
5. Migraciones nuevas con downgrade real y roundtrip head→base→head verde.
6. Entrada de changelog en `docs/07-changelog/prod-13-rendimiento-y-datos.md` y
   runbooks afectados (`docs/06-runbooks/`) actualizados (purga, retención, pool).
7. PR del plan mergeado a `master`.

## Próximo Plan

Con los P1 de rendimiento y datos cerrados, la serie continúa con los P2:

- **prod-14-tenancy-defensa-profundidad** [P2] — multi-tenancy: defensa en
  profundidad (junctions, service_user, meta-test). Se beneficia directamente de
  este plan: el test de recall multi-tenant (task_prod13_12) y la caché de
  membership con invalidación (task_prod13_21) son insumos de su meta-test.
- Después: **prod-15-gobernanza-roadmap-docs** y **prod-16-frontend-i18n-calidad**.
