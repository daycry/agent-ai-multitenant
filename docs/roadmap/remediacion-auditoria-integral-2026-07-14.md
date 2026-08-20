---
plan_id: remediacion-auditoria-integral-2026-07-14
title: Remediación delta de auditoría integral — seguridad, contratos y rendimiento
status: pending_human_validation
blocking_plan: []
started_at: 2026-07-31
completed_at: null
estimated_duration_calendar: 2-3 semanas
estimated_effort_person_days: 13
estimated_cost_human_eur: 5.850 € – 7.800 €
estimated_cost_ai_eur: 40 € – 90 €
created_by: github-copilot-audit-2026-07-14
spec_sections_referenced: []
docs_language: es
priority: P0
source_audit: auditoria-integral-2026-07-14
---

# Plan de remediación delta — Auditoría integral 2026-07-14

## Cabecera

| Campo                 | Valor                                              |
| --------------------- | -------------------------------------------------- |
| **ID del Plan**       | `remediacion-auditoria-integral-2026-07-14`        |
| **Prioridad**         | P0 para restaurar CI; P1/P2 para el resto          |
| **Bloqueado por**     | Ninguno; coordina con prod-02/07/08/09/13/14/15/16 |
| **Duración estimada** | 2-3 semanas                                        |
| **Esfuerzo estimado** | 13 persona-días                                    |
| **Rama sugerida**     | `plan/remediacion-auditoria-integral-2026-07-14`   |

## Resumen

Este plan implementa únicamente el delta confirmado por la auditoría del 14 de
julio. No duplica los planes de producción existentes: restaura los cuatro gates
de seguridad hoy rojos, resuelve dos contratos arquitectónicos incoherentes
(córtex/RLS y modelo de embeddings), consolida el acceso a BD de workers y añade
límites operativos para WebSocket/readiness.

El estado es `pending_approval`. No se activa ninguna fase ni se cambia el estado
de los cuatro documentos actualmente `in_progress`.

## Alcance

**Entra**:

- Hardening de `textfile-init` y meta-tests conscientes de overlays Compose.
- ADR y aplicación de la decisión sobre `cortex_conversations.tenant_id`/RLS.
- ADR y aplicación del contrato real de embeddings de KB.
- Factoría única de engines/sesiones Celery con `NullPool`.
- Deadline/backpressure en WebSockets.
- Separación `/healthz` (liveness) y `/readyz` (readiness).
- Alineación mínima del toolchain frontend y warnings actuales de hooks.
- Tests automáticos y documentación de referencia afectados.

**Queda fuera**:

- Hardening global `/admin/*`, tickets WS/cookies/headers (`prod-09`).
- Junctions tenant sin RLS (`prod-14`).
- Exporters y alertas Prometheus (`prod-08`).
- Guardrails completos (`prod-03`/ADR 0102).
- Campaña de validación humana y normalización global del roadmap (`prod-15`).
- Subida completa de cobertura a 70/80 (`prod-02`, por ratchet).
- Refactor i18n/general del frontend (`prod-16`).

## Decisiones clave

1. **No silenciar gates con excepciones implícitas.** Los overlays se evalúan como
   se despliegan y las excepciones de seguridad se documentan por clase de servicio.
2. **Córtex requiere ADR.** Opciones: (A) tenant-scoped con RLS real; (B)
   owner-scoped con política estructural propia y renombrado/eliminación del falso
   `tenant_id`; (C) excepción explícita al principio 1. Recomendación: B si el
   producto confirma que el córtex es singleton del System Owner; C no aporta
   defensa y debe ser la última opción.
   → **RESUELTA el 2026-08-19 por el [ADR 0156](../05-architecture-decisions/0156-aislamiento-estructural-del-cortex.md)**:
   se elige **B**, extendida a las **seis** tablas del subsistema (el enunciado
   solo miraba `cortex_conversations`, y era una de seis). El renombrado del
   `tenant_id` se descarta con motivo escrito.
3. **Embeddings requieren un contrato único.** Opciones: (A) un único modelo y
   dimensión de plataforma, retirando el selector por KB; (B) registro por modelo,
   dimensión compatible, reindexado y búsqueda agrupada. Recomendación: A para el
   alcance single-host actual; B solo si existe un caso real multi-modelo.
4. **Celery usa conexiones cortas.** Una factoría común con `NullPool` evita pools
   efímeros y concentra settings/observabilidad. Los tests pueden inyectar
   sessionmakers sin red.
