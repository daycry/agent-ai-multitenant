---
title: "Auditoría del ciclo de vida de un proyecto: workers, agentes, memorias y runtimes"
status: informe
date: 2026-09-01
docs_language: es
scope:
  - ciclo de vida de una ejecución en el worker
  - contenedores de implementación, revisión y test/stack
  - orquestación y pipeline de review
  - bucle del agente en el runtime
  - memorias de los agentes
  - agentes, equipos, seeds y prompts
  - cierre de plan y mantenimiento periódico
findings: 81
verified_adversarially: 25
branch: fix/auditoria-git-dependencias-2026-09-01
baseline_commit: 9b5c6fe5
remediation: ./remediacion-ciclo-vida-proyecto-2026-09-01.md
---

# Auditoría del ciclo de vida de un proyecto (2026-09-01)

## §1 Alcance y método

Todo lo que le pasa a un proyecto desde que una tarea se reclama hasta que el plan cierra con
su PR: cómo el worker lanza y vigila la ejecución, qué contenedor la aloja y con qué envoltura,
cómo el orquestador decide quién revisa y con qué evidencia, qué hace el agente dentro del
bucle y qué le protege, qué recuerda y qué le devuelven las memorias, cómo se configuran los
agentes que hacen todo eso, y qué ocurre después (PR, documentación, poda, backup).

**Método.** Siete barridos estáticos del código en paralelo, uno por dimensión, con la orden de
distinguir lo nuevo de lo ya conocido en las auditorías anteriores. Después, **verificación
adversarial de los 25 hallazgos críticos y altos**: se abrió cada línea citada y se intentó
refutar el hallazgo buscando el código que lo contradijera, el test que lo cubriera o la ruta
que lo mitigara. Dos se reprodujeron ejecutando código real (§5 C-02, §3 A-02). Los 56
medios y bajos llevan la evidencia del analista, no re-verificada; van marcados.

**Baseline.** `master` en `9b5c6fe5` más los cambios sin commitear de la rama
`fix/auditoria-git-dependencias-2026-09-01` (auditoría del mismo día sobre git, dependencias y
guardas de borrado). Lo que esa rama cierra —lock con dueño, reparación del puntero `.git`,
des-versionado por autoría, protección a cualquier profundidad, base del SDK sin `rm`/`mv`—
**no** se re-auditó aquí y se da por resuelto.

**Fuera de alcance.** Frontend, marketplace, SSO, córtex del owner, asistente personal,
instalador (salvo el compose que genera), observabilidad. **No se ha tocado el stack vivo**: no
había `docker/.env` en la máquina de la auditoría; las consultas SQL que confirmarían el
impacto real de varios hallazgos están anotadas en §11.

**Cómo leer un hallazgo.** `Estado` es `broken` (falla de forma determinista), `risk` (falla
bajo una condición realista), `gap` (promesa del diseño sin cumplir) o `improvement`.
`Conocido` dice si ya estaba en una auditoría anterior y si aquello se cerró. Los `fichero:línea`
son citas comprobadas sobre el baseline.

---

## §2 Resumen ejecutivo

**81 hallazgos; 25 verificados adversarialmente y los 25 sostenidos.** Ninguno es una fuga
cross-tenant en la base de datos: RLS, punteros de scope y la frontera de tenant del
orquestador están bien (§10). El patrón dominante de esta auditoría ya no es el «cableado del
último tramo» del 2026-07-25. Es **atribución y contrato**:

> Varias piezas leen «la última ejecución de la tarea» sin saber si es del implementador o
> del reviewer, de esta reclamación o de la anterior. El runtime aparca con una política que el
> worker evalúa con otra. La fase de tests se cerró con un test que no ejercita a Celery. El
> compose de producción niega el `exec` que el propio diseño exige. Los agentes core pinean un
> proveedor que el catálogo cerrado no tiene, y las copias lo heredan.

Cuatro roturas **deterministas en producción** (A-01, A-02, B-01, F-01) explican por sí solas
por qué el recorrido E2E del 2026-08-29 sólo prosperó con el equipo CI4 sobre Ollama: es el
único equipo que no pinea proveedor, y sus tareas no tenían criterios automáticos que
disparasen la fase de tests. Después vienen cuatro defectos de **atribución** en el pipeline de
review (C-01…C-04) que hacen que el reviewer juzgue lo que no es, y un bloque de **sandbox**
(D-01…D-04, B-04, B-07) en el que las promesas de aislamiento se cumplen en la envoltura del
contenedor y se pierden en lo que la rodea.

### Distribución

| Severidad | A · Ejecución | B · Contenedores | C · Orquestación | D · Runtime | E · Memorias | F · Agentes | G · Cierre | Total  |
| --------- | ------------- | ---------------- | ---------------- | ----------- | ------------ | ----------- | ---------- | ------ |
| Crítico   | 2             | 1                | 0                | 0           | 0            | 1           | 0          | **4**  |
| Alto      | 3             | 3                | 4                | 4           | 3            | 2           | 2          | **21** |
| Medio     | 4             | 6                | 4                | 4           | 6            | 5           | 5          | **34** |
| Bajo      | 3             | 2                | 3                | 3           | 3            | 3           | 5          | **22** |
| **Total** | **12**        | **12**           | **11**           | **11**      | **12**       | **11**      | **12**     | **81** |

### Las diez que arreglaría primero

| #   | Hallazgo                                                                                     | Por qué primero                                                                                         |
| --- | -------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| 1   | **A-02** La fase de tests post-run no puede correr en un worker prefork                      | Todo `done` con criterios automáticos llega al reviewer como fallo de infraestructura. Reproducido.     |
| 2   | **A-01** Proyecto sin política de aprobación: run aparcado, tarea `in_progress` para siempre | No hay red que lo recupere. Un proyecto creado por API o chat está en ese estado por defecto.           |
| 3   | **B-01** El compose del instalador deja `EXEC=0` en el socket-proxy                          | Checks, `pre_install`, `stack_exec` y sidecars dan 403 en producción; un test protege el valor roto.    |
| 4   | **F-01** Los agentes core pinean `provider="anthropic"`                                      | Fuera del catálogo cerrado; las copias adoptadas lo heredan; todo run de un equipo core aborta.         |
| 5   | **C-03** El reviewer recibe su propio veredicto como salida del implementador                | Se ancla a su propio rechazo: es el Goodhart que `gov_06` mide, y lo mide sobre otra fila.              |
| 6   | **C-02** El diff de re-review arrastra commits de tareas hermanas                            | Reproducido: el reviewer certifica o rechaza trabajo que no es de la tarea.                             |
| 7   | **C-01 / A-05** El reconciler y V-1 actúan sobre reclamaciones sin identidad                 | Con la cola llena, una tarea re-despachada se juzga por el run anterior y el implementador nunca corre. |
| 8   | **D-01** El spec y el token interno son legibles desde `shell_exec`                          | Reproducido: `python -c "import os; print(os.environ[...])"`. El token autoriza `mcp-oauth-token`.      |
| 9   | **C-04 / A-06** Un `rebase_conflict` o `commit_failed` no bloquea la tarea                   | El reviewer aprueba un commit que no está en la rama; el plan cierra y el PR sale sin ese código.       |
| 10  | **B-02 / B-03** Sidecars sin `cap_add` y AppArmor sin `x` en `/opt`                          | ADR 0129 «probado» con un MagicMock; el perfil que pina el instalador impide ejecutar Java y Chromium.  |

---

## §3 Eje A — Ciclo de vida de la ejecución (12 hallazgos)

### A-01 · CRÍTICO · Proyecto sin `human_approval_policy`: el run aparca y el worker no crea la solicitud

- Estado: `broken` · Conocido: nuevo (el ADR 0104 cerró sólo el lado del runtime; la migración
  `20260802_0133` deja `NULL` a propósito). **Verificado.**
