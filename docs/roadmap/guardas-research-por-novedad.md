---
plan_id: guardas-research-por-novedad
title: Guardas de research por novedad (per-target + esterilidad) + memoria de lecturas + instrumentación
status: in_progress
blocking_plan: []
started_at: 2026-07-03
completed_at: null
estimated_duration_calendar: 1 día
estimated_effort_person_days: 1
estimated_cost_human_eur: 400 € – 700 €
estimated_cost_ai_eur: 10 € – 25 €
created_by: operador + auditoría-runs-2026-07-02
spec_sections_referenced: []
docs_language: es
---

# Plan guardas-research-por-novedad — lecturas legítimas sin fricción, churn cortado con precisión

> **Origen:** petición del operador (2026-07-03): «revisar el producir output cuando lee: verificar que si es
> lectura legítima no lo pare ni fuerce a usar un write tool (exploración de ficheros nuevos), y parar por
> ejemplo cuando ha leído más de X veces el mismo fichero — o propón una mejor solución». Aprobado A+B+C con
> libertad de rediseño («no hace falta que sean parches») y con la restricción explícita: **debe funcionar
> para TODOS los providers LLM, no solo claude_sdk**.
>
> **Hallazgo del análisis:** el problema no es "que haya guardas" sino que hoy son la maquinaria central que
> compensa a un loop desmemoriado (ventana de contexto de 8 items). El rediseño invierte la relación: loop
> informado primero (memoria de trabajo de lecturas), guardas keyed a ESTERILIDAD real como red de seguridad.
> Dos reemplazos totales evaluados y descartados con motivo: máquina de fases rígida (sobre-ingeniería que
> pelea con modelos clase-opus) y sesión SDK persistente (palanca real pero toca la arquitectura de seguridad
> → va como ADR `proposed` aparte, gated a decisión del operador).

## Cabecera

| Campo           | Valor                                     |
| --------------- | ----------------------------------------- |
| **ID del Plan** | `guardas-research-por-novedad`            |
| **Rama git**    | `plan/runs-visor-trabajo` (rama de curso) |

## Decisiones del operador (cerradas)

1. **A+B+C aprobado** (señales justas + instrumentación + memoria de lecturas), con libertad de rediseño.
2. **Provider-agnóstico obligatorio**: todo vive en el loop compartido (`graph.py` + `_decide_messages`) que
   ejecutan los 4 providers por igual; nada específico de claude_sdk en el núcleo.
3. La **sesión SDK persistente** (memoria conversacional total + prompt caching, solo claude_sdk) NO entra
   aquí: se redacta como ADR `proposed` para decisión posterior.

## Problema (con evidencia)

| Defecto actual                                                                                                                                                     | Evidencia                                                                                                                                                                |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `_RESEARCH_STREAK_LIMIT=5` es ciego a la novedad: 5 lecturas seguidas — aunque TODAS sean ficheros nuevos — disparan «STOP researching… produce (e.g. write_file)» | explorar 10-15 ficheros antes de escribir es lo normal en un repo real; el nudge presiona a escribir prematuramente y sugiere `write_file` incluso en tareas de análisis |
| El nudge sticky (F2b.3, 2026-07-02) **no se limpia**: una vez disparado persiste hasta que el agente escriba algo                                                  | amplifica el sesgo anterior de forma permanente                                                                                                                          |
| `_DISTINCT_READ_LIMIT=22` corta AMPLITUD, no esterilidad: 22 ficheros distintos tras producir → `research_exhausted`                                               | 22 ficheros distintos es un review/verificación razonable; el presupuesto de iteraciones ya acota el total                                                               |
| No existe la señal «mismo fichero leído >X veces»: el churn cuenta revisitas _consecutivas_ de _cualquier_ target visto                                            | el patrón intercalado A,A,B,A,A,C evade el churn (cada target nuevo lo resetea) y el mensaje genérico no dice QUÉ fichero deja de releer                                 |
| Lecturas con ERROR de un path nuevo cuentan como novedad (añaden target y resetean churn)                                                                          | un modelo degenerado inventando paths inexistentes parece "explorador" para siempre                                                                                      |
| Cero instrumentación de guardas                                                                                                                                    | no sabemos la tasa de falsos positivos; los umbrales se eligen "a ojo" (misma trampa que el budget de 100k tokens)                                                       |

## Lo que YA existe (no se construye)

| Pieza                                                                                                                                  | Dónde                                                        |
| -------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| Clasificación research/producing namespace-aware + `_read_target` normalizado (paths sin offset/limit; queries rag/memory como target) | `graph.py:94-128`                                            |
| Churn consecutivo (`read_churn_streak`) + nudges por repetición exacta y path-churn de escrituras                                      | `graph.py` reflect + `_repetition_nudge`/`_path_churn_nudge` |
| Canales sticky `GUIDANCE`/`REPETITION WARNING`/`PROGRESS` en el prompt (provider-agnósticos)                                           | `providers.py:_decide_messages` + `state.py`                 |
| Bloque `PROGRESS` con ficheros ESCRITOS + aviso 80 % presupuesto                                                                       | `graph.py:_progress_summary` (F2b.1/2)                       |
| `steps_log` jsonb consultable por run (para instrumentación)                                                                           | tabla `executions` + `node_step`                             |

