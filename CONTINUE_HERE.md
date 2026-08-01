# CONTINUE HERE — dónde retomar el trabajo

> **Última actualización: 2026-07-30** · rama `plan/runs-visor-trabajo`
>
> Este archivo es un **puntero**, no una copia del estado. La fuente de verdad es
> el frontmatter de `docs/roadmap/*.md`. Si algo de aquí contradice a un
> frontmatter, **gana el frontmatter** y hay que corregir este archivo. Está
> escrito así a propósito: un resumen que duplica datos envejece mintiendo, que
> es el modo de fallo nº1 de
> [verificar-antes-de-implementar.md](docs/03-guides/verificar-antes-de-implementar.md).

## En una frase

El código está al día y desplegado desde el 2026-07-28, pero **la sesión del
2026-07-30 metió trabajo SIN desplegar**: esquema en `0124` (dos migraciones
nuevas), 67 módulos y tests nuevos, y **4 ADR esperando tu decisión**
(`0133`-`0136`). Lo que queda pendiente sigue siendo sobre todo **humano** —
validar las 46 fases entregadas, aprobar los 14 planes nunca empezados y arreglar
la facturación de CI—, más dos cosas nuevas: decidir si se encienden **seis jobs
de fondo que nunca han corrido** (punto 0) y cerrar el bloqueante de despliegue
del ADR `0136` (el instalador no genera `API_SERVER_INTERNAL_TOKEN_SECRET`, y con
el guard fail-closed nuevo el api-server **no arranca en prod** sin él).

## Lo primero que hay que saber

1. **No relanzar ni desbloquear tareas** hasta que el operador dé el sistema por
   verificado. Observación pasiva sí. (Desplegar ya no está vetado: se hizo el
   2026-07-28 por orden expresa suya.)
   - **Y ojo con esto al desplegar**: aquel `up -d` **relanzó dos tareas
     congeladas** —el reconciler las rescató a los 90 s— con ~165 k tokens de
     gasto. La comprobación de «¿queda algo corriendo?» NO las ve, porque su
     rasgo es no tener ejecución. Cuenta reclamaciones huérfanas y despliega con
     `--scale orchestrator=0`:
     [gotchas/deploy-relaunches-frozen-tasks.md](docs/03-guides/gotchas/deploy-relaunches-frozen-tasks.md).
2. **El perfil seccomp endurecido está ACTIVO** desde ese despliegue
   (`WORKERS_SECCOMP_PROFILE_PATH`, antes vacío). Solo afecta a los sandboxes que
   lanza el worker, y **no se ha ejercitado con un run real**: el primer run que
   se lance será el primero bajo el perfil estricto. Válvula de escape:
   `WORKERS_SECCOMP_PROFILE_PATH=` en el `.env`, sin tocar el compose.
3. **CI está caído y no es culpa del código**: facturación de la cuenta `daycry`
   («recent account payments have failed»). Lo arregla el operador en
   <https://github.com/settings/billing>. Mientras tanto, las suites se corren en
   local (ver más abajo).
4. **El PR #66 está MERGEADO** desde el 2026-07-30 07:32 UTC (merge commit
   `72fe899b`): los 543 commits que `plan/runs-visor-trabajo` llevaba de ventaja ya
   están en `master`, y el **criterio 5 de cierre** («PR mergeado») dejó de bloquear
   a los planes anteriores a esa fecha.
   - **Pero el trabajo del 2026-07-30 NO está commiteado**: ~200 ficheros en el árbol
     (dos migraciones, 67 módulos/tests nuevos, 5 ADR) sobre una rama que ya es
     idéntica a `master`. Eso necesita **rama nueva + PR propio**; no lo empujes a
     `plan/runs-visor-trabajo`, que ya cumplió su ciclo.
   - Y no te fíes de esta línea: `git rev-list --left-right --count origin/master...HEAD`
     y `gh pr view <n> --json state,mergedAt` lo dicen en un segundo. Este archivo
     afirmó durante horas que el PR seguía abierto **después** de mergearse.