- Evidencia: el worker resuelve la política **efectiva** (preset `development` si el proyecto
  no tiene) y la inyecta al runtime (`apps/workers/src/workers/execution.py:293-317`, `:1500`).
  Al finalizar llama a `request_approval_if_needed(project=project, …)` (`execution.py:1987`),
  que evalúa `project.human_approval_policy` **cruda** (`approval_repo.py:398`) y devuelve
  `False` si está vacía (`:205-206`). El docstring lo declara: «una política ausente/vacía
  […] NO es de este ADR: la resuelve el ADR 0104 heredando el preset […] en el worker, así que
  aquí sigue devolviendo False».
- Qué pasa: el runtime aparca en `awaiting_human_approval` con la política efectiva; el worker
  decide con la cruda que no hacía falta, no crea `ApprovalRequest`, y `finalize_execution` ya
  escribió `status=awaiting_human_approval, completed_at=NULL`. Nadie recupera ese estado: el
  reconciler exige último run terminal (`maintenance/reconciler.py:63-83`), el sweeper sólo barre
  `running`, `approval_expiry` necesita una solicitud. La tarea queda `in_progress`, el agente
  cargado y el plan congelado; la única salida es cancelar a mano.
- Arreglo: pasar `prepared.approval_policy` como `policy=`; si el runtime aparcó y el worker
  decide «no hacía falta», tratar como F12 (`failed(approval_policy_mismatch)` + transición).
  Coste S. Plan: `task_cv_01`.

### A-02 · CRÍTICO · La fase de tests post-run nunca corre: `AsyncResult.get()` dentro de un task prefork

- Estado: `broken` · Conocido: C-04 / `task_wf_22` del 2026-07-25 (**el cierre introdujo el
  fallo**). **Verificado y reproducido.**
- Evidencia: `apps/workers/src/workers/tasks/test_runtime_task.py:263-269` hace
  `async_result.get(timeout=budget)` vía `asyncio.to_thread` dentro de `run_execution`. Los
  workers arrancan sin `--pool` (prefork), y no hay `allow_join_result`, `disable_sync_subtasks`
  ni `task_always_eager` en `apps/workers`. Reproducido en el venv con Celery 5.6.3:
  `_set_task_join_will_block(True); AsyncResult(...).get()` →
  `RuntimeError: Never call result.get() within a task!`. Los tests unitarios usan un
  `_FakeApp` (`tests/unit/test_test_phase_queue.py:80-95`) que no lo ve.
- Qué pasa: cada `done` con criterios automáticos cae en `except Exception` →
  `infra_failure_outcome(stage="test_phase_dispatch_failed")` persistido como
  `test_run_completed`. El reviewer ve un fallo de infraestructura en todos los runs y ningún
  criterio ejecutable se verifica jamás. Aunque se parchee con `allow_join_result`, el patrón de
  espera síncrona en la lane única `default,ingestion,test,review` con `--concurrency=2` se
  autoinanicia con dos runs esperando a la vez.
- Arreglo: mínimo `allow_join_result()` + lane `test` separada; correcto: no esperar dentro
  de `run_execution` (encadenar y que el consumidor de `test_run_completed` despache el
  review). Coste S/M. Plan: `task_cv_00`.

### A-03 · ALTO · Una excepción del vigía de cancelación se re-lanza al terminar el contenedor

- Estado: `broken` · Conocido: nuevo. **Verificado.**
- Evidencia: el bucle de `_watch_for_cancel` (`execution.py:1852-1866`) consulta la BD cada 3 s
  sin `try`; el `finally` hace `watcher.cancel(); with suppress(CancelledError): await watcher`
  (`:1874-1877`), que re-lanza cualquier otra excepción, y `staged_credentials.cleanup()` va
  después (`:1881-1882`).
- Qué pasa: un blip de BD durante un run de 2 h hace que, **al terminar bien el contenedor**,
  `_launch_and_stream` reviente: el `execution.finished` se descarta, el run va al DLQ, la fila
  queda `running` hasta que el sweeper la sella como `stale_after_worker_loss` (etiqueta falsa),
  la tarea acaba `blocked` y el fichero de credencial queda en disco.
- Arreglo: `try/except` + `continue` en el bucle; `cleanup()` en su propio `finally` antes de
  esperar al vigía. Coste S. Plan: `task_cv_08`.

### A-04 · ALTO · Muerte dura del worker con contenedor vivo: resultado perdido, fila 7 h en `running`

- Estado: `risk` · Conocido: parcial (ADR 0149 y C-05 describen que «el reaper las recupera»,
  no la mecánica). **Verificado.**
- Evidencia: la re-entrega de Celery (`task_reject_on_worker_lost`) se descarta por el run-lock
  antes de llegar al supersede (`tasks/run_cycle.py:254-262`); `list_managed_execution_ids` usa
  `all=True` (`container.py:331-350`), así que un contenedor `exited` sin `remove` cuenta como
  vivo y el sweeper no lo trata como huérfano (`stale_sweeper.py:208-213`); sólo cae por edad
  (`_STALE_EXECUTION_AFTER = 7h`).
- Qué pasa: el backup nocturno (`docker compose stop --timeout 180`) mata al worker cada noche;
  el contenedor DooD sigue quemando tokens hasta su hard-limit, emite `execution.finished` en sus
  logs y nadie lo lee; la tarea queda `in_progress` y con lock 7 h, y acaba `blocked`.
- Arreglo: en el sweeper, para filas `running` con contenedor `exited/dead`, leer los logs →
  `_scan_logs_for_terminal` (ya existe) y finalizar con el resultado real; si no hay terminal,
  sellar ya. Coste M. Plan: `task_cv_12`.

### A-05 · ALTO · V-1 revierte reclamaciones que llevan más de 30 min **en cola**

- Estado: `risk` · Conocido: variante «despliegue» en `gotchas/deploy-relaunches-frozen-tasks.md`;
  la variante «cola profunda» es nueva. **Verificado.**
- Evidencia: `_RECONCILE_ORPHAN_CLAIM_MIN_AGE = 30 min` (`reconciler.py:48`); la reversión a
  `ready` limpia `assigned_agent_id`/`started_at` (`:201-256`); `ExecutionRequest` no lleva
  ningún identificador de reclamación (grep `claim_id|dispatch_id` vacío); `_prepare_run` sólo
  comprueba `status`; el competidor se descarta y ACKea (`run_cycle.py:255`).
- Qué pasa: con `prefetch=1`, `--concurrency=2` y runs de hasta 2 h, un mensaje espera más de
  30 min en cuanto hay tres tareas `ready`. El orquestador re-reclama y encola un segundo
  mensaje. Peor: msg1 corre, el reviewer rechaza, msg3 sale con `prior_review_feedback`, y el
  msg2 viejo (sin feedback) sale antes de la cola: corre ciego y msg3 muere como
  `concurrent_run_locked`.
- Arreglo: `claim_id` en la tarea que viaje en `ExecutionRequest` y que `_prepare_run` compare.
  Coste M. Plan: `task_cv_13`.

### A-06 · MEDIO · `commit_failed`/`rebase_conflict` no impiden que la tarea avance

Ver C-04, que lo mide desde el pipeline de review: la transición a `in_review`/`done` se
persiste antes del commit (`execution.py:2001` frente a `:2064`), el fallo sólo estampa
`abort_code`, y el panel de escalados lista `rebase_conflict` pero no `commit_failed`. Plan:
`task_cv_11`.

### A-07 · MEDIO · Un error del daemon al lanzar no se captura

`_launch_and_stream` no envuelve `_start`/`docker.from_env()`; `container_launched_at` se
sella **antes** del arranque (`execution.py:1848-1850`), así que un `pull` fallido o el
socket-proxy caído se diagnostica cinco minutos después como «pérdida de worker», con una
entrada DLQ sin lector. Arreglo: capturar → `failed(container_launch_failed)` + transición;
sellar tras `_start`. Coste S. Plan: `task_cv_15`.

### A-08 · MEDIO · Un run de REVIEW que aparca en aprobación queda no terminal para siempre

