---
title: "Auditoría adversarial de los planes prod-XX implementados — errores, gaps e implementaciones incorrectas"
type: audit
status: informe
date: 2026-07-06
docs_language: es
related:
  [
    "prod-01-despliegue-ejecutable",
    "prod-02-ci-en-verde",
    "prod-03-guardrails-validacion-humana",
    "prod-04-backup-dr-restaurable",
    "prod-06-ciclo-vida-ejecucion",
    "prod-12-hardening-tools-agentes",
    "prod-17-bucle-ai-reviewer",
    "prod-18-worktree-en-ejecucion",
  ]
---

# Auditoría adversarial — prod-XX implementados (2026-07-06)

## Estado de remediación (2026-07-07)

Todos los hallazgos **CRÍTICOS y ALTOS** resueltos con TDD (test rojo → fix → verde),
un commit por hallazgo en la rama `plan/runs-visor-trabajo`, más el MEDIO
`SoftTimeLimitExceeded` (MUST-ADDRESS (a) de prod-06). 192 pruebas verdes + mypy/ruff/black
del proyecto en verde. **Sin desplegar** (rebuild de api-server + workers pendiente de la
ventana que decida el operador — rebootar los workers con runs en vuelo dispara la re-entrega
de ~7 h por diseño).

| #   | Hallazgo                                                                | Estado                                                                                                                                                                            | Commit    |
| --- | ----------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------- |
| C1  | Imagen api-server sin `alembic.ini`/`migrations/`                       | ✅ resuelto (verificado: `alembic history` corre desde la imagen construida)                                                                                                      | `f5ae375` |
| A1  | El plan no converge ante fallo con dependientes atascados               | ✅ resuelto (fixpoint de completabilidad + safety-net en el reconciler)                                                                                                           | `18eea7d` |
| A2  | El techo del envelope estrangula los budgets por-kind de claude_sdk     | ✅ resuelto (techo subido a 500k/50/7200s)                                                                                                                                        | `80f5f18` |
| A3  | Doble contenedor por hard-timeout de Celery « budget del contenedor     | ✅ resuelto (soft/hard 7500/7800s > 7200s del contenedor)                                                                                                                         | `80f5f18` |
| A5  | Doble self-review sobre los runs del propio reviewer                    | ✅ resuelto (early-return `is_review` en el nodo `self_review`)                                                                                                                   | `42ac465` |
| A6  | Sin lock de worktree ante re-entrega concurrente                        | ✅ resuelto (lock Redis `SET NX EX` por-tarea, release CAS-guarded)                                                                                                               | `363245f` |
| A7  | El bind tar del backup se auto-incluye + verificación sin `tar -tf`     | ✅ resuelto (`--exclude` del backup_root anidado + `tar -tf` estructural al bind_tar)                                                                                             | `5a63357` |
| A8  | Fugas del gate: `delete_file`/`run_*`/`send_notification` sin categoría | ⚠️ parcial (mapa tool→categoría cerrado + test inverso; el default de `human_approval_policy` nullable queda como **decisión de producto**: fail-closed vs plantilla por defecto) | `2106ed3` |
| A9  | `cortex-beat` ausente del compose generado + lane privileged sin root   | ✅ resuelto (builder `cortex-beat` + `WORKERS_RUN_AS_ROOT`/`WORKERS_BACKUP_VOLUMES` en la lane privileged)                                                                        | `f5ae375` |
| A10 | Streaming roto en el compose generado (`EVENTS_REDIS_URL=/3`)           | ✅ resuelto (`/3`→`/0`, alineado con `manuals.yml`)                                                                                                                               | `f5ae375` |
| —   | prod-06 MUST-ADDRESS (a): handler de `SoftTimeLimitExceeded`            | ✅ resuelto (finaliza la fila `running`, clasifica por cancel flag, mata el contenedor huérfano)                                                                                  | `99eb017` |

### Segunda ola (2026-07-07): medios/bajos investigados e implementados

Tras re-verificar cada hallazgo diferido contra el código real (workflow de
investigación + verificación adversarial; el límite de sesión cortó 3 investigaciones
y las verificaciones, suplidas con TDD rojo→verde). Implementados con TDD, commit por
hallazgo:

| #           | Hallazgo                                                                                          | Estado                                                                                  | Commit    |
| ----------- | ------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- | --------- |
| M8b         | Guarda de slug vacío de `stack_exec` sin test de regresión                                        | ✅ test de mutación (quitar la guarda → cae)                                            | `cd329c7` |
| CANCELAWAIT | Cancel no finalizaba ejecuciones `awaiting_human_approval` (colgadas + request resucitable)       | ✅ sella en línea + cierra el ApprovalRequest (nuevo estado `cancelled`)                | `de48bc4` |
| M2          | 3 rutas de cierre sellaban `.status` a mano saltándose la guarda idempotente                      | ✅ primitivo único `seal_terminal_execution`                                            | `7939935` |
| M9          | Gate de cobertura decorativo (`--cov-fail-under=19` vs 30.4% real)                                | ✅ floor 19→30 + meta-test que asserta `>=30`                                           | `fb1ca91` |
| M5          | Reconciler de reviews sin cap propio (bucle si broker caído / worker SIGKILL)                     | ✅ cap por edad de `updated_at` → escala a `blocked`                                    | `1d9b830` |
| M1          | El sweeper mataba runs en provisión lenta (>5 min) y descartaba su resultado                      | ✅ columna `container_launched_at` (migr. 0104); solo huérfano si el contenedor existió | `c51dc57` |
| OFFSITE     | Offsite de backup implementado pero código muerto (no cableado al beat)                           | ✅ `_upload_bundle_to_destinations` best-effort, solo bundle verificado                 | `847a4bd` |
| HARDDEP     | `DELETE` de tarea hard-borraba un prerequisito → dependientes vacuamente elegibles (DAG corrupto) | ✅ 409 si otras dependen de ella (fail-safe)                                            | `ada289d` |
| A8b         | Proyecto sin `human_approval_policy` corría todo en auto (fail-open)                              | ✅ hereda preset `development` (decisión del operador, ADR 0104)                        | `3bd1b3a` |

**Diferido (con spec):**

- **M4** (no-atomicidad DB↔git: un crash entre finalize y push deja el diff fuera del
  bare → PR final incompleto): implementable pero es el ÚNICO cambio adyacente a
  corrupción de worktree (nueva pasada del reconciler con commit/push al bare + reuso
  del lock A6) — merece una sesión dedicada con test git-backed, no el final de una
  sesión con límite de cuota. Spec completa capturada en la investigación.

**No abordados (deliberado):**

- **M8** (exfil vía POST a host permitido): sin fix sin proxy MITM (terminación TLS),
  explícitamente fuera de la postura de ADR 0094; riesgo residual acotado y aceptado.
- **A4** (e2e del ciclo autónomo): el comportamiento ya lo cubren los tests de A1/M5
  (unit + integración); el e2e completo queda como deuda de cobertura.
- **LOWBUNDLE / M3**: docstrings obsoletos, healthcheck admin-panel 404, seeds
  `{"all":"auto"}`, drift ADR 0095 D3 — cosméticos/aceptados; M3 (test_worker_lost
  débil) ya cubierto por `test_run_lock.py`.

**No abordados de la 1ª ola:**

- **C2** (cierre 20/20 marcado sin arrancar el stack): es una lección de _proceso_, no código
  vivo — los bugs boot-blocking que destapó ya se corrigieron en su día (`3c7d8b6`, `326b884`).
  Nada que codificar.
- **A4** (e2e del ciclo autónomo multi-tarea): el _comportamiento_ que habría destapado (A1) ya
  está cubierto por `test_plan_progress_blocked` (unit) + el safety-net del reconciler; el e2e
  completo con fixtures multi-tarea queda pendiente como deuda de cobertura.
- **A8 (2ª parte)**: proyecto sin `human_approval_policy` corre todo en auto → decisión de
  producto (¿fail-closed global, o plantilla Sandbox por defecto?), no un fix mecánico.
- **MEDIOS/BAJOS restantes** (falso positivo del sweeper en provisión lenta, no-atomicidad
  DB↔git, exfil vía POST a host permitido, gate de cobertura decorativo, guardrail post_tool en
  modo LOG, docstrings obsoletos, offsite de backup sin cablear al beat): reales pero de ventana
  estrecha o ya aceptados-documentados; no bloquean fiabilidad ni coste en el camino común.