5. **Queda un `DELETE` sin ejecutar**: 8 filas de `agent_tools` conceden
   `send_notification` a los agentes CI4 DevOps y Project Manager, y esa tool no
   tiene ejecutor (devuelve `ok=False, "not wired"` y les quema un turno). Ningún
   seed las repone. Copia previa: bundle `20260728T114814Z`.

## Estado del roadmap (regenerable, ver §«Comprobar»)

| Estado                     |  N  | Qué significa aquí                                        |
| -------------------------- | :-: | --------------------------------------------------------- |
| `completed`                | 25  | cerradas del todo                                         |
| `pending_human_validation` | 46  | código entregado **y desplegado**; esperan tests humanos  |
| `pending_approval`         | 14  | **nunca empezadas** — necesitan tu aprobación (protocolo) |
| `blocked`                  |  1  | `guardas-research-por-novedad`: solo le falta el e2e      |
| `in_progress`              |  0  | correcto: el protocolo permite una como mucho             |

**ADR en `proposed`: cuatro, y son tuyos** (nacidos el 2026-07-30 de los planes de
seguridad): `0133` almacenamiento de la sesión del panel (cookie vs localStorage),
`0134` auto-registro en producción, `0135` qué autoriza exactamente una aprobación
humana (extensión del 0020) y `0136` dominios criptográficos worker↔api. Cada uno
lleva opciones con su coste y una recomendación argumentada. El `0137` (la tabla
`users` se queda global) era técnico y ya está `accepted` e implementado.

## Las casillas del córtex: lo que decía aquí era falso

> **Corregido el 2026-07-27.** Este archivo afirmaba que las 76 casillas del
> córtex eran «**rancias**: implementado y desplegado, los planes quedaron sin
> marcar». Se verificaron **una a una contra el código**, con una pasada
> adversarial por fase. Resultado: **29 implementadas, 45 parciales, 2 ausentes**.
> La afirmación anterior extrapolaba tres comprobaciones puntuales a 76 tareas.

Estado actual tras marcar lo verificado y cerrar cinco defectos reales:

| Dónde                                    |  N  | Qué son de verdad                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| ---------------------------------------- | :-: | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `cortex-f1`…`f5`                         | 15  | **Eran 42; el 2026-07-30 se cerraron 27** con test ejecutado, y F1 quedó completa (12/12). Las 15 que siguen abiertas están **anotadas una a una en su propio plan** con lo que falta (`⏳ Pendiente (2026-07-30)`): QA visual, e2e de Playwright, la co-construcción de identidad de F3 (decisión de producto) y la migración D3. El inventario [`gaps-cortex-2026-07-27.md`](docs/roadmap/gaps-cortex-2026-07-27.md) ya está PARCIALMENTE RANCIO: varias entradas suyas se cerraron el 28 y el 30 — verifica contra el código, no contra él. |
| `prod-06`                                |  1  | **Rancia.** «Dar caller a `apply_reviewer_verdict`» — ya lo tiene (`workers/execution.py:473`), llegó con el ADR 0087.                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `15-instalador`                          |  2  | Pentest externo y release v1.0.0: **decisión y contratación tuya**, no código.                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `prod-17`, `prod-18`, `guardas-research` |  4  | **e2e bloqueados**: exigen runner Docker y lanzar runs reales.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `06.10`, `06.11`                         |  2  | Diferidas por decisión propia (typeahead cosmético; plan 06.12 aparte). Están en planes ya `completed`.                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `prod-03`…`prod-16`, `remediacion-…`     | 171 | De los **14 planes `pending_approval`**: nunca empezados, esperan tu aprobación.                                                                                                                                                                                                                                                                                                                                                                                                                                                               |

