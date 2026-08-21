---
plan_id: guardas-research-por-novedad
title: Guardas de research por novedad (per-target + esterilidad) + memoria de lecturas + instrumentación
status: blocked
blocking_plan: []
started_at: 2026-07-03
completed_at: null
estimated_duration_calendar: 1 día
estimated_effort_person_days: 1
estimated_cost_human_eur: 400 € – 700 €
estimated_cost_ai_eur: 10 € – 25 €
created_by: operador + auditoría-runs-2026-07-02
blocked_reason: >-
  Solo quedan F2 y G13, y las dos son VERIFICACIÓN e2e: exigen desplegar la
  imagen nueva y relanzar runs reales. El despliegue está parado por decisión
  del operador (no relanzar ni desbloquear nada hasta dar el sistema por
  verificado). Todo el código de la fase G está entregado y probado.
blocked_at: 2026-07-26
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
  - ⚠️ **Aclarado el 2026-08-19: esto NO es un test automático y no puede llegar a serlo.**
    Se revisó esta casilla buscando un `command:` desfasado y no hay ninguno —ni lo
    habrá—: «relanzar un run real y mirar sus `steps_log`» es un **test humano**, con
    stack arriba y un run de verdad, no un fichero de pytest. Escrito como «**Test:**»
    dentro del bullet parecía una medida automática pendiente de cablear, y no lo es; por
    eso ni figura ni debe figurar en el inventario de `test_declared_tests_exist.py`,
    que sólo vigila comandos declarados.
  - 🔒 **Y sigue en `[ ]` a propósito**, no por olvido. El `blocked_reason` del frontmatter
    lo dice: F2 y G13 exigen desplegar la imagen nueva y relanzar runs, y hay una **orden
    permanente del operador** de no relanzar ni desbloquear nada hasta dar el sistema por
    verificado. Marcarla sin ese run sería exactamente el checkbox que miente que este
    repaso viene a quitar. Lo que SÍ está entregado y verificable es el código de las
    fases A–E: D2 vive hoy en
    `docker/agent-runtimes/agent-runtime/agent_runtime/internal_api.py` (`run_stack`
    espera `timeout_s + 180` frente a los `+120` del server, con el porqué en su propio
    docstring) y D1 en `docker/docker-compose.manuals.yml` (servicio `workers-aux`).
- [x] **F3 — Métricas vs baseline**: comparar nudges/trips por run y relecturas por run contra los runs del
      07-01/07-03 (SQL sobre `steps_log`); documentar en este plan los números y el ajuste de umbrales si toca.

  **Resultado (2026-07-03, SQL sobre `safeguard_stats` de los steps finalize):**

  | Métrica                          | Baseline (07-01/07-02)   | Post-guardas (07-03)                                                |
  | -------------------------------- | ------------------------ | ------------------------------------------------------------------- |
  | `max_iterations_exceeded`        | 3 runs × 50 iter (07-02) | **0**                                                               |
  | `repetitive_loop_detected`       | 1 run × 26 iter          | **0**                                                               |
  | `research_exhausted`             | 3 runs × ~30 iter        | 1 run × **11 iter** (legítimo: workspace vacío pre-fix durabilidad) |
  | Iteraciones medias (runs `done`) | 7,3–11,5                 | 10,2                                                                |
  | Runs con actividad de guardas    | n/d (sin instrumentar)   | 2/20 (10 %) — **cero falsos positivos** en runs sanos               |

  Sin ajuste de umbrales: los trips solo dispararon en el caso genuinamente estéril
  y las escaladas son tempranas y baratas (11 iter vs 30-50 del baseline).

## Criterios de cierre

1. Checkboxes en `[x]` con su test automático en verde.
2. Un run real explorador (10+ ficheros nuevos) SIN ningún nudge de research en sus steps.
3. Un run real con relectura patológica cortado por per-target o esterilidad, con `safeguard_stats` visible.
4. `stack_exec` con comando lento (>15 s) completando sin ReadTimeout.
5. ADR 0097 en `proposed` esperando decisión del operador.

---

## Ampliación 2026-07-03 — Recalibración de restricciones del runtime (auditoría de plataforma)

