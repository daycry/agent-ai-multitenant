---
title: "Comparativa AutoGPT — qué merece la pena adaptar y qué no"
status: informe
date: 2026-08-27
tipo: analisis-comparativo
docs_language: es
---

# Comparativa AutoGPT → nuestra plataforma. Informe para el operador

Fecha: 2026-08-27. Rama: `chore/infra-images-un-nombre-y-trivy`. Revisión en solo lectura sobre `master` y el repo real de AutoGPT (rama `dev`/`master` de hoy, no de memoria).

---

## 1. La respuesta en tres líneas

**No merece la pena adaptar prácticamente nada de AutoGPT.** De ~35 candidatos examinados, 13 propuestas fueron refutadas con fichero:línea, 14 no aplican a nuestro alcance, y de las 3 que sobrevivieron ninguna es en rigor una adopción: dos son **defectos nuestros** que el ejercicio de comparar destapó, y la tercera se implementa copiando nuestro propio `routers/plans.py`, no su código.

El valor de este encargo no está en lo que se copia (≈1 día-persona de idea ajena), sino en **lo que se decide no hacer** (~80 días-persona de propuestas que suenan bien y estarían mal) y en **siete defectos propios verificados** que la comparación sacó a la luz, entre ellos uno de ejecución de comando en el contenedor del worker y una instalación que hoy no puede terminar en una máquina limpia.

Si sólo se lee una línea: **cortar el tag `v1.0.0` y validar `remote_url` son lo urgente; AutoGPT no lo es.**

---

## 2. Lo que vale la pena copiar

### 2A. Lo que de verdad viene de ellos: una cosa, y a medias

**Avisar de que una publicación del marketplace fue aprobada o rechazada — 1 día-persona.**

- _Problema nuestro_: D6 mete una espera humana en el flujo de publicación y hoy nadie empuja el veredicto. Cero referencias a notificación en `apps/api-server/src/api_server/marketplace/review.py` y en `apps/api-server/src/api_server/routers/marketplace/admin.py`; el `EVENT_REGISTRY` tiene 26 eventos y ninguno de marketplace.
- _Qué construir_: 2 eventos (`marketplace_listing_approved` / `_rejected`), 4 plantillas builtin (`BUILTIN_TEMPLATES` cuva por `(event_type, locale)`, no por canal: son 4, no 32), 2 entradas de catálogo y 2 llamadas.
- _Tres correcciones que no son opcionales_: (a) el alcance es **tenant**, no autor — nuestro `IncomingEvent` no tiene targeting por usuario y meterlo en un servicio BYPASSRLS es otro debate con ADR; (b) **no copiar su `try/except`**: ellos hacen `await` en línea dentro de la revisión y avisan de rechazos que luego revierten — copiar `routers/plans.py:1503-1522` (`schedule_after_commit` + `enqueue_event_dispatch`); (c) escribir en el plan que un listing global (`tenant_id IS NULL`) o un tenant sin canales produce un no-op silencioso, o la casilla mentirá al cerrarse.
- _Encuadre honesto_: el autor **ya ve** el motivo del rechazo en su pantalla (`review-status-badge.tsx:97-102`). Lo que falta es el empujón, no la información. Es latencia percibida, no un agujero.

### 2B. Donde está el valor real: lo que la comparativa destapó de lo nuestro

Ordenado por valor/coste. Todo verificado con fichero:línea; los tres primeros los he vuelto a comprobar yo mismo en esta pasada.

**P0 — Cortar el tag `v1.0.0` y verificar que las 5 imágenes existen. 0,5-2 días.**
`git ls-remote --tags origin` y `git tag -l` devuelven **vacío**, y `release-images.yml` sólo dispara en `push: tags: ["v*"]`. El compose que genera el instalador referencia `ghcr.io/daycry/<app>` en diez puntos y `docker compose pull` falla: **`scripts/install.sh` no puede terminar hoy en una máquina limpia.** Presupuestar la primera ejecución real, no sólo el tag: el workflow nunca ha corrido en verde en master y el fallo hermano de namespace tumbó los 14 builds de runtime el 2026-08-21. Añadir un `docker manifest inspect` post-tag: el contrato existente (`tests/unit/test_compose_images_contract.py`) afirma que son _construibles_, no que estén _publicadas_. Y corregir dos premisas que hoy mienten: ADR 0148 §«Lo que está en juego» afirma que las 5 imágenes se publican, y `task_prod01_20` lo da por hecho.