5. **Liveness no es readiness.** `/healthz` no depende de servicios externos;
   `/readyz` prueba solo dependencias necesarias para aceptar tráfico, con timeout.

## Tareas

### Fase A — Restaurar el gate de seguridad

#### `task_audit14_01` — Compose monitoring endurecido y tests overlay-aware

- [x] **Título**: Corregir `textfile-init` y evaluar `workers` tras fusionar base+overlay
- **Descripción**: Añadir a `textfile-init` el baseline compatible con BusyBox
  (`no-new-privileges`, `cap_drop: [ALL]`, AppArmor validado). Refactorizar helpers
  de `tests/security` para renderizar `docker-compose.yml` +
  `docker-compose.monitoring.yml` como despliegue real; conservar asserts separados
  para servicios definidos solo en el overlay y documentar one-shot/host-agent.
- ℹ️ **Comando `auto_audit14_01_b` obsoleto (verificado 2026-07-31):**
  `docker compose -f docker-compose.yml -f docker-compose.monitoring.yml config`
  sale con código 1 («service "workers" has neither an image nor a build
  context»), y no por esta tarea: el compose base ya solo trae infraestructura —
  los servicios de aplicación los genera el instalador y viven en
  `docker-compose.manuals.yml`. El render del despliegue real, añadiendo ese
  tercer fichero, sale 0.
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_audit14_01_a
    runtime: python-pytest
    command: "pytest tests/security/test_apparmor.py tests/security/test_seccomp_profiles.py tests/security/test_pentest_findings.py -v"
  - id: auto_audit14_01_b
    runtime: python-pytest
    command: "docker compose -f docker/docker-compose.yml -f docker/docker-compose.monitoring.yml config --quiet"
  ```

#### `task_audit14_02` — ADR de aislamiento owner/tenant del córtex

- [x] **Título**: Decidir el contrato estructural de `cortex_conversations`
- **Descripción**: Redactar el ADR con las opciones A/B/C de la decisión 2,
  inventario de todas las tablas córtex, rutas BYPASSRLS y efecto sobre login,
  memoria, backups y tests cross-owner.
- ✅ **Hecho (2026-08-19):** [ADR 0156](../05-architecture-decisions/0156-aislamiento-estructural-del-cortex.md),
  `accepted`. Ratifica la **opción B** (eje owner con policy estructural propia) y
  deja escrito el descarte de A (policy por tenant: más permisiva Y deja al owner
  a oscuras), de C (excepción documentada: no opone nada a `app_user`) y de la
  sub-opción «renombrar o eliminar el `tenant_id` falso» (eliminarlo rompe la
  memoria del owner; renombrarlo, o no cambia nada o lo saca del descubrimiento
  del invariante, que es peor). El inventario destapó que el alcance real era
  mayor que el enunciado: no es una tabla, son **seis**, y la `0125` solo había
  cubierto una.
- **Enunciado corregido (2026-08-19):** decía «ADR `proposed`… la decisión es
  humana». El operador dio luz verde explícita para elegir la mejor opción y
  dejarla `accepted` en vez de dejar el ADR esperándole; así se hizo.
- **Tiempo**: 0,5 días · **Complejidad**: m
- **Tests automáticos**: lint/frontmatter de ADR (`pytest tests/docs -q`, verde).

#### `task_audit14_03` — Aplicar aislamiento del córtex ratificado

- [x] **Título**: Migración reversible, repositorios y meta-test coherentes con el ADR
- **Descripción**: Implementar la opción ratificada sin perder el filtro
  `owner_user_id`; añadir defensa estructural, actualizar modelos/repositorios y
  hacer que el invariante de RLS exprese la regla elegida sin falsos negativos.
- ✅ **Verificada y marcada (2026-08-19).** Los tests de integración que faltaban
  se corrieron en una pasada **serial y limpia** —Postgres y Redis alcanzables
  desde el host, ningún agente escribiendo, un solo proceso de pytest— y salieron
  en verde junto con los otros quince ficheros que tocó la ola:
  **143 passed** en `tests/integration` + `tests/migrations`, incluidos
  `test_cortex_owner_rls.py` (catálogo de las cinco tablas, aislamiento funcional
  bajo `app_user`, fail-closed sin GUC, `WITH CHECK` cross-owner y round-trip
  anclado por nombre) y el invariante nº 5 de `test_rls_invariant.py`.
  - **Dos tests había que arreglar, y no relajándolos**: `test_cortex_identity.py`
    y `test_cortex_affect_store.py` afirmaban `relrowsecurity is False` sobre
    tablas que el ADR 0156 acaba de proteger. Eran tests que fijaban el defecto —
    venían del ADR 0074, que leyó «tenant-less» como «sin eje que defender».
    Ahora afirman lo contrario y **piden más**: `ENABLE` + `FORCE` + que la policy
    cuelgue de `app.user_id`. Sus nombres decían `..._no_rls_...` y también se
    corrigieron: un nombre que miente se lee en el informe de fallos.
  - **Aviso de método que costó tres horas**: la primera pasada de la suite corrió
    en 4 shards **mientras cinco agentes escribían en el árbol** y tumbó Postgres
    por memoria del anfitrión. De sus ocho rojos, cuatro del córtex y uno de
    WebSocket pasaron al repetirlos en serie: eran contaminación, no defectos.
    Documentado en
    [`cuatro-shards-y-cinco-agentes-tumban-postgres.md`](../03-guides/gotchas/cuatro-shards-y-cinco-agentes-tumban-postgres.md).
- 🛠️ **Lo entregado:** migración
  [`0140_cortex_owner_rls`](../../apps/api-server/migrations/versions/20260819_0140_cortex_owner_rls.py)
  (encadenada a la cabeza real `0139_executions_steps_rollup`): `ENABLE` +
  `FORCE` + policy `<tabla>_owner_only` en las **cinco** tablas del córtex que la
  `0125` no cubría, con `downgrade` que las devuelve al estado de las 0092-0095 y
  no toca la RLS de `cortex_conversations`. Los filtros `owner_user_id` de
  aplicación **se conservan** (siguen siendo la única capa que actúa con roles
  BYPASSRLS); lo que se corrigió son los docstrings de `db/cortex*.py` y
  `cortex/threads.py`, que afirmaban «no hay RLS / sin RLS de respaldo».
  Meta-test: invariante nº 5 `OWNER_SCOPED_TABLES` en `test_rls_invariant.py` —
  las cinco entradas de allowlist que decían «aislado por `owner_user_id`» sin que
  nadie lo comprobase pasan a ser una aserción. Referencia actualizada:
  `docs/04-reference/multi-tenancy.md`.
- **Enunciado corregido (2026-08-19):** el texto de 2026-07-31 daba la
  implementación por «entregada» a falta del ADR. Era falso en el alcance: la
  `0125` protegía **1 de 6** tablas y ella misma lo dejó escrito. Lo que faltaba no
  era ratificar, era hacer las otras cinco.
- **Tiempo**: 2 días · **Complejidad**: l · **Depende de**: `task_audit14_02`
- **Tests automáticos**:
  ```yaml
  - id: auto_audit14_03_a
    runtime: python-pytest
    command: "pytest tests/security/test_pentest_findings.py -v"
  - id: auto_audit14_03_b
    runtime: python-pytest
    command: "pytest tests/integration/test_cortex_owner_rls.py tests/integration/test_rls_invariant.py -v -m cross_tenant"
  - id: auto_audit14_03_c
    runtime: python-pytest
    command: "pytest tests/migrations -v"
  ```
- **Estado de la verificación (2026-08-19):** `auto_audit14_03_a` ejecutado y en
  **verde** (23 passed), incluidos los dos tests nuevos del córtex; se comprobó que
  se ponen ROJOS rompiendo la migración a propósito. `auto_audit14_03_b` y
  `_c` están **escritos pero NO ejecutados**: la suite de integración estaba
  corriendo en cuatro shards contra la misma base de datos y lanzarlos se habrían
  pisado. Quedan pendientes de una pasada limpia.

### Fase B — Contrato ejecutable de embeddings

#### `task_audit14_04` — ADR del modelo de embeddings de KB

- [x] **Título**: Elegir modelo único de plataforma o multi-modelo real
- **Descripción**: Documentar las opciones de la decisión 3 con coste de migración,
  dimensión pgvector, query sobre varias KB, reindexado, rollback y UX. Medir el
  inventario real de modelos configurados antes de recomendar la migración final.
- ✅ **Hecho (2026-08-19):**
  [ADR 0155](../05-architecture-decisions/0155-modelo-de-embeddings-de-kb.md),
  `accepted`, **opción A** (un único modelo de plataforma).
  El inventario se midió **antes** de decidir, contra la BD del stack vivo, y es
  lo que cierra la discusión: **un solo valor distinto** en toda la instalación
  (`nomic-embed-text-v1.5`, 14 KBs), **0 documentos y 0 chunks**, y
  `API_SERVER_EMBEDDING_MODEL` sin fijar ni en `api-server` ni en `workers` (los
  dos caen al default `nomic-embed-text`). O sea: no hay ninguna realidad
  multi-modelo que preservar, la migración toca 14 filas sin un solo vector
  detrás, y la pantalla llevaba enseñando una etiqueta que **no es un tag válido
  del registro de Ollama** y que ningún embedder envió jamás.
  Descartadas por escrito: **B** (registro multi-modelo real — sin demanda
  medida, y dejaría `memory_entries`, que no tiene el campo, en el contrato
  contrario) y **C** (dejarlo y documentarlo como cosmético, que era el statu quo
  y estaba escrito en el gotcha del naming).
- **Tiempo**: 0,5 días · **Complejidad**: m
- **Tests automáticos**: lint/frontmatter de ADR; script read-only de inventario.
  Verificado con `pytest tests/docs/test_docs_internal_links.py