> **Origen:** auditoría de plataforma 2026-07-03 (`auditoria-plataforma-2026-07-03.md`), **causa raíz F**
> (las guardas castigan exploración legítima) + síntoma en vivo reportado por el operador («sigue apareciendo
> el tema de producir output; la exploración legítima no funciona», ejemplo: tarea «Tests de feature»).
> Verificación adversarial en Opus 4.8 de los hallazgos r1-r7. Esta ampliación NO reemplaza las Fases A-F;
> añade la **Fase G** (recalibración) con IDs propios para no colisionar con la Fase F (deploy, F1-F3).
>
> **Estado:** `pending_approval` (ningún fix implementado aquí; solo el plan). El resto del plan A-F sigue como
> estaba.

### Inventario de restricciones del runtime (con veredicto y propuesta)

| #   | Restricción hoy                                                                                              | Hallazgo · veredicto | Problema                                                                                                                                                                                                                                                                                                 | Tarea  |
| --- | ------------------------------------------------------------------------------------------------------------ | -------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------ |
| 1   | Allowlist `shell_exec` (base SDK `_SDK_BASE_SHELL_COMMANDS` ∪ `allowed_commands`)                            | r1 · E1 (en vivo)    | Faltan utilidades de LECTURA naturales (`sed -n`, `awk`, `sort`, `uniq`, `cut`, `tr`, `echo`). `sed` denegado 2× en vivo. La lista ya incluye `rm`/`mv` (destructivos), así que vetar `sed` no aporta seguridad, solo fricción.                                                                          | G6     |
| 2   | `_read_target` ignora offset/limit (`graph.py:128-141`)                                                      | r2 · matizado        | Paginar = releer el mismo target; contadores per-target acumulativos sin decay. _Matiz:_ el lector real no pagina (erra >1 MB), pero la acumulación sin reset tras producir sí muerde.                                                                                                                   | G1/G2  |
| 3   | `has_produced` se latchea con producing tools FALLIDAS (`graph.py:950`)                                      | r4 · confirmado      | Un `shell_exec` denegado latchea `has_produced` → desvía trips a `needs_human_review` y cambia el nudge a «FINISH».                                                                                                                                                                                      | G3     |
| 4   | Lecturas erradas / fallos de plataforma suman esterilidad                                                    | r2/r4 · —            | Un fallo de PLATAFORMA (tool sin executor, `command not allowed`, worktree EACCES) castiga al agente como churn propio.                                                                                                                                                                                  | G3b    |
| 5   | `_RESEARCH_TOOLS` solo 4 nombres base; `search_code` fuera + sin executor                                    | r5a · matizado       | `search_code` no gana novedad y cuenta como MUTADOR; _matiz:_ sí resetea la racha estéril (sub-claim invertido) y no tiene executor (toda llamada `ok=False`).                                                                                                                                           | G4     |
| 6   | `LoopDetector` fingerprintea `(tool,args)` sin reset (threshold=3)                                           | r7 · matizado        | **Defecto de fondo real:** un ciclo edit→build→edit→build con un comando de test/build IDÉNTICO acumula y tripa a la 4.ª invocación aunque haya progreso intercalado (falso positivo de convergencia). 4/5 casos reales fueron comandos idempotentes EXITOSOS re-ejecutados. Siempre escala (no aborta). | G8     |
| 7   | Visor hardcodea «stop researching, produce output» para toda variante (`graph.py:1030`)                      | r3 · E1              | El visor amplifica el síntoma incluso cuando el nudge real es «ya has producido, FINISH».                                                                                                                                                                                                                | G5     |
| 8   | Memoria de lecturas (`_READ_DIGESTS_MAX=20`, `_READ_DIGEST_CHARS=100`)                                       | (mejora)             | Digests de 100 chars no sustituyen una relectura; una relectura de fichero no modificado debería ser gratis.                                                                                                                                                                                             | G9/G10 |
| 9   | Presupuestos por-kind (50/500k claude_sdk, 25/250k review)                                                   | (correcto)           | Recalibrados en la baseline 07-02; sin cambio. Solo mostrar el restante en el visor.                                                                                                                                                                                                                     | G11    |
| —   | git ausente del sandbox; red interna sin egress salvo registry-proxy; deny-by-default del allowlist de tools | (por diseño)         | Se mantienen (principios 2, ADR 0094, ADR 0092). No se tocan.                                                                                                                                                                                                                                            | —      |

### Fase G — Recalibración (tareas)