**Lo que hay que llevarse de aquí, más que los números:** un resumen que dice
«esto ya está» sin evidencia por ítem envejece mintiendo, y cuesta más caro que
no tenerlo — porque se lee y se cree. Antes de saltarte una casilla porque este
archivo dice que es rancia, **ábrela**. Y al revés: la pasada adversarial dio al
menos un falso positivo comprobado, así que tampoco te fíes de un `partial` sin
mirar el fichero.

## Qué necesita al operador (por orden de coste)

0. **Seis jobs de fondo que nunca han corrido, y decidir si se encienden.** Sus
   entradas de beat nombran tasks que **ningún worker registra** (el módulo no
   está en `celery_app(imports=...)`), así que beat las encola y el worker las
   rechaza con `NotRegistered`, sin ruido: standup diario (ADR 0120), vigía de
   credenciales (ADR 0122), retro de planes (ADR 0124), asesor de configuración
   (ADR 0125), restore-drill (ADR 0126) y GC de conocimiento (G-03). Están
   declaradas entregadas y desplegadas. **No las he cableado a propósito**:
   arreglarlo enciende los seis de golpe y uno **ensaya una restauración de
   backup**. Lista en vivo y detalle en
   [gotchas/beat-entry-whose-task-nobody-imports.md](docs/03-guides/gotchas/beat-entry-whose-task-nobody-imports.md);
   la guarda que lo impide en el futuro ya está en CI.

1. **Validar las 46 fases en `pending_human_validation`.** El despliegue ya está
   hecho (2026-07-28), así que esto por fin es posible: era el cuello de botella
   y llevaba semanas atascado. Recetas de build, para la próxima, en
   [gotchas/image-build-recipes-that-bite.md](docs/03-guides/gotchas/image-build-recipes-that-bite.md);
   procedimiento en [06-runbooks/03-system-upgrade.md](docs/06-runbooks/03-system-upgrade.md).
2. **Aprobar (o descartar) los 14 planes `pending_approval`** — casi todos
   `prod-XX`. Sin aprobación no puedo arrancarlos.
3. **Dos verificaciones que exigen humano delante**: la prueba en navegador del
   OAuth de MCP (ADR 0127) y el **primer run bajo el perfil seccomp estricto**,
   que ya está activo pero sin ejercitar (ver punto 2 de arriba).
4. **Sembrar un dataset dorado de evals.** El productor, el lector y el
   muestreador están puestos y probados; elegir qué tareas cerradas son «buenas»
   es curaduría humana. Mecanismo: `POST /tasks/{id}/promote-to-dataset` (hay UI).
5. **`registry-egress-followups`** (`open`): **F3 cerrada el 2026-07-28**;
   F1/F4/F5 siguen abiertos. F5 lleva escrito su orden correcto y su trampa (si
   se hace a medias reabre la puerta trasera de B-04).

## Órdenes permanentes del operador

Valen para toda sesión, no solo para la que las recibió:

- **Responder en castellano.**
- **Entregables** (auditorías, planes, diseños) en `docs/roadmap/`, NO en
  `docs/plans`. Los ADR en `docs/05-architecture-decisions/`.
- **Prioridad: código limpio y mantenible.** TDD, módulos enfocados, refactor
  oportunista, sin big-bang. Lo que esté gated va por ADR primero.
- **ADR `proposed` → implementarlos de forma autónoma** eligiendo la mejor opción.
  Excepción: si un ADR implica una decisión de PRODUCTO nueva, parar y preguntar
  (así se resolvió el 0117).
- **Fallo de un run**: si la causa es de plataforma, arreglarlo yo (TDD + deploy +
  relanzar). Escalar solo lo que sea decisión humana.
- **No desbloquear sin verificación** (ver arriba).

## Cómo comprobar que este archivo sigue siendo cierto

Cuatro comandos. Si alguno contradice lo de arriba, **actualiza este archivo**:

