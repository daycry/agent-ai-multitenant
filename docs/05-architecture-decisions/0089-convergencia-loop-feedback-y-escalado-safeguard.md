---
adr: "0089"
title: Convergencia del loop del agente — propagación del feedback del review + escalado recuperable del safeguard
status: accepted
date: 2026-06-28
deciders: operador, System Architect (claude-opus)
phase: remediacion-ciclo-vida-ejecucion
related: ["0087", "0088", "0013", "0086"]
docs_language: es
---

# ADR 0089 — Convergencia del loop del agente

## Contexto

Tras la remediación del ciclo de vida (ADR 0088), la monitorización de runs reales mostró que
tareas de implementación **legítimas no convergían**: el agente re-escribía el MISMO fichero
byte-a-byte hasta que el `LoopDetector` abortaba (`repetitive_loop_detected` → tarea `blocked`),
o agotaba los review-retries. Un análisis profundo (panel multi-agente + verificación adversaria,
solo lectura) estableció que **el `LoopDetector` NO es el bug** — su huella incluye el `content`
byte-a-byte, así que editar el mismo fichero con contenido **distinto** nunca aborta; los
safeguards solo **cortan el síntoma**. La causa raíz: **el feedback del review no llega de forma
accionable al agente que ejecuta**, en dos niveles, y el desenlace del safeguard **descarta el
trabajo**.

## Decisiones

### D1 — El feedback del review DEBE llegar accionablemente al agente que ejecuta

- **Intra-run** (self-review → siguiente `decide`): el `review_feedback` viajaba por `context`, que
  `_decide_messages` recorta a `context[-8:]`; tras 3-4 acciones se **expulsaba** de la ventana y
  el modelo re-derivaba la MISMA decisión. **Decidido:** campos escalares dedicados
  (`last_review_feedback`, `repetition_warning`) renderizados SIEMPRE, destacados y FUERA de la
  ventana rodante, truncados (coste fijo).
- **Inter-run** (AI reviewer rechaza → re-despacho del implementer): el `what_to_fix` se persistía
  en `task_audit_events` (kind=`review_comment`) pero **nadie lo leía** al re-despachar → el
  implementer re-implementaba a ciegas. **Decidido:** el orchestrator lee los `review_comment`
  previos (newest-first, acotado) y los inyecta como `prior_review_feedback` en el spec del
  implementer; el runtime los antepone al system_preamble ("intentos anteriores rechazados; debes
  corregir: <criterio> → <qué arreglar>").

### D2 — Un safeguard que dispara con trabajo producido ESCALA, no aborta

Cuando el `LoopDetector` —o un budget-abort (`max_iterations`/`max_tool_calls`/`max_wall_clock`)—
dispara Y el agente **ya produjo** un deliverable (`has_produced`), el run termina en
`needs_human_review` (que **preserva y commitea** el worktree, ADR 0087) en vez de `aborted` (que
lo descarta). Un bucle **estéril** (sin producción) mantiene el `aborted` duro. El string del
`abort_code` (`repetitive_loop_detected`, etc.) **no cambia** — solo el status de ciclo de vida.

### D3 — Los tools read-only idempotentes están exentos del ABORT DURO

Solo los tools **mutadores** (productores + cualquier verbo desconocido, conservador) disparan
`REPETITIVE_LOOP`; un verificador read-only repetido (`pytest`/`git status`/`list_files`) en un
TDD sano recibe el **nudge** pero no aborta — su terminación la garantizan `max_iterations`/
`wall_clock`. Cierra un falso positivo real del contador acumulativo-total.

## Invariantes preservadas

- El **fingerprint** del `LoopDetector` sigue incluyendo los args COMPLETOS (content) → editar el
  mismo fichero con contenido **distinto** produce huella distinta y NUNCA cuenta como bucle
  (iteración legítima protegida; test de regresión añadido, que antes faltaba).
- La **terminación** del run NO depende del detector ni del nudge: `max_iterations`/`max_tool_calls`/
  `max_wall_clock`/`recursion_limit` siguen siendo el techo duro garantizado.
- El `threshold=3` y los strings de `SafeguardCode` no cambian (contrato persistido).

## Alternativas rechazadas

- Fingerprint `tool+path` (sin content): mataría la edición iterativa legítima. Intocable.
- Subir el threshold a ciegas: solo retrasa el abort sin resolver la irrecuperabilidad.
- Detección semántica/embeddings: no determinista, cara, rompe los tests offline.
- Escalar SIEMPRE (sin gatear por `has_produced`): genera toil en bucles basura sin deliverable.

## Trazabilidad

Análisis y verificación adversaria en el scratchpad de la sesión; plan en
`~/.claude/plans`. Implementación: A1/B1/B2/B3/C (agent-runtime) + A2 (orchestrator/workers).