La rama `awaiting_human_approval` es inalcanzable para `request.review` (`execution.py:1964-1981`),
pero el reviewer recibe la misma `approval_policy` y puede aparcar; `_apply_review_verdict` lo
cuenta como error de infraestructura y consume `retry_count`. Arreglo: política que no aparque
para reviews (workspace ya read-only). Coste S. Plan: `task_cv_15`.

### A-09 · MEDIO · `dlq:executions` no tiene lector

Única escritura en `run_cycle.py:193-210`; ningún endpoint, métrica ni beat lo lee; `maxlen=10_000`
lo recorta en silencio. Los fallos de A-03 y A-07 desaparecen ahí. Arreglo: métrica
`agentic_dlq_depth` + listado System Admin. Coste S. Plan: `task_cv_43`.

### A-10 · BAJO · El soft time limit sella la ejecución pero no la tarea

`_finalize_soft_timeout` (`run_cycle.py:121-190`) no llama a `transition_task_after_run`; la
tarea depende del reconciler. Además el `.git` se repone antes de que muera el contenedor.
Coste S.

### A-11 · BAJO · `AGENT_TASK_SPEC` queda íntegro en `ContainerResult.config_env`

`_SENSITIVE_ENV_SUFFIXES` (`container.py:110-125`) no cubre el spec; con la válvula
`WORKERS_MODEL_CREDENTIAL_FILE=false` o con `mcp_servers[].headers` el secreto viaja en el
`inspect`. Coste S.

### A-12 · BAJO · Consultas por `task_id` sin `tenant_id` en caminos administrativos

`_finalize_soft_timeout`, `_run_task_tests`, `_persist_declared_checks`. No explotables (los ids
vienen de `_prepare_run` ya validado), pero violan la regla dura nº 1. Coste S.

---

## §4 Eje B — Contenedores y aislamiento (12 hallazgos)

### B-01 · CRÍTICO · El compose del instalador deja `EXEC=0` en el socket-proxy

- Estado: `broken` · Conocido: el ADR 0093 documenta el fix sólo para `docker-compose.manuals.yml`.
  **Verificado.**
- Evidencia: `apps/installer/backend/src/installer_backend/compose_generator.py:932` `"EXEC": "0"`;
  `docker/docker-compose.manuals.yml:151` `EXEC: "1"` con el comentario «Con EXEC=0 todo
  exec_run daba 403 Forbidden»; `tests/unit/test_compose_generator.py:985` asegura el `0`;
  los workers generados hablan con `tcp://docker-socket-proxy:2375` (`:1276`); consumidores:
  `test_runtime.py:948` (`_wait_healthy`), `:1271` (checks, `pre_install`, `run_command`),
  `tasks/review_runtime_task.py:446`.
- Qué pasa: en cualquier instalación generada por el wizard —el compose de producción según el
  ADR 0061— cada `exec_run` recibe 403: los acceptance checks salen como
  `runtime_launch_failed`, `stack_exec` devuelve error al agente, los sidecars nunca pasan el
  healthcheck. El ADR 0060 promete «sin `docker exec`» como característica de seguridad cuando
  el diseño exige `exec`.
- Arreglo: `EXEC=1`, invertir el aserto, corregir el ADR 0060, test de coherencia con
  `manuals.yml`. Coste S. Plan: `task_cv_02`.

### B-02 · ALTO · Los sidecars ADR 0129 no pueden arrancar bajo `cap_drop ALL` sin `cap_add` ni `user`

- Estado: `broken` (alta confianza; no ejecutado) · Conocido: la causa está en
  `gotchas/docker-cap-drop-all-breaks-official-images.md`, con fix sólo en el compose (`x-infra-caps`).
  **Verificado en el código.**
- Evidencia: `build_aux_run_kwargs` (`test_runtime.py:599-610`) devuelve `cap_drop: ["ALL"]` sin
  `cap_add` ni `user`; `docker-compose.yml:84` define `x-infra-caps: [CHOWN, DAC_OVERRIDE, FOWNER,
SETGID, SETUID]` para las mismas imágenes; el test de hardening usa `MagicMock`.
- Qué pasa: `gosu postgres`/`su-exec redis` hacen `setuid` sin `CAP_SETUID` → el contenedor
  muere; `_wait_healthy` hace `exec_run` sobre un contenedor parado → 409 → `runtime_launch_failed`.
  Cualquier proyecto que declare `services` no funciona. Y `remove(force=True)` sin `v=True`
  deja un volumen anónimo por sidecar que el proxy (`VOLUMES=0`) no deja podar.
- Arreglo: `cap_add` con el mismo conjunto del compose, `v=True`, test `docker` real. Coste S.
  Plan: `task_cv_04`.

### B-03 · ALTO · El perfil AppArmor no permite ejecutar bajo `/opt` ni `/ms-playwright`

- Estado: `risk` (no verificable sin host Linux con AppArmor) · Conocido: C-03 del 07-25 se
  cerró pinando los perfiles; éste es el fallo siguiente. **Verificado en el perfil.**
- Evidencia: `docker/apparmor/agent-runtime.profile` da `ix` sólo a `/usr/**`, `/bin/**`,
  `/sbin/**`, `/lib/**`, `/lib64/**` (`:42-46`), `/workspace/**` (`:51`) y `/home/agent/**`
  (`:67`); `java-gradle/Dockerfile:11` cita `/opt/gradle/lib`; las bases de maven/gradle llevan
  `JAVA_HOME=/opt/java/openjdk`; las imágenes Playwright ponen Chromium en `/ms-playwright`;
  el perfil se aplica también al test-runtime (`test_runtime.py:1215`).
- Qué pasa: con `WORKERS_APPARMOR_PROFILE=agent-runtime` (lo que el instalador genera), `java`,
  `gradle` y `chrome` mueren con EACCES al `execve`; en dev (WSL2 sin AppArmor) nunca se ve.
- Arreglo: `/opt/** rix,` y `/ms-playwright/** rix,` + test que recorra `PATH`/`JAVA_HOME` de
  cada Dockerfile contra el perfil. Coste S. Plan: `task_cv_05`.

### B-04 · ALTO · La dep-cache se comparte entre tenants por hash de lockfile, en RW y 0777

- Estado: `risk` · Conocido: nuevo (F4 de `registry-egress-followups` trató la escribibilidad,
  no el scoping). **Verificado.**
- Evidencia: `dep_cache.py:227` `self._root / f"{prefix}-{lock_hash}"` (sin tenant; grep `tenant`
  vacío en el módulo); `:253` `os.chmod(host_path, 0o777)`; `test_runtime.py:1157-1165` lo monta
  con `read_only=False` en el contenedor no confiable.
- Qué pasa: dos proyectos de tenants distintos con el mismo `pom.xml`/`Gemfile.lock`/`composer.lock`
  (starters, plantillas) comparten el mismo directorio escribible; maven/gradle/bundler no
  verifican contenido contra el lockfile y composer suele llevar `shasum` vacío: un JAR o zip
  envenenado por el `stack_exec` del tenant A lo ejecuta el tenant B en sus tests.
- Arreglo: clave `{tenant_slug}/{prefix}-{hash}`; `chown 1000` en vez de 0777. Coste M. Plan:
  `task_cv_24`.

### B-05 · MEDIO · Imágenes escritas por el tenant sin allowlist ni procedencia; `version` deshace el pin por digest

`runtime_services.py:46` acepta cualquier `host/repo:tag`; `_image_from_version` (`:214-222`)
construye `postgres:<version>` sin `@sha256`; `review_image`/`main_image`/`runtime_image` van
al daemon del host, que hace `pull` fuera de cualquier proxy. Ya en `2026-08-28-imagenes-de-proyecto-y-preview.md`
§5.4 (abierto); el bypass por `version` es nuevo. Nota funcional: `runtime_image` sólo aplica
en `stack_exec`, no en los acceptance checks. Plan: `task_cv_44`.

### B-06 · MEDIO · El preview de review monta el worktree del plan en RW hasta 48 h

`review_runtime_task.py:576` no pasa `workspace_read_only=True`; la app del tenant puede
reescribir el árbol que el humano valida y un `git add -A` posterior lo arrastraría. Plan:
`task_cv_26`.

