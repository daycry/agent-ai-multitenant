---
name: no-desbloquear-sin-verificacion
description: "Orden del operador 2026-07-03: NO desbloquear/relanzar tareas del plan CI4 (ni nada) hasta que TODO el sistema esté verificado — quedan cosas por mirar. Anula el relanzamiento automático post-reset-de-cuota."
metadata:
  node_type: memory
  type: feedback
  originSessionId: 75127a11-d792-4ccf-aaf9-63b6eb2823b6
---

2026-07-03 (~12:00 local): tras dejar armado un monitor para relanzar las 5 tareas blocked del plan CI4 al resetearse la cuota de la suscripción Claude (11:40 UTC), el operador ordenó: «no desbloquees nada, hasta que no esté todo el sistema verificado, que aún quedan cosas por mirar».

**Why:** el operador quiere validar el sistema completo antes de gastar más cuota/runs; el desbloqueo automático le quita ese control.

**How to apply:** monitor de relanzamiento PARADO. No resetear tasks a backlog, no disparar promote_ready_plans, no relanzar ejecuciones — aunque la cuota se resetee — hasta OK explícito del operador. La observación pasiva (monitor de transiciones del Kanban, consultas SELECT) sí está bien. Relacionado: [[supervision-runs-autofix-plataforma]] (la delegación de autofix sigue viva para CÓDIGO, pero el relanzamiento de runs queda gated).
