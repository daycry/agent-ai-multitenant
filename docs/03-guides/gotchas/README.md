# Gotchas / troubleshooting

Notas sobre **bugs no-obvios** del toolchain que ya nos han pasado y
cómo se resolvieron. Antes de inventar un fix nuevo para un error de
Docker, Compose, mypy, pre-commit, asyncpg, OpenTelemetry o
git/Windows, **busca aquí primero**: hay muchas posibilidades de que
el problema ya esté documentado.

> Estas notas NO sustituyen a un ADR. Un ADR documenta una **decisión**
> arquitectónica; una gotcha documenta una **trampa** del toolchain.

## Cómo añadir una nueva

1. Crea un archivo nuevo con slug `<area>-<short-slug>.md` (e.g.
   `docker-compose-volumes-merge.md`, `windows-asyncio-proactor.md`).
2. Usa el front-matter:

   ```yaml
   ---
   title: ... # frase corta
   area: docker | python | windows | pre-commit | otel | postgres | ...
   encountered: 2026-05-21 # fecha
   stack: docker compose v2.x, vault 1.17, ... # versiones afectadas
   ---
   ```

3. Cuatro secciones obligatorias:
   - **Síntoma** — qué se ve.
   - **Causa raíz** — por qué pasa.
   - **Fix** — qué se cambió.
   - **Cómo verificar el fix** — comando o aserción que valida la solución.

4. Cuando una versión más reciente del tooling vuelve la nota
   obsoleta, **edita la nota** en vez de borrarla: añade una nota
   "obsoleto desde X.Y" arriba para que el lector lo vea.

## Índice por área

### docker / docker-compose

- [docker-compose-volumes-merge.md](./docker-compose-volumes-merge.md)
  — `volumes:` se mergea entre overrides, no se reemplaza.
- [readiness-en-el-healthcheck-del-contenedor-es-un-bucle.md](./readiness-en-el-healthcheck-del-contenedor-es-un-bucle.md)
  — apuntar el `healthcheck` de la api-server a `/readyz` parece la mejora obvia
  y es un bucle de reinicios: hay UN healthcheck por contenedor, readiness prueba
  dependencias externas y el watchdog reinicia lo `unhealthy`. Liveness al
  contenedor (`/healthz`), readiness al proxy (`health_uri /readyz`).
- [vault-dev-mode-port-conflict.md](./vault-dev-mode-port-conflict.md)
  — `-dev` + config.hcl mount → EADDRINUSE 8200.
- [vault-entrypoint-config-flag.md](./vault-entrypoint-config-flag.md)
  — pasar `-config=` al `command:` choca con el entrypoint.
- [docling-mcp-no-public-image.md](./docling-mcp-no-public-image.md)
  — `ghcr.io/docling-project/docling-mcp` no existe; el dev compose
  lo deja comentado.
- [minio-dev-volume-xl-meta-version.md](./minio-dev-volume-xl-meta-version.md)
  — `decodeXLHeaders: Unknown xl meta version N`: el volumen dev lo
  escribió una build más nueva; recrear el volumen o subir el pin.
- [minio-delete-service-account-no-es-idempotente.md](./minio-delete-service-account-no-es-idempotente.md)
  — `404 XMinioInvalidIAMCredentials`: borrar dos veces una service account
  revienta, así que el paso 4 de la rotación no se podía reintentar. Ningún
  doble lo reproducía.

- [image-build-recipes-that-bite.md](./image-build-recipes-that-bite.md)
  — las seis recetas que muerden en un sitio: **tres** imágenes cuelgan de
  `BASE_IMAGE` (workers, orchestrator y notification-dispatcher — la base `:ci`
  está desfasada), `WITH_CLAUDE=1` para el SDK opcional, el grafo del agente
  vive en la imagen BASE y su contexto es la raíz, el worker lanza
  `agent-runtime:v1` **SIN prefijo** (construir el prefijado no llega a los
  runs), y admin-panel se construye DESDE PowerShell (Git Bash mangla el
  build-arg de ruta).