> **Cierre (2026-07-26).** Casi toda esta fase la resolvió el **ADR 0103**
> (`accepted`, implementación COMPLETA el 2026-07-12) y los checkboxes se habían
> quedado atrás. Estado real, guarda por guarda:
>
> | Guarda                | Estado                                                          | Dónde                                                                                                                                                                        |
> | --------------------- | --------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
> | G1                    | **RECHAZADA** por el ADR (recomendación B)                      | el residual real de r2 era la acumulación sin decay, que cierra G2 — meter offset/limit en la clave ABRE un hueco: un re-read con offset variable se disfraza de exploración |
> | G2, G3b, G4a, G5, G10 | hechas (SAFE del ADR)                                           | `tool_classification.py`, `graph.py`, visor                                                                                                                                  |
> | G3                    | hecha                                                           | `graph.py:1263` — `_is_producing_tool(tool) and bool(observation.get("ok"))`                                                                                                 |
> | G8                    | hecha, opción B **ratificada** por el operador (2026-07-12)     | `LoopDetector.note_progress`                                                                                                                                                 |
> | G9                    | **NO implementada** por recomendación del propio ADR (opción C) | G10 + la memoria de lecturas ya mitigan la relectura                                                                                                                         |
> | G4b                   | follow-up con plumbing (pasar `security_level` al runtime)      | fuera del ADR                                                                                                                                                                |
> | G6, G11, G12          | **cerradas hoy** (ver abajo)                                    |                                                                                                                                                                              |
> | G13, F2               | **bloqueadas**: exigen desplegar y lanzar runs reales           | orden del operador vigente                                                                                                                                                   |
>
> **G6 — cerrada hoy.** G6b y G6c ya estaban; faltaba G6a y el mensaje tenía un
> hueco. Se añade el preset **«Lectura»** (`sed, awk, sort, uniq, cut, tr, head,
tail, grep, wc`) a la UI de comandos. Es un PRESET y no una base implícita
> siempre activa a propósito: la allowlist es deny-by-default por diseño
> (principio 2), y conceder siete binarios en silencio a todo proyecto —incluidos
> los que el operador cerró a conciencia— es decisión suya, no de la plataforma.
> Queda en un clic. Se deja fuera `echo`, que el plan listaba: sin shell no
> redirige a ninguna parte, así que no sirve para leer nada.
>
> Y una corrección al propio plan: G6b proponía sugerir `head -n N | tail` como
> alternativa, pero eso lleva **tubería** y `stack_exec` no la admite — sería
> mandar al agente a un segundo fallo. El mensaje ofrece `read_file` con
> `offset`/`limit`, que no pasa por la allowlist y siempre funciona. 7 tests.
>
> **G11 — cerrada hoy.** El aviso de «te quedan N iteraciones» vivía **solo
> dentro del prompt**: lo veía el modelo y no el operador. El visor mostraba lo
> gastado sin techo contra el que compararlo, que es como no mostrar nada (12
> iteraciones tranquilizan si el tope es 50 y son una urgencia si es 15). El
> runtime adjunta ahora el sobre de presupuesto al **primer** step —no al
> `finalize`, donde llegaría cuando ya no sirve para intervenir— y el visor resta.
> Es el envelope que ESE run recibió: recalcularlo al leer mostraría el
> presupuesto de hoy y mentiría sobre runs pasados. 4 tests de runtime + 4 de
> frontend.

- [~] **G1 — offset/limit en la clave del target**: `_read_target` incluye offset/limit → paginar deja de
  contar como releer. **Test:** leer `A[0:100]` y `A[100:200]` cuenta como 2 targets, no 2 lecturas del mismo.
- [x] **G2 — decay/reset per-target tras turno productivo**: `read_counts[target]` decae/resetea cuando un
      turno productivo toca ese target (write al fichero o `stack_exec` OK); umbrales proporcionales al
      presupuesto (como `_sterile_hard_limit`), no fijos 3/5. **Test:** releer `Routes.php` tras un `phpunit`
      fallido en un bucle TDD NO dispara el nudge same-target.
- [x] **G3 — `has_produced` exige `result.ok` (r4)**: la rama de producing tools comprueba `observation.ok`
      antes de latchear `has_produced`/`turn_productive`. **Test:** un `shell_exec` denegado NO pone
      `has_produced=True`; un `write_file` OK sí.
- [x] **G3b — fallos de plataforma no suman esterilidad**: errores identificables de plataforma (tool sin
      executor, `command not allowed`, worktree vacío/EACCES) no incrementan la racha estéril ni el contador
      per-target. **Test:** 3 `command not allowed` seguidos no disparan el trip de esterilidad.
