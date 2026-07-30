---
name: auditoria-runs-2026-07-02-remediacion
description: "Auditoría completa de runs/memoria/workers + remediación F0-F3 IMPLEMENTADA, COMMITEADA (07c91cc) y DESPLEGADA en dev; e2e final bloqueado por credencial claude_sdk caducada."
metadata:
  node_type: memory
  type: project
  originSessionId: 75127a11-d792-4ccf-aaf9-63b6eb2823b6
---

2026-07-03 (madrugada): auditoría forense de las 51 executions del tenant Demo + revisión de workflow/prompts, y remediación completa el mismo día. Informe canónico: `docs/roadmap/auditoria-runs-2026-07-02.md`. Commits `07c91cc` (remediación) + gotcha HOME en rama `plan/runs-visor-trabajo` (sin push).

**Implementado y desplegado (imágenes rebuild + compose recreate):** F0 infra /data (entrypoint self-heal con HOME=/tmp, fail-fast `workspace_unavailable`, stack_exec con APIError capturado, backup de binds, reaper de exited, git branch idempotente contra race TOCTOU de tasks hermanas); F1 (panel escaladas por estado del último run, ADR 0096 verdict vs escalación, memorizer con needs_human_review terminal, fix tokens=0 via `_harvest` multi-canal, `<finish status/>` para claude_sdk, criteria reales al reviewer, shell_exec sin git, `_REVIEW_RUN_SYSTEM`); F2 (destilador con provider del agente + fallback, llm_empty desglosado, 66 memorias-ruido purgadas soft-delete); F2b (PROGRESS/GUIDANCE sticky, aviso 80%, feedback 2000, budgets review 25it/1h, **max_tokens por-kind 500k/250k** — el default 100k cortaba runs sanos al armarse la contabilidad real —, tools en inglés re-seedeados).

**Validado en vivo:** worktrees montados RW, promotor DAG despachando, fail-fast y supersede funcionando, tokens contándose (102k en un run).

**Pendiente al cerrar:**

- **Credencial claude_sdk CADUCADA** («Not logged in · Please run /login») — bloquea toda convergencia; el operador debe renovar el oauth token del provider (id 019ee682-7fa9). Notificado por push.
- Run zombi 019f24a7-b61a (Auditar dependencias) `running` sin contenedor: se auto-resuelve por visibility-timeout/sweep ~04:06 → abortará por credencial → blocked.
- Tras re-login: relanzar «Aplicar cabeceras» (019f1399-34ec, blocked; SU TRABAJO ESTÁ en el worktree — el run 019f24cc completó SecurityHeaders.php antes del provider_error) y «Auditar dependencias» (019f1399-34f5) vía human-action `reassign_with_guidance`; luego fluyen las 4 backlog del plan CI4.
- Full integration suite nunca corrió entera de una vez (se mató a los ~35 min); las suites por-área pasan todas.

Relacionado: [[runs-no-convergen-causas-estructurales]], [[agent-runtime-convergencia-hardening]], [[estado-trabajo-en-curso]].