- [trivy-en-rojo-tres-causas-distintas.md](./trivy-en-rojo-tres-causas-distintas.md)
  — Trivy se pone rojo SOLO (refresca su BD cada corrida), y las tres causas se
  confunden: (1) el paquete viene de la base → refrescar digest; (2) lo instala
  nuestro `apt-get` pero la capa está `CACHED` por `type=gha` → un `apt-get upgrade`
  NO sirve, hay que invalidar la capa; (3) el binario no tiene dueño en dpkg
  (`/usr/bin/pebble` de Canonical) → no lo arregla ni el digest ni apt.
- [deploy-relaunches-frozen-tasks.md](./deploy-relaunches-frozen-tasks.md)
  — un `up -d` lanza runs que nadie pidió: el reconciler rescata a los 90 s las
  tareas `in_progress` **sin ejecución** (>30 min), y son invisibles al chequeo
  de «¿queda algo corriendo?». Despliega con `--scale orchestrator=0`.
- [agent-run-failed-si-el-sandbox-no-alcanza-la-api-interna.md](./agent-run-failed-si-el-sandbox-no-alcanza-la-api-interna.md)
  — `assert 'failed' == 'done'` sin una palabra sobre red: con **agente
  asignado** el worker mintea el token interno y el runtime **no arranca** si
  `/internal/agent/*` no contesta en `agentic-agents` (prod-01 task_11). El
  discriminante es `agent_id`, no la credencial del LLM; en CI faltaba el
  `api-server` entero.

### ollama / embeddings

- [ollama-embedding-model-naming.md](./ollama-embedding-model-naming.md)
  — el embedder pide `nomic-embed-text` (NO `-v1.5`) y debe ser de 768 dims;
  el modelo es configurable (ADR 0056), cambiar de dims es Plan 12.

### postgres / asyncpg / sqlalchemy

- [alter-role-password-es-de-cluster-no-de-base.md](./alter-role-password-es-de-cluster-no-de-base.md)
  — un `ALTER ROLE ... PASSWORD` cambia la credencial en TODAS las bases del
  servidor, no en la que crees. Rompe el stack y el contenedor sigue
  `healthy`, porque `/healthz` no toca la base: la señal es `/readyz` a 503 y
  `pg_stat_activity` sin el rol de la aplicación.

- [postgres-port-clash-with-laragon.md](./postgres-port-clash-with-laragon.md)
  — host 5432 lo ocupa Laragon; usamos 15432.
- [postgres-roles-bypassrls.md](./postgres-roles-bypassrls.md)
  — `migrations_user` necesita `BYPASSRLS`, `app_user` no.
- [postgres-alter-default-privileges-per-db.md](./postgres-alter-default-privileges-per-db.md)
  — los privilegios por defecto no viajan a una BD nueva, así que una BD migrada
  a mano queda completa y muda: el login cae con `permission denied for table
user_mfa_totp`. Y el aviso contrario, que es peor porque pasa en verde:
  conceder de más deshace los REVOKE deliberados (migración 0138) y deja el arnés
  MÁS permisivo que producción.
- [asyncpg-set-local-no-bind-params.md](./asyncpg-set-local-no-bind-params.md)
  — `SET LOCAL x = $1` falla; usar `set_config('x', $1, true)`.
- [asyncpg-no-multistatement.md](./asyncpg-no-multistatement.md)
  — `op.execute` no acepta múltiples sentencias separadas por `;`.
- [alembic-metadata-a-medias-propone-borrar-lo-que-no-ve.md](./alembic-metadata-a-medias-propone-borrar-lo-que-no-ve.md)
  — `alembic check` que muere con `NoReferencedTableError` NO es un problema
  local: `env.py` importaba un solo módulo de la capa de datos y `Base.metadata`
  veía 34 tablas de 84. Para autogenerate, «no está en la metadata» y «bórralo de
  la BD» son lo mismo, así que un `--autogenerate` con la metadata a medias
  propone `DROP INDEX` sobre el HNSW del RAG — y aplicarlo no da error: la
  búsqueda pasa a secuencial en silencio.
