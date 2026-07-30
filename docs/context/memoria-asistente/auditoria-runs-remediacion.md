---
name: auditoria-runs-remediacion
description: Auditoría exhaustiva del ciclo de vida de ejecuciones (runs)/reviews + plan de remediación aprobado (rama plan/runs-visor-trabajo)
metadata:
  node_type: memory
  type: project
  originSessionId: 9b6ffa32-bda3-49a0-a5ed-708c0fca5208
---

2026-06-27: auditoría multi-agente del pipeline de ejecución de tareas/reviews
(orchestrator→worker→contenedor→providers→review-runtime→persistencia→UI). **41 hallazgos
confirmados + 11 en disputa** (volcado en el scratchpad de la sesión:
`findings-confirmed.md` / `findings-contested.md`). Plan aprobado en
`~/.claude/plans/necesito-que-hagas-una-federated-book.md`.

**8 clusters de causa raíz** (por qué fallan los runs):

- C1 **El AI reviewer corre a ciegas** (F51, crítico, confirmado a mano): el orchestrator
  construye `review_context` (dispatch.py:459) pero `_agent_spec`/`_build_runtime_env` del
  worker NUNCA lo inyectan al contenedor → reviewer sin output del implementer ni criterios →
  summary sin `<verdict>` → worker fuerza reject → tarea rebota a backlog y acaba **blocked**.
- C2 self-review autoritativa (ADR 0087) sobre-escala: `written_files` solo reconoce
  `write_file` literal (shell/MCP/custom invisibles) → review solo-prosa → needs_human_review.
- C3 pérdida de eventos sin reconciler (errores DB/Redis se dead-lettean y ACKean).
- C4 post-run del worker no atómico (transición en txn separada tras finalize → atascos).
- C5 contrato contenedor↔worker frágil ("exited 0 with no result" espurios; budget==wall-clock).
- C6 proveedores sin retry/timeout (un 429 mata el run; claude_sdk cuelga / auto-ejecuta nativas).
- C7 taxonomía estado/finish/UI incoherente (timeline congelado, detalle "running" eterno).
- C8 lifecycle review-runtime incompleto (GATED por ADR 0063).

**Plan por fases**: P0 convergencia → P1 operacional → P2 taxonomía/UI → P3 concurrencia →
GATED (autostart review-runtime). TDD, sin big-bang. **2 decisiones del operador pendientes**:
D1 (autoridad de finish_status, addendum ADR 0087; P2.2 gated) y D2 (des-diferir ADR 0063;
fase GATED). Relacionado: [[refactor-self-review-autoritativo]],
[[agent-runtime-convergencia-hardening]].

**PROGRESO (2026-06-27, sin commitear):**