**P0 — Validar `remote_url` en el borde y endurecer el chokepoint de git. 0,75 días.**
Confirmado en esta sesión: `schemas/projects.py:685` es `remote_url: str = Field(min_length=1, max_length=2048)` — cero validación de esquema, host o userinfo; y `git_repos.py` `_run_git` sólo inyecta `'safe.bareRepository=all'` en `GIT_CONFIG_PARAMETERS`, sin `protocol.allow` ni `http.followRedirects`. Ese campo va tal cual a `git remote add origin <url>` y luego a `git fetch`. Git soporta el transporte `ext::`, que **ejecuta un comando**, y no tenemos `GIT_ALLOW_PROTOCOL` en ningún sitio. El worker es el que tiene el token de Vault, `DOCKER_HOST` al socket-proxy y el data-root montado. No comprobado por ejecución (regla de solo lectura), pero la ruta de código está verificada línea a línea y no hay guarda entre el campo y el `git remote add`. Fix: validador Pydantic con la forma de `assert_safe_url` (`cortex/web_safety.py:106-171`) + cuatro parámetros en el chokepoint que ya existe.

**P0 — Cablear la guarda de revisión en el camino de instalación del marketplace. 0,5 días.**
Confirmado: `installations.py` tiene **cero** menciones a `review_status`, y `catalog_visibility_clause` sólo se invoca en `catalog.py:78` y `:110`. Un listing **compartido** en `pending_review`/`rejected` es instalable por el tenant destino conociendo su id: `marketplace_listings_shared_read` deja pasar la fila, el catálogo la esconde, y la instalación la sirve. Aplicar la cláusula que **ya existe** (no escribir un `installable_listing_where()` nuevo: sería la tercera copia de la misma regla) en `installations.py` y en `_sibling_listings` (`common.py:98-113`, que alimenta también el camino de update). El test que de verdad falla antes: publicar en A → share a B → instalar desde B. Sin el share el test está verde de nacimiento.

**P1 — Atar la credencial git a su host. 1,25 días (0,75 + 0,5 de tests).**
`build_git_auth_env` (`git_auth.py:62-79`) entrega `GIT_PASSWORD` a lo que git pregunte, y el PUT permite repuntar `remote_url` **conservando** el PAT ya guardado (`routers/projects.py:546-556`, documentado como feature). Fuente del host permitido: por defecto el del `remote_url` ya configurado — sin campo nuevo, sin migración, sin default que adivinar. Tres call sites. Actor requerido: `tenant_admin`, no «cualquiera». **No es una adopción de AutoGPT**: `HostScopedCredentials` es de junio de 2025 y para su bloque HTTP genérico; el advisory que se citaba como causa es de `autogpt-classic` y su raíz —credencial en la URL— es justo lo que nuestro ADR 0072 rechazó por escrito. Lo único suyo que merece copiarse son dos detalles de `matches_url()`: comparar hostname **y** puerto, y que el comodín `*.ejemplo.com` case también el ápex.

**P1 — Cablear `maybe_alert_outliers`, que está terminado y muerto. 0,5 días.**
Confirmado: `stats/outliers.py:542` **no tiene ni un solo llamante en todo el repo** — sólo su definición y su `__all__`; ni un test. Es un motor de alertas por tasa de éxito con umbral configurable por tenant, `min_runs`, ventana y debounce, con despacho de notificaciones, sin enchufar. Un beat en `beat_schedule.py` entrega hoy el 80% de lo que se pedía como «tasa de error por tool», a la granularidad que nuestro modelo de ejecución realmente tiene.

**P1 — Cerrar la ruta directa que se salta el ADR 0132. 0,5 días.**
`PUT /tasks/{task_id}` (`routers/tasks.py:407` → `apply_partial_update` en `:489`) escribe `title`/`description`/`acceptance_criteria` en cualquier estado, `in_progress` e `in_review` incluidos, sin 409 y sin evento. La ruta plan→spec se cerró en `task_wf_45` (`_REPLAN_EDITABLE`, `sync_to_kanban.py:446`); ésta nunca. Reutilizar la misma constante para que la regla viva en un sitio.

**P2 — El resto, por si hay hueco:** extender el vigía del ADR 0122 a los servidores MCP reusando `test_mcp_connection` (1 d, detecta el MCP roto _antes_ de quemar runs); generalizar la racha de 3 fallos de transporte de `stack_exec` a cualquier tool (0,5 d); métrica `agentic_safety_net_corrections_total` — hoy las redes de seguridad reparan en silencio y la _tasa_ de reparación es la señal, no el recuento (0,25 d); `allowed_domains.py:75` hace suffix match y `.whitelist.com` pasa (30 min, defensa en profundidad, no explotable); marcar el run que se cierra sin reviewer independiente (`execution.py:371-376`, self-bias que contradice el `SameModelJudgeError` de evals, 1 d); badge de procedencia del marketplace en `agent-tools-section.tsx` usando el `source_listing_id` que ya está en la fila (0,5 d).