- [postgres-unique-igual-a-la-pk-se-descarta-en-silencio.md](./postgres-unique-igual-a-la-pk-se-descarta-en-silencio.md)
  — un `UniqueConstraint` con las MISMAS columnas que la PRIMARY KEY no se crea:
  PostgreSQL lo descarta dentro del `CREATE TABLE`, sin error ni `NOTICE`, aunque
  el DDL que Alembic emite lo incluya. El objeto nunca existió, así que
  `alembic check` propone crearlo para siempre y la migración «obvia» añade un
  índice único redundante. `pg_constraint` es la autoridad, no el fichero de
  migración.
- [alembic-revision-id-32-chars.md](./alembic-revision-id-32-chars.md)
  — `alembic_version.version_num` es `varchar(32)`: un revision id > 32
  chars revienta con `StringDataRightTruncationError`.
- [alembic-db-ahead-after-branch-switch.md](./alembic-db-ahead-after-branch-switch.md)
  — `Can't locate revision identified by 'XXXX'`: la BD se migró en otro
  branch y quedó por delante; downgrade trayendo los ficheros, no
  `UPDATE alembic_version`.
- [alembic-dev-db-branch-only-revision.md](./alembic-dev-db-branch-only-revision.md)
  — `Can't locate revision`: la DB de dev quedó en una revisión cuyo fichero
  solo existe en otra rama; verifica migraciones SOLO en la DB de test.
- [downgrade-que-asume-que-no-hay-datos.md](./downgrade-que-asume-que-no-hay-datos.md)
  — `column "X" contains null values` al bajar: un `downgrade` que restaura un
  NOT NULL confiando en que «no puede haber filas de este tipo». El test sólo
  se pone rojo en la suite completa (BD de ámbito sesión compartida), y parece
  flaky de orden cuando denuncia una cadena no reversible.
- [postgres-parametro-opcional-sin-tipo-en-text.md](./postgres-parametro-opcional-sin-tipo-en-text.md)
  — `could not determine data type of parameter $n`: un filtro opcional
  `:x IS NULL OR col = :x` en `text()` no le da tipo al parámetro, y el cast
  obvio (`:x::uuid`) lo empeora porque el regex de bind params de `text()` no
  reconoce un parámetro seguido de `::` y lo manda sin valor. `CAST(:x AS tipo)`
  arregla las dos.
- [sqlalchemy-flush-fallido-mata-la-transaccion-exterior.md](./sqlalchemy-flush-fallido-mata-la-transaccion-exterior.md)
  — `Can't operate on closed transaction inside context manager`: un `flush()`
  que falla deja `DEACTIVE` la transacción EXTERIOR aunque vaya dentro de
  `begin_nested()`, y el `rollback()` que la desatasca se lleva el
  `set_config('app.tenant_id')` de la RLS → consultas que devuelven vacío sin
  error. Consulta ANTES del flush.

### llm-providers

- [llm-provider-resolution-two-paths.md](./llm-provider-resolution-two-paths.md)
  — hay DOS vías de resolver un proveedor (por `provider_id` = la fila concreta,
  y por `kind` = el más nuevo activo). Mezclarlas hace que sincronizar
  `ollama-cloud` traiga los modelos de `ollama-local`.

### runs / celery / datos

- [celery-visibility-timeout-redelivery-window.md](./celery-visibility-timeout-redelivery-window.md)
  — un run muerto tarda ~7 h en re-entregarse: el `visibility_timeout` está por
  encima del hard-limit A PROPÓSITO, para no duplicar runs largos sanos.
- [git-and-data-ops-belong-in-the-worker.md](./git-and-data-ops-belong-in-the-worker.md)
  — la api-server NO monta el volumen de repos: cualquier operación git o de
  `/data` se delega al worker por Celery.
- [verify-routes-with-curl-not-app-import.md](./verify-routes-with-curl-not-app-import.md)
  — importar `api_server.main:app` en el contenedor da una app PARCIAL; verificar
  con `curl` al gateway, y leer 401 como «existe» y 404 como «no montada».

### pre-commit / mypy / ruff