> Petición del operador: "de los planes prod-xx que están implementados, analiza si hay algún
> error, gap o implementación incorrecta". Cuatro revisores read-only en paralelo (prod-01/02,
> prod-06, prod-17/18, y las partes absorbidas de prod-03/04/12), cada uno leyendo el plan
> completo y el código real con mandato de ENCONTRAR fallos, no de confirmar existencia. El
> hallazgo prod-17 #1 (doble self-review) fue verificado adicionalmente a mano sobre el código.

## Veredicto global

Lo implementado es **funcionalmente completo y mayormente sólido en el camino feliz** (sweeper de
zombis, cancelación cooperativa, bucle del reviewer fail-closed, worktree con trailers, TLS,
gates de CI sin escape hatches). Los fallos graves se concentran en **tres patrones**:

1. **Tests que validan forma, no comportamiento** (argv de tar, YAML del compose, flags de
   Celery): por ahí pasaron el `--create` ausente, los bugs boot-blocking del stack y la promesa
   falsa de "sin doble contenedor".
2. **Tracks que se pisan sin reconciliar**: prod-06 (techo de budgets) × remediación 07c91cc
   (budgets por-kind); prod-17 × ADR 0086-0095 (doble review).
3. **El sistema de recuperación (backup) es la zona más frágil**: roto por 3 vías independientes
   (auto-inclusión del bind tar; lane sin root en el compose generado; beat ausente del compose
   generado).

## Hallazgos CRÍTICOS

| #   | Plan    | Hallazgo                                                                                                                                                                                                                                                                                                                                                                                 | Evidencia                                                                                                                                             |
| --- | ------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| C1  | prod-01 | La imagen api-server NO empaqueta `alembic.ini` ni `migrations/` (wheel solo lleva `src/api_server`); el servicio one-shot `migrations` del compose generado no puede ejecutar `alembic upgrade head` (su docstring afirma lo contrario). Contradice task_prod01_01/12. Confirmado en vivo: la migración 0103 tuvo que aplicarse desde el host.                                          | `apps/api-server/Dockerfile:74-76`; `apps/api-server/pyproject.toml:133`; `apps/installer/backend/src/installer_backend/compose_generator.py:564-584` |
| C2  | prod-01 | El cierre 20/20 se marcó sin arrancar el stack real: los commits `3c7d8b6` y `326b884` (2026-06-18, POST-cierre) arreglaron bugs boot-blocking (cap_drop en crash-loop de postgres/redis/vault; imagen sin `celery`/paquete workers; healthchecks sin wget/curl; `celery -A workers` no resolvía → ambas lanes muertas). Los tests automáticos (snapshots de YAML) no podían detectarlo. | commits `3c7d8b6`, `326b884`                                                                                                                          |

## Hallazgos ALTOS

