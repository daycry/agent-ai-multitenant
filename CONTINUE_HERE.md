# CONTINUE HERE — dónde retomar el trabajo

> **Última actualización: 2026-07-27** · rama `plan/runs-visor-trabajo`
>
> Este archivo es un **puntero**, no una copia del estado. La fuente de verdad es
> el frontmatter de `docs/roadmap/*.md`. Si algo de aquí contradice a un
> frontmatter, **gana el frontmatter** y hay que corregir este archivo. Está
> escrito así a propósito: un resumen que duplica datos envejece mintiendo, que
> es el modo de fallo nº1 de
> [verificar-antes-de-implementar.md](docs/03-guides/verificar-antes-de-implementar.md).

## En una frase

El código está al día y **nada está desplegado**: hay una remediación grande
cerrada, todos los ADR aceptados, y el trabajo pendiente es humano (tests,
despliegue, aprobar planes nuevos).

## Lo primero que hay que saber

1. **No desplegar ni relanzar nada.** Orden vigente del operador: no relanzar ni
   desbloquear tareas —ni tras el reset de cuota— hasta que dé el sistema por
   verificado. Observación pasiva sí.
2. **CI está caído y no es culpa del código**: facturación de la cuenta `daycry`
   («recent account payments have failed»). Lo arregla el operador en
   <https://github.com/settings/billing>. Mientras tanto, las suites se corren en
   local (ver más abajo).
3. **La rama `plan/runs-visor-trabajo` tiene el PR #66 abierto** y va muy por
   delante de lo que describe su título. Todo lo empujado está ahí.

## Estado del roadmap (regenerable, ver §«Comprobar»)

| Estado                     |  N  | Qué significa aquí                                        |
| -------------------------- | :-: | --------------------------------------------------------- |
| `completed`                | 25  | cerradas del todo                                         |
| `pending_human_validation` | 46  | código entregado; **esperan tests humanos + despliegue**  |
| `pending_approval`         | 14  | **nunca empezadas** — necesitan tu aprobación (protocolo) |
| `blocked`                  |  1  | `guardas-research-por-novedad`: solo le falta el e2e      |
| `in_progress`              |  0  | correcto: el protocolo permite una como mucho             |

**ADR en `proposed`: ninguno.** Los cuatro que quedaban se cerraron el 2026-07-26
(0076, 0110, 0117, 0128).

## Las casillas del córtex: lo que decía aquí era falso

> **Corregido el 2026-07-27.** Este archivo afirmaba que las 76 casillas del
> córtex eran «**rancias**: implementado y desplegado, los planes quedaron sin
> marcar». Se verificaron **una a una contra el código**, con una pasada
> adversarial por fase. Resultado: **29 implementadas, 45 parciales, 2 ausentes**.
> La afirmación anterior extrapolaba tres comprobaciones puntuales a 76 tareas.

Estado actual tras marcar lo verificado y cerrar cinco defectos reales:

| Dónde                                    |  N  | Qué son de verdad                                                                                                                                                                             |
| ---------------------------------------- | :-: | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `cortex-f1`…`f5`                         | 42  | **Huecos reales**, uno a uno en [`gaps-cortex-2026-07-27.md`](docs/roadmap/gaps-cortex-2026-07-27.md). ~14 son «falta el test», ~5 divergencias de nombre (no tocar), ~15 tramos funcionales. |
| `prod-06`                                |  1  | **Rancia.** «Dar caller a `apply_reviewer_verdict`» — ya lo tiene (`workers/execution.py:473`), llegó con el ADR 0087.                                                                        |
| `15-instalador`                          |  2  | Pentest externo y release v1.0.0: **decisión y contratación tuya**, no código.                                                                                                                |
| `prod-17`, `prod-18`, `guardas-research` |  4  | **e2e bloqueados**: exigen runner Docker y lanzar runs reales.                                                                                                                                |
| `06.10`, `06.11`                         |  2  | Diferidas por decisión propia (typeahead cosmético; plan 06.12 aparte). Están en planes ya `completed`.                                                                                       |
| `prod-03`…`prod-16`, `remediacion-…`     | 171 | De los **14 planes `pending_approval`**: nunca empezados, esperan tu aprobación.                                                                                                              |

**Lo que hay que llevarse de aquí, más que los números:** un resumen que dice
«esto ya está» sin evidencia por ítem envejece mintiendo, y cuesta más caro que
no tenerlo — porque se lee y se cree. Antes de saltarte una casilla porque este
archivo dice que es rancia, **ábrela**. Y al revés: la pasada adversarial dio al
menos un falso positivo comprobado, así que tampoco te fíes de un `partial` sin
mirar el fichero.

## Qué necesita al operador (por orden de coste)

1. **Desplegar y validar.** Es el cuello de botella de las 45 fases en
   `pending_human_validation`. Recetas de build en
   [gotchas/image-build-recipes-that-bite.md](docs/03-guides/gotchas/image-build-recipes-that-bite.md).
   Pendiente además: rescatar en dev 2 tareas congeladas y 3 planes varados.
2. **Aprobar (o descartar) los 15 planes `pending_approval`** — casi todos
   `prod-XX`. Sin aprobación no puedo arrancarlos.
3. **Dos verificaciones que exigen humano delante**: la prueba en navegador del
   OAuth de MCP (ADR 0127) y el smoke del perfil seccomp estricto en dev.
4. **Sembrar un dataset dorado de evals.** El productor, el lector y el
   muestreador están puestos y probados; elegir qué tareas cerradas son «buenas»
   es curaduría humana. Mecanismo: `POST /tasks/{id}/promote-to-dataset` (hay UI).
5. **`registry-egress-followups`** (`open`): F1/F3/F4/F5 siguen abiertos.

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

```bash
.venv/Scripts/python.exe -m pytest tests/unit/ -q      # 2858
.venv/Scripts/python.exe -m pytest tests/security/ -q  # 73
.venv/Scripts/python.exe -m mypy apps/ packages/       # 583 ficheros, limpio
cd apps/admin-panel && npx vitest run                  # 402
cd docker/agent-runtimes/agent-runtime && ../../../.venv/Scripts/python.exe -m pytest tests/ -q   # 472
```

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

## Mapa: dónde está cada cosa

| Busco…                       | Está en                                                                                                |
| ---------------------------- | ------------------------------------------------------------------------------------------------------ |
| Qué se hizo y por qué        | `docs/07-changelog/<plan_id>.md`                                                                       |
| Estado y tareas de un plan   | `docs/roadmap/<plan_id>.md` (frontmatter + checkboxes)                                                 |
| Una decisión de arquitectura | `docs/05-architecture-decisions/`                                                                      |
| Una trampa del toolchain     | [`docs/03-guides/gotchas/`](docs/03-guides/gotchas/) (67)                                              |
| Cómo no perder el tiempo     | [`docs/03-guides/verificar-antes-de-implementar.md`](docs/03-guides/verificar-antes-de-implementar.md) |
| Principios y protocolo       | `CLAUDE.md`                                                                                            |

## Últimos hitos (para contexto, no para fiarse)

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