- [pre-commit-revierte-el-arbol-de-los-agentes-en-paralelo.md](./pre-commit-revierte-el-arbol-de-los-agentes-en-paralelo.md)
  — un agente ve sus ediciones borradas sin haber tocado git: `pre-commit`
  **aparta los unstaged a un patch** mientras corren los hooks, y con el hook de
  mypy sobre el árbol entero esa ventana dura minutos. No comitees mientras haya
  agentes escribiendo.
- [pre-commit-python-version-pin.md](./pre-commit-python-version-pin.md)
  — pin `python3.12` rompe en host 3.13.
- [pre-commit-checkyaml-compose-tags.md](./pre-commit-checkyaml-compose-tags.md)
  — `check-yaml` no entiende `!reset` / `!override`.
- [mypy-local-package-imports.md](./mypy-local-package-imports.md)
  — el hook venv no ve los `apps/<x>` editables; usar `additional_dependencies`
  o `exclude:`.
- [precommit-mixed-line-ending-vs-gitattributes.md](./precommit-mixed-line-ending-vs-gitattributes.md)
  — `mixed-line-ending --fix=lf` no respeta `.gitattributes`; hay que
  excluir `.ps1` / `.cmd` / `.bat` del hook.
- [black-vs-ruff-format-chained-call-comment.md](./black-vs-ruff-format-chained-call-comment.md)
  — los dos formateadores se pelean en bucle por un comentario DENTRO de una
  llamada encadenada; sacarlo fuera del paréntesis los hace converger.
- [node-modules-a-medio-instalar-finge-regresion.md](./node-modules-a-medio-instalar-finge-regresion.md)
  — un `npm install` interrumpido finge una regresión de versión: 4 tests rojos
  «por el bump» que pasaban tras reinstalar del todo. Si comparas dos versiones,
  las dos ramas necesitan instalación COMPLETA.
- [prettier-hook-version-vs-npx.md](./prettier-hook-version-vs-npx.md)
  — el hook usa `mirrors-prettier` **v4.0.0-alpha.8** pineado y `npx prettier` baja
  la última v3: formatear a mano no arregla el hook y el commit falla en bucle.
  Arréglalo con `pre_commit run prettier --files …`.
- [prettier-all-files-libuv-windows.md](./prettier-all-files-libuv-windows.md)
  — `prettier --all-files` crashea en Windows por libuv
  (`UV_HANDLE_CLOSING`, exit 3221226505); usar prettier _scoped_
  (`--files <cambiados>`).

### opentelemetry

- [otel-console-exporter-pytest-stdout.md](./otel-console-exporter-pytest-stdout.md)
  — `ConsoleSpanExporter` revienta cuando pytest captura stdout.
- [otel-global-provider-tests.md](./otel-global-provider-tests.md)
  — el provider global no se puede reemplazar; tests añaden un span processor.
  Incluye la reincidencia de 2026-08-18: `configure_tracing()` devolvía el
  provider que OTEL había descartado, y el exporter quedaba mudo sin error.

### windows

- [node-exporter-rslave-windows.md](./node-exporter-rslave-windows.md)
  — node-exporter no arranca en Docker Desktop por el mount `rslave` de `/`;
  override `docker-compose.windows.yml` con `volumes: !override` (no `!reset`).
- [windows-asyncio-engine-dispose.md](./windows-asyncio-engine-dispose.md)
  — `asyncio.run(engine.dispose())` en teardown crashea el proactor.
- [windows-git-crlf-vs-hooks.md](./windows-git-crlf-vs-hooks.md)
  — `core.autocrlf` pelea con `mixed-line-ending`; arreglado con
  `.gitattributes`.
- [python-write-text-crlf-en-windows.md](./python-write-text-crlf-en-windows.md)
  — editar un fuente con `pathlib.write_text()` desde un script (p. ej. para
  mutar y restaurar al comprobar un RED) lo reescribe ENTERO en CRLF, y
  `git diff` no lo enseña. Usa `write_bytes` o `newline="\n"`.
- [windows-commit-to-branch-not-reentrant.md](./windows-commit-to-branch-not-reentrant.md)
  — el helper de tests `commit_to_branch` no puede llamarse dos veces sobre la
  MISMA rama: su `rmtree(ignore_errors=True)` no borra los objetos read-only de
  `.git` en Windows y el segundo `git clone` sale con 128. Avanza la punta con
  `commit-tree` + `update-ref` dentro del bare.