## Alcance

**Entra:**

- **A — Señales justas (novedad, no cantidad):**
  - Contador **por-target** `read_counts`: a la 3.ª lectura del MISMO target → nudge específico con el nombre
    del fichero; cualquier target leído ≥5 veces → cuenta para el backstop duro.
  - El streak del nudge pasa de «5 research seguidas» a «N research seguidas SIN target nuevo» (esterilidad);
    explorar ficheros nuevos = cero fricción, sin límite blando.
  - Lecturas con **error** (`ok=False`) cuentan como estériles y NO añaden target (anti-gaming).
  - **Retirar `_DISTINCT_READ_LIMIT=22`** como trip; retirar el carve-out `is_review & research_streak≥10`
    (cubierto por esterilidad + presupuesto de review de 25 iter).
  - Límite duro de esterilidad **relativo al presupuesto**: `max(10, 25 % de max_iterations)`.
  - El sticky `GUIDANCE` **se limpia** cuando el turno siguiente es productivo (target nuevo o producing tool).
- **B — Instrumentación:** contador `safeguard_stats` (qué nudge/trip disparó y cuántas veces) adjunto al step
  de finalize → queda en `steps_log`, consultable por SQL para medir falsos positivos y ajustar umbrales con
  datos (baseline: runs del 07-01 y 07-03).
- **C — Memoria de lecturas:** digest por fichero leído (1.ª línea significativa, ~100 chars + tamaño; cap 20
  entradas/~1.600 chars, LRU) rendida en el bloque `PROGRESS` — ataca la causa raíz de la relectura (el modelo
  pierde el contenido con la ventana de 8). Para `list_files`: nº de entradas del listado.
- **D — Fix stack_exec ReadTimeout (bug destapado por el run 019f252e, tarea «Tests de feature»):**
  diagnóstico corregido tras medir — los timeouts por-request YA estaban bien; la causa real es **inanición
  de pool**: el worker único consume `default,ingestion,test,review` con `concurrency=2`; con 2 agent-runs
  en vuelo los 2 slots quedan ocupados y el comando de la cola `test` espera sin consumidor hasta agotar los
  300 s (el agent-run bloquea esperando a la MISMA pool que ocupa — la separación por colas del ADR 0093 no
  separa nada si la pool es compartida). Fix: **servicio `workers-aux`** en compose consumiendo
  `test,review,privileged` con pool propia — cura la inanición y de paso revive el backup diario (cola
  `privileged`, dormida — hallazgo F0.4). Hardening menor: margen httpx del runtime > timeout del server
  (hoy empatan a `timeout_s+120` y la carrera produce `ReadTimeout` opaco en vez del 502 estructurado).
- **E — ADR `proposed`:** sesión SDK persistente por run (borrador para decisión del operador; NO se implementa).
- Tests unit/integración de todo + verificación e2e contra baseline.

**Queda fuera (GUARDRAILS DUROS):**

- **NO** tocar el catálogo cerrado de providers (ADR 0021) ni introducir lógica específica de un provider en
  el loop común.
- **NO** retirar los guardas duros restantes (budgets, loop-detector, path-churn): siguen siendo la red de
  seguridad de CUALQUIER diseño.
- **NO** implementar la sesión SDK persistente (solo ADR).
- **NO** recrear workers en el deploy (los cambios A-C viven en la imagen `agent-runtime`, que se instancia
  POR RUN; D también es runtime-side. Cero impacto en runs en vuelo — lección del visibility-timeout de 7 h).

## Decisiones clave / restricciones

- **Prioridad de nudges** en reflect: per-target > churn de esterilidad > repetición exacta (el mensaje más
  específico gana; solo uno por turno).
- Umbrales iniciales: `SAME_TARGET_NUDGE=3`, `SAME_TARGET_HARD=5`, `STERILE_NUDGE=3` (el actual),
  `STERILE_HARD=max(10, 25 % max_iterations)`. Revisables con los datos de B.
- La elegibilidad del backstop duro NO cambia (produced / review_retries>0 / is_review): un run de análisis
  estéril sigue acotado solo por presupuesto (invariante D3).
- El digest de C se construye del `last_observation.output` en reflect (sin I/O extra) y NUNCA supera su cap
  (presupuesto de prompt ~400 tokens).

## Estructura de ficheros

- Runtime (compartido por todos los providers)
  - `docker/agent-runtimes/agent-runtime/agent_runtime/graph.py` — señales A, stats B, digests C en
    `_progress_summary`, limpieza de sticky.
  - `docker/agent-runtimes/agent-runtime/agent_runtime/internal_api.py` — D: timeout por-request en `run_stack`.
  - `docker/agent-runtimes/agent-runtime/agent_runtime/stack_exec_tool.py` — D: threading del timeout.
- Docs
  - `docs/05-architecture-decisions/0097-sesion-sdk-persistente-por-run.md` _(nuevo, `proposed`)_ — E.