tests/docs/test_new_gotchas_documented.py tests/unit/test_docs_governance.py`
  (26 passed) y con la consulta de inventario transcrita en el ADR.

#### `task_audit14_05` — Implementar y probar el contrato de embeddings

- [x] **Título**: La configuración mostrada por API/UI gobierna ingesta y retrieval
- **Descripción**: Para opción A, normalizar datos al modelo canónico y retirar el
  selector engañoso conservando una migración compatible; para B, resolver modelo
  antes de construir embedder, validar dimensión, agrupar query por modelo y añadir
  reindexado explícito. En ambos casos, error visible y métrica ante mismatch.
- ✅ **Verificada y marcada (2026-08-19).** Los dos comandos que faltaban se
  corrieron en pasada serial y limpia y salieron en verde (**143 passed** con los
  otros quince ficheros de la ola). Y de paso se cerró lo que quedaba explícitamente
  aplazado: **la migración**.
  - **Migración `0141_kb_embedding_canonical`** (encadenada a la cabeza real
    `0140_cortex_owner_rls`; el carril que la escribió la aplazó a propósito para
    no colisionar en la cabeza con la 0140, y ese motivo ya no existe). Hace el
    `UPDATE` de las 14 filas con el alias heredado, mueve el _server default_ de
    la columna al nombre canónico, y su `downgrade` **repone el default pero NO
    deshace el `UPDATE`**: revertirlo repondría en las filas una etiqueta que no
    identifica a ningún modelo servible. Un downgrade que restaura un dato roto no
    es reversibilidad, es simetría mal entendida — y hay un test que lo dice si
    alguien «lo arregla».
  - **Sin la migración, la API ya no mentía** (canoniza el sello en LECTURA), pero
    el valor ALMACENADO seguía desfasado: lo ve quien abre la tabla con `psql` o
    restaura un backup. Eso es lo que cierra la 0141.
  - **`tests/integration/test_kb_embedding_canonical_migration.py`** (4 casos) con
    el rojo comprobado por mutación en tres direcciones: quitarle el `WHERE` al
    `UPDATE` —el error fácil, que re-sellaría KBs cuyos vectores salieron de otro
    espacio semántico—, quitar el `SET DEFAULT`, y hacer el `downgrade` simétrico.
  - **Tres docstrings corregidos** en `db/knowledge.py` y `seeds/builtin_kbs.py`:
    describían el mix-and-match por KB, que es justo la opción B que el ADR 0155
    descarta.
    Lo demás ya estaba verificado antes: `auto_audit14_05_c` (vitest,
    23 passed en `app/admin/knowledge-bases/`), los tests unitarios nuevos
    (`tests/unit/test_kb_embedding_contract.py` +
    `tests/unit/test_kb_embedding_contract_wiring.py`, 25 casos), `tests/unit`
    completa, `ruff`, `black`, el gate `mypy` (707 ficheros) y `tsc --noEmit`.
    Lo entregado, por reglas del [ADR 0155](../05-architecture-decisions/0155-modelo-de-embeddings-de-kb.md):
    punto único de resolución (`api_server/ingestion/embedding_contract.py`); la API
    sella el modelo activo y rechaza con 422 cualquier otro (`routers/_kb_embedding.py`,
    los dos routers de creación); la respuesta devuelve el sello canonizado más
    `platform_embedding_model` y `embedding_model_stale`; `ingest_document` se niega
    y deja el documento `failed` con los dos modelos en el mensaje antes de embeber;
    el camino vectorial filtra por grafías equivalentes (`rag/search.py`); y la UI
    deja de mandar la etiqueta falsa al crear y avisa del desfase.
- ⚠️ **Dos desviaciones anotadas**: (1) la **migración de datos NO la escribe este
  carril** —había otro creando migraciones en la misma ola y colisionaban en la
  cabeza de Alembic—; el DDL exacto está en el ADR 0155, y mientras tanto la
  canonización en lectura ya impide que la API mienta. (2) La «métrica» ante
  mismatch es el **evento estructurado** `kb.embedding_model_mismatch` con nombre
  estable, no un contador Prometheus: este plan excluye por escrito los exporters
  (`prod-08`), que es quien debe cablear el contador sobre ese evento.
- **Tiempo**: 2 días · **Complejidad**: l · **Depende de**: `task_audit14_04`
- **Tests automáticos**:
  ```yaml
  - id: auto_audit14_05_a
    runtime: python-pytest
    command: "pytest tests/integration/test_ingestion_worker.py tests/integration/test_embeddings.py tests/integration/test_vector_search.py -v"
  - id: auto_audit14_05_b
    runtime: python-pytest
    command: "pytest tests/integration/test_kb_embedding_model_contract.py -v"
  - id: auto_audit14_05_c
    runtime: node-vitest
    command: "npm --prefix apps/admin-panel run test -- app/admin/knowledge-bases/page.test.tsx"
  ```

### Fase C — Recursos y límites operativos

#### `task_audit14_06` — Factoría única de sesiones de worker con NullPool

- [x] **Título**: Sustituir las 36 creaciones directas de engine en workers
- **Descripción**: Crear módulo de infraestructura de BD para workers que devuelva
  engine/sessionmaker owner-aware, `poolclass=NullPool`, `pool_pre_ping` donde
  proceda y cierre garantizado. Migrar los 30 módulos por lotes pequeños sin cambiar
  semántica tenant. Prohibir imports directos de `create_async_engine` fuera del
  módulo y tests.
- ℹ️ **Desviación aceptada (2026-07-31):** la guarda de la prohibición vive en
  `tests/unit/test_worker_engines_nullpool.py` (hoy es un muro: `PENDING_MIGRATION`
  está vacía) y no en `tests/docs/test_worker_db_factory_contract.py`, que nunca
  se creó. `pool_pre_ping` se dejó DESACTIVADO a propósito: con `NullPool` cada
  checkout abre conexión nueva y el ping sería un `SELECT 1` por sesión a cambio
  de nada — decisión fijada en un test para que no parezca un olvido.
- **Tiempo**: 2 días · **Complejidad**: l
- **Tests automáticos**:
  ```yaml
  - id: auto_audit14_06_a
    runtime: python-pytest
    command: "pytest tests/unit/test_worker_db_factory.py tests/unit/test_worker_engines_nullpool.py -v"
  - id: auto_audit14_06_b
    runtime: python-pytest
    command: "pytest tests/integration/test_worker_tenant_boundary.py tests/integration/test_autonomous_cycle.py -v"
  - id: auto_audit14_06_c
    runtime: python-pytest
    command: "pytest tests/unit/test_worker_engines_nullpool.py tests/unit/test_worker_db_factory.py -v"
  ```

#### `task_audit14_07` — Backpressure y timeout de envío WebSocket

- [x] **Título**: Cerrar clientes lentos sin dejar coroutines colgadas
- **Descripción**: Añadir setting de timeout de envío, envolver `send_json`, cerrar
  con código documentado y cancelar/await de `reader` y `xread` en todos los caminos.
  No cambiar todavía el transporte de credencial, que pertenece a `prod-09`.
- ✅ **Hecho (2026-08-19):** las cuatro piezas del enunciado, en `routers/ws.py`.
  Setting `ws_send_timeout_seconds` (10 s, `0` desactiva) en `config.py:450`;
  `_send_event` (`ws.py:386`) envuelve el `send_json` con `asyncio.wait_for` y
  cierra con **1013** «Try Again Later» (`_CLOSE_SLOW_CONSUMER`, `ws.py:99` —
  1008 diría que el cliente violó una política y 1011 que el servidor se rompió;
  ninguna de las dos invita a reconectar); `_settle` (`ws.py:349`) cancela **y
  espera** `reader` y `xread` en TODOS los caminos de salida, y `_close`
  (`ws.py:371`) pone el mismo deadline al cierre, que viaja por el mismo socket
  atascado. `cortex_ws` importa `_pump`/`_reject`, así que hereda las cuatro.
  Acreditado por `tests/unit/test_ws_pump_backpressure.py`, que conduce el
  `_pump` **de producción** con dobles en sus dos bordes de E/S; los dobles
  modelan que un `XREAD` no muere en el instante del `cancel()`, que es lo que
  hace observable la diferencia entre `cancel()` y `cancel()` + `await`. El
  transporte de credencial no se tocó (sigue siendo de `prod-09`).
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_audit14_07_a
    runtime: python-pytest
    command: "pytest tests/unit/test_ws_pump_backpressure.py -v"
  - id: auto_audit14_07_b
    runtime: python-pytest
    command: "pytest tests/integration/test_ws_streaming.py tests/integration/test_ws_tenant_isolation.py -v"
  ```