```bash
# 1. Recuento por estado del roadmap
for f in docs/roadmap/*.md; do grep -m1 '^status:' "$f"; done | sort | uniq -c | sort -rn

# 2. ¿Alguna fase in_progress? (el protocolo permite UNA como mucho)
grep -l '^status: in_progress' docs/roadmap/*.md

# 3. ¿ADR sin decidir?
grep -l '^status: proposed' docs/05-architecture-decisions/*.md

# 4. Tareas sin marcar en las fases vivas
grep -c '^- \[ \]' docs/roadmap/*.md | grep -v ':0$'
```

Y antes de implementar cualquier tarea de un plan antiguo, lee el §1 de
[verificar-antes-de-implementar.md](docs/03-guides/verificar-antes-de-implementar.md):
de las últimas 21 tareas «pendientes» que revisé, la mayoría estaban hechas y dos
estaban **rechazadas** por un ADR posterior.

## Verificación local (con CI caído)

> **Esta lista tenía tres agujeros y costó un rojo invisible.** Es el espejo de
> los pasos de test de `.github/workflows/ci.yml`, y le faltaban `tests/docs/`,
> `packages/shared-llm/tests` y la suite del browser-runtime. Con CI caído nadie
> los corría, así que `tests/docs/test_runbooks_consistency.py` estuvo **en rojo
> desde el 2026-07-28** sin que se viera (lo rompió, sin querer, el propio commit
> que documentó las trampas de aquel despliegue). Si añades un paso de test a CI,
> **añádelo también aquí**.

Cifras verificadas el **2026-08-01**:

```bash
# Los 6 pasos de pytest que corre CI, en el mismo orden
.venv/Scripts/python.exe -m pytest tests/unit/ -q          # 3200
.venv/Scripts/python.exe -m pytest tests/security/ -q      # 73
.venv/Scripts/python.exe -m pytest tests/docs/ -q          # 264
.venv/Scripts/python.exe -m pytest packages/shared-llm/tests -q   # 191 (+1 skip sin claude_agent_sdk)
cd docker/agent-runtimes/agent-runtime && ../../../.venv/Scripts/python.exe -m pytest tests/ -q   # 495
# El browser-runtime NO se instala en el venv: necesita el PYTHONPATH que le pone CI,
# o muere en la recolección con `ModuleNotFoundError: No module named 'browser_runtime'`
PYTHONPATH=docker/agent-runtimes/browser-runtime \
  .venv/Scripts/python.exe -m pytest docker/agent-runtimes/browser-runtime/tests -q   # 19

# Y lo que no es pytest
.venv/Scripts/python.exe -m mypy apps/ packages/           # 623 ficheros, limpio
cd apps/admin-panel && npx vitest run                      # 808 en 98 ficheros
cd apps/admin-panel && npx tsc --noEmit && node scripts/check-i18n.mjs
```

> Los tres primeros pasos juntos (`tests/unit/ tests/security/ tests/docs/`) son
> **4273 tests en ~4 min**; correrlos en un solo `pytest` es lo más barato que
> hay y es lo que conviene hacer antes de cada commit.

> **Usa el intérprete del venv, no `python` a secas.** Los paquetes de
> `packages/` están en editable sólo en `.venv/`; con el Python global la suite
> muere en la recolección con `ModuleNotFoundError: shared_domain` — sin correr
> ni un test, y en un fichero que no has tocado. Detalle en
> [gotchas/pytest-needs-the-repo-venv.md](docs/03-guides/gotchas/pytest-needs-the-repo-venv.md).