- **Cluster C1 CERRADO+VERIFICADO** (la causa #1):
  - P0.1: `_agent_spec`/`run_task` inyectan `review`/`review_context` al contenedor +
    `build_review_preamble` (instrucción de `<verdict>` obligatorio). Ficheros: execution.py,
    **main**.py. 12 tests.
  - P0.2: `_apply_review_verdict` distingue fallo de infra del reviewer (status!=done → deja
    in_review, NO reject) de done-sin-formato (reject defensivo). execution.py. Integ verde.
  - P0.3: `parse_reviewer_output` tolerante (`<verdict>(.*?)</verdict>` + `_normalise_verdict`).
    reviewer_bridge.py. 11 unit + 6 integ.
- **Cluster C2 PARCIAL**: P0.4(a) clasificación producing/research namespace-aware
  (`_base_tool_name`/`_is_producing_tool`/`_is_research_tool`) → MCP/custom `*.write_file`
  visibles al harvest. graph.py. 3 tests. **Pendiente P0.4(b)**: shell_exec-production +
  política de escalado cuando has_produced pero written_files vacío (acoplado a D1).
- Verificación: container 68 verde, unit 1684 verde (1 fallo PREEXISTENTE ajeno:
  test_user_role_values, falta `plan_approver`), review integ 6 verde.
- **PENDIENTE**: P0.5 (transición atómica con finalize — refactor txn del worker), P0.6
  (beat reconciler), P1/P2/P3/GATED. Rama plan/runs-visor-trabajo. Tests nuevos:
  test_agent_spec_review_context, test_reviewer_verdict_parsing, test_review_run_preamble,
  test_producing_classification.

**Implementación por WORKFLOWS (agentes paralelos, ficheros disjuntos, TDD; verifico yo entre fases):**

- **Fase 1 HECHA+VERIFICADA** (6 agentes): UI (P2.6/P2.7/F47/F48/F49/F50), execution_repo
  (P2.5/F45/F46/F52), providers.py (F35/F36/F34/F33/F25/F30 retry+timeout+ProviderTimeout),
  shared-llm (F32 malformed/truncated signal, F31 native tools disallowed), runtime-boot
  (F18 spec-error/F22 retry/F23 mcp), container.py (F17/F21). Verif: container 90, shared-llm 82,
  unit 1723. test_user_role_values arreglado (enum ganó plan_approver, ADR 0079).
- **Fase 2 HECHA+VERIFICADA** (2 agentes): graph.py (F25 captura excepción proveedor→aborted,
  F27 taxonomía SafeguardCode, P2.2 finish_status authority→needs_human_review), execution.py
  (P0.5 transición ATÓMICA con finalize, P1.1 fallback a container_result.logs, P1.3 grace,
  P2.1 cancelled→cancelled, P2.3 commit-en-escalado+commit_failed, P3.3 cancel-races, F12).
  **REGRESIÓN cazada+arreglada por mí**: A5 puso demux=True en \_pump_logs y NO entrega líneas
  en vivo (stream Redis vacío); fix = seguir SOLO stdout sin demux (todos los eventos van por
  stdout vía \_emit→print). Verif: unit 1736, integ ejecución 18 (task_transition/capture/
  worktree/worker_runs/streams). **Gotcha**: los unit de A5 con fake demux enmascaraban la
  regresión; solo la integración con contenedor real la cazó → SIEMPRE correr integración.
- **Fase 3 EN CURSO** (3 agentes): orchestrator dispatch resiliente (F01/F02/F05/F04/F07/F08/F09),
  reconciler beat (P0.6), taxonomía tareas (F43 'awaiting_human' huérfano→blocked, F44 panel
  escaladas, P2.4 CHECK migración).
- **Fase 3 HECHA+VERIFICADA**: orchestrator C3 (F01/F02/F04/F05/F07/F08/F09 crash-safe +
  claim atómico + NACK transitorios), reconciler beat P0.6 (maintenance.reconcile_pipeline_state),
  taxonomía (F43 awaiting_human→blocked, F44 panel, migración 0101 CHECK reversible). **Bugs
  extra cazados+arreglados por mí**: seed reconciler sin system_prompt; `plan_progress._OPEN_TASK_STATUSES`
  faltaban ready/assigned_to_human/awaiting_human_approval (plan se cerraba con tareas pendientes).
- **Fase 4 (GATED C8) HECHA+VERIFICADA**: autostart review-runtime (F39/F40/F41, ADR 0088 D2),
  providers.py consume CompletionSignals (F32 cerrado). **Gap cerrado**: fuente única
  `api_server.review_autostart` invocada por orchestrator Y reconciler (idempotente).
- **DECISIONES gated documentadas en ADR 0088**: D1 (finish_status autoritativo→escala),
  D2 (des-diferir ADR 0063 autostart). Changelog: docs/07-changelog/remediacion-ciclo-vida-ejecucion.md.
- **ESTADO FINAL (2026-06-28): COMMITEADO + PUSHEADO**. 8 commits en
  `origin/plan/runs-visor-trabajo` (hasta `7ff9024`): shared-llm, agent-runtime, api-server,
  workers, orchestrator+reconciler, admin-panel, docs(ADR 0088+changelog), fix test_models.
  pre-commit (black/ruff/mypy/prettier) PASA en todos. Verif: unit 1797, container 114,
  shared-llm 82, integración amplia 38+. Migración 0101 reversible. NO verificable sin stack:
  spawn real contenedor review-runtime + docker rm -f (best-effort).
- **DESPLEGADO EN DEV (2026-06-28)**: 5 imágenes rebuildeadas (api-server:manuals/workers:ci/
  orchestrator:manuals WITH_CLAUDE, agent-runtime:v1, admin-panel:manuals) + 5 contenedores app
  recreados (todos healthy, /healthz vía Caddy=ok en :8080) + **migración 0101 APLICADA** (head BD
  dev=0101, constraint ck_tasks_status_valid). Gotcha: `docker build`/`compose` en PS 5.1 NO usar
  `2>&1` (envuelve stderr como error→aborta); usar $LASTEXITCODE.
- **HALLAZGO+FIX en monitorización de run real (2026-06-28, commit 2516415 pusheado)**: el
  self-review recortaba cada written_file a `_REVIEW_MAX_FILE_CHARS=4000` SIN marcador antes de
  mostrarlo al reviewer ("base your verdict on this ACTUAL code") → un fichero completo >4 KB (un
  AuthController de 4.6 KB) se leía como "truncated mid-expression" y se RECHAZABA en cada intento
  (1/3→…→escalado espurio a needs_human_review). Mi P0.4 lo destapó (al fin el review ve el código).
  Fix en `agent_runtime/providers.py::_review_messages`: cap→12000 + marcador inequívoco cuando se
  excede. Test test_review_truncation_marker. **Lección**: cualquier cap de visualización en prompts
  de juez necesita marcador explícito o el juez lo confunde con contenido incompleto.
- **2º HALLAZGO+FIX en monitorización (2026-06-28, commit 0ac98b6 pusheado)**: la cosecha
  `written_files` solo capturaba lo escrito con write_file en el run ACTUAL → en re-runs
  INCREMENTALES (re-run tras escalado que comiteó trabajo previo P2.3) el reviewer no veía los
  ficheros de runs previos → "missing/incomplete files" → rechazo en bucle, sin converger. Fix en
  `graph.py::self_review`: cosecha el worktree REAL (`_harvest_worktree_files`, lee /workspace o
  AGENT_WORKSPACE_ROOT en disco, excluye .git/vendor/node_modules, acotado, run-actual primero);
  fallback al capture por-run sin worktree. Tests en test_review_sees_code. **Lección**: el
  self-review debe juzgar el deliverable ACUMULADO del worktree, no solo lo producido en un run.
  Ambos fixes en agent-runtime:v1 (rebuild bed66ea, ambos desplegados). El run 019f0e2a escaló con
  la imagen intermedia; un re-run con bed66ea debería llegar a `done`.
- **CONVERGENCIA DEL LOOP (2026-06-28, commits ec72cb5 + 290270d pusheados, ADR 0089)**: análisis
  profundo (panel multi-agente + verificación adversaria) del por qué tareas legítimas no convergen
  y abortan en bucle. Causa raíz (NO el loop detector, que es correcto): **el feedback del review no
  llega accionablemente al agente**. (A1) intra-run: `review_feedback` se salía de `context[-8:]` →
  campos escalares pegajosos (last_review_feedback/repetition_warning) fuera de la ventana en
  `_decide_messages`. (A2) inter-run: el `what_to_fix` se guardaba en task_audit_events pero
  `dispatch._route_ai` nunca lo leía → ahora lo inyecta como `prior_review_feedback` en el spec →
  `__main__.build_prior_feedback_preamble`. (B2/B3) safeguard con has_produced ESCALA a
  needs_human_review (preserva+commitea) en vez de aborted; mismo gating a MAX_ITERATIONS. (C)
  exención read-only del abort duro. Invariante: fingerprint content-aware (regresión nueva).
  Verif: container 137, unit 1806, integ orchestrator/worker/dispatch verde. Rebuild de 4 imágenes
  (agent-runtime + api-server/workers/orchestrator) + recreate + e2e PENDIENTE de validar.
  **Gotcha hooks**: black/prettier de pre-commit revierten el reformateo si hay ficheros sin-stagear
  (stash conflict) → o se pre-formatea el árbol, o se commitea con TODO stageado.
- **PENDIENTE operador**: QA visual del visor de Runs (http://localhost:8080), correr e2e Playwright
  con dev-server, decidir PR/merge a master de `plan/runs-visor-trabajo`. **Gotchas aprendidos**: (1) unit verde puede enmascarar regresión de
  streaming en vivo — SIEMPRE correr integración con contenedor real; (2) pre-commit auto-fixea
  (black/ruff/prettier) y rompe commits granulares con ficheros sin-stagear — pre-formatear el árbol
  (git add -A; pre-commit run; repetir) ANTES de los commits granulares; (3) black puede envolver una
  firma y huérfanar un `# noqa` → ponerlo en la línea del `def`.