#### `task_audit14_08` — Readiness separada de liveness

- [x] **Título**: Añadir `/readyz` con checks críticos y deadlines
- **Descripción**: Mantener `/healthz` como liveness de proceso. Añadir `/readyz`
  con PostgreSQL y Redis, timeout por check, respuesta 503 estructurada sin secretos
  y test de degradación parcial. Cablear el consumidor correcto (proxy/compose) sin
  crear restart loops por dependencias opcionales como Vault/Ollama/Docling.
- ✅ **Hecho (2026-08-19):** el endpoint **ya estaba** y NO se ha reimplementado
  (`routers/health.py:175`: PostgreSQL + Redis, deadline por check, 503
  estructurado, saneado de credenciales, degradación parcial y recuperación sin
  reinicio; 6 tests verdes en `tests/integration/test_health_readiness.py` +
  `tests/unit/test_readiness_scrub.py`). Lo que faltaba era el llamante, y se
  cableó donde no provoca bucles: el **proxy**. `health_uri /readyz` en los dos
  upstreams del Caddyfile generado (`proxy_generator._API_UPSTREAM`, línea 55) y
  en `docker/caddy-manuals/Caddyfile`. Verificado contra Caddy 2.8 real, no sólo
  por texto: con `/readyz` en 503 el proxy contesta `503 Server: Caddy` («no
  upstreams available») aunque el upstream sirva esa misma ruta con 200, y repone
  el backend en cuanto readiness vuelve a 200, sin reiniciar nada. El
  `healthcheck` del CONTENEDOR se queda en `/healthz` **a propósito**: Docker
  admite uno solo y el watchdog reinicia lo `unhealthy`
  (`watchdog/service_monitor.py`), así que apuntarlo a readiness convertiría «se
  cayó PostgreSQL» en «la api-server se reinicia en bucle» — el restart loop que
  la descripción prohíbe. Las dos mitades las fija
  `tests/unit/test_readyz_has_a_consumer.py`; el operador lo tiene en
  `docs/06-runbooks/health-check.md` §2-bis.
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_audit14_08_a
    runtime: python-pytest
    command: "pytest tests/integration/test_health_readiness.py -v"
  - id: auto_audit14_08_b
    runtime: python-pytest
    command: "pytest tests/smoke -v -k 'healthz or readyz'"
  # Añadido al cablear el consumidor: `_b` SALTA sin `SMOKE_BASE_URL` (o sea,
  # siempre en CI y en el portátil), así que sin esta guarda estática el
  # cableado del proxy no lo vigilaría nadie.
  - id: auto_audit14_08_c
    runtime: python-pytest
    command: "pytest tests/unit/test_readyz_has_a_consumer.py -v"
  ```

### Fase D — Toolchain y documentación

#### `task_audit14_09` — Frontend sin warnings y matriz de versiones compatible

- [x] **Título**: Corregir dependencias de hooks y alinear Next/ESLint/TypeScript
- **Descripción**: Eliminar los ocho warnings actuales sin introducir memoización
  innecesaria (mover defaults dentro del hook cuando sea suficiente). Elegir una
  combinación soportada y fijada de Next/ESLint/TypeScript; actualizar lockfile y
  convertir warnings de lint en gate. Coordinar el upgrade mayor con `prod-16`.
- ✅ **Hecho (2026-08-19).** Las tres mitades, y la primera resultó ser **un solo
  defecto repetido cinco veces**: `const xs = query.data ?? []` devuelve un array
  NUEVO en cada render mientras la consulta no ha respondido, así que con esa
  variable en las dependencias el `useMemo` no memoiza nada y el `useEffect` corre
  de más. Estaba en `approval-policy`, `knowledge-bases`, `plans`, `tasks` y
  `users`, y de ahí salían los ocho warnings.
  - **Arreglado como pedía el enunciado, sin memoización innecesaria**: donde hay
    UN consumidor, el `?? []` se muda dentro del hook y la dependencia pasa a ser
    `query.data`, que sí es estable; donde hay dos (`plans`, `tasks`), una sola
    `useMemo` da la referencia estable en vez de repetir el default en cada hook.
    De paso, `users/page.tsx` se quedaba con una variable muerta que `tsc` cazó.
  - **Matriz fijada**: `next` 15.5.23 · `eslint-config-next` 15.5.23 (clavado a
    `next`, que es quien dicta su versión) · `eslint` 8.57.1 · `typescript` 5.9.3,
    las cuatro con versión exacta y coincidiendo con lo que resuelve el
    `package-lock.json`, en **las dos** apps Next (`admin-panel` e `installer`).
  - **Gate**: `npm run lint` pasa a `next lint --max-warnings=0` en las dos. Sin
    esa bandera `next lint` sale con código 0 aunque emita warnings — que es
    exactamente cómo estos ocho vivieron meses en verde.
  - **Guarda nueva** `tests/unit/test_frontend_version_matrix.py` (7 tests), con
    el rojo comprobado por mutación en las cuatro direcciones: retirar
    `--max-warnings=0`, devolver `eslint` a un rango `^`, desalinear
    `eslint-config-next` de `next`, y pinear a una versión que el lockfile
    contradiga. Lleva además el test que descubre una tercera app Next, para que
    los otros tres no sigan en verde sobre las dos de siempre.
  - **Fuera de alcance a propósito**: `next lint` está deprecado y desaparece en
    Next 16; migrar al CLI de ESLint es de la casilla que suba de mayor.
- **Evidencia**: `next lint` → «No ESLint warnings or errors» (antes: 8 warnings);
  `tsc --noEmit` exit 0; 135 tests de las áreas tocadas en verde;
  `pytest tests/unit/test_frontend_version_matrix.py` → 7 passed.
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_audit14_09_a
    runtime: node-vitest
    command: "npm --prefix apps/admin-panel run lint"
  - id: auto_audit14_09_b
    runtime: node-vitest
    command: "npm --prefix apps/admin-panel run typecheck && npm --prefix apps/admin-panel run test && npm --prefix apps/admin-panel run build"
  ```