### B-07 · MEDIO · Una sola L2 `agentic-agents` con ICC para todos los sandboxes de todos los tenants

`isolation.py:15-16` promete «no inter-container traffic»; `container.py:180` y el compose
ponen `enable_icc: true`; api-server y workers están en la misma red. Un agente del tenant A
puede abrir un listener y otro del B conectar; los previews escuchan ahí saltándose el proxy
firmado. El ADR 0094 rechazó esta red para los test-runtimes por el mismo motivo. Plan:
`task_cv_25`.

### B-08 · MEDIO · El wrapper `timeout` del `exec_run` no mata (sin `-k`)

`test_runtime.py:1270` `timeout {n} sh -c …` envía SIGTERM; un proceso que lo ignora cuelga el
hilo del worker sin techo, con el registry-proxy conectado. `timeout -k 10` + deadline externo.
Plan: `task_cv_45`.

### B-09 · MEDIO · `agent-runtime:v1` y `browser-runtime:v1` no se publican ni fijan por digest

El ADR 0148 cubrió las 14 plantillas; el contenedor donde corre el bucle del agente sigue siendo
«cada host construye su variante» (`release-images.yml` no lo menciona). Plan: `task_cv_44`.

### B-10 · MEDIO · Los consumidores de `worktree_host_path` no validan contra `data_root`

`run_cycle.py:38-56`, `test_runtime_task.py:528`, `review_runtime_task.py:574` aceptan cualquier
ruta de host; el socket-proxy filtra endpoints, no payloads (`VOLUMES=0` no impide binds en
`HostConfig`). El ADR 0060 sobrepromete. Plan: `task_cv_45`.

### B-11 · BAJO · Envoltura de recursos desigual entre los tres tipos

Sin `nano_cpus` en el agente, sin `memswap_limit` en ninguno (swap hasta 2× `mem_limit`),
sidecars sin `read_only`/`user`/CPU, `network_policy: none` que sí conecta un bridge, y
`remove` sin `v=True`. Coste S.

### B-12 · BAJO · El seccomp estricto del agente es ahora el default para 14 toolchains y Chromium sin ejercitarse

Omite `sched_setaffinity`, `inotify_*`, `pidfd_open`, `io_uring_*`…; Gradle file-watching, JVM
con afinidad y watchers de Jest/Vitest pueden fallar con errores opacos sólo en producción.
Arreglo: smoke matrix en `build-runtime-templates.yml` con el perfil real. Coste S.

---

## §5 Eje C — Orquestación y pipeline de review (11 hallazgos)

### C-01 · ALTO · El reconciler transiciona una tarea re-despachada usando una ejecución anterior a su reclamación

- Estado: `risk` · Conocido: V-1 del 07-25 cerró `latest is None`; éste es «`latest` existe pero
  es más viejo que el claim». **Verificado.**
- Evidencia: `_reconcile_stuck_tasks` filtra `Task.started_at < cutoff` (`reconciler.py:297`),
  toma la última `Execution` por `created_at` (`:306-311`) y llama
  `transition_task_after_run(db, task_id, latest.status)` (`:331`) sin comparar
  `latest.created_at` con `task.started_at`; `done` → `in_review` si hay reviewer
  (`execution.py:388-393`).
- Qué pasa: reject → `backlog` → `ready` → claim `in_progress` → mensaje en cola más de 5 min
  (normal con la lane única) → el reconciler ve el run del reviewer (`done`) y mueve la tarea a
  `in_review` → el mensaje del implementador llega y `_task_is_launchable` lo descarta → se
  despacha OTRO review del trabajo viejo → reject → … hasta `blocked`, sin que el implementador
  haya corrido.
- Arreglo: si `latest.created_at < task.started_at`, tratar como reclamación huérfana. Coste S.
  Plan: `task_cv_10`.

### C-02 · ALTO · El diff de re-review arrastra commits de tareas hermanas intercalados

- Estado: `broken` · Conocido: `task_wf_60` tiene test para hermana ANTES del rango, no
  intercalada. **Verificado y reproducido.**
- Evidencia: `review_diff.py:74-84` `newest, oldest = shas[0], shas[-1]` … `git diff {oldest}^..{newest}`.
  Reproducción con la función real: A1 (`Task-Id=T1`) → B (`Task-Id=T2`, `otro.py`) → A2 →
  `compute_task_review_diff(wt, "T1")` contiene `otro.py`.
- Qué pasa: tras un reject, el worktree se sincroniza al HEAD de la rama (que ya tiene lo que
  empujaron las hermanas) y A2 se rebasea encima; el rango incluye a B. El reviewer rechaza por
  trabajo ajeno o certifica trabajo que nadie le pidió revisar.
- Arreglo: diff por commit (`diff-tree -p` por sha con el trailer) o limitado a los paths de esos
  shas. Coste S. Plan: `task_cv_07`.

### C-03 · ALTO · El reviewer recibe su PROPIO veredicto anterior como `implementer_output`

- Estado: `broken` · Conocido: el filtro por agente se puso el 2026-09-01 sólo en
  `<commands-run>`. **Verificado.**
- Evidencia: `dispatch.py:1781-1791` selecciona `Execution.output` por tarea y tenant, ordenado
  por `created_at`, `limit 3`, **sin filtro de agente**; `:1875` sí filtra
  `Execution.agent_id != reviewer.id` para los comandos. Mismo defecto en
  `_read_predecessor_briefs` (`:2611-2618`) y `_read_prior_failure` (`:2533-2552`).
- Qué pasa: con el propio seed del test (`reviewer_runs_after=3`), `implementer_output` sale como
  «[attempt 4 — latest] <verdict>reject</verdict>» y el entregable real como «[attempt 1 —
  earlier]» recortado; una tarea dependiente recibe como «lo que entregó la tarea 1» el veredicto.
- Arreglo: mismo predicado en los tres sitios, o columna `kind` en `executions`. Coste S. Plan:
  `task_cv_06`.

### C-04 · ALTO · Un `rebase_conflict` no bloquea la tarea: el reviewer puede aprobar a `done` trabajo que no llegó a la rama

- Estado: `risk` · Conocido: ADR 0099 §2 y P7 hacen visible el código; el panel exige
  `Task.status == blocked`, que nunca ocurre. **Verificado.**
- Evidencia: la transición se persiste en `execution.py:2001` y el commit va en `:2064`;
  `_mark_commit_failed` (`:2090`) sólo marca la ejecución; ningún consumidor de
  `commit_failed`/`rebase_conflict` bloquea la tarea (grep en orchestrator/api-server: sólo
  `worktree_backfill.py`, que reintenta y vuelve a conflictuar). Tras `rebase --abort` el HEAD
  del worktree sigue siendo el commit local con `Task-Id` → diff válido → `apply_reviewer_verdict`
  → `done`.
- Qué pasa: el plan cierra, el PR no lleva ese trabajo, y el siguiente `sync_to_head`
  (`reset --hard` + `clean -fdx`) lo borra del worktree. La única señal es `abort_code` en una
  fila que nadie mira.
- Arreglo: si el commit devuelve `abort_code`, `in_review → blocked` en la misma transacción;
  guarda en `open_plan_pr` que cuente trailers. Coste S/M. Plan: `task_cv_11`.

### C-05 · MEDIO · Las escaladas del bucle AI-reviewer no notifican ni salen en el panel

`reviewer_bridge.py:326-372` (max_retries), `execution.py:450-476` (cap `review_inconclusive`)
y el cap M5 del reconciler sólo escriben audit; el panel (`plans.py:2166-2175`) mira la última
ejecución, que es la del reviewer (`done`, sin abort); `plan_blocked` sólo se emite desde
`_on_task_done`. Plan: `task_cv_41`.

### C-06 · MEDIO · El despacho de review esquiva la pausa por presupuesto y el estado del proyecto

`_on_task_in_review` (`dispatch.py:1521-1628`) sólo filtra `Project.deleted_at`;
`budget_pause_block` y `status == active` viven sólo en `_dispatch`. Tenant al 100 % → los
reviews siguen gastando. Plan: `task_cv_41`.

