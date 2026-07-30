---
name: runs-no-convergen-causas-estructurales
description: "Por qué los runs de implementación no llegan a `done` — estructural (no el modelo). R1/R5/R6 IMPLEMENTADOS+VALIDADOS (ADR 0090); plan de 5 tracks aprobado (SDK, asignación por rol, persistencia, backstop)."
metadata:
  node_type: memory
  type: project
  originSessionId: 9b6ffa32-bda3-49a0-a5ed-708c0fca5208
---

2026-06-28: monitorización en vivo de la tarea JWT (`019efe40-…`, proyecto demo/api-ci) con sonnet
Y opus probó que **el modelo NO era la causa** de la no-convergencia. El pipeline está sano
(self-review autoritativo detecta defectos reales, feedback llega vía [[refactor-self-review-autoritativo]],
B3 escala preservando+commiteando, finalize OK). Los bloqueantes son **estructurales**:

- **R6 (bloqueante real):** el worktree es por-tarea y **persiste entre ejecuciones** (prod-18/ADR 0085);
  cada run escalado deja ficheros commiteados → el siguiente hereda **duplicados en conflicto**
  (p.ej. `JwtFilter.php`+`JwtAuthFilter.php`, `JwtService.php` en `Libraries/` y `Services/`, dos
  migraciones de refresh_tokens). El agente tiene **CERO capacidad de borrar**: `rm` y `git rm` por
  shell → `command not allowed` (`allowed_commands` vacío); `apply_patch` → `unknown tool` (referenciado
  pero NO cableado en el ToolRegistry); no existe `delete_file`. Opus probó las 3 vías, todas fallaron
  → escaló por max_iterations. **Fix:** tool `delete_file` path-jailed a /workspace + decidir base
  limpia por intento (ADR 0085 addendum) + cablear/retirar `apply_patch`.
- **R5:** `conduct_execution` (workers/execution.py ~945-1010) valida task↔tenant pero NO `task.status`
  antes de lanzar → una re-entrega de Celery (`acks_late`, p.ej. tras reiniciar el worker) lanza
  runtime sobre una tarea ya `blocked`/`cancelled` ("docker fantasma"). Fix: guard de elegibilidad
  (status lanzable) antes de crear execution/lanzar contenedor.
- **R1:** un agent-runtime que muere al arrancar (stream Redis 0 eventos, contenedor `--rm` desaparece)
  deja al worker colgado en bucle `GET /containers/<id>/json → 404` (visto en docker-socket-proxy) →
  ForkPoolWorker bloqueado (beat+reconciler muertos) → run clavado en `running`, kanban→blocked no
  surte efecto. Fix: tratar 404/NotFound como terminal + deadline al primer evento. Recuperación
  manual: `docker restart agentic-platform-workers-1` (cuidado: re-entrega el run).
- **R2** (ya workaroundeado): herencia de modelo ADR 0065 es agente→equipo→proyecto→plataforma; el
  EQUIPO pineaba sonnet-4-6/high y tapaba el opus-4-8/medium del PROYECTO. Workaround: poner el equipo
  en opus. Decisión pendiente: ¿proyecto debe override equipo? Ver [[model-per-agent-inheritance]].
- **R3:** la tarea de implementar estaba asignada al agente "Project Manager" como implementer Y reviewer.

**Estado 2026-06-29:** R1/R5/R6 **commiteados** (`8d9daee`) + **validados en vivo** (run `019f127f`,
demo opus): R5 saltó re-entrega sobre `blocked`, run finalizó limpio 744s sin colgar (R1), escalado
preservó+commiteó. Documentado en **ADR 0090** (commit `8aa4801`). R2 ya workaroundeado (equipo=opus).

El demo destapó 2 cosas más → **plan de 5 tracks APROBADO** (`~/.claude/plans/necesito-que-hagas-una-federated-book.md`):

- **Track 1 (SDK):** el `claude_sdk` es agéntico nativo pero lo metemos en nuestro loop con allowlist
  de shell vacío (muros git/rm), budgets bajos y `max_turns` muerto → relajar para `kind==claude_sdk`
  (incremental; seguridad-dura del contenedor intocable). ADR addendum 0021.
- **Track 2 (R3, asignación):** el plan YA lleva `role` por tarea (`planning_llm`) + existe
  `team_role_agents` (rol→agente), pero `sync_to_kanban._build_task` lo descarta → el dispatch elige
  por LOAD_BALANCED (cayó en el PM). Fix: cablear role→`assigned_agent_id`, dispatch respeta preset,
  reviewer≠implementer.
- **Track 3 (persistencia):** restart/down(sin -v)/up NO pierde nada; el borrado del bare repo fue
  `down -v` o reset de Docker Desktop/WSL (`/data/agent-platform` es bind a la VM WSL2). Fix: backup+
  guardas+docs.
- **Track 4 (read-churn):** tras self-review fallido opus releyó 2-3 ficheros it.17→50 sin escribir,
  ignorando el nudge blando → backstop duro `research_streak>=10 AND (has_produced OR review_retries>0)`
  → escalar `RESEARCH_EXHAUSTED` (addendum D4 a ADR 0089). Red provider-agnóstica.

Relacionado: [[auditoria-runs-remediacion]], [[agent-runtime-convergencia-hardening]],
[[refactor-self-review-autoritativo]].