#### `task_audit14_10` — Referencias y runbooks sincronizados

- [x] **Título**: Documentar decisiones y retirar afirmaciones obsoletas verificadas
- **Descripción**: Actualizar referencias de multi-tenancy, KB/embeddings,
  healthchecks y workers; marcar el punto MCP de `analisis-diferidos-2026-07-12.md`
  como resuelto con evidencia del test, sin reescribir el resto del informe.
  Coordinar la normalización global de estados con `prod-15`.
- ✅ **Hecho (2026-08-19)**, ya con las cuatro dependencias (03, 05, 06, 08)
  cerradas, que es lo que la bloqueaba:
  - **multi-tenancy** — `04-reference/multi-tenancy.md`: el eje owner del córtex,
    sus policies y por qué tenant-less no significa sin RLS (ADR 0156 / migración
    0140).
  - **KB / embeddings** — `03-guides/kb-ingestion.md`, `06-runbooks/ollama-gpu-setup.md`
    y el gotcha `ollama-embedding-model-naming.md`, que **afirmaba lo contrario**
    de lo decidido: institucionalizaba la divergencia («no son el mismo string a
    propósito; no los compares entre sí»), que es exactamente la opción C que el
    ADR 0155 descarta.
  - **healthchecks** — `06-runbooks/health-check.md` y `04-reference/sesiones.md`
    (el deadline de envío del WS y por qué la re-validación de credencial de
    prod-09 dependía de que el bucle diera la vuelta).
  - **workers** — `04-reference/backup-restore.md`, `06-runbooks/dr-manual-backup.md`
    y `CONTINUE_HERE.md`: el api-server ya no ejecuta los adaptadores de destino,
    los encola. Y `04-reference/stack-services.md` gana el **watchdog**, que era ya
    un servicio de compose y no aparecía en la referencia de servicios: qué hace,
    por qué vive bajo `profiles:`, que habla con Docker por el socket-proxy y por
    qué su healthcheck de contenedor apunta a `/healthz` y no a `/readyz`.
  - **El punto MCP** de `analisis-diferidos-2026-07-12.md`: marcado resuelto con la
    evidencia (`routers/mcp.py:415/430/442` y el test que lo fija en
    `test_mcp_tool_import_and_threading.py:369+`, incluido el refresco en el
    upsert, que era el tercer agujero). **El texto original se deja intacto**: es
    un informe fechado, y reescribirlo por dentro borraría que el defecto existió.
  - **La normalización global de estados NO se toca aquí**, y no por olvido: vive
    en `prod-15 task_gov_reestado_04`, cuya única pieza pendiente es nombrar
    responsable y ventana de la cola de validación. Eso compromete el calendario
    de una persona; el ADR 0138 lo deja fuera de su alcance por escrito.