### C-07 · MEDIO · El reviewer puede acabar siendo el implementador cuando no hay preset

`_candidates` (`dispatch.py:2776-2850`) no excluye `task.reviewer_agent_id`; `_revert_to_ready`
y `_clear_dead_preset` limpian el preset; `skill_match` sin match cae a `load_balanced` sin
traza. El filtro de `<commands-run>` borra entonces justo la evidencia del implementador.
Plan: `task_cv_41`.

### C-08 · MEDIO · `implementer_output` «latest» viaja sin tope

`dispatch.py:226` sólo recorta los intentos anteriores; el comentario de `<commands-run>` afirma
«4.000 × 3», falso para el más reciente. Cap con marcador. Coste S.

### C-09 · BAJO · `TransientHandlerError` deja la entrada en el PEL «para un reclaim» que sólo ocurre al arrancar

`consumer.py:150-160`; `reclaim_stale_pending` sólo en el lifespan (`app.py:70`). Coste S.

### C-10 · BAJO · Queries sin `tenant_id` en servicios BYPASSRLS

`dispatch.py:2814-2818`, `:2197`, `:2590`, `:1556-1558`; `reconciler.py` `db.get(Agent, …)`;
`plan_retro.py` SQL crudo. Van por PK y no cruzan tenants en la práctica. Coste S.

### C-11 · BAJO · `retry_count` compartido entre reject, fallo de infra del reviewer, reject de aprobación y `reassign_with_guidance`

Dos blips de proveedor + un reject legítimo → `blocked`. Mitigado por `retry`, que resetea.
Coste S.

---

## §6 Eje D — Bucle del agente en el runtime (11 hallazgos)

### D-01 · ALTO · El spec completo y el token interno son legibles desde `shell_exec` y `python_function`

- Estado: `risk` · Conocido: nuevo (prod-07 sacó sólo la credencial del modelo, y por
  `docker inspect`, no por el propio agente). **Verificado y reproducido por el analista.**
- Evidencia: `shell_exec.py:95` `subprocess.run(argv, …)` sin `env=`; `__main__.py:104` lee
  `AGENT_TASK_SPEC` de `os.environ` y nunca lo retira; `execution.py:207-215` pone spec y
  `AGENTIC_INTERNAL_TOKEN` en el env del contenedor. `ShellExecTool({"python"})` con
  `python -c "import os;print(os.environ['AGENTIC_INTERNAL_TOKEN'])"` imprime el token.
  `python_function_tool.py:22,86-89` promete «empty env»: cierto para su env, pero mismo uid →
  `/proc/1/environ` legible.
- Qué pasa: el spec lleva `mcp_servers[].headers/env`, `approval_policy`, `approved_actions` y
  el `code` de las python_function; el token autoriza `/internal/agent/mcp-oauth-token`
  (access tokens OAuth de cualquier servidor del proyecto), `memory-store`, `promote-to-kb`.
  Cualquier proyecto Python tiene `python` en su allowlist; una inyección en un fichero del
  repo basta para exfiltrarlos por `http_post` a un dominio permitido.
- Arreglo: `env` mínimo explícito en `shell_exec` y el runner python (S); spec+token por
  fichero en `/run/secrets` y retirados de `os.environ` tras leerlos (M). Plan: `task_cv_20`.

### D-02 · ALTO · Sin tope por observación: una lectura grande rebosa el prompt y el `steps_log`

- Estado: `risk` · Conocido: nuevo (P1-5 cubrió la evicción entre items; prod-12 acotó MCP a 64 KB
  y shell a 20k). **Verificado.**
- Evidencia: `file_tools.py:23` `_MAX_READ_BYTES = 1_000_000` y `read_file` devuelve el contenido
  entero; `http_tool.py:338` `max_body_bytes = 1_000_000`; `python_function_tool.py:137` sin tope;
  `graph.py:1339` `context = {"role": "observation", **observation}` y `providers.py:455-460` lo
  pinta dos veces («Context so far» y «Last observation»); `steps.py:141-143` guarda `result`
  íntegro; ningún cap de `steps_log` en el worker; el guardrail `post_tool` sólo escanea los
  primeros 50k. Medido por el analista: una observación de 900 KB produce un user message de
  1.800.281 caracteres.
- Qué pasa: un `read_file` de un dump, un `http_get` de 1 MB o una `python_function` verbosa
  queman medio presupuesto en un turno, el run muere por `provider_error` o `max_tokens`, y la
  fila `executions` engorda MB por paso. A diferencia de `list_files`, aquí no hay truncado
  anunciado: es todo o nada.
- Arreglo: `_MAX_OBSERVATION_CHARS` (~24k) en `act()` con marcador explícito, `offset`/`limit`
  en `read_file`, `result` recortado + `bytes_total` en el step. Coste M. Plan: `task_cv_21`.

### D-03 · ALTO · El hook `pre_llm` no ve las observaciones reales

- Estado: `broken` · Conocido: B-05 del 07-25, cerrado por `task_wf_50` **a medias**: el test usa
  una forma que el bucle nunca produce. **Verificado.**
- Evidencia: el hook extrae `entry.get("content")` de cada item del contexto (`graph.py:1022-1026`);
  las observaciones se pliegan como `{"role":"observation","tool":…,"ok":…,"output":{…}}`
  (`:1339`), sin `content`. `tests/test_llm_guardrail_hooks.py:105` usa `{"role":"tool","content":…}`.
  Con la forma real: 0 eventos; con la del test: 1.
- Qué pasa: sólo se escanean el preámbulo, las memorias y los pasajes de KB. Todo lo que las
  tools, MCP y ficheros aportaron en turnos anteriores viaja sin mirar; con política `block` el
  tenant cree tener una capa que no corre.
- Arreglo: escanear `_decide_messages(state)[1].content` (cabeza y cola ante el cap). Coste S.
  Plan: `task_cv_22`.

### D-04 · ALTO · Tools MCP no catalogadas escapan al gate de aprobación aunque el preset sea «Cliente Externo»

- Estado: `gap` · Conocido: residual de g6 / prod-03 A8 (cerrados para builtins y MCP
  catalogadas); el ADR 0153 cerró la categoría no listada, no la tool sin categoría. **Verificado.**
- Evidencia: `approval.py:319-325` `category = self._tool_categories.get(canonical)` → `None`
  → `return None` («a tool absent from this map is not sensitive and is never gated», `:27`);
  `mcp_tools.py:494-498` registra **todas** las tools que lista el servidor; `allowed_tools`
  sólo existe si el agente tiene `agent_tools` (`dispatch.py:1659-1660`). Reproducción del
  analista: política customer-external, `gate.review("jira.create_issue", {...})` → `None`.
- Qué pasa: el operador importa 2 tools de un servidor que expone 30; el agente puede llamar a
  las 30 y sólo las 2 se aparcan. Un `fs.write_file` de MCP tampoco cae en `code_changes`.
- Arreglo: fallback `external_http_post` para nombres con namespace no mapeados y/o registrar
  sólo lo catalogado. Coste S. Plan: `task_cv_23`.

### D-05 · MEDIO · La guarda de wall-clock sólo mira al inicio de `plan`

`safeguards.py:535` desde `plan`; `_DEFAULT_CALL_TIMEOUT_S = 900` × 3 intentos y `stack_exec`
hasta 3.600 s: un turno que arranca en el segundo 590 puede tardar 45 min más y el contenedor
muere por hard-kill sin `finish_status` (el mislabel que F19 quiso evitar). Pasar el restante
como `timeout`. Plan: `task_cv_40`.

### D-06 · MEDIO · `max_cost_usd` es letra muerta para azure_foundry / copilot / ollama

`_openai_compat.py:220` `cost_usd = usage.get("cost", 0.0)` (ningún endpoint lo manda);
`safeguards.py:531` compara contra 0; el sobre de presupuesto lo expone como techo real. Estimar
con precios del catálogo. Plan: `task_cv_40`.

