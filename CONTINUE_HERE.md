# CONTINUE HERE — dónde retomar el trabajo

> **Última actualización: 2026-09-09** · El plan activo es
> [`ui-reestructuracion-2026-09-09`](docs/roadmap/ui-reestructuracion-2026-09-09.md)
> (`in_progress` desde el 2026-09-09, rama `plan/ui-reestructuracion-2026-09-09`): entregadas `task_ui_04` —la foto de las 81 rutas y el mapa de pantallas, tomados ANTES de mover nada— y **`task_ui_01`** —`admin-shell.tsx` de 522 a 190 líneas, tres áreas (Trabajo · Proyecto · Sistema) y el indicador de salud que sólo aparece si duele—; **la siguiente es `task_ui_02`**, el endpoint agregado y el dashboard tenant-céntrico. El del marketplace cerró sus **quince casillas** y está en `pending_human_validation` (rama `plan/marketplace-mcp-2026-09-02`, PR #183 **abierto y sin mergear**): `task_mk_30` se cerró **en negativo** porque el operador rechazó el ADR 0167 (opción (b), con reapertura atada al cierre del plan). Esperan al operador: **mergear el PR #183** (la ola 3 del plan de UI reutiliza lo que entrega), validar `human_mk_01..03` y `human_cv_01..04`, y decidir la opción (a) del ADR 0081 reabierto.
>
> Este archivo es un **puntero**, no una copia del estado. La fuente de verdad es
> el frontmatter de `docs/roadmap/*.md`. Si algo de aquí contradice a un
> frontmatter, **gana el frontmatter** y hay que corregir este archivo. Está
> escrito así a propósito: un resumen que duplica datos envejece mintiendo, que
> es el modo de fallo nº1 de
> [verificar-antes-de-implementar.md](docs/03-guides/verificar-antes-de-implementar.md).
>
> Todas las cifras de abajo se midieron el 2026-08-12 (el estado del despliegue, el 2026-08-13) con los comandos que
> aparecen junto a ellas. Lo que no se pudo medir se dice, no se estima.

## 0-bis. Lo más reciente (2026-09-10): el instalador arreglado y la instalación desde cero en WSL

- **Plan nuevo, ya entregado en código**:
  [`remediacion-instalador-runs-de-serie-2026-09-09`](docs/roadmap/remediacion-instalador-runs-de-serie-2026-09-09.md)
  (`pending_human_validation`; rama `plan/instalador-runs-de-serie-2026-09-09`,
  commit `3dd03fb6`, empujada). Cierra los cuatro huecos por los que **una
  instalación limpia con Ollama no podía ejecutar ni un run**: variables `LLM_*`
  que nadie leía (ahora `API_SERVER_LLM_OLLAMA_*` + `seeds/init_llm_providers.py`),
  default `claude_sdk` hardcodeado (la siembra fija `model.default_config`),
  `base_url` sin `/v1` (`normalize_ollama_base_url`), y sin modelo de chat con
  tool-calling (`chat_model: qwen2.5:3b` en el `install.yaml` y en el bootstrap).
  Su test humano `human_inst_01` **es** la instalación desde cero de abajo.
- **La instalación desde cero va en WSL2 (Ubuntu-22.04) con Docker Engine
  nativo**, no en Docker Desktop: el compose generado monta el `data_root` del
  worker como bind a la misma ruta, que bajo Desktop apunta al disco de la VM
  `docker-desktop` y deja los worktrees vacíos (gotcha
  `worktree-bind-dood-empty-vs-named-volume`). Receta y estado en
  `.dev/wsl-01-sudo-docker-engine.sh`, `.dev/wsl-01b-sudo-remate.sh`,
  `.dev/wsl-02-install-desde-cero.sh` (fases `clone → images → build → python →
install`) e `.dev/install-local.yaml` (perfil `minimal`, dominio
  `agentic.example.com`, imágenes desde un `registry:2` local en
  `localhost:5000` con tag `local`, como el e2e de CI).
- **Dos trampas de la máquina que costaron una noche**: (1) `.wslconfig` con
  `memory=12GB` dejó a Windows con <1 GB y **la VM se congeló** (`vmmemWSL` a 7 GB
  con 0 % CPU, `wsl -l -v` sin responder, `WslService` en `StopPending`; sólo se
  destrabó matando `wslservice.exe` y `vmmemWSL` como administrador). Ahora
  `memory=10GB`, `swap=8GB`, `autoMemoryReclaim=gradual`, y **una fase a la vez**
  dentro de WSL — construir `browser-runtime` en paralelo con un `--dry-run` y
  pushes fue lo que la tumbó. (2) Ubuntu 22.04 trae Python 3.10 y el instalador
  exige ≥ 3.12: el venv se hace con `uv` (`uv venv --python 3.12`). Y WSL
  regenera `/etc/hosts` en cada arranque: la línea de `agentic.example.com` hay
  que reponerla (o `generateHosts=false` en `/etc/wsl.conf`).
- **Después de instalar**, las tres pruebas acordadas con el operador, todas con
  `qwen2.5:3b`: una tarea que el agente no sabe resolver (debe parar en
  `ask_human`), una que sí (crear `hola.txt` en el worktree) y una que instala
  CodeIgniter 4 con composer (necesita proyecto con repo y el runtime
  `php-phpunit`, que el script construye).

## 0. Ahora mismo (2026-09-09): arrancó la reestructuración de la UI

Plan **activo**:
[`ui-reestructuracion-2026-09-09`](docs/roadmap/ui-reestructuracion-2026-09-09.md)
(`in_progress` desde el 2026-09-09, rama `plan/ui-reestructuracion-2026-09-09`,
creada sobre la cabeza de la rama del marketplace porque su contenido es el que
tendrá `master` al mergear el PR #183 — **cuando se mergee con squash hay que
rebasar esta rama**, es la trampa que ya conocemos).

- **`task_ui_04`, entregada** (y adelantada a propósito: el plan la lista al final
  de la ola 1, pero una guarda de conservación tomada DESPUÉS de mover pantallas
  fija el resultado en vez de protegerlo). Dos guardas nuevas:
  `tests/unit/test_admin_panel_routes_preserved.py` con la foto de las **81
  rutas** del panel, y `tests/docs/test_panel_screen_map.py`, que comprueba el
  mapa nuevo [`04-reference/mapa-de-pantallas-del-panel.md`](docs/04-reference/mapa-de-pantallas-del-panel.md)
  en las dos direcciones (ruta sin fila y fila sin ruta). Las dos se verificaron
  rompiéndolas a mano, no sólo viéndolas pasar.
- **`task_ui_01`, entregada**: `admin-shell.tsx` baja de 522 a 190 líneas y lo
  que hacía se reparte en ocho piezas (`nav-model.ts` con las tres áreas, las
  tres barras, el marco, el grupo colapsable, el selector de área y el indicador
  de salud). El shell **re-exporta** el modelo porque cuatro ficheros de test
  importaban de él. Con cinco decisiones que el enunciado no fijaba, escritas en
  la casilla del plan — la que más se nota: `/admin/runs` y `/admin/docs` **no**
  se retiran todavía, para no dejar rutas vivas sin camino a media ola.
- **La siguiente es `task_ui_02`**: `GET /tenant/dashboard` y el dashboard con
  las seis secciones. Es la casilla que retira Runs del menú (y la salud del
  dashboard, que ya está en la cabecera).
- **Queda UNA pregunta abierta en el mapa**, y no se inventa: dónde vive
  `/admin/plans[id]/escalated` (Tablero del proyecto o pestaña Revisiones de la
  bandeja). La deciden `task_ui_03` y `task_ui_11`. La de
  `/admin/settings/security` la cerró el operador el 2026-09-09: al menú de
  usuario.

Y el plan que acaba de cerrarse, cuyo PR sigue abierto:
[`remediacion-marketplace-mcp-2026-09-02`](docs/roadmap/remediacion-marketplace-mcp-2026-09-02.md)
(`status: pending_human_validation` desde el 2026-09-09; empezó el 2026-09-03 en la
rama `plan/marketplace-mcp-2026-09-02`, PR #183). **Las quince casillas están
`[x]`** con su test en verde. Lo que le falta para `completed` es humano y está
abajo.

- **Olas 0, 1 y 2 entregadas**: ADR 0165 y 0166 `accepted` (`task_mk_0a`, `0b`),
  instalar desde el catálogo (`00`), el egress de los MCP remotos (`02`) y las
  tools MCP al catálogo sin paso manual (`01`); los tipos diferidos dejan de
  venderse (`10`, opción (b)), la puerta de despliegue enseña lo que pasó (`11`),
  `needs_consent` mira también los permisos declarados (`14`), un test que llama
  la tool de verdad dentro de un run (`12`), procedencia + cola de revisión +
  fork sin tools MCP (`13`); `project.integrations` (`20`), las anclas en el
  preámbulo del run y las skills Atlassian leyéndolas (`21`), el catálogo
  distribuyendo MCP y la guía diciendo la verdad (`22`), nombres en vez de UUIDs
  e i18n de skills (`23`). Tarea por tarea, en el changelog del plan:
  [`07-changelog/remediacion-marketplace-mcp-2026-09-02.md`](docs/07-changelog/remediacion-marketplace-mcp-2026-09-02.md)
  (criterio de cierre 4; su `completed_at` sigue `null` a propósito).
- **La ola 3 se cerró EN NEGATIVO, y conviene saber leerlo**: el
  [ADR 0167](docs/05-architecture-decisions/0167-tools-de-plataforma-para-el-arbol-jira-confluence.md)
  proponía `jira_children_of_parent` y `confluence_page_under_root` como tools de
  plataforma, y el operador eligió **(b) rechazar** el 2026-09-09. Así que
  `task_mk_30` está `[x]` **sin que exista ese código**: lo entregado es el ADR y
  la nota de la guía. El motivo es de evidencia —los tres modos de fallo se
  midieron antes de la ola 2 y `human_mk_02`/`human_mk_03` aún no han corrido—, y
  la reapertura está **mecanizada**: el ADR lleva
  `reopen_when: [remediacion-marketplace-mcp-2026-09-02]`, así que el día que este
  plan pase a `completed` la guarda
  `test_a_fired_trigger_is_declared_and_not_silent` se pondrá **roja** hasta que
  alguien anote `reopen_triggered_on:` y vuelva a decidir. Si te encuentras ese
  rojo, no es una regresión: es esta nota cobrando.
- **Al desplegar esta rama**: el api-server necesita
  `API_SERVER_EGRESS_PROXY_URL` (el compose manual y el generador ya lo emiten);
  el worker tiene que arrancar con la lane `marketplace` para que el import
  automático de un despliegue ocurra (sin ella la tarjeta dice «sin importar» y
  el botón manual basta, ADR 0166 D4); y la primera «Probar conexión» contra un
  MCP remoto **con OAuth** sigue siendo `human_mk_02`.
- **LA APLICACIÓN ENTERA CORRE EN DOCKER en esta máquina desde el 2026-09-09
  (tarde)**, que es el camino del producto y el que vale para validar: overlay
  `docker/docker-compose.manuals.yml` sobre base+dev, con las cinco imágenes
  construidas en local (`api-server:manuals`, `admin-panel:manuals`,
  `orchestrator:manuals`, `notification-dispatcher:manuals`, `workers:ci`).
  **Panel: http://localhost:8081/login** (API en `/api`, vía caddy; el 8080 lo
  ocupa `MTAgentService` de MiniTool y se remapea con un override local fuera del
  repo). Tres trampas que costaron una vuelta cada una: el volumen externo
  `agentic-platform-agent-data` hay que crearlo una vez a mano (lo dice el propio
  overlay); el build del panel **desde Git Bash** hornea `C:/laragon/bin/git/api`
  en vez de `/api` (`MSYS_NO_PATHCONV=1`, y el gotcha ya no busca sólo
  `Program Files`); y el proveedor Ollama en Docker apunta a `http://ollama:11434/v1`
  (host interno permitido en el egress-proxy), no a `host.docker.internal`.
  **Un run real recorrió el bucle completo bajo el aislamiento de producto** — 14
  pasos, `qwen2.5:3b`, parada en `ask_human`— y con él queda **acreditado el
  primer run bajo el perfil seccomp estricto**: el sandbox vivo llevaba
  `seccomp={default-deny}`, `CapDrop=ALL`, rootfs read-only, uid 1000, y un bridge
  por run `Internal=true` con exactamente dos peers (api-server y egress-proxy).
  Ollama recibió las `/v1/chat/completions` desde la IP del proxy. Para no pasar
  de 16 GB: parar `tts stt docling-serve clamav searxng` antes y levantar la app
  **por nombre de servicio** (el `up` a secas despierta los trece).
- **HAY RUNS también en el stack de dev desde el 2026-09-09** (para depurar, no
  para validar):
  `scripts\dev\up.ps1 -Runs` levanta además worker (celery, seis lanes) y
  orchestrator (:8002), y la imagen `agent-runtime:v1` está construida. Un run
  real recorre el bucle entero — `perceive → recall → plan → act → observe →
reflect` (×2) → puerta de validación humana, 14 pasos, `qwen2.5:3b`, 6208
  tokens—. Cuatro cosas que hacen falta y no son obvias: **(1)** el modelo tiene
  que soportar **tool-calling** (`orca-mini` responde `does not support tools` y
  el run muere en `plan`; `qwen2.5:3b` sí); **(2)** el `base_url` de un proveedor
  Ollama **incluye `/v1`** (sin él, `404 page not found` dentro del run, porque
  el cliente hace `POST {base_url}/chat/completions`); **(3)** los agentes
  sembrados piden kind `claude_sdk`, así que para usar Ollama hay que apuntar su
  `model_config` a un proveedor de kind `ollama`; y **(4)** el sandbox necesita
  ruta al api-server —`up.ps1 -Runs` ata uvicorn a `0.0.0.0` y apaga
  `WORKERS_AGENT_NETWORK_INTERNAL` por eso—. Cuando un run acabe `failed` con
  `steps_log` vacío, el motivo está en la columna **`output`** de `executions`,
  no en ningún log.
- **Verificado en vivo el 2026-09-08** en esta máquina (nueva, sin Docker de la
  plataforma) con el **stack de dev** —`scripts/dev/up.ps1`: infra en Docker,
  api-server y panel en el host— y el MCP público `mcp.context7.com`: guardado
  con aviso D11 → `422 EGRESS_BLOCKED` real del tinyproxy → ajuste → render +
  rebuild en 5 s → sondeo `permitido` → «Probar conexión» 200 → import de 2
  tools → idempotente → retirada R4. Dos trampas de esa sesión: `up.ps1` se
  murió por **memoria** (16 GB no bastan para los trece servicios más `next dev`;
  bastó parar tts/stt/docling/ollama/clamav/searxng) y los cuatro scripts de dev
  llevaban la URL de Redis **sin contraseña** (arreglado; gotcha
  `redis-con-contrasena-rompe-la-integracion.md`). El stack sigue levantado:
  `scripts/dev/down.ps1` lo para.
- **Lo que queda es humano, en los dos planes**: `human_cv_01..04` del plan del
  ciclo de vida —`remediacion-ciclo-vida-proyecto-2026-09-01`,
  `pending_human_validation`, todo mergeado en `master` por los PR #177, #178,
  #179 y #182 con su changelog y las addenda de los ADR 0060, 0071, 0072, 0102,
  0129, 0148 y 0163— y `human_mk_01..03` del marketplace. Hasta que el operador
  los valide, ninguno de los dos pasa a `completed`.
- **La cola, después del de UI**:
  [`memoria-agentes-2026-09-09`](docs/roadmap/memoria-agentes-2026-09-09.md)
  (`approved`, 12 días-persona: memoria por capas con citas y uso; su UI la pinta
  `task_ui_31`) y `gov-01`. No pueden empezar mientras el de UI esté
  `in_progress`: el protocolo admite uno.
- **Trampa de esta máquina**: el token de `gh` no tiene el scope `workflow`,
  así que un push que toque `.github/workflows/` por HTTPS se rechaza. Se
  empuja por SSH: `git push git@github.com:daycry/agent-ai-multitenant.git HEAD:refs/heads/<rama>`
  (o `gh auth refresh -h github.com -s workflow` una vez).

Comprobar antes de fiarse: `gh pr list`, `git log --oneline -5`,
`./.venv/Scripts/python.exe -m pytest tests/unit -q` y
`grep -c "\[x\]" docs/roadmap/remediacion-marketplace-mcp-2026-09-02.md` (**15 el
2026-09-09**, las quince, una de ellas en negativo). Las cifras de las suites,
medidas hoy, están en §«Verificación local».

**Y los tests de integración ya NO son sólo de CI en esta máquina**, que es lo que
decía esta línea hasta hoy: con el stack de dev levantado hay Postgres en
`127.0.0.1:15432` y la Redis con contraseña, y `tests/integration/` corre en local
contra ellos —los cuatro ficheros que verifican este plan, **19 tests en 88 s el
2026-09-09**—. De uno en uno, eso sí: el conftest hace `DROP DATABASE` sobre un
nombre único para todo el repo (§gotcha de la BD compartida).

**Orden de despliegue**: imagen
del `agent-runtime` y worker antes que orquestador (`claim_id`, spec y token por
fichero, `ask_human_remaining`); los proyectos con
`push_policy=direct_to_default_allowed` deben pasar a `branch_only_pr_required`.

Lo que sigue (§1 en adelante) describe el despliegue del 2026-08-13 y sigue
siendo el inventario de riesgos para desplegar en otra máquina.

## En una frase

**La rama está DESPLEGADA desde el 2026-08-13** (antes lo estaba desde el
2026-07-28). El stack corre la cabeza de la rama y la BD va por la última
migración; comprueba las dos cosas con los comandos del §2 antes de fiarte de
esta frase.

Lo que sigue abajo describe **el despliegue que ya se hizo**, no uno pendiente.
Se conserva porque el §1 es el inventario de riesgos que hay que releer el día
que se despliegue en OTRA máquina o se restaure un backup anterior a las cinco
conversiones a particionado — ahí sí vuelve a aplicar entero.

El número de commits sin empujar no se escribe aquí a propósito: cambia con cada
commit, así que cualquier cifra escrita queda falsa antes de que nadie la lea. Ya
pasó (`git rev-list --count origin/master..HEAD` decía 19 mientras esta frase
decía 18). Si lo necesitas, mídelo con ese comando.

> **Cómo fue el despliegue del 2026-08-13**, para que el siguiente sea más corto:
> las cinco conversiones a particionado YA estaban aplicadas (`executions` con sus
> seis particiones), así que sólo quedaba la `0139` — **5 segundos**, sin ventana.
> Se desplegó con `--scale orchestrator=0` sobre 0 tareas en vuelo y 0
> reclamaciones huérfanas, y el orchestrator se restauró después comprobando que
> no había nada que despachar. Cinco imágenes reconstruidas; `agent-runtime` NO,
> porque el commit no tocó ninguna de sus entradas de build (los cuatro paquetes
> `shared-*`, su `pyproject.toml` y su `agent_runtime/`) y reconstruirla habría
> dado una imagen idéntica.
>
> **Y una trampa nueva del juego de ficheros compose**: el stack incluye
> `manuals.yml`, así que desde el 2026-08-12 hay que añadir
> `-f docker/docker-compose.monitoring.apps.yml`. Sin él, el `up -d` le QUITA al
> worker el mount del textfile-collector y las cuatro métricas de aplicación
> dejan de existir **en silencio**. Verificado tras el despliegue: el drop-dir en
> 1777 con tres `.prom` de dos escritores distintos, y las series en Prometheus.

## Inventario de riesgos de despliegue (el del 2026-08-13 ya pasó)

Por orden de «cuánto duele si lo ignoras». **No es una lista de pendientes**: los
puntos 1, 2, 3 y 6 se ejecutaron el 2026-08-13 y están resueltos en ESTA máquina.
Se conservan porque vuelven a aplicar enteros al desplegar en otra máquina, al
restaurar un backup anterior a las cinco conversiones a particionado, o al
preparar el despliegue de producción. Los puntos 4, 5 y 7 **siguen abiertos**.

1. **Las cinco migraciones de particionado copian tablas enteras.** `0131`
   (`guardrail_events`), `0134` (`notification_logs`), `0135`
   (`llm_usage_events`), `0136` (`audit_log`) y `0137` (`executions`). La quinta
   es la cara: `executions` es la tabla pesada del sistema (76 % de su tamaño es
   `steps_log`, medido en el ADR 0151) y la migración la copia **dentro de una
   sola transacción**, con el `ALTER TABLE … RENAME` tomando `ACCESS EXCLUSIVE`
   durante toda la operación. Trocear no ayudaría: el bloqueo ya está tomado.
   - **Mide ANTES de abrir la ventana**:
     `SELECT pg_size_pretty(pg_total_relation_size('executions')), (SELECT count(*) FROM executions);`
   - Procedimiento completo:
     [`06-runbooks/particiones-append-only.md`](docs/06-runbooks/particiones-append-only.md)
     §4 «Convertir la tabla grande».
   - `0137` además **retira cuatro FK** hacia `executions` (ADR 0154). El
     `downgrade` las restaura, limpiando antes las filas colgantes.
   - **La `0139` toca `executions` y NO es de esta familia**, aunque el nombre
     asuste: denormaliza `last_model` / `tokens_in` / `tokens_out` desde
     `steps_log` (prod-13, `task_prod13_18`) y su backfill es un `UPDATE` de las
     filas que tienen pasos, sin copiar la tabla. Escala medida contra la BD viva
     el 2026-08-12: **180 runs, 165 con pasos, 2624 kB sumando las seis
     particiones**. O sea instantáneo: **no abre ventana de mantenimiento** ni
     añade nada a la del punto anterior.

2. **Cuántas migraciones aplicas depende de dónde esté tu BD, y eso no lo sabe
   este fichero.** La rama añade **17** sobre `origin/master`, de la `0123` a la
   cabeza. **Cuál es la cabeza no se escribe aquí a propósito**: era la `0138`
   cuando se redactó el punto 1 y la `0139` la desplazó dentro de esta misma
   tanda, así que copiar el número sólo garantiza que dentro de dos commits este
   párrafo mienta. Sácalo del repo y compáralo con la BD viva, que es la única
   que sabe por dónde vas:

   ```bash
   ls apps/api-server/migrations/versions/ | sort | tail    # la cabeza del repo
   docker compose exec postgres psql -U migrations_user -d agentic_platform \
     -c "select version_num from alembic_version;"          # dónde está la BD
   ```

3. **Despliega con `--scale orchestrator=0`.** El `up -d` del 2026-07-28
   **relanzó dos tareas congeladas** (~165 k tokens quemados; el reconciler las
   rescató a los 90 s). La comprobación de «¿queda algo corriendo?» NO las ve,
   porque su rasgo es no tener ejecución: cuenta reclamaciones huérfanas.
   [gotchas/deploy-relaunches-frozen-tasks.md](docs/03-guides/gotchas/deploy-relaunches-frozen-tasks.md).

4. **El backup nocturno ahora PARA el stack.** Es el ADR 0149 (firmado el
   2026-08-01, opción A) y está implementado: a las 03:00 se paran `api-server`,
   `orchestrator`, `workers`, `cortex-beat`, `notification-dispatcher` y
   `admin-panel` mientras dura la captura, con plazo de 180 s que **degrada a
   `partial`** en vez de convertirse en una caída, y rearranque en `finally`.
   Corte esperado: 1-3 min diarios. Palanca para apagarlo:
   `WORKERS_BACKUP_QUIESCE_SERVICES=[]`.
   - **Y una trampa suya**: el instalador **no emite
     `WORKERS_RESTORE_COMPOSE_FILE`**, que es el puntero al compose que usa el
     quiesce (y el restore). Con un `data_root` distinto del default, el quiesce
     no encuentra el compose y degrada **todas las noches**, diciéndolo sólo en
     el log (`backup.quiesce.no_compose_file`). Detalle en
     [`06-runbooks/04-disaster-recovery.md`](docs/06-runbooks/04-disaster-recovery.md)
     §«El quiesce».

5. **El perfil seccomp endurecido sigue ACTIVO y sigue sin ejercitarse.** Sólo
   afecta a los sandboxes que lanza el worker. El primer run que lances será el
   primero bajo el perfil estricto. Válvula: `WORKERS_SECCOMP_PROFILE_PATH=` en
   el `.env`, sin tocar el compose.

6. **Construye el panel de verdad antes de dar el despliegue por bueno.** El
   2026-08-10 la imagen del panel no construía —`useSearchParams()` sin frontera
   de `<Suspense>` en `/login` y `/accept-invite`— y ni vitest ni `tsc` lo veían:
   **el prerender sólo corre al construir**. Ya está arreglado (`21a9d955`), pero
   la lección vale para el próximo cambio de pantalla:
   `NEXT_PUBLIC_API_URL=/api npx next build` en `apps/admin-panel`.

7. **Queda un `DELETE` sin ejecutar** (heredado): 8 filas de `agent_tools`
   conceden `send_notification` a los agentes CI4 DevOps y Project Manager, y esa
   tool no tiene ejecutor (devuelve `ok=False, "not wired"` y les quema un
   turno). Ningún seed las repone. Copia previa: bundle `20260728T114814Z`.

## Estado de la rama respecto a `origin/master`

```bash
git rev-list --left-right --count origin/master...HEAD   # detrás  delante
```

**El número no se escribe aquí**, por lo mismo que en «En una frase»: sube con
cada commit. Mídelo. Lo que sí es estable y sí importa:

- **1 commit por detrás**: `72fe899b`, el merge del PR #66. Hay que integrarlo
  antes de abrir el PR de esta rama.
- El **PR #66 está mergeado** desde el 2026-07-30, así que el criterio 5 de
  cierre («PR mergeado») dejó de bloquear a los planes anteriores a esa fecha.
- Los commits de esta rama —del 2026-07-30 en adelante— **necesitan su propio
  PR**, y hasta que se mergee ningún plan posterior puede pasar a `completed`.

## Estado del roadmap (regenerable, ver §«Comprobar»)

Dos recuentos, y la diferencia entre ellos importa: **los guardas sólo ven los
ficheros con `plan_id`**.

Recontado el **2026-09-09** con el script del §«Comprobar» (y por eso las cifras
subieron: son cinco semanas de planes nuevos, no un cambio de criterio).

| Estado                     | Planes (con `plan_id`) | Ficheros | Qué significa aquí                                                                                                                                                               |
| -------------------------- | :--------------------: | :------: | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `pending_human_validation` |           40           |    51    | código entregado; esperan tests humanos                                                                                                                                          |
| `completed`                |           19           |    25    | cerradas del todo                                                                                                                                                                |
| `pending_approval`         |           13           |    13    | ver el aviso de abajo: **ya no significa «sin empezar»**                                                                                                                         |
| `approved`                 |           2            |    2     | `gov-01` y `memoria-agentes-2026-09-09` (el de UI ya arrancó)                                                                                                                    |
| `in_progress`              |           1            |    1     | `ui-reestructuracion-2026-09-09` (el protocolo, uno)                                                                                                                             |
| `blocked`                  |           1            |    1     | `guardas-research-por-novedad`: sólo le falta el e2e                                                                                                                             |
| **Total con `plan_id`**    |         **76**         |  116\*   | \*los 116 son TODOS los `.md` con `status:`, incluidos los 23 de estados que no son de plan (`published`, `informe`, `archived`, `open`, `delivered`, `remediation_implemented`) |

Los 11 ficheros de diferencia en `pending_human_validation` **no llevan
`plan_id`** —ocho son las fases del córtex, con casillas y `blocking_plan`
propios— así que se saltan TODOS los guardas de gate, changelog y
`in_progress`. Es deuda inventariada y acotada:
`test_no_new_roadmap_file_escapes_the_guards_by_omitting_plan_id` impide que
aparezca el número dieciocho.

> ### ⚠️ `pending_approval` ya no quiere decir «nunca empezado»
>
> Re-medido el **2026-09-09**: **los trece** planes en ese estado tienen casillas
> marcadas, ninguno está sin empezar de verdad, y **ocho no tienen ninguna
> abierta** — `cadena-pr-plan`, `prod-03`, `prod-04`, `prod-05`, `prod-07`,
> `prod-09`, `prod-13` y `prod-14` están **entregados** con la etiqueta de
> «definido pero no empezado». Eran seis el 2026-08-12: `prod-13` y `prod-14`
> cerraron su última casilla desde entonces, así que la incoherencia **creció**
> mientras esperaba decisión. Los otros cinco tienen entre 1 y 4 casillas
> abiertas (`prod-16`, cuatro).
>
> No se les ha cambiado el estado a propósito, por dos razones: pasar a
> `pending_human_validation` exige su entrada en `docs/07-changelog/` (sólo
> `prod-07` la tiene) y, sobre todo, **cambiarles el estado afirmaría una
> aprobación que nadie ha dado**. La decisión es tuya. Lo que sí hay es un
> guarda para que no crezca:
> `test_no_new_plan_is_delivered_while_still_labelled_unstarted`.

**ADR en `proposed`: ninguno.** El `0152-recall-vectorial-multitenant-hnsw` —el
único pendiente cuando se escribió esto— quedó `accepted`, y el 0167 se decidió el
2026-09-09 (`rejected`, opción (b), con `reopen_when:`). Comprobado ese día con
`grep -l '^status: proposed' docs/05-architecture-decisions/*.md`, que no devuelve
nada. **El 0167 es además el primer `rejected` del corpus**: no está borrado ni
vacío a propósito — un rechazo con su razonamiento dentro es lo que impide que la
misma propuesta vuelva dentro de tres meses sin la medición que le falta.

## Qué necesita al operador (por orden de coste)

0. **Mergear el PR #183.** Es lo más barato y lo que más destraba: la rama del
   plan de UI se creó sobre su cabeza (no sobre `master`), y su ola 3 reutiliza
   lo que ese PR entrega. Con squash-merge, después hay que **rebasar**
   `plan/ui-reestructuracion-2026-09-09`. El ADR 0167 ya está decidido —(b),
   rechazado el 2026-09-09 con reapertura mecanizada—, así que nada más bloquea.
1. **Validar las 40 fases en `pending_human_validation`.** Sigue siendo el cuello
   de botella real: mientras ninguna llegue a `completed`, toda fase que dependa
   de ellas lee su gate como incumplido (es la causa de fondo que mide el ADR
   0138). Procedimiento:
   [06-runbooks/03-system-upgrade.md](docs/06-runbooks/03-system-upgrade.md).
2. **Decidir qué pasa con los 13 `pending_approval`**, y en particular con los
   ocho que ya están entregados (ver el aviso de arriba). Aprobarlos a posteriori
   o rechazar el trabajo son las dos salidas honestas; dejarlos como están hace
   que el roadmap mienta a quien lo lea.
3. **Rellenar dos columnas.** `docs/roadmap/README.md` §«Cola de validación
   humana» publica el orden (12-backup → 15-instalador → 08-sso → 09-marketplace)
   con las columnas **Responsable** y **Ventana** en `⬜ por asignar` / `⬜ por
fijar`. El ADR 0138 las deja fuera de su alcance por escrito porque
   comprometen el calendario de una persona. Es lo único que le falta a
   `task_gov_reestado_04` de prod-15, y son dos celdas.
4. **Seis jobs de fondo que nunca han corrido, y decidir si se encienden.** Sus
   entradas de beat nombran tasks que **ningún worker registra**, así que beat las
   encola y el worker las rechaza con `NotRegistered`, sin ruido: standup diario
   (ADR 0120), vigía de credenciales (ADR 0122), retro de planes (ADR 0124),
   asesor de configuración (ADR 0125), restore-drill (ADR 0126) y GC de
   conocimiento (G-03). **No están cableados a propósito**: arreglarlo enciende
   los seis de golpe y uno **ensaya una restauración de backup**. Lista viva en
   [gotchas/beat-entry-whose-task-nobody-imports.md](docs/03-guides/gotchas/beat-entry-whose-task-nobody-imports.md).
5. **Dos verificaciones que exigen humano delante**: la prueba en navegador del
   OAuth de MCP (ADR 0127) y el **primer run bajo el perfil seccomp estricto**.
6. **Sembrar un dataset dorado de evals.** El productor, el lector y el
   muestreador están puestos y probados; elegir qué tareas cerradas son «buenas»
   es curaduría humana. Mecanismo: `POST /tasks/{id}/promote-to-dataset` (hay UI).
7. **Dos decisiones ligadas del ADR 0149**, con sus opciones ya escritas:
   ¿Redis es crítico o recreable? y ¿`vault_data` viaja dentro del blob cifrado?
   Ninguna bloquea el despliegue.
8. **`registry-egress-followups`** (`open`): F3 cerrada el 2026-07-28; F1/F4/F5
   siguen abiertos. F5 lleva escrito su orden correcto y su trampa (si se hace a
   medias reabre la puerta trasera de B-04).
9. ~~**CI sigue caído por facturación** de la cuenta `daycry`.~~ **RESUELTO.**
   Comprobado el 2026-08-27: CI corre y pasa — run `33083267973` sobre
   `1aec3ebc`, los doce jobs en verde.

   Esta línea se queda tachada en vez de borrada porque su vida útil fue el
   problema. Sobrevivió a su causa, y el 2026-08-27 un agente la citó como
   hecho —`CONTINUE_HERE.md:223`— y la metió **tres veces en un ADR firmado**,
   donde afirmaba que no se podía publicar nada porque no había controles. Los
   había. Una nota de estado sin fecha de caducidad se convierte en una fuente
   de verdad falsa justo cuando alguien la necesita: **antes de repetir de aquí
   un estado operativo, compruébalo.**

## Deuda conocida que NO bloquea el despliegue

- ~~🔴 **El subset e2e del panel lleva semanas ROJO y CI no podía decirlo**~~ —
  **CERRADO, medido el 2026-09-09**: el job «Frontend e2e (Playwright, mocked
  subset)» da **421 passed en 3,5 min** sobre 107 specs (run `34325423184`, job
  `102381452781`, y otra vez en el run de la PR). Cero rojos, cero `test.skip`.
  - El diagnóstico del 2026-08-19 era correcto y su arreglo funcionó: los ~100
    rojos eran mayoritariamente **deuda del arnés** —selectores por nombre
    accesible que dejaron de ser únicos, `data-testid` renombrados— y se fueron
    cayendo con las olas de estas tres semanas. Lo que hizo visible el progreso
    fue el `--max-failures=15 --timeout=15000`: un job que terminaba en minutos
    en vez de agotar 60 y salir `cancelled`.
  - **Se deja tachado y con su fecha, no borrado**, por lo mismo que la línea 9
    de §«Qué necesita al operador»: una nota de estado que sobrevive a su causa
    se convierte en fuente de verdad falsa. Esta llevaba tres semanas diciendo
    «hay ~100 rojos» cuando ya no los había, y el plan de UI —que rehace los e2e
    del tablero— se habría escrito asumiendo un arnés roto.
  - Lo que sigue valiendo del párrafo original: **ni un `test.skip` para poner el
    marcador en verde**. Si algo no se arregla, se queda rojo y se explica.

- ~~**`routers/backup.py` importa `workers`**~~ — **cerrado el 2026-08-19**
  (prod-15 `task_gov_app_boundary_11`, hallazgo api-9 / decisión D5). Las dos
  sondas de destino remoto se encolan por nombre
  (`workers.backup_test_destination` / `workers.backup_list_remote`, cola
  `privileged`) y corren donde están las `WORKERS_BACKUP_*`. El contrato HTTP de
  los dos endpoints no cambió. Guardas: `tests/unit/test_app_boundaries.py`
  (ya exige CERO `worker-work`) y
  `tests/unit/test_backup_probe_runs_in_the_worker.py`. **Pendiente de un
  humano**: probar el botón «probar conectividad» con un destino remoto real —
  nunca se ha ejercitado con credenciales, ni antes ni después.
- ~~**Los demos de fase siguen en la raíz de `scripts/`**~~ — **movidos el
  2026-08-19** a `scripts/demos/` (prod-15 `task_gov_higiene_10`), con las
  referencias de `pyproject.toml`, `.gitignore`, los cinco launchers
  `scripts/dev/run-human-tests*.ps1` y las guías actualizadas. Guarda:
  `tests/unit/test_scripts_layout.py`. **Pendiente de un humano**: correr un
  launcher de verdad (p.ej. `scripts/dev/run-human-tests-05.ps1`) — los `.ps1`
  no los ejercita ninguna suite.
- **Un plan `completed` sin changelog**: `ciclo-vida-planes-fixes` (inventariado
  en `_CHANGELOG_DEBT_2026_07_29`).

## Órdenes permanentes del operador

Valen para toda sesión, no sólo para la que las recibió:

- **Responder en castellano.**
- **Entregables** (auditorías, planes, diseños) en `docs/roadmap/`, NO en
  `docs/plans`. Los ADR en `docs/05-architecture-decisions/`.
- **Prioridad: código limpio y mantenible.** TDD, módulos enfocados, refactor
  oportunista, sin big-bang. Lo que esté gated va por ADR primero.
- **ADR `proposed` → implementarlos de forma autónoma** eligiendo la mejor opción.
  Excepción: si un ADR implica una decisión de PRODUCTO nueva, parar y preguntar.
- **Fallo de un run**: si la causa es de plataforma, arreglarlo yo (TDD + deploy +
  relanzar). Escalar sólo lo que sea decisión humana.
- **No relanzar ni desbloquear tareas** hasta que el operador dé el sistema por
  verificado. Observación pasiva sí; desplegar ya no está vetado.

## Cómo comprobar que este archivo sigue siendo cierto

Cinco comandos. Si alguno contradice lo de arriba, **actualiza este archivo**:

```bash
# 1. Recuento por estado del roadmap (por FICHERO; ojo, no todos son planes)
for f in docs/roadmap/*.md; do grep -m1 '^status:' "$f"; done | sort | uniq -c | sort -rn

# 2. ¿Alguna fase in_progress? (el protocolo permite UNA como mucho)
grep -l '^status: in_progress' docs/roadmap/*.md

# 3. ¿ADR sin decidir?
grep -l '^status: proposed' docs/05-architecture-decisions/*.md

# 4. ¿Cuánto lleva la rama sin empujar, y va por detrás?
git rev-list --left-right --count origin/master...HEAD

# 5. Cabeza del esquema en el repo vs. la BD viva
ls apps/api-server/migrations/versions/ | sort | tail -3
```

Y antes de implementar cualquier tarea de un plan antiguo, lee el §1 de
[verificar-antes-de-implementar.md](docs/03-guides/verificar-antes-de-implementar.md):
de las últimas 21 tareas «pendientes» que se revisaron, la mayoría estaban hechas
y dos estaban **rechazadas** por un ADR posterior.

## Verificación local (CI ya funciona; esto sigue siendo lo barato)

Cifras **medidas el 2026-09-09** en esta rama, en el mismo orden que los pasos de
`.github/workflows/ci.yml`:

```bash
# Los tres primeros van juntos: es lo más barato que hay
.venv/Scripts/python.exe -m pytest tests/unit/ tests/security/ tests/docs/ -q
#   → 6735 passed, 9 skipped, 0 fallos en 238 s

.venv/Scripts/python.exe -m pytest packages/shared-llm/tests -q          # 191 (+1 skip sin claude_agent_sdk)
cd docker/agent-runtimes/agent-runtime && ../../../.venv/Scripts/python.exe -m pytest tests/ -q   # 745
PYTHONPATH=docker/agent-runtimes/browser-runtime \
  .venv/Scripts/python.exe -m pytest docker/agent-runtimes/browser-runtime/tests -q               # 19

.venv/Scripts/python.exe -m mypy apps/ packages/    # Success: 743 ficheros, limpio
```

> **Había seis rojos esa mañana y se arreglaron el mismo día**, y merecen quedar
> escritos porque los seis eran **guardas haciendo su trabajo** sobre cambios
> deliberados que nadie acompañó: `test_marketplace_router_package` (la ruta nueva
> del buscador de tenant, `task_mk_23`), `test_agents_router_package`
> (`AgentForkResponse`, `task_mk_13`), `test_domain_models_package`
> (`projects.integrations`, `task_mk_20`) y las tres aserciones de
> `test_readme_badges_do_not_lie` (168 ADR y 149 migraciones, en los DOS README).
> La regla que se saltó es la de siempre: **la guarda se actualiza en el commit
> que mueve lo que vigila**, y si no, el rojo espera en `tests/unit` —que CI corre
> y nadie mira entero— a que alguien lo lea.
>
> Y los 2 rojos del 2026-08-12 (`test_model_options_deprecation` leyendo
> `routers/agents.py` por ruta) **ya no están**: el carril que partió el router
> actualizó su constante, que es exactamente lo que aquí se pedía.

**Del panel (`apps/admin-panel`), medido también el 2026-09-09** y con el árbol
limpio esta vez: `vitest` **1616 tests en 181 ficheros**, `tsc --noEmit` limpio,
`check-i18n` y `check-component-size` sin avisos (la concesión de
`agent-tools-section.tsx` bajó a sus 587 líneas reales) y `next build` **completo**
—el paso que ninguno de los anteriores cubre y el que se cayó el 2026-08-10—:

```bash
cd apps/admin-panel
npx vitest run
npx tsc --noEmit
node scripts/check-i18n.mjs && node scripts/check-component-size.mjs
NEXT_PUBLIC_API_URL=/api npx next build      # el que NINGUNO de los anteriores cubre
```

> **Usa el intérprete del venv, no `python` a secas.** Los paquetes de
> `packages/` están en editable sólo en `.venv/`; con el Python global la suite
> muere en la recolección con `ModuleNotFoundError: shared_domain` — sin correr
> ni un test, y en un fichero que no has tocado.
> [gotchas/pytest-needs-the-repo-venv.md](docs/03-guides/gotchas/pytest-needs-the-repo-venv.md).

> **La suite del agent-runtime NO está en `testpaths`**: sólo la corre CI en un
> paso propio, y con CI caído hay que invocarla a mano desde su directorio.
>
> **Integración: un solo pytest a la vez.** El conftest hace `DROP DATABASE` +
> `CREATE DATABASE` sobre un nombre único para todo el repo y `flushdb()` de la
> Redis de test en cada setup. Dos procesos simultáneos se destruyen la BD
> («tabla que no existe») y se borran las sesiones («401 session has been
> revoked» en un test que no toca auth). Si necesitas paralelismo, dale a cada
> proceso **las dos**: `TEST_PG_DB_NAME=agentic_platform_test_<algo>` y
> `TEST_REDIS_URL=redis://localhost:6379/<1-14>`.
> [gotchas/integration-tests-share-one-database.md](docs/03-guides/gotchas/integration-tests-share-one-database.md).
>
> **Y ahí es donde se esconden los rojos**: son cientos de ficheros que CI no
> corre y nadie corre enteros, así que un test rezagado sobrevive commits. Cuando
> cambies lo que devuelve una ruta, **busca por la ruta, no por el fichero** —
> `grep -rln "auth/sso/oidc/callback" tests/`— y corre ese lote.
> [gotchas/cambio-de-contrato-deja-tests-rezagados.md](docs/03-guides/gotchas/cambio-de-contrato-deja-tests-rezagados.md).

## Mapa: dónde está cada cosa

| Busco…                       | Está en                                                                                                |
| ---------------------------- | ------------------------------------------------------------------------------------------------------ |
| Qué se hizo y por qué        | `docs/07-changelog/<plan_id>.md`                                                                       |
| Estado y tareas de un plan   | `docs/roadmap/<plan_id>.md` (frontmatter + checkboxes)                                                 |
| Una decisión de arquitectura | `docs/05-architecture-decisions/`                                                                      |
| Una trampa del toolchain     | [`docs/03-guides/gotchas/`](docs/03-guides/gotchas/)                                                   |
| Cómo no perder el tiempo     | [`docs/03-guides/verificar-antes-de-implementar.md`](docs/03-guides/verificar-antes-de-implementar.md) |
| Principios y protocolo       | `CLAUDE.md`                                                                                            |

## Últimos hitos (para contexto, no para fiarse)

- **2026-08-10** — Las cinco tablas append-only convertidas a particionadas
  (part-01, ADR 0151/0154), Vault operable, y **una fuga cross-tenant que se
  había metido en la propia ola** y la cazó un test. Además: el healthcheck de
  los dos tinyproxy nunca fue válido y el `|| true` lo tapaba; la imagen del
  panel no construía por `useSearchParams` sin `<Suspense>`.
- **2026-08-01** — **Los ocho ADR pendientes, firmados** (`95fc7fbc`) y lo que
  desbloquean, implementado: Fernet en columna como excepción acotada a Vault
  (0146), imágenes de runtime por digest (0148), quiesce del backup (0149),
  retención de tablas append-only (0151). Más guardrails por capas, coste
  facturable, sesiones y el i18n del panel.
- **2026-07-31** — Marketplace v2: el despliegue como entidad («que instalar sea
  recibir»), rotación de claves, y el backup de Redis que **restauraba vacío**.
- **2026-07-28** — **Último despliegue real**: 106 commits, 6 imágenes, esquema
  0118→0121. Copia previa `20260728T114814Z`, imágenes anteriores etiquetadas
  `:predeploy-20260728` (rollback = `docker tag` de vuelta). Lección cara: el
  `up -d` **relanzó dos tareas congeladas**.