- **Tiempo**: 0,5 días · **Complejidad**: s · **Depende de**: tareas 03, 05, 06, 08
- **Tests automáticos**:
  ```yaml
  - id: auto_audit14_10_a
    runtime: python-pytest
    command: "pytest tests/docs -v"
  ```

## Hallazgos cubiertos

| Hallazgo                           | Tareas       |
| ---------------------------------- | ------------ |
| AUD14-01 compose/gate de seguridad | 01           |
| AUD14-02 córtex/RLS                | 02, 03       |
| AUD14-03 embedding model por KB    | 04, 05       |
| AUD14-04 engines Celery dispersos  | 06           |
| AUD14-05 WebSocket sin deadline    | 07           |
| AUD14-06 readiness ausente         | 08           |
| AUD14-07 warnings/toolchain        | 09           |
| AUD14-08 documentación obsoleta    | 10 + prod-15 |

## Riesgos

1. Una política RLS incorrecta puede bloquear el córtex pre-tenant o dar falsa
   seguridad si el engine sigue con BYPASSRLS. El test cross-owner es obligatorio.
2. Cambiar el contrato de embeddings puede exigir reindexado de todos los chunks;
   debe existir dry-run, backup y rollback antes de mutar datos.
3. Migrar 30 módulos de workers de una vez aumenta el blast radius. Hacer lotes por
   familia y ejecutar sus suites antes del siguiente lote.