### D-07 · MEDIO · Guardrails: fail-open en la costura rodea el fail-closed del ADR 0102 D5

`guardrails.py:605-612` «pipeline unavailable; run proceeds UNSCREENED»; el fail-closed vive
sólo dentro de `pipeline.run`; el escaneo trunca a 50k sin mirar la cola. Abortar si el spec
trae `block` y el pipeline no arranca. Plan: `task_cv_40`.

### D-08 · MEDIO · El modelo no ve qué acción produjo cada observación, y lo evictado se condensa a nada

`graph.py:1280-1286` la observación no lleva `args`; la línea condensada de un `shell_exec`
evictado es `- [observation] shell_exec True`. Incluir `args` capados. Plan: `task_cv_45`.

### D-09 · BAJO · `ask_human` sin techo de preguntas por task

Cada respuesta re-despacha con presupuesto fresco y sólo se replican las 3 últimas respuestas
(`dispatch.py:2644`): ciclo sin límite de coste ni de fatiga humana. Plan: `task_cv_45`.

### D-10 · BAJO · Lote read-only: el elemento que exige aprobación se expulsa en silencio

`graph.py:846-857` filtra sin observación y llama `review` sin `args` (no se puede canjear una
acción ya aprobada dentro de un lote). Plan: `task_cv_45`.

### D-11 · BAJO · Un FINISH truncado repetido acaba como `repetitive_loop_detected` sobre `noop`

El operador lee «bucle sobre noop» en vez de «el proveedor corta la salida». Que el `noop` lleve
`ok=False` con el motivo. Coste S.

---

## §7 Eje E — Memorias (12 hallazgos)

### E-01 · ALTO · La escalera de lectura contradice el ADR 0071 y la guía

- Estado: `broken` · Conocido: nuevo (la guía `memoria-de-agentes.md:57-61` promete lo que el
  código no hace). **Verificado.**
- Evidencia: `internal_agent.py:582-585` `ladder = ["private","team_shared","project_shared","global"]`
  … `return ladder[ladder.index(agent_scope):]`, sobre `agent.memory_scope` crudo; el orden
  oficial es `private < project_shared < team_shared < global` (`memorizer/policy.py:206-211`,
  ADR 0071 §2); `:410-411` recorta en silencio los scopes pedidos a la escalera.
- Qué pasa: un agente `project_shared` (los seeds) nunca recibe `team_shared`, donde el memorizer
  enruta la pericia semántica del equipo; un agente `global` lee sólo `global` y no ve ni sus
  episódicos ni las retros de plan. Los punteros ya acotan la visibilidad; el techo por scope
  sólo resta.
- Arreglo: scopes legibles = todo scope compartido con puntero. Coste S. Plan: `task_cv_30`.

### E-02 · ALTO · `memory_store` interno ignora la política de equipo y el enrutado semantic/episodic

- Estado: `gap` · Conocido: nuevo (M2 de 2026-06 cerró la asimetría de `project_id`). **Verificado.**
- Evidencia: `internal_agent.py:503-516` `scope = payload.scope or agent.memory_scope … if scope
!= agent.memory_scope: raise 403`; persiste sin `route_scope_for_type` (grep vacío en el
  router); el memorizer sí usa `resolve_effective_memory_scope` + `route_scope_for_type`
  (`memorizer.py:380-384`, `:625`).
- Qué pasa: dos caminos de escritura con dos políticas sobre la misma tabla; un agente `global`
  guarda un episódico en `global` y todo el tenant lo recibe en auto-recall.
- Arreglo: unificar con las funciones del memorizer. Coste S. Plan: `task_cv_31`.

### E-03 · ALTO · Las memorias recuperadas llegan al modelo sin valla y el guardrail del recall es sólo LOG

- Estado: `risk` · Conocido: B-05 del 07-25, parcialmente cerrado (ahora se escanea, ADR 0102 g1;
  no se valla ni se bloquea). **Verificado.**
- Evidencia: `providers.py:455-457` pinta cada item del contexto como línea JSON bajo «Context
  so far»; `graph.py:531-548` «LOG mode: records, never blocks»; `_fence_untrusted` no aparece
  en `providers.py` ni `graph.py` (sólo en los preámbulos de `__main__.py`).
- Qué pasa: una memoria es texto que un LLM destiló de salidas de tools o que otro agente guardó
  en `team_shared`/`global`: el canal de persistencia perfecto para una inyección, reinyectado en
  cada run afín del tenant.
- Arreglo: bloque propio vallado y descarte de hits `block`. Coste S. Plan: `task_cv_27`.

### E-04 · MEDIO · El destilador lee claves que los steps reales no tienen

`distillation.py:105-114` lee `note/output/content`; los steps llevan `summary` y `result`
(`steps.py:35-45`, `:130-143`); el fixture del test usa `note`. En producción el LLM destila de
`status + título + output[:1500]` y seis líneas `- [tool_call]` vacías. Plan: `task_cv_32`.

### E-05 · MEDIO · El auto-recall vive tres o cuatro turnos

`_CONTEXT_WINDOW = 8` (`providers.py:348`); las memorias entran como items del contexto y cada
observación las empuja fuera; a los 15 turnos desaparecen. Bloque sticky acotado. Plan:
`task_cv_32`.

### E-06 · MEDIO · `plan_retro` inserta por SQL crudo saltándose la persistencia común

`plan_retro.py:213-227` sin `agent_id`, `metadata`, dedup ni `tenant_id` en las consultas; LLM
del env (que llm-10 documenta como caído) → siempre texto estructurado; marker sólo en Redis
(TTL 30 d) → duplicados si se pierde. Plan: `task_cv_45`.

### E-07 · MEDIO · El memorizer humano no heredó las correcciones F2.1/F2.3/llm-10

`memorizer.py:772-784`: LLM del env, sin causa, sin skip-reason, sin racha; `distillation.py:305-309`
`except Exception: return []`. Las decisiones humanas del Plan 16 mueren en `ok:no_candidates`.
Plan: `task_cv_45`.

### E-08 · MEDIO · Memorias sin olvido, sin tope ni consolidación; dedup O(N) por candidato

`db/memory.py:183-185` excluye `memory_entries` de la purga; el olvido de `cortex/forgetting.py`
sólo cubre `private` + `cortex`; `persistence.py:238-253` una `SELECT` por candidato sobre una
expresión sin índice. Ya en ADR 0077 (abierto). Coste M.

### E-09 · MEDIO · El ranking del recall ignora recencia, tipo y calidad de origen

`recall.py:360-397` fusiona BM25 + vector + entidad; `MemoryRecallHitOut` no expone `created_at`
ni `distill_model`. Una regla destilada por `llama3.2:1b` hace tres meses y una de ayer con
Claude empatan. Coste S.

### E-10 · BAJO · El embedding de la query falla en silencio

`internal_agent.py:560-566` `except EmbeddingError: return None` sin log ni métrica; el recall
degrada a BM25 y nadie lo ve. Plan: `task_cv_45`.

### E-11 · BAJO · Nada filtra secretos ni rutas de host en lo que se memoriza

El destilador corre en el worker, fuera de los cuatro puntos del ciclo de guardrails
(principio 10); `memory_store` persiste `payload.content` verbatim. Plan: `task_cv_45`.

### E-12 · BAJO · `CLAUDE.md` y la guía prometen cosas que el código ya no cumple

«un agente IA ni escribe ni lee `private`» frente a `cortex_maintenance.py:426-433` (el córtex
escribe `private`, ADR 0054/0074 aceptados sin actualizar `CLAUDE.md`); la guía describe la
lectura por punteros que el router no hace (E-01). Coste S.

---

## §8 Eje F — Agentes, equipos, seeds y prompts (11 hallazgos)

### F-01 · CRÍTICO · Los agentes core pinean `provider="anthropic"`, fuera del catálogo cerrado; las copias lo heredan

- Estado: `broken` · Conocido: `auditoria-dirigida-2026-07-16.md:185` («ningún run los usa
  directamente») — **abierto, y la premisa es falsa**: las copias copian `model_config` verbatim.
  **Verificado.**
