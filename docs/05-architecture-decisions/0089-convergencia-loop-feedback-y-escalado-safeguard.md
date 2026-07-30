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

### D4 (addendum 2026-06-29) — Backstop DURO de read-churn

La monitorización en vivo (opus/claude_sdk) mostró el **reverso** de D3: tras un self-review fallido,
el modelo entró en **read-churn** — releyó los mismos 2-3 ficheros con args distintos las iteraciones
17→50 **sin escribir nada**, ignorando el nudge blando (`research_streak>=5`, advisory). Como los
tools read-only están exentos del abort duro (D3) y los args distintos nunca fingerprintean como
bucle, el `max_iterations` (techo per-kind 25/50) era demasiado laxo: dejó quemar 33 iteraciones.

**Decidido:** un backstop DURO en `plan()` — `_research_exhausted(research_streak, has_produced,
review_retries, hard_limit)` dispara cuando `research_streak >= _RESEARCH_HARD_LIMIT (=10)` Y el run
tiene algo que preservar (`has_produced` O `review_retries > 0`). Resultado:
`_abort_or_escalate_status(has_produced)` con `abort_code = research_exhausted` (nuevo miembro del
enum, aditivo → contrato persistido seguro). Reutiliza la fontanería de escalado existente (resumen
del deliverable en `finalize`, commit WIP del worker en `needs_human_review`) — sin preservación
nueva. Umbral 10 = 2x el nudge blando y ≪ 25/50: falla rápido (~12 iter) en vez de quemar el budget.

**Invariante:** un run analysis-only **estéril** (solo lee, sin producir y sin review previo fallido)
**NO** se corta — el gate falla y su terminación sigue acotada por `max_iterations`/`wall_clock` (D3
intacto). Test de regresión que lo blinda añadido.

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
