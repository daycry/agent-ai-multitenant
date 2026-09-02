# CONTINUE HERE — dónde retomar el trabajo

> **Última actualización: 2026-09-02** · #177 mergeado; PR #178 (ola 2) abierto; rama `plan/remediacion-ciclo-vida-2026-09-ola3` (ver §0)
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

## 0. Ahora mismo (2026-09-02): remediación del ciclo de vida, por olas

Plan
[`docs/roadmap/remediacion-ciclo-vida-proyecto-2026-09-01.md`](docs/roadmap/remediacion-ciclo-vida-proyecto-2026-09-01.md)
(`status: in_progress`), nacido del informe
[`auditoria-ciclo-vida-proyecto-2026-09-01.md`](docs/roadmap/auditoria-ciclo-vida-proyecto-2026-09-01.md).

- **Mergeado en `master` (PR #177)**: auditoría git/dependencias + olas 0 y 1
  (`task_cv_00…08`, `task_cv_10…15`). Ocho pasadas de CI hicieron falta; lo que
  destaparon está en el historial del PR y en los tests (ruff-format y no
  black; ids de revisión ≤ 32 chars; fixtures locales; `workers-aux` en las
  listas de quiesce/restore).
- **PR #178 (ola 2, rama `…-ola2`)**: siete de ocho tareas; queda `task_cv_25`
  (bridge efímero por ejecución), anotada en el plan con el patrón a seguir.
- **Rama `…-ola3`** (encima de `…-ola2`): `task_cv_30`, `31`, `32`, `34`, `35`
  hechas; quedan `task_cv_33` (guía de ejecución en el dispatch + `merge` con
  capacidades) y `task_cv_36` (refresco de arranque de plantillas/políticas/
  corpus + cruce `allowed_commands` vs binarios del runtime). Cuando #178 haga
  squash-merge: `git rebase --onto origin/master <sha-cabeza-de-ola2> plan/remediacion-ciclo-vida-2026-09-ola3`.
- **Ola 4 entera** (`task_cv_40…45`), las actualizaciones de ADR de los
  criterios de cierre y el changelog: sin empezar.

Comprobar antes de fiarse: `gh pr list`, `git log --oneline -5` en cada rama,
`./.venv/Scripts/python.exe -m pytest tests/unit -q` y
`grep -c "\[x\]" docs/roadmap/remediacion-ciclo-vida-proyecto-2026-09-01.md`.
Los tests de integración (BD) sólo corren en CI. **Orden de despliegue**: imagen
del `agent-runtime` y worker antes que orquestador (`claim_id`, spec y token por
fichero).

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

| Estado                     | Planes (con `plan_id`) | Ficheros | Qué significa aquí                                       |
| -------------------------- | :--------------------: | :------: | -------------------------------------------------------- |
| `pending_human_validation` |           36           |    47    | código entregado; esperan tests humanos                  |
| `completed`                |           19           |    25    | cerradas del todo                                        |
| `pending_approval`         |           14           |    14    | ver el aviso de abajo: **ya no significa «sin empezar»** |
| `in_progress`              |           1            |    1     | `marketplace-v2-despliegue` (el protocolo permite una)   |
| `blocked`                  |           1            |    1     | `guardas-research-por-novedad`: sólo le falta el e2e     |
| **Total con `plan_id`**    |         **71**         |          |                                                          |

Los 11 ficheros de diferencia en `pending_human_validation` **no llevan
`plan_id`** —ocho son las fases del córtex, con casillas y `blocking_plan`
propios— así que se saltan TODOS los guardas de gate, changelog y
`in_progress`. Es deuda inventariada y acotada:
`test_no_new_roadmap_file_escapes_the_guards_by_omitting_plan_id` impide que
aparezca el número dieciocho.

> ### ⚠️ `pending_approval` ya no quiere decir «nunca empezado»
>
> Medido el 2026-08-12: **los catorce** planes en ese estado tienen casillas
> marcadas, y **seis no tienen ninguna abierta** — `cadena-pr-plan`, `prod-03`,
> `prod-04`, `prod-05`, `prod-07` y `prod-09` están **entregados** con la
> etiqueta de «definido pero no empezado». Es incoherencia nueva, creada por las
> olas de estas dos semanas.
>
> No se les ha cambiado el estado a propósito, por dos razones: pasar a
> `pending_human_validation` exige su entrada en `docs/07-changelog/` (sólo
> `prod-07` la tiene) y, sobre todo, **cambiarles el estado afirmaría una
> aprobación que nadie ha dado**. La decisión es tuya. Lo que sí hay es un
> guarda para que no crezca:
> `test_no_new_plan_is_delivered_while_still_labelled_unstarted`.

**ADR en `proposed`: uno solo**, `0152-recall-vectorial-multitenant-hnsw`. Los
ocho que estaban pendientes se firmaron el 2026-08-01 (`95fc7fbc`) y lo que
desbloqueaban está implementado.

## Qué necesita al operador (por orden de coste)

1. **Validar las 36 fases en `pending_human_validation`.** Sigue siendo el cuello
   de botella real: mientras ninguna llegue a `completed`, toda fase que dependa
   de ellas lee su gate como incumplido (es la causa de fondo que mide el ADR
   0138). Procedimiento:
   [06-runbooks/03-system-upgrade.md](docs/06-runbooks/03-system-upgrade.md).
2. **Decidir qué pasa con los 14 `pending_approval`**, y en particular con los
   seis que ya están entregados (ver el aviso de arriba). Aprobarlos a posteriori
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

- 🔴 **El subset e2e del panel lleva semanas ROJO y CI no podía decirlo**
  (descubierto el 2026-08-19). El job «Frontend e2e (Playwright, mocked subset)»
  acumula **106+ tests en rojo**; cada uno agota su timeout de 30 s, así que sólo
  en esperas se iban ~53 de los 60 minutos del job y GitHub lo marcaba
  `cancelled` — que no es ni verde ni rojo. Un job que no termina no informa de
  nada, y por eso nadie lo vio.
  - **Ya arreglado**: el job falla rápido (`--max-failures=15 --timeout=15000`),
    así que a partir de ahora da veredicto en minutos. Y una causa raíz cerrada:
    `sidebar-complete.spec.ts` seleccionaba por nombre accesible
    (`{ name: "Agentes" }`), que dejó de ser único el día que entró «Agentes
    humanos» en la nav — `strict mode violation`, no un fallo de producto.
    Ahora selecciona por `data-testid`, que es lo que esta casa usa.
  - **Lo que queda**: los ~100 rojos restantes, sin agrupar por causa. Muestra de
    3 specs: 5 fallos, de los que 1 era el selector de arriba. `settings-memories`
    falla en el número de PUT y en un `data-testid` que no aparece; hay que
    mirarlos uno a uno y separar **deuda del arnés** (selector movido, testid
    renombrado) de **defecto real del panel**. Lo segundo NO se maquilla.
  - Ni un `test.skip` para poner el marcador en verde: si algo no se arregla, se
    queda rojo y se explica.

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

## Verificación local (con CI caído)

Cifras **medidas el 2026-08-12** en esta rama, en el mismo orden que los pasos de
`.github/workflows/ci.yml`:

```bash
# Los tres primeros van juntos: 4773 tests en ~7,3 min, y es lo más barato que hay
.venv/Scripts/python.exe -m pytest tests/unit/ tests/security/ tests/docs/ -q
#   → 4770 passed, 2 failed, 1 skipped en 437 s   ← los 2 rojos, abajo

.venv/Scripts/python.exe -m pytest packages/shared-llm/tests -q          # 191 (+1 skip sin claude_agent_sdk)
cd docker/agent-runtimes/agent-runtime && ../../../.venv/Scripts/python.exe -m pytest tests/ -q   # 501
PYTHONPATH=docker/agent-runtimes/browser-runtime \
  .venv/Scripts/python.exe -m pytest docker/agent-runtimes/browser-runtime/tests -q               # 19

.venv/Scripts/python.exe -m mypy apps/ packages/    # Success: 659 ficheros, limpio
```

> **Los 2 rojos del 2026-08-12, y de dónde salen**:
> `tests/unit/test_model_options_deprecation.py::test_the_by_kind_endpoint_is_gone`
> y `::test_the_replacement_is_still_there` mueren con
> `FileNotFoundError: routers/agents.py`. No es una regresión funcional: otro
> carril partió ese router en el paquete `routers/agents/`
> (`crud.py` / `tools.py` / `skills.py` / …) y el test lee el fichero **por
> ruta**. El arreglo es actualizar su constante `_AGENTS`; lo debe hacer el
> carril que movió el router.

**Del panel (`apps/admin-panel`) no hay cifras nuevas en esta pasada**, y decirlo
es más útil que copiar las viejas: durante la medición había otro carril con
cambios sin comitear en `app/admin/tools/page.tsx` y `lib/i18n/dictionary.ts`, así
que cualquier número habría descrito un árbol a medio escribir. Los cuatro pasos
que hay que correr antes de dar el panel por bueno:

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