- Evidencia: 11 `model_provider="anthropic"` en `seeds/builtin_agents.py` (más
  `qa_e2e_automator.py`); `LLMProviderKind = {claude_sdk, copilot, azure_foundry, ollama}`
  (`db/llm_providers.py:61-73`); ningún mapeo `anthropic → claude_sdk` en `model_resolver.py`,
  `dispatch.py` ni `platform_settings.py`; `resolve_model_config` devuelve verbatim si el agente
  pinea (`platform_settings.py:993`); `teams.py:332-333` copia `system_prompt` y `model_config`;
  `startup.py:162-164` re-afirma el seed en cada arranque; el equipo CI4 no pinea (ADR 0055).
- Qué pasa: un tenant adopta `full-stack-web` (o cualquiera de los cinco equipos core) → siete
  copias con `provider="anthropic"` → el worker no encuentra fila `llm_providers` de ese kind →
  `model_unresolved` antes de arrancar. Explica por qué el E2E vivo sólo ha probado CI4.
- Arreglo: quitar el pin (heredar por ADR 0055), test contra `LLM_PROVIDER_KINDS`, migración de
  datos con el patrón 0145. Coste S+M. Plan: `task_cv_03`.

### F-02 · ALTO · La guía de las dos puertas promete `grep/ls/cat` por `shell_exec`, pero sólo Claude SDK recibe esos comandos

- Estado: `broken` · Conocido: la divergencia por proveedor estaba anotada como deliberada en
  2026-07-16; la contradicción con el prompt nace con la guía del 2026-08-30. **Verificado.**
- Evidencia: `tool_usage_guidance.py:54-57` «`shell_exec` es sólo para utilidades de lectura del
  sandbox (grep, ls, cat)»; `run_spec.py:144-146` `if kind == "claude_sdk": allowed_commands =
base ∪ proyecto`; `shell_exec.py:58-62` «command not allowed»; los 8 agentes CI4 llevan
  `shell-exec` y su allowlist es `php/composer/phpunit/spark`.
- Qué pasa: sobre `gpt-oss:120b` (Ollama, el caso vivo) `shell_exec("ls -la")` → «command not
  allowed: ls», justo después de que el prompt le dijera que ésa es la puerta. Es el ADR 0162
  con signo contrario.
- Arreglo: subconjunto de sólo lectura para todos los kinds, o guía consciente del kind. Coste S.
  Plan: `task_cv_34`.

### F-03 · ALTO · Las copias adoptadas llevan el prompt congelado y nada les regenera guía ni capacidades

- Estado: `gap` · Conocido: 0145/0146 lo reconocen («un seed no toca datos de tenant»); H13 lo
  cerró a mano. **Verificado.**
- Evidencia: `builtin_agents.py:96-105` hornea `system_prompt + execution_guidance(tools)` al
  sembrar; `agent_persona.py` lee `system_prompt(s)` sin regenerar la guía; `teams.py:332` copia
  el texto literal; `forks.py:42-54` `_DIFFABLE_FIELDS` no incluye tools/skills; 0145/0146 sólo
  insertan en `agent_tools`; en el mapa core sólo `devops` lleva `shell-exec`, así que la
  condición de 0145 excluye a backend/frontend/qa/architect.
- Qué pasa: una copia de `backend-senior` adoptada en julio no tiene `stack_exec` ni
  `delete/move`, y su prompt dice lo de entonces; un `merge` trae la guía del built-in (para
  otras tools) y el pin de F-01. Cada equipo adoptado nace mejor o peor según la fecha.
- Arreglo: generar la guía en el dispatch a partir de las tools efectivas; `merge` con
  `capabilities`. Coste M. Plan: `task_cv_33`.

### F-04 · MEDIO · El prompt EN no llega nunca al modelo y `docs_language` no viaja

`agent_persona.py:34` `for lang in ("es","en")` determinista; `docs_language` no aparece en
orchestrator ni workers; el Technical Writer recibe la orden de «respetar `docs_language`» y no
el valor. Plan: `task_cv_35`.

### F-05 · MEDIO · Textos vivos que se contradicen en el mismo prompt

`_CI4_STACK_HYGIENE` («donde ya está el repo») junto a «en tu workspace NO hay repositorio»;
`SHELL_ONLY_*` («git log/diff») frente a ADR 0163; la descripción de `shell-exec` cita `mv`,
retirado el 2026-09-01; `_DECIDE_SYSTEM` «a git worktree». Plan: `task_cv_35`.

### F-06 · MEDIO · `ci4-tech-writer` recibe `shell-exec` (y la guía SHELL_ONLY) que el mapa por rol no da al `technical_writer`

Cuarta instancia de «mismo rol, capacidades distintas según el seed» (H16 cerró write/delete).
Derivar `shell-exec` del rol. Plan: `task_cv_35`.

### F-07 · MEDIO · El refresco de arranque no cubre plantillas de proyecto, corpus de KBs, políticas ni plantillas humanas

`startup.py:162-183` sólo agents/tools/skills/junctions/teams; la skill CI4 reescrita el
2026-09-01 no llega a ninguna instalación sin `python -m api_server.seeds` con Ollama arriba.
Plan: `task_cv_36`.

### F-08 · MEDIO · Allowlists de plantilla que prometen binarios ausentes en su runtime

`devops-bootstrap` autoriza `docker/terraform/ansible` sobre `python-pytest`; `webapp` autoriza
`node/npm/npx` sobre `python-pytest`; y la guía STACK_ONLY dice que «not found» nunca significa
binario ausente. Plan: `task_cv_36`.

### F-09 · BAJO · Prompt editable por `tenant_admin` sin control de contenido; la persona va primera

`schemas/agents.py:159` sin máximo ni scan; `enforce_prompt_edit_gate` mide calidad, no
seguridad. Pasar por `prompt_injection` de `shared-guardrails`. Coste S.

### F-10 · BAJO · Prompts CI4 que prometen Selenium/Chrome que `php-phpunit` no tiene

Acotar a Unit/Integration salvo `browser-runtime` declarado. Coste S.

### F-11 · BAJO · Filas de tools no cableadas siguen sembrándose vivas

`apply-patch`, `search-code`, `summarize-text`, `send-notification`; la protección real está en
`PUT /agents/{id}/tools`. Coste S.

---

## §9 Eje G — Cierre de plan y mantenimiento (12 hallazgos)

### G-01 · ALTO · El cierre de plan cuelga de un único `send_task` best-effort sin reconciliación ni aviso

- Estado: `gap` · Conocido: D-02 del 07-25 (visibilidad en UI) cerrado; el hueco «enqueue falla
  → nada» es nuevo. **Verificado.**
- Evidencia: `routers/review.py:526` ignora el `bool` de `enqueue_open_plan_pr`;
  `celery_client.py:302-304` devuelve `False` en fallo; `plan_docs.py:104-105` afirma «el cierre
  se reintenta por varias vías (reconciler…)» y `open_plan_pr` no aparece en `maintenance/`,
  `beat_schedule.py` ni en ningún router; no existe evento `plan_pr_failed`.
- Qué pasa: Redis reiniciando en el instante del veredicto → plan `completed`, `pr_url` y
  `pr_error` NULL, sin changelog, sin retro-hook de docs, «Todavía sin PR» para siempre. Y cuando
  la task corre y falla (PAT caducado), nadie recibe notificación.
- Arreglo: pasada del reconciler + evento. Coste S. Plan: `task_cv_14`.

### G-02 · ALTO · Un segundo cierre pisa `pr_url` con el 422 «A pull request already exists»

- Estado: `broken` (latente) · Conocido: nuevo. **Verificado.**
- Evidencia: `_persist_pr_result` tiene `keep_existing_url` (`plan_pr.py:87`) pero el camino
  principal (`:397-399`) no lo pasa (sólo el camino skip, `:262`); `plan_git.py:1244` llama al
  opener sin comprobar PR existente; `pr_openers.py:60` lanza en `>= 300`.