> **La suite del agent-runtime NO está en `testpaths`**: solo la corre CI en un
> paso propio. Con CI caído hay que invocarla a mano desde su directorio.
>
> Integración: necesita el Postgres de compose arriba; se corre por bloques
> (`python -m pytest tests/integration/test_X.py -q -p no:randomly`).
>
> **Y ahí es donde se esconden los rojos.** Son **517 ficheros** que CI no corre
> y nadie corre enteros, así que un test rezagado sobrevive commits. El
> 2026-08-01 aparecieron **nueve** afirmando el contrato viejo del login SSO
> (`200 + JSON` en vez del `303 + Set-Cookie` del ADR 0133): el commit que cambió
> el contrato actualizó el fichero que tenía delante y dejó los otros tres.
> Cuando cambies lo que devuelve una ruta, **busca por la ruta, no por el
> fichero** — `grep -rln "auth/sso/oidc/callback" tests/` — y corre ese lote.
> Detalle: [gotchas/cambio-de-contrato-deja-tests-rezagados.md](docs/03-guides/gotchas/cambio-de-contrato-deja-tests-rezagados.md).
>
> **Y un solo pytest de integración a la vez.** El conftest crea su BD con
> `DROP DATABASE` + `CREATE DATABASE` sobre un nombre único para todo el repo, y
> además hace `flushdb()` de la Redis de test en cada setup de app. Dos procesos
> simultáneos se destruyen la BD (fallos fantasma de «tabla que no existe») y se
> borran las sesiones entre ellos (`401 session has been revoked` en un test que
> no toca auth). Si necesitas paralelismo, dale a cada proceso **las dos**:
> `TEST_PG_DB_NAME=agentic_platform_test_<algo>` y `TEST_REDIS_URL=redis://localhost:6379/<1-14>`.
> Detalle y firma para reconocerlo:
> [gotchas/integration-tests-share-one-database.md](docs/03-guides/gotchas/integration-tests-share-one-database.md).

## Mapa: dónde está cada cosa

| Busco…                       | Está en                                                                                                |
| ---------------------------- | ------------------------------------------------------------------------------------------------------ |
| Qué se hizo y por qué        | `docs/07-changelog/<plan_id>.md`                                                                       |
| Estado y tareas de un plan   | `docs/roadmap/<plan_id>.md` (frontmatter + checkboxes)                                                 |
| Una decisión de arquitectura | `docs/05-architecture-decisions/`                                                                      |
| Una trampa del toolchain     | [`docs/03-guides/gotchas/`](docs/03-guides/gotchas/) (68)                                              |
| Cómo no perder el tiempo     | [`docs/03-guides/verificar-antes-de-implementar.md`](docs/03-guides/verificar-antes-de-implementar.md) |
| Principios y protocolo       | `CLAUDE.md`                                                                                            |

## Últimos hitos (para contexto, no para fiarse)

- **2026-07-28** — **Desplegado**: 106 commits, 6 imágenes, esquema 0118→0121 con
  round-trip de reversibilidad probado. Copia previa `20260728T114814Z` y las
  imágenes anteriores etiquetadas `:predeploy-20260728` (rollback = `docker tag`
  de vuelta). Dos trampas de build cazadas en caliente y ya documentadas, y una
  lección cara: el `up -d` **relanzó dos tareas congeladas**.
- **2026-07-27** — Cerradas las 3 tareas que quedaban de `tools-y-cierre-plan-fixes`
  (T2 tools MCP gateables · T4 candado de paridad catálogo↔executor · T8 changelog
  automático al cerrar plan) y auditados los 5 planes del córtex. De paso: tres
  settings de cadencia del beat que eran código muerto, el camino web nativo del
  ADR 0076 que nadie activaba, siete ADR cuyo cuerpo decía `proposed` con el
  frontmatter en `accepted`, y una e2e que asertaba un testid inexistente.
  Suites: unit 2840 · runtime 472 · security 73 · vitest 402 · mypy 583 · verde.
  **Sin desplegar.**

- **2026-07-26** — Barrido del backlog: 3 planes `in_progress` cerrados, los 4 ADR
  `proposed` resueltos. De paso, dos hallazgos que no buscaba: la credencial de
  `claude_sdk` vivía en `os.environ` (podía facturar a la cuenta equivocada) y la
  **restauración completa estaba rota** por un servicio fantasma en
  `restore_app_services`.
- **2026-07-25** — Remediación del workflow de gestión de proyectos: 56 tareas,
  8 olas, ADR 0132. `pending_human_validation`.
