---
name: reviewer-ciego-convergencia-fix
description: ADR 0095 — el AI reviewer estaba ciego (sin worktree) y no convergía (abort max_iterations + bucle infinito); fix = worktree read-only + safeguards reviewer-aware + cap de escalado. Implementado+desplegado+verificado e2e.
metadata:
  node_type: memory
  type: project
  originSessionId: 9b6ffa32-bda3-49a0-a5ed-708c0fca5208
---

**Reviewer ciego + no-convergencia** (ADR 0095, rama `plan/runs-visor-trabajo`, 2026-06-30) — un
run del AI Code Reviewer (claude_sdk/opus) abortaba con `max_iterations_exceeded` y dejaba la tarea
en bucle `in_review`. Evidencia (steps_log de `019f181e`): 50 iter, submit_result=0,
`read_file composer.json → "not a file"` ×6. **Causa DOBLE y estructural**:

1. **Ciego**: un run de review **no montaba worktree** (`execution.py:1063`, diseño ADR 0085) →
   `/workspace` tmpfs vacío; solo `review_context` (prosa del implementador + test_report), nunca el
   código. Su prompt le ordena inspeccionar código → bucle de `read_file`/`list_files` sin encontrar nada.
2. **No converge**: claude_sdk FINISH = turno de prosa sin tool-call; el modelo no para de tool-callear.
   Los safeguards (`_research_exhausted`, `_research_nudge`) gateados a `has_produced` → no aplican a un
   reviewer (estéril). Agota max_iterations → `STATUS_ABORTED` → `_apply_review_verdict` `return None`
   (no transiciona) → reconciler re-despacha cada ~5min **sin cap → bucle infinito**.

**Fix (3 frentes, refina ADR 0085/0089), commits f15cf95…3b7e923:**

- **D1 (Prong A)** `execution.py`/`isolation.py`/`container.py`: el reviewer monta el worktree del
  implementador (`worktrees/{task_id}`, ya en disco) en **READ-ONLY** (`_resolve_review_worktree` sin git
  ops; `build_hardened_run_kwargs(workspace_read_only=)`; `ContainerSpec.workspace_read_only`). El commit
  sigue gateado a `worktree_inputs` (solo implementador) → el reviewer no commitea.
- **D2 (Prong B1)** `graph.py`/`__main__.py`: `is_review` cableado por `AgentDeps` (desde `spec.review`).
  El nudge le dice "emite tu `<verdict>` ya" (no write_file); `_research_exhausted` SÍ corta un review
  estéril; un safeguard que dispara escala a `needs_human_review` (no abort).
- **D3 (Prong B2)** `execution.py` `_apply_review_verdict`: el branch infra-fallido (status!=done sin
  verdict) bumpea `retry_count` y al llegar a `max_retries` escala la TAREA a **`blocked`** (audit
  reason `review_inconclusive`) en vez de `return None` infinito. Al salir de in_review, el reconciler
  para solo (no hace falta tocar maintenance.py). OJO: `TaskStatus` NO tiene `needs_human_review` (ese es
  de ExecutionStatus); para la tarea el escalado humano es `blocked` (igual que reject-exhaustion).

**Verificado e2e** (run real `019f184f`, tras reset de la tarea a in_review): `read_file composer.json
→ ok=true`, **13 iter** (vs 50), status **done** sin abort, `<verdict>approve</verdict>` fundamentado
("composer.json ahora es verificable…") → tarea **done**. Build: agent-runtime:v1 + workers:ci
**WITH_CLAUDE=1** (workers FROM api-server:ci; api_server/shared NO cambiaron, no se reconstruye). El
reviewer corre en un agent-runtime con nombre Docker aleatorio (no "agent-runtime") → no filtrar por
nombre. Relacionado: [[runs-no-convergen-causas-estructurales]], [[registry-egress-feature]],
[[auditoria-runs-remediacion]] (C1 reviewer a ciegas=F51).