- [windows-tcp-ghost-listener.md](./windows-tcp-ghost-listener.md)
  — `Get-NetTCPConnection` reporta un listener cuyo PID ya no
  existe; bind real (no la query) es la única fuente fiable.
- [powershell-invoke-restmethod-localhost-hang.md](./powershell-invoke-restmethod-localhost-hang.md)
  — `Invoke-RestMethod http://localhost:N/...` se cuelga porque
  resuelve `::1` antes de `127.0.0.1`; usa la IP explícita.
- [powershell-ps1-vs-python-py.md](./powershell-ps1-vs-python-py.md)
  — `.ps1` se invoca directo, `.py` con `.venv\Scripts\python.exe`;
  mezclarlos da `SyntaxError` o pantalla en blanco.
- [docker-msys-build-arg-leading-slash-windows.md](./docker-msys-build-arg-leading-slash-windows.md)
  — `docker build --build-arg X=/api` desde Git Bash mangla `/api` a
  `C:/Program Files/Git/api` (MSYS); hornea una URL rota en el admin-panel.
  Construir desde PowerShell o con `MSYS_NO_PATHCONV=1`. Linux/prod no afectados.

### admin-panel / next.js

- [admin-documents-no-root-route.md](./admin-documents-no-root-route.md)
  — `/admin/documents/<id>` sin sufijo da 404; usa `/citations` o
  `/ingestion`.
- [executions-steps-log-shape.md](./executions-steps-log-shape.md)
  — la Timeline requiere `steps_log` con `index` + shape canónico
  (`agent_runtime.steps`).
- [uvicorn-windows-multiprocessing-spawn.md](./uvicorn-windows-multiprocessing-spawn.md)
  — `Stop-Process` no mata workers spawnados por `multiprocessing`;
  usa `taskkill /F /T`.

### next.js / typescript

- [nextjs-eslint-root-inherit.md](./nextjs-eslint-root-inherit.md)
  — `.eslintrc.json` hereda del root y exige plugins TS ausentes;
  `root: true`.
- [nextjs-public-env-build-time.md](./nextjs-public-env-build-time.md)
  — `NEXT_PUBLIC_*` se inlinea al compilar; setearla después de
  `npm run dev` no la actualiza.

### redis / auth

- [redis-aof-ignores-a-restored-rdb.md](./redis-aof-ignores-a-restored-rdb.md)
  — con `appendonly yes` (como lo arranca el compose), un Redis que encuentra un
  `dump.rdb` y ningún `appendonlydir` **no lee el RDB**: crea un AOF vacío y
  sirve `DBSIZE 0` sin un solo error. Un backup/restore basado en `BGSAVE` +
  `dump.rdb` restaura sesiones, broker de Celery y rate limits VACÍOS y nadie se
  entera. Se captura el `appendonlydir` tras un `BGREWRITEAOF`.
- [celery-defaults-del-api-server-ignoran-redis-port.md](./celery-defaults-del-api-server-ignoran-redis-port.md)
  — con `REDIS_PORT=6380` en `docker/.env`, los defaults de Celery del api-server
  siguen apuntando al 6379, donde hay algo que **acepta la conexión y no
  contesta**: cada enqueue desde un proceso del host cuesta ~110 s antes de
  rendirse, y el turno del córtex se los come enteros. Hay que redirigir DOS
  variables, no una: la que revienta es `API_SERVER_RESULT_BACKEND`.
- [auth-rate-limit-dev-loop.md](./auth-rate-limit-dev-loop.md)
  — el rate limit de `/auth/login` (5 por 15 min, contando también los logins que
  ACIERTAN) no cabe en una tanda e2e, y su síntoma no es un 429 visible: es un
  `toHaveURL` que no llega nunca y un caso que consume su timeout entero. Limpia
  `rl:login:*` en un bucle de desarrollo; sube
  `API_SERVER_LOGIN_RATE_LIMIT_COUNT` sólo en el arnés.