4. Timeouts WebSocket demasiado bajos expulsan clientes sanos bajo carga. El valor
   debe ser configurable y validado con un cliente lento controlado.
5. Readiness mal cableada puede causar flapping. Solo dependencias críticas y
   deadlines cortos; liveness permanece independiente.

## Tests humanos del plan

```yaml
- id: human_audit14_01
  description: "Hardening y composición reales en host Linux"
  checklist:
    - "docker compose base+monitoring config muestra el baseline en workers y textfile-init"
    - "textfile-init completa chmod y sale 0 con AppArmor cargado"
    - "tests/security queda 100% verde"

- id: human_audit14_02
  description: "Contrato de embeddings visible y honesto"
  checklist:
    - "Crear/editar una KB solo ofrece opciones que la ingesta ejecuta realmente"
    - "Ingestar, buscar y reindexar conserva resultados vectoriales"
    - "Un mismatch de modelo/dimensión produce error visible, no BM25-only silencioso"

- id: human_audit14_03
  description: "Degradación y recuperación operativa"
  checklist:
    - "Con PostgreSQL o Redis caído, /healthz sigue vivo y /readyz devuelve 503"
    - "Al recuperar la dependencia, /readyz vuelve a 200 sin reinicio"
    - "Un cliente WebSocket lento se cierra sin crecimiento sostenido de tasks/memoria"
```

## Criterios de cierre

1. Todos los checkboxes están `[x]` tras ejecutar sus tests automáticos.
2. `pytest tests/docs tests/security -q` está verde.
3. Suite unitaria, ruff, mypy, Vitest, lint y build siguen verdes.
4. ADRs de córtex y embeddings están ratificados por humano antes de implementar.
5. Tests humanos del plan validados.
6. Entrada en `docs/07-changelog/remediacion-auditoria-integral-2026-07-14.md`.
7. PR del plan mergeado a la rama por defecto conforme al protocolo vigente.