- Tests
  - `docker/agent-runtimes/agent-runtime/tests/test_research_nudge.py` — reescritura de semántica + casos nuevos.
  - `tests/unit/test_loop_detection.py` — timings del backstop actualizados.
  - `docker/agent-runtimes/agent-runtime/tests/test_sticky_feedback.py` — limpieza del sticky + digests.
  - test nuevo del timeout de `run_stack` (unit del cliente).

## Tareas

### Fase A — Señales por novedad

- [x] **A1 — Contador per-target + nudge específico**: `read_counts` en reflect; a la 3.ª lectura del mismo
      target, nudge que NOMBRA el fichero. **Test:** leer A,A,B,A → nudge menciona `A` y no dispara en B.
- [x] **A2 — Esterilidad en vez de cantidad**: el streak solo crece sin target nuevo o con lectura errónea;
      lecturas erróneas no añaden target. Retirar el trigger `research_streak≥5` y el campo `research_streak`.
      **Test:** 8 lecturas de ficheros NUEVOS → sin nudge; 3 lecturas fallidas de paths nuevos → nudge.
- [x] **A3 — Backstop re-keyed**: `_research_exhausted(sterile_streak, max_same_target_reads, …)`; retirar
      `distinct_reads` y el carve-out de review; límite relativo al presupuesto. **Test:** 23 lecturas distintas
      tras producir → NO trip; mismo fichero ×5 tras producir → trip; límite 12 con max_iterations=50.
- [x] **A4 — Sticky que se limpia**: turno productivo sin nudge nuevo → `guidance_nudge=None`. **Test:** nudge
      disparado, luego lectura de target nuevo → el prompt ya no lleva `GUIDANCE`.

### Fase B — Instrumentación

- [x] **B1 — `safeguard_stats`**: contadores por tipo de nudge/trip, adjuntos al step de finalize (y a los
      returns terminales del nodo plan). **Test:** run con nudge + trip → el último step lleva
      `safeguard_stats` con ambos contadores; consultable vía `steps_log`.

### Fase C — Memoria de lecturas

- [x] **C1 — Digests en PROGRESS**: mapa LRU (cap 20) target→digest desde `last_observation`; render en
      `_progress_summary` bajo «files you have already read». **Test:** tras leer 2 ficheros, PROGRESS contiene
      sus nombres + 1.ª línea; con 25 lecturas solo quedan las 20 últimas; el bloque no supera su cap.

### Fase D — stack_exec ReadTimeout (bug 019f252e)

- [x] **D1 — Servicio `workers-aux`** (compose): segundo worker celery consumiendo `test,review,privileged`
      (concurrency 2) — la cola `test` deja de depender de los slots ocupados por agent-runs y la `privileged`
      (backup diario) cobra vida. **Test:** con 2 agent-runs simulados ocupando la pool principal, un
      `workers.run_stack_command` encolado en `test` se consume igualmente (verificación operacional +
      `celery inspect active_queues` por nodo).
- [x] **D2 — Margen httpx > server**: `run_stack` del runtime pasa a `timeout_s + 180` (server sigue en
      `timeout_s + 120`) para que un timeout real aflore como 502 estructurado con causa, no como
      `ReadTimeout` opaco. **Test:** unit — el timeout del request de `run_stack(timeout_s=X)` es X+180;
      `memory_recall` mantiene el default de 15 s.

### Fase E — ADR sesión persistente (solo redacción)

- [x] **E1 — ADR 0097 `proposed`**: sesión SDK viva por run (host tools mediados sin interrupt), pros
      (memoria total, caching, coste), contras (superficie de seguridad, divergencia con HTTP providers),
      opciones y recomendación. Sin código.

### Fase F — Deploy + verificación

- [x] **F1 — Rebuild SOLO `agent-runtime:v1`** (WITH_CLAUDE=1, contexto raíz) — sin recrear workers ni tocar
      runs en vuelo; los runs nuevos la recogen al instante. **Test:** suites del runtime verdes; imagen
      contiene los cambios (`docker run --entrypoint python … -c "import …"`).
- [ ] **F2 — Relanzar «Tests de feature»** (víctima del ReadTimeout) y verificar en su run nuevo: phpunit
      via stack_exec ≤600 s sin ReadTimeout, sin nudges sobre su exploración inicial, `safeguard_stats` en el
      step final. **Test:** e2e observacional + SQL sobre `steps_log`.
- [ ] **F3 — Métricas vs baseline**: comparar nudges/trips por run y relecturas por run contra los runs del
      07-01/07-03 (SQL sobre `steps_log`); documentar en este plan los números y el ajuste de umbrales si toca.

## Criterios de cierre

1. Checkboxes en `[x]` con su test automático en verde.
2. Un run real explorador (10+ ficheros nuevos) SIN ningún nudge de research en sus steps.
3. Un run real con relectura patológica cortado por per-target o esterilidad, con `safeguard_stats` visible.
4. `stack_exec` con comando lento (>15 s) completando sin ReadTimeout.
5. ADR 0097 en `proposed` esperando decisión del operador.