- [redis-con-contrasena-rompe-la-integracion.md](./redis-con-contrasena-rompe-la-integracion.md)
  — al activar `--requirepass` (endurecimiento de prod-10) los **249 ficheros**
  de integración mueren dentro de una fixture con `AuthenticationError`, porque
  el conftest construía su URL de Redis a mano y sin credencial. La aplicación,
  en cambio, funciona. Al endurecer una credencial, busca quién más la construye
  a mano.
- [joserfc-decode-no-valida-exp.md](./joserfc-decode-no-valida-exp.md)
  — `joserfc.jwt.decode` verifica la FIRMA y nada más: acepta tokens caducados
  sin un solo error (la validación de `exp` es una llamada aparte a
  `JWTClaimsRegistry`), y con una clave `str` lanza un `ValueError` que no es
  `JoseError` → 500 en vez de 401. Migrar desde `python-jose` deja la suite en
  verde con las sesiones convertidas en eternas.

### ci / github actions

- [expresion-anidada-en-actions-es-texto-literal.md](./expresion-anidada-en-actions-es-texto-literal.md)
  — `invalid tag "ghcr.io/${{ github.repository_owner }}/..."` con **actionlint
  en verde**: una expresión dentro de otra ya abierta es una CADENA, no se
  evalúa. El valor va como argumento de `format(...)`, y `env` no está
  disponible en un `env:` de nivel job (`github` sí). Duele el doble en un
  camino que sólo corre en `master`: el merge es su primera ejecución.
- [ci-github-actions-node-deprecation.md](./ci-github-actions-node-deprecation.md)
  — Node 20 deprecado; `actions/checkout@v4 → v5`, etc.
- [ci-no-tiene-docker-env-y-el-compose-lo-exige.md](./ci-no-tiene-docker-env-y-el-compose-lo-exige.md)
  — `required variable SERVICE_USER_PASSWORD is missing a value` en
  `docker compose config`, y en local exit 0: el runner no tiene `docker/.env`
  (gitignored) y desde prod-10 cada credencial es `${VAR:?…}`, que aborta el
  proyecto ENTERO —también `logs` y el `down` del teardown—. El workflow copia
  `.env.example` en vez de enumerar las variables a mano.
- [test-fixture-admin-db-url-override.md](./test-fixture-admin-db-url-override.md)
  — el api-server usa DOS urls de BD y quien setea `API_SERVER_DATABASE_URL` pero
  NO `API_SERVER_ADMIN_DATABASE_URL` deja el admin engine en su DSN por defecto,
  que apunta a `agentic_platform`: **la base del operador**. En una fixture eso
  es verde en local y `relation X does not exist` solo-en-CI; en un arnés e2e a
  mano el síntoma es un 500 opaco en el login y la consecuencia es escribir en la
  base de producción.
- [playwright-route-glob-intercepts-navigation.md](./playwright-route-glob-intercepts-navigation.md)
  — `page.route("**/X")` en Playwright 1.60 intercepta también la navegación
  `page.goto(".../X")` (misma cola de path) → la página recibe el JSON del mock
  como documento. Usar predicado por `pathname` exacto, no glob desnudo.
- [playwright-next-dev-compila-la-ruta-y-agota-el-test.md](./playwright-next-dev-compila-la-ruta-y-agota-el-test.md)
  — una e2e falla en «Cargando…» y la captura del fallo muestra la página
  perfecta: `next dev` compila la ruta en la primera petición (27,8 s medidos en
  una máquina cargada, contra 30 s de presupuesto por test). CI no lo sufre
  porque sirve un build de producción; la receta está en el gotcha. No se
  arregla subiendo el timeout.
- [expect-de-cinco-segundos-no-cubre-un-backend-vivo.md](./expect-de-cinco-segundos-no-cubre-un-backend-vivo.md)
  — el caso OPUESTO al anterior, y el gotcha explica el criterio para no
  confundirlos: contra backend vivo, 21 de 41 casos caen por el reloj porque
  el `services-grid` del dashboard espera a `/admin/system-health`, cuyo techo por
  sonda es de 10 s (las ocho van en paralelo, así que NO se suman).
  5 s es aritméticamente insuficiente; se sube con `E2E_EXPECT_TIMEOUT` sólo en
  esos specs, con el default intacto para el subset mockeado de CI.