| #   | Plan                | Hallazgo                                                                                                                                                                                                                                                                                                                                                                                                                                                        | Evidencia / escenario                                                                                      |
| --- | ------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| A1  | prod-06             | **El plan no converge ante fallo con dependientes**: la promoción DAG exige deps `done` y `transition_to_blocked` trata `backlog` como "advanceable" → con A→B y A `failed`(→`blocked`), B queda `backlog` para siempre y el plan `in_progress` sin escalar ni cerrar ni avisar. Viola el criterio de cierre #2 del propio plan.                                                                                                                                | `dag_promotion.py:87`; `plan_progress.py:190-203`                                                          |
| A2  | prod-06             | **El techo del envelope pisa los budgets por-kind de claude_sdk** (conflicto prod-06 × 07c91cc): `EXECUTION_BUDGET_CEILING` clampa a 100k tokens/25 iter/600s; el worker aplica los por-kind (500k/50/7200) solo vía `setdefault`. En cuanto el operador configura `execution_budgets` (la feature de prod-06), los runs de claude_sdk vuelven a cortarse a ~23 iteraciones — el bug que 07c91cc arregló — y el operador no puede subirlos por el clamp.        | `envelope.py:24-30`; `workers/config.py:181-213`; `workers/execution.py:356-365`                           |
| A3  | prod-06             | **Doble contenedor + doble coste LLM rutinario con claude_sdk**: hard time limit default de Celery = 2100s (35 min) « presupuesto del contenedor claude_sdk (7200s). SIGKILL del hijo a los 35 min + `task_reject_on_worker_lost` → reentrega → segundo contenedor mientras el primero sigue vivo y facturando. `supersede` solo deduplica la fila DB, no el contenedor.                                                                                        | `platform_settings.py:838-839`; `workers/config.py:133-134`; `celery_app.py:106`                           |
| A4  | prod-06             | **El e2e del criterio de cierre #2 no existe**: `tests/e2e/test_plan_autonomous_lifecycle.py` (plan multi-tarea termina sin transición manual) no está en el árbol. Habría destapado A1.                                                                                                                                                                                                                                                                        | glob vacío en `tests/e2e/`                                                                                 |
| A5  | prod-17             | **Doble self-review sobre los runs del propio reviewer** (verificado a mano: el grafo encadena `finalize→self_review` sin exención por `is_review`; el skip-set solo cubre estados terminales; el worker usa el mismo grafo). Coste x2 en CADA review; y si esa segunda review (que juzga un veredicto contra los acceptance_criteria de la tarea revisada — rubric que no encaja) sale inconclusa, un **approve correcto acaba en `blocked`** (rama ADR 0096). | `graph.py:1540,1348-1361`; `execution.py:823-848`                                                          |
| A6  | prod-18             | **Sin lock de worktree**: re-entrega `acks_late` con el contenedor DooD aún vivo → un segundo `conduct_execution` de la misma tarea hace `sync_to_head` (`reset --hard` + `clean -fdx`) sobre el worktree donde el contenedor original sigue escribiendo → corrupción/pérdida del trabajo en vuelo. El claim atómico solo cubre `ready→in_progress`.                                                                                                            | `git_repos.py:341-342,418-420`                                                                             |
| A7  | prod-04 (absorbido) | **El bind tar del backup se auto-incluye recursivamente**: `backup_bind_paths=["/data/agent-platform"]` contiene `backup_root=/data/agent-platform/backups` y el argv no lleva `--exclude` → cada backup diario embebe todos los bundles previos + los artefactos del run en curso → crecimiento cuadrático y/o `rc≠0` ("file changed as we read it") → el backup falla. Activo con la config por defecto.                                                      | `backup.py:452-459`; `config.py:390,465,498`                                                               |
| A8  | prod-03 (absorbido) | **Fugas del gate de aprobación**: `send_notification` (¡bajo el preset `customer-external` que promete gatear comunicación!), `delete_file` (destructiva; `write_file` sí se gatea) y `run_pytest/lint/typecheck/build` no tienen categoría → escapan al gate. Además un proyecto creado sin `human_approval_policy` (nullable, sin default) corre TODO en auto: el gate ni se instancia.                                                                       | `approval.py:33-40,69-81` vs `tool_names.py:109-142`; `execution.py:1296` → `__main__.py:542`              |
| A9  | prod-01             | **`cortex-beat` ausente del compose generado** → en una instalación por el instalador NADA se agenda: backups, rotación, sweepers/mantenimiento, promoción DAG safety-net, sync de precios, bucles del córtex. Solo existe en `manuals.yml`. Ídem `workers-backup`/`workers-aux`: la lane privileged generada va sin `WORKERS_RUN_AS_ROOT` → el volume-tar daría EACCES.                                                                                        | `compose_generator.py:1172-1197` vs `docker-compose.manuals.yml:305,372,437`; `docker-entrypoint.sh:24-32` |
| A10 | prod-01             | **Streaming en vivo roto en el compose generado**: `WORKERS_EVENTS_REDIS_URL=/3` (sin consumidor) mientras el WS del api-server lee `exec:{id}` en DB 0. Ya corregido en `manuals.yml:445` con comentario explícito — pero no en el generador.                                                                                                                                                                                                                  | `compose_generator.py:696,596`; `auth/deps.py:76-80`                                                       |

## Hallazgos MEDIOS (selección)

- **prod-06**: MUST-ADDRESS (a) de la cancelación NO implementado — sin handler de
  `SoftTimeLimitExceeded` en `run_execution()`: el soft-timeout deja fila `running` sin finalizar
  - contenedor huérfano + DLQ sin clasificar por flag de cancel (`tasks.py:88`).
