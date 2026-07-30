---
name: remediacion-auditoria-prod-implementados
description: "Remediación de la auditoría adversarial de planes prod-XX (2026-07-06); 2 olas, 18 hallazgos con TDD; M4 diferido; sin desplegar."
metadata:
  node_type: memory
  type: project
  originSessionId: 46819ab5-f853-4ca2-aea8-a56ed20f06f1
---

Auditoría adversarial de los planes prod-XX implementados (informe en
`docs/roadmap/auditoria-prod-implementados-2026-07-06.md`). Remediado con TDD en la rama
**`plan/runs-visor-trabajo`**, commit por hallazgo, **sin desplegar** (rebuild api-server+workers
pendiente de ventana del operador — rebootar workers con runs en vuelo dispara re-entrega ~7h).

**1ª ola (2026-07-06, CRÍTICOS+ALTOS):** C1 (imagen con migraciones, f5ae375), A1 (convergencia DAG,
18eea7d), A2/A3 (budgets+timeouts Celery, 80f5f18), A5 (doble self-review, 42ac465), A6 (lock worktree,
363245f), A7 (backup auto-inclusión, 5a63357), A8-1ª (categorías gate, 2106ed3), A9/A10 (compose,
f5ae375), soft-timeout handler (99eb017).

**2ª ola (2026-07-07, medios/bajos):** M8b (cd329c7), CANCELAWAIT (de48bc4), M2 seal_terminal_execution
(7939935), M9 floor cobertura 19→30 (fb1ca91), M5 cap reconciler reviews (1d9b830), M1
container_launched_at+migr.0104 (c51dc57), OFFSITE backup al beat (847a4bd), HARDDEP 409 (ada289d),
A8b preset development por defecto + **ADR 0104** (3bd1b3a).

**M4** (no-atomicidad DB↔git: crash entre finalize y push → PR incompleto) IMPLEMENTADO (5a9e038):
4ª pasada del reconciler `_reconcile_unpushed_worktrees` backfillea el worktree superviviente,
idempotente por trailer Execution-Id + lock A6, age-gated 5min.
**No abordados (aceptados):** M8 (exfil POST, ADR 0094), A4 (e2e, deuda cobertura), LOWBUNDLE/M3
(cosméticos/ya cubierto).

Head de migraciones ahora = **0104_exec_container_launched**. Pendiente del operador: decidir ventana de
deploy. Ver [[implementacion-auditoria-2026-07-04]] y [[auditoria-runs-2026-07-02-remediacion]].
