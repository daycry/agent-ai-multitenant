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

- [image-build-recipes-that-bite.md](./image-build-recipes-that-bite.md)
  — las seis recetas que muerden en un sitio: **tres** imágenes cuelgan de
  `BASE_IMAGE` (workers, orchestrator y notification-dispatcher — la base `:ci`
  está desfasada), `WITH_CLAUDE=1` para el SDK opcional, el grafo del agente
  vive en la imagen BASE y su contexto es la raíz, el worker lanza
  `agent-runtime:v1` **SIN prefijo** (construir el prefijado no llega a los
  runs), y admin-panel se construye DESDE PowerShell (Git Bash mangla el
  build-arg de ruta).
- [deploy-relaunches-frozen-tasks.md](./deploy-relaunches-frozen-tasks.md)
  — un `up -d` lanza runs que nadie pidió: el reconciler rescata a los 90 s las
  tareas `in_progress` **sin ejecución** (>30 min), y son invisibles al chequeo
  de «¿queda algo corriendo?». Despliega con `--scale orchestrator=0`.

### ollama / embeddings

- [ollama-embedding-model-naming.md](./ollama-embedding-model-naming.md)
  — el embedder pide `nomic-embed-text` (NO `-v1.5`) y debe ser de 768 dims;
  el modelo es configurable (ADR 0056), cambiar de dims es Plan 12.

### postgres / asyncpg / sqlalchemy

- [postgres-port-clash-with-laragon.md](./postgres-port-clash-with-laragon.md)
  — host 5432 lo ocupa Laragon; usamos 15432.
- [postgres-roles-bypassrls.md](./postgres-roles-bypassrls.md)
  — `migrations_user` necesita `BYPASSRLS`, `app_user` no.
- [postgres-alter-default-privileges-per-db.md](./postgres-alter-default-privileges-per-db.md)
  — los privilegios por defecto no viajan a una BD nueva.
- [asyncpg-set-local-no-bind-params.md](./asyncpg-set-local-no-bind-params.md)
  — `SET LOCAL x = $1` falla; usar `set_config('x', $1, true)`.
- [asyncpg-no-multistatement.md](./asyncpg-no-multistatement.md)
  — `op.execute` no acepta múltiples sentencias separadas por `;`.
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
- [auth-rate-limit-dev-loop.md](./auth-rate-limit-dev-loop.md)
  — el rate limit de `/auth/login` se acumula entre runs del E2E
  y trips 429; limpia `rl:login:*` antes de probar.
- [joserfc-decode-no-valida-exp.md](./joserfc-decode-no-valida-exp.md)
  — `joserfc.jwt.decode` verifica la FIRMA y nada más: acepta tokens caducados
  sin un solo error (la validación de `exp` es una llamada aparte a
  `JWTClaimsRegistry`), y con una clave `str` lanza un `ValueError` que no es
  `JoseError` → 500 en vez de 401. Migrar desde `python-jose` deja la suite en
  verde con las sesiones convertidas en eternas.

### ci / github actions

- [ci-github-actions-node-deprecation.md](./ci-github-actions-node-deprecation.md)
  — Node 20 deprecado; `actions/checkout@v4 → v5`, etc.
- [test-fixture-admin-db-url-override.md](./test-fixture-admin-db-url-override.md)
  — fixture que setea `API_SERVER_DATABASE_URL` pero NO
  `API_SERVER_ADMIN_DATABASE_URL`: el admin engine cae al DSN por defecto;
  verde en local (BD dev migrada) pero `relation X does not exist` solo-en-CI
  (BD por defecto vacía).
- [playwright-route-glob-intercepts-navigation.md](./playwright-route-glob-intercepts-navigation.md)
  — `page.route("**/X")` en Playwright 1.60 intercepta también la navegación
  `page.goto(".../X")` (misma cola de path) → la página recibe el JSON del mock
  como documento. Usar predicado por `pathname` exacto, no glob desnudo.

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
- [beat-entry-whose-task-nobody-imports.md](./beat-entry-whose-task-nobody-imports.md)
  — una entrada de beat cuyo módulo no está en `celery_app(imports=...)` se encola
  y muere con `NotRegistered` **sin ruido**: seis features «desplegadas» que nunca
  corrieron. Síntoma: ninguno. Guarda genérica en `test_approval_expiry_beat.py`.