- **prod-06**: falso positivo del sweeper si la provisión del worktree tarda >5 min (fila
  `running` se crea antes de provisionar; gracia fija no configurable) → mata un run sano en
  provisión y su resultado real se descarta (`maintenance.py:598-600` vs `execution.py:1285/1377`).
- **prod-06**: asignaciones crudas de `.status` fuera de finalize persisten en sweeper y cascada
  (`maintenance.py:705-708`; `execution_repo.py:331`) — lo que el plan quería erradicar.
- **prod-06**: `test_worker_lost_redelivery.py` solo asserta 2 flags de config; no prueba
  reentrega/supersede/"sin doble contenedor" (la afirmación es falsa en el caso A3).
- **prod-17/18**: no-atomicidad DB↔git (crash entre finalize y `_commit_and_push_worktree` deja
  tarea avanzada con diff nunca en el bare → PR final incompleto); reconciler de reviews sin cap
  propio con broker caído; 3 ficheros de test citados por los planes que NO existen
  (`test_autonomous_review_loop.py`, `test_test_runtime_wiring.py`,
  `test_execution_commits_to_worktree.py` — la sustancia está cubierta bajo otros nombres).
- **prod-04 (absorbido)**: la verificación de backup no hace `tar -tf` a los `bind_tar` (solo
  checksum) → una corrupción estructural coherente con el manifest pasa como válida
  (`backup_verification.py:235-244`). El offsite está implementado (S3/B2/SFTP/rclone) pero **no
  cableado** al beat diario (código muerto).
- **prod-12 (absorbido)**: exfiltración posible vía POST a host permitido (el proxy solo filtra
  host en CONNECT, no verbo/path — p.ej. `api.github.com`); mitigado porque el proxy se
  desengancha antes de la fase de checks. La guarda de slug vacío no tiene test de regresión en
  su ruta real.
- **prod-02**: gate de cobertura decorativo — `--cov-fail-under=19`, solo `tests/unit`, el córtex
  (integración) no cuenta; frente al 70/80% de CLAUDE.md (`ci.yml:214`).
- **prod-03 (absorbido)**: el hook post_tool de guardrails es modo LOG (nunca bloquea), solo
  `prompt_injection`, fail-open ante error — **aceptado-documentado** (slice g1), pero conviene
  ser explícito: hoy el guardrail registra, no protege.

## Bajos / aceptados-documentados (lista corta)

Docstrings obsoletos (`reviewer_bridge.py:20-27` describe fail-open pre-prod-17; migración 0090
promete `revoke(terminate=True)` que el código deliberadamente no hace); promoción DAG vía beat
30s en vez de instantánea (documentado); dependencia hard-borrada tratada como satisfecha;
healthcheck del admin-panel que acepta 404 como sano; `storage.googleapis.com` en la allowlist
(riesgo residual aceptado en `filter.txt`); seeds con `{"all": "auto"}` (clave no canónica,
footgun latente); cancel no finaliza ejecuciones `awaiting_human_approval`; drift ADR 0095 D3
(cap N=2 vs `retry_count/max_retries` compartido con rejections).

## Prioridad de remediación sugerida

1. **A2 + A3** (budgets pisados + doble contenedor): pegan directamente al coste LLM y a la
   fiabilidad de cada run de claude_sdk en cuanto se usa la configuración de budgets.
2. **A5** (doble review): coste x2 por review + riesgo de mandar approves correctos al panel de
   escaladas. Fix pequeño (exención `is_review` en el skip-set del self_review) una vez decidido.
3. **A7 + A9-lane-backup + verificación bind_tar**: el backup hoy no es confiable por tres vías;
   es el seguro de todo lo demás.
4. **A1 + A4** (convergencia del DAG ante fallo + su e2e): rompe el "happy path autónomo" que
   prod-06 vino a garantizar.
5. **A8** (fugas del gate): cerrar el mapa tool→categoría (o invertir a fail-closed para tools
   wired sin categoría) + default de policy en proyectos.
6. **C1 + A9 + A10** (instalador/compose generado): decidir si el compose generado se reconcilia
   con `manuals.yml` o se declara no-soportado hasta prod-15/instalador v2.
7. **A6 + medios de prod-17/18** (lock de worktree, atomicidad): reales pero de ventana
   estrecha; pueden ir tras los anteriores.