### tests (patrones que engañan)

- [tests-caplog-vs-logging-disable.md](./tests-caplog-vs-logging-disable.md)
  — `caplog` deja de ver el record en la suite completa (integración corre antes
  y la app llama `logging.disable`); usar un logger falso, no caplog.
- [integration-tests-asyncpg-needs-the-plain-dsn.md](./integration-tests-asyncpg-needs-the-plain-dsn.md)
  — `asyncpg.connect` necesita `migrations_pg_dsn`; `admin_database_url` lleva
  `+asyncpg` y es para SQLAlchemy.
- [vitest-select-change-before-options-load.md](./vitest-select-change-before-options-load.md)
  — un `fireEvent.change` sobre un `<select>` sin opciones cargadas se descarta
  EN SILENCIO; esperar a las `<option>` primero.
- [workflow-parallel-review-source-contamination.md](./workflow-parallel-review-source-contamination.md)
  — un «flaky» reportado por revisores en paralelo puede ser contaminación entre
  ellos; re-correr en serie sobre el árbol limpio antes de creérselo.
- [integration-tests-share-one-database.md](./integration-tests-share-one-database.md)
  — dos pytest de integración a la vez se dropean la BD mutuamente (una sola
  `agentic_platform_test` para todo el repo); `TEST_PG_DB_NAME` distinto por proceso.
- [tests-de-integracion-en-la-redis-del-stack-vivo.md](./tests-de-integracion-en-la-redis-del-stack-vivo.md)
  — dar a cada shard «su» base de Redis (1, 2, 3…) mete el arnés en el broker de
  Celery del stack levantado: el worker vivo drena la cola y el test cae con
  `assert len(raw) == 1` sobre cero elementos, tres capas más allá. De la 5 en adelante.
- [cuatro-shards-y-cinco-agentes-tumban-postgres.md](./cuatro-shards-y-cinco-agentes-tumban-postgres.md)
  — separar `TEST_PG_DB_NAME` y `TEST_REDIS_URL` por shard no separa la RAM del
  anfitrión: 4 pytest + 5 subagentes + el stack dejan 0 GB libres, WSL2 reinicia y
  Postgres vuelve haciendo recuperación. `restartCount=0` NO lo desmiente. Los
  rojos de esa pasada no valen: cuatro de ocho pasaron al repetirlos en serie.
- [localhost-ipv6-primero-cuesta-dos-segundos.md](./localhost-ipv6-primero-cuesta-dos-segundos.md)
  — `localhost` resuelve `::1` antes que `127.0.0.1` y Docker Desktop sólo escucha
  en IPv4: 2 s regalados por CADA conexión del arnés, sin ningún error. Se ve en
  `/readyz`, cuyo deadline por check es de 2 s, que da 503 con las dependencias vivas.
- [git-checkout-para-deshacer-una-mutacion-borra-el-trabajo-ajeno.md](./git-checkout-para-deshacer-una-mutacion-borra-el-trabajo-ajeno.md)
  — `git checkout -- fichero` no deshace TU cambio: restaura el fichero entero
  desde el índice y se lleva las 149 líneas sin comitear que había encima. Pasó
  dos veces el mismo día. Revierte la mutación en sentido inverso, y mira
  `git diff --stat` antes: si el número no es el tuyo, no es tu reversión.
- [cambio-de-contrato-deja-tests-rezagados.md](./cambio-de-contrato-deja-tests-rezagados.md)
  — cambiar el contrato de una ruta (un 200 que pasa a 303) deja rojos tests que
  NO tocaste y que no volviste a correr. Buscar por la RUTA (`grep -rn "/auth/sso"`),
  no por el fichero que editaste.
- [arreglar-la-cache-rompe-tests-que-vivian-de-su-fallo.md](./arreglar-la-cache-rompe-tests-que-vivian-de-su-fallo.md)
  — reparar una caché rota pone ROJOS tests que pasaban precisamente porque no
  cacheaba. El rojo es la prueba de que el arreglo funciona, no una regresión.