**Cuatro cosas que son ADR, no casilla:** ¿puede un tenant instalar su propio listing sin revisar? (hoy sí, y está escrito así); inmutabilidad del snapshot de versión aprobada — `snapshot_version` reescribe la fila y `reviewed_at` queda sellando bytes que nadie revisó (2-3 d **con** ADR previo); `StrictHostKeyChecking=accept-new` está en ADR 0072:94 y tocarlo exige superseder; targeting por usuario en el dispatcher.

---

## 3. Lo que no

Trece propuestas refutadas y catorce que no aplican. Las dejo nombradas para que nadie las vuelva a traer en seis meses.

**Refutadas porque ya lo tenemos, mejor y con otro nombre:**

- _Versionar el Plan como ellos versionan el grafo_ — el veredicto de rechazo ya congela el **texto** de cada criterio con su evidencia (`reviewer_bridge.py:331-360`), y el ADR 0132 ya ponderó y descartó la opción «auditar la edición» a favor de la reconciliación de tres vías. Su grafo es una plantilla reutilizable; nuestro Plan es una unidad de cambio de un solo uso atada a una rama.
- _Veredicto de run generado por LLM con nota de acierto_ — existe, corre en cada run, contrasta contra los acceptance_criteria y **bloquea** (`self_review` es nodo del grafo; `providers.py:443-447` dice literalmente «el status autodeclarado es un HINT, verifícalo contra los criterios»). El suyo es informativo, está tras feature flag y no tiene guarda anti-autosesgo. Un tercer canal de veredicto reabriría el ADR 0108.
- _Panel de diagnóstico de la cola_ — Grafana ya tiene profundidad de colas, tareas por estado y tasa de fallo 24 h, con **alertas push** (`CeleryQueueGrowing`, `ExecutionFailureRateHigh`, `TasksBlockedHigh`). Y sus agregados de huérfanas y encoladas mostrarían ceros permanentes: nuestros reconciliadores las cierran en 30 s / 5 min / 7 h. Su panel existe porque su arquitectura exige mirar-y-pulsar; la nuestra se autorrepara.
- _Recuperación de truncado por límite de tokens_ — implementado en julio de 2026 (hallazgo #10c, `providers.py:684-704` + tres consumidores + tests), y mejor: reintento **dirigido** en vez de recortar `max_tokens` un 15% a ciegas. Además su código no lee ninguna señal tipada: hace match de subcadena sobre el texto de una excepción, para otro fallo.
- _Allowlist de tools fail-open_ — falso: hay **dos puertas**, y la segunda es restrictiva. Sin filas `agent_tools` el boot no cablea las familias de catálogo (`__main__.py:971-974`), fijado por `test_no_tool_specs_does_not_register_extra_families`. Y el modo estricto ya está nombrado y rechazado en el ADR 0044 Alt-2.
- _Batería de regresión con sus advisories_ — 6 de 7 ya tienen defensa y test; su bypass por userinfo es **estructuralmente imposible** aquí (match exacto de conjunto + `pinned_url` sobre la IP validada); tres de los siete son de `autogpt-classic`, que su propio SECURITY.md declara fuera de alcance; y uno es de su sistema de créditos.
- _Menú único donde el marketplace es un bloque más_ — cita como prueba la §«problema» de un diseño cuyo plan de remediación está terminado y mergeado (`deploy.py:620-627` ya escribe `AgentSkill`/`AgentTool`; `deploy.py:408,442` ya escribe `mcp_servers`). Y contradice el ADR 0142 D2/D4. **Nota de método**: este repo escribe la sección «problema» de sus diseños con mucho detalle y ese detalle sobrevive intacto al arreglo — antes de citar `docs/roadmap/*` como prueba de un hueco, comprobar el `status` del frontmatter y si los commits están en `master`.
- _Comentarios dobles en la revisión, ratings, `isAvailable` por versión, estado de revisión en la versión_ — o ya existen con otro nombre (una fila de `marketplace_listings` **es** una versión; `approve(promote=False)` **es** «apruebo con reservas»), o presuponen un catálogo público de comunidad que no somos, o —el caso del grano de versión— tienen una versión nuestra que cuesta un tercio: dejar de machacar `listing.manifest` al republicar, en vez de reapuntar veinte lecturas.
- _Pinning DNS en el córtex_ — no es incoherencia, es la opción (b) de prod-12 aplicada donde su condición se cumple (proxy obligatorio por código). Y el arreglo propuesto **rompería** `web_fetch`: pinnear emite `CONNECT <ip>:443` contra un `filter.txt` de regex FQDN con `FilterDefaultDeny`.

**No aplican por diferencia de producto, no por madurez:** reanudar un grafo a mitad (nuestro estado real es el árbol de ficheros del worktree, no el historial de mensajes; y exigiría salida a BD desde el contenedor del agente, contra el principio 2); plantillas de plan y subgrafos (un Plan es un cambio, no una función: sería una abstracción con cero clientes); paralelismo intra-run (carrera de escritura sobre el worktree, con precedente documentado); canvas visual (sus aristas llevan datos tipados, las nuestras dependencias semánticas); safe mode simulado (8-12 d para resolver algo que resolvemos por construcción); auto-update de instalaciones (D7 acertó y su lectura no cambia); un quinto proveedor LLM; modos batch/flex; su modelo de bloques in-process; su contenedor único con supervisord; E2B.

---

## 4. Dónde estamos por delante (sólo lo medido)

- **Aislamiento**: contenedor efímero por tarea con `cap_drop ALL`, rootfs read-only, seccomp y AppArmor, sin socket Docker y sin opt-out (`isolation.py:148-154`). Ellos ejecutan los bloques **en el proceso del executor**, en un ThreadPoolExecutor compartido entre usuarios. Consecuencia medible: dos RCE Critical en dos semanas por la misma bandera no aplicada en dos puertas (GHSA-r277-3xc5-c79v, GHSA-4crw-9p35-9x54). Con nuestro modelo esa categoría no existe.
- **Multi-tenancy**: RLS en PostgreSQL desde la migración inicial, con un test que rompe la suite si nace una tabla con `tenant_id` sin políticas. Ellos tienen `Organization`/`Team`/`visibility` a nivel de aplicación y ninguna política RLS en el esquema.
- **Su advisory más reciente de webhooks (GHSA-349p, High) no nos afecta**: `incoming_webhooks.py:143` compara `config.origin != origin.value` antes de verificar nada. Comprobado, no supuesto. Falta sólo el test que fije el invariante.
- **Capa LLM**: Protocol tipado frente a su `if/elif`; reintento que honra `Retry-After` en 429 frente a su `break` sin reintentar; precios por modelo con vigencia y moneda frente a su `cost_usd = None` en OpenAI y Anthropic; MCP con los tres transportes sobre el SDK oficial frente a su JSON-RPC a mano sólo por HTTP.
- **Fernet en columna (ADR 0146)**: validación externa útil. Un proyecto independiente aterriza en la misma solución. La diferencia son las tres condiciones que ellos no tienen: anillo de claves y CLI de re-cifrado, exclusión del `pg_dump`, y `decrypt` que **lanza** en vez de devolver `{}` — su fallo silencioso se lee, en una rotación mal hecha, como «este usuario no tiene credenciales».
- **Instalador**: el nuestro genera secretos, tiene desinstalación con purga por categoría y seis códigos de salida; el suyo copia `.env.default`, no desinstala y no actualiza. Que hoy esté roto por falta de tag no cambia esto — y es justamente por eso que el tag es P0.

---

## 5. La diferencia de fondo

AutoGPT es una **plataforma de automatización con bloques** para una comunidad abierta: el usuario dibuja un grafo, conecta sus propias credenciales, y la unidad de trabajo es una función que se re-ejecuta con entradas distintas. Casi todo su diseño se deduce de ahí — el canvas, el error pin cableado a mano, el pinning DNS como única defensa (no hay proxy delante), el marketplace con estrellas, el `correctness_score` informativo (no tienen una definición estructurada de «hecho» contra la que juzgar), la ejecución in-process (no pueden aislar un nodo). Nosotros somos una **plataforma multi-tenant de equipos de agentes sobre repositorios de código**: la unidad es un Plan que termina en un PR, el estado real vive en un worktree, la definición de hecho son `acceptance_criteria` que a veces se ejecutan en un test-runtime, y el aislamiento por contenedor y la RLS no son features sino el perímetro. Por eso la comparación punto por punto casi siempre sale a nuestro favor sin que eso signifique nada: no somos mejores en su terreno, estamos en otro. **La forma correcta de leer este informe es al revés de como se leería una lista de deberes**: las secciones 3 y 5 son el entregable, la 2A es casi anecdótica, y la 2B —siete defectos propios que sólo salieron porque mirar a otro producto obliga a mirarse a uno mismo con la misma vara— es donde estaba el dinero.