- [x] **G4 — clasificar research por metadata (r5a)**: añadir `search_code` a `_RESEARCH_TOOLS` y clasificar por
      metadata del catálogo (`security_level=safe`/read-only ⇒ research) en vez de lista fija; cablear el
      executor de `search_code` (o retirarlo — coord. `tools-y-cierre-plan-fixes.md` g4). **Test:** una tool MCP
      read-only cuenta como research; `search_code` gana novedad y no cuenta como mutador.
- [x] **G5 — resumen del visor por variante (r3) + `safeguard_stats` en el visor**: cada variante de nudge
      (same-target / esterilidad / repetición / ya-produjo-FINISH) rinde su propio resumen; exponer
      `safeguard_stats` (instrumentación B1) en el visor de runs. **Test:** el step de un nudge «FINISH» no
      muestra «stop researching»; el visor lista los contadores por tipo.
- [x] **G6 — allowlist de lectura + error accionable + presets (r1)**: **G6a** ampliar la base con
      `sed, awk, sort, uniq, cut, tr, echo` (escribir en el worktree ya está permitido, así que estas de lectura
      no añaden superficie); **G6b** al denegar, el mensaje sugiere la alternativa concreta (`head -n N | tail`,
      o `read_file` con offset/limit) además de la lista; **G6c** presets de `allowed_commands` por
      runtime-template en la UI de comandos. **Test:** `sed -n '1,50p'` se ejecuta; un comando fuera del
      allowlist devuelve un error con alternativa; el preset `php-phpunit` trae composer/php/phpunit.
- [x] **G8 — `LoopDetector` con reset por progreso (r7)**: el detector deja de tripar comandos idempotentes
      re-ejecutados en un ciclo con progreso intercalado — resetear/decaer el fingerprint `(tool,args)` cuando
      hubo un turno productivo intermedio (write o target nuevo), de modo que edit→build→edit→build no acumule.
      Un comando FALLIDO repetido no se trata como mutador (no mutó nada): al 2.º fallo idéntico, inyectar el
      error + alternativa en el canal sticky GUIDANCE en vez de contar hacia el hard-trip. El corte duro queda
      para repeticiones idénticas EXITOSAS de mutadores SIN progreso intermedio. **Test:** `composer audit`
      ejecutado con éxito 4× intercalado con writes NO tripa; 4× idéntico sin ningún progreso sí; un
      `command not allowed` repetido inyecta guidance y no cuenta como mutador.
- [~] **G9 — cache de contenido por target leído**: si el agente relee un fichero NO modificado desde la última
  lectura, servir del cache del propio runtime (respuesta gratis, sin container round-trip) y NO contar
  esterilidad; invalidar al escribir el path. Convierte la relectura de pecado en no-op. **Test:** releer un
  fichero no modificado no incrementa `read_counts` ni hace round-trip; tras escribirlo, la siguiente lectura
  sí va al disco.
- [x] **G10 — subir `_READ_DIGEST_CHARS`**: a un presupuesto útil (~400) con firma de símbolos para código.
      **Test:** el digest de un `.py` incluye la 1.ª def/clase; el bloque PROGRESS no supera su cap.
- [x] **G11 — presupuesto restante en el visor**: mostrar iteraciones/tokens restantes también en el visor, no
      solo en el PROGRESS del prompt. **Test:** el visor de un run en curso muestra el presupuesto restante.
- [x] **G12 — docs + ADR**: actualizar este plan y los ADR 0089/0092 afectados con la nueva semántica.
      **Test:** n/a (documental); los ADR reflejan el reset del loop-detector y la clasificación por metadata.
- [ ] **G13 — e2e de validación**: re-lanzar «Tests de feature» con la imagen nueva y verificar **0**
      `command not allowed: sed`, **0** nudges por paginación/TDD legítimo, ciclo edit-build con comando
      idéntico sin trip, y los `needs_human_review` de r6 resueltos. **Test:** e2e observacional + SQL sobre
      `steps_log`/`safeguard_stats`.

### Criterios de cierre de la Fase G

1. Checkboxes G1-G13 en `[x]` con test automático en verde.
2. Un run explorador (10+ ficheros nuevos, con paginación) SIN ningún nudge de research.
3. Un ciclo edit→build→edit→build con un comando de test IDÉNTICO NO dispara `repetitive_loop`.
4. `sed`/`awk` disponibles; error de allowlist accionable.
5. Un fallo de plataforma (tool sin executor) no contamina las guardas ni escala a `needs_human_review`.