- [fastapi-0141-include-router-no-aplana.md](./fastapi-0141-include-router-no-aplana.md)
  — FastAPI 0.141 dejó de aplanar `include_router`: `app.routes` pierde 300 rutas
  sin dar un error, y las guardas que introspeccionan rutas pasan sobre una lista
  casi vacía. Descender por `original_router` acumulando el prefijo.
- [sembrar-filas-retrofechadas-en-tabla-particionada.md](./sembrar-filas-retrofechadas-en-tabla-particionada.md)
  — un test de ventana temporal siembra a propósito FUERA de la ventana
  (`now - 100 días`) y muere con `no partition of relation found for row`: las
  cinco tablas del ADR 0151 no tienen partición `DEFAULT` y detrás del mes en
  curso no hay nada. Usar `ensure_partition_for`, que reusa el DDL de producción
  (con RLS); crearla a mano deja una partición SIN aislamiento entre tenants.
- [singleton-httpx-atado-al-event-loop.md](./singleton-httpx-atado-al-event-loop.md)
  — una guarda de fuga entre tenants que pasa SOLA y falla EN LOTE con un 500: el
  `httpx.AsyncClient` en `lru_cache` conserva conexiones de un loop ya cerrado
  (`RuntimeError: Event loop is closed`), y ese error no degrada a BM25 como sí
  hace un `httpx.HTTPError`. Antes de creerse la contaminación entre tests, mira
  qué caché global de PRODUCCIÓN sobrevive entre peticiones.
- [pytest-en-segundo-plano-no-avisa-de-que-docker-murio.md](./pytest-en-segundo-plano-no-avisa-de-que-docker-murio.md)
  — una hora de pytest quemando CPU contra una base que ya no existía: `-q` con
  stdout redirigido no vuelca los puntos, y las fixtures reintentan la conexión,
  así que «proceso vivo con CPU» se lee como progreso. Comprobar `docker ps`
  ANTES de esperar, partir en shards y usar `--timeout`.
- [partitioned-table-introspection.md](./partitioned-table-introspection.md)
  — una tabla particionada es `relkind = 'p'`: la introspección que filtra por
  `'r'` la declara SIN RLS teniéndola (falso positivo que invita a eximirla), y
  asyncpg devuelve `relkind` como `bytes`.
- [alembic-round-trip-anclado-por-nombre.md](./alembic-round-trip-anclado-por-nombre.md)
  — `command.downgrade(cfg, "-1")` es relativo a la CABEZA, no a la migración que
  el test quiere deshacer: en cuanto se apila otra encima, el test deja de cubrir
  lo que decía cubrir y luego falla señalando a una migración inocente. Anclar por
  nombre a la revisión anterior.
- [rls-en-bucle-invisible-para-el-guard-estatico.md](./rls-en-bucle-invisible-para-el-guard-estatico.md)
  — activar la RLS con `op.execute(f"ALTER TABLE {table} ENABLE …")` deja en el
  fichero una sentencia sin nombre de tabla: el guard ESTÁTICO de
  `test_pentest_findings.py` no la ve y pide eximir la tabla que sí está
  protegida. Sentencias literales, una por tabla.
- [commit-a-media-request-pierde-los-guc-de-rls.md](./commit-a-media-request-pierde-los-guc-de-rls.md)
  — `set_config('app.tenant_id', …, true)` tiene ámbito de TRANSACCIÓN, así que un
  `await session.commit()` dentro de un handler borra el contexto de tenant: la
  consulta siguiente devuelve cero filas con `app_user` o filas de todos los
  tenants con la sesión del System Admin. Síntoma: ninguno hasta que alguien añade
  esa consulta. Usar `schedule_after_commit`.
- [beat-entry-whose-task-nobody-imports.md](./beat-entry-whose-task-nobody-imports.md)
  — una entrada de beat cuyo módulo no está en `celery_app(imports=...)` se encola
  y muere con `NotRegistered` **sin ruido**: seis features «desplegadas» que nunca
  corrieron. Síntoma: ninguno. Guarda genérica en `test_approval_expiry_beat.py`.