- Qué pasa: cualquier reintento (el de G-01, el operador, el ciclo `rejected → completed`) →
  422 → `pr_url=None`. El enlace desaparece de la ficha y la rama deja de ser podable.
- Arreglo: `keep_existing_url` en el camino principal; ante 422/409 recuperar la URL por `head`.
  Coste S/M. Plan: `task_cv_14`.

### G-03 · MEDIO · `direct_to_default_allowed` y `plan_validation_mode` son políticas fantasma

`apply_push_policy` no tiene llamantes; `plan_validation_mode` sólo se escribe; el ADR 0072
promete un merge que nadie ejecuta, y el `update-ref` sin guard FF retrocedería `main`. Retirar
o cablear. Plan: `task_cv_45`.

### G-04 · MEDIO · El worktree `plan-docs-{id8}` se crea en cada cierre y nunca se retira

`plan_docs.py:141` `wt.add(...)` sin `remove`; la policy de poda no lo conoce → 30 días por plan
cerrado. Plan: `task_cv_42`.

### G-05 · MEDIO · Ninguna tarea beat tiene lock de instancia única y `acks_late` las reentrega

Sin `expires` ni `SET NX`; un sweep de remotos de 40 min solapa con el siguiente; un backup
reentregado hace un segundo quiesce. Plan: `task_cv_42`.

### G-06 · MEDIO · La poda suelta el lock del ADR 0163 y confía sólo en el mtime del directorio

El `unlock` añadido el 2026-09-01 en `_remove_worktree` cierra el fantasma bloqueado, pero deja
la poda apoyada en una dependencia que ningún test fija: hoy no poda un run vivo porque el
ocultado del `.git` refresca el mtime. Añadir `executions.status='running' → keep` y no soltar
un lock cuyo `execution_id` esté vivo; actualizar el ADR 0163 §5. Plan: `task_cv_42`.

### G-07 · MEDIO · `_run_git` fija `timeout=120` también para push/fetch de remoto

Un repo con historia pesada nunca cierra el plan y `pr_error` no dice que sea de tamaño.
Parametrizar. Plan: `task_cv_43`.

### G-08 · BAJO · El quiesce nocturno mata el worker pero no el agent-runtime

El contenedor sigue facturando hasta su hard-limit (6 h) y la fila 7 h; «el reaper las recupera»
es cierto para la fila, no para el gasto. Plan: `task_cv_43`.

### G-09 · BAJO · `restore_reconcile` marca CRITICAL planes `approved` que legítimamente no tienen rama

La rama nace en el primer `worktree add`; el restore-drill mensual acaba con rc≠0 sin incidencia
real. Plan: `task_cv_45`.

### G-10 · BAJO · Nada vigila la caducidad de credenciales git

Se descubre en el `pr_error` de un plan ya `completed`. Evento throttled y sonda opcional.
Plan: `task_cv_45`.

### G-11 · BAJO · El watchdog resuelve cada contenedor una sola vez

Tras un recreate de postgres/redis queda ciego (`NotFound` en cada tick). Plan: `task_cv_45`.

### G-12 · BAJO · Retro de plan: ventana de 48 h y marker sólo en Redis

Beat parado más de 48 h → sin retro para siempre; Redis restaurado → retros duplicadas.
Idempotencia por tag. Plan: `task_cv_45`.

---

## §10 Lo que está bien (y conviene no romper)

- **Frontera de tenant en la memoria**: RLS `ENABLE` + `FORCE` en `memory_entries`, GUC fijada en
  la sesión interna, `tenant_id` explícito en las tres consultas del recall; `private` inalcanzable
  para una IA por construcción (`internal_agent.py:423`, `recall.py:131-139`).
- **Despacho doble cerrado en tres capas**: claim atómico `WHERE status='ready' RETURNING`,
  advisory lock por plan en la promoción DAG, run-lock por tarea con token y TTL alineado con el
  visibility timeout; finalize + transición en una sola transacción e idempotentes.
- **Cancelación cooperativa** con lectura final del flag y preservación del resultado real si el
  contenedor terminó antes del kill; el evento de fin se publica tras soltar el run-lock.
- **Envoltura del sandbox**: cap-drop ALL, root RO, uid 1000 uniforme en las 16 imágenes,
  seccomp/AppArmor compartidos entre agente y test-runtime, tripwire del socket antes de cada
  lanzamiento, credencial del modelo por fichero RO en `/run/secrets` con limpieza, egress por
  proxy allowlist desconectado antes de los checks, 14 plantillas por digest con Trivy bloqueante.
- **SSRF y aprobaciones**: resolución única con pin de IP y `follow_redirects=False`; canje de
  acciones aprobadas por huella exacta (ADR 0135); categoría no listada falla cerrado (ADR 0153).
- **Contrato de FINISH y detector de bucle**: `submit_result` truncado no cierra con output vacío;
  el detector «falla-idéntico» corta el caso medido; `list_files` anuncia su truncado.
- **Cierre de plan**: docs antes del PR, `forbidden` como gate más fuerte, push forzado para que el
  PR nunca apunte a rama incompleta, guard de ancestro con motivo accionable, docs idempotentes
  que respetan la edición humana, askpass efímero sin token en argv ni logs.
- **Seeds**: un solo criterio para ejecutar/escribir por rol, refresco de arranque en una
  transacción por paso con orden fijado por FK, migraciones 0145/0146 con respaldo y downgrade.

## §11 Lo que no se pudo comprobar (y qué haría falta)

Sin stack ni BD viva en la máquina de la auditoría. Las consultas que darían el impacto real:

- **A-02**: `SELECT count(*) FROM task_audit_events WHERE kind='test_run_completed' AND payload::text LIKE '%test_phase_dispatch_failed%' AND created_at > now()-interval '30 days'`.
- **A-01**: `SELECT t.id FROM tasks t JOIN executions e ON e.task_id=t.id WHERE t.status='in_progress' AND e.status='awaiting_human_approval' AND NOT EXISTS (SELECT 1 FROM approval_requests a WHERE a.execution_id=e.id)`.
- **F-01 / F-03**: copias con `model_config->>'provider'='anthropic'`, copias core sin `stack_exec`,
  copias sin la guía (`position('DOS PUERTAS' in system_prompt)=0`).
- **D-02**: `SELECT id, pg_column_size(steps_log) FROM executions ORDER BY 2 DESC LIMIT 10`.
- **A-05**: profundidad real de la cola `default` (`agentic_celery_queue_depth` de 30 días).
- **B-02 / B-03 / B-12**: un host Linux con Docker y AppArmor cargado; los comandos exactos están
  en cada hallazgo.
- **B-01**: si alguna instalación real corre el compose generado con `EXEC=0`.
- **E-08**: `SELECT scope, count(*) FROM memory_entries WHERE deleted_at IS NULL GROUP BY 1`.

## §12 Relación con las auditorías anteriores

- Del 2026-07-25 quedan **cerrados de verdad**: A-09 (ficha), B-06 (`update_plan` por hooks),
  C-01/C-02/C-03 (HOME, envelope, perfiles pinados), C-09 (cierre de plan unificado), D-02
  (PR visible), V-1 (claim huérfano).
- Quedan **cerrados a medias**, y esta auditoría lo mide: B-05 (los hooks existen y no ven las
  observaciones, D-03; las memorias se escanean y no se vallan, E-03), C-04 (la lane de tests
  se separó con un `get()` que Celery prohíbe, A-02), C-05 (el run-lock sobrevive al hard kill
  pero descarta la re-entrega, A-04).
- De los hallazgos E2E del 2026-08-29: H13/H15/H16 cerrados; su causa de fondo (copias
  congeladas, F-03) sigue abierta; el H1 tiene un hermano de contenido (F-08).
- Del 2026-07-16: el pin `anthropic` (F-01) se anotó como inocuo y no lo es.
- De hoy mismo: el `unlock` del reaper (G-06) resuelve el fantasma y deja una dependencia sin
  fijar; va como seguimiento propio.
