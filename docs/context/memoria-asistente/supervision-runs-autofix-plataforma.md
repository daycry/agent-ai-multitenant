---
name: supervision-runs-autofix-plataforma
description: "Al supervisar runs, delegación del operador — diagnosticar cada fallo y corregir yo mismo las causas que sean CÓDIGO de la plataforma; relanzar con guidance si es del agente; escalar solo decisiones humanas."
metadata:
  node_type: memory
  type: feedback
  originSessionId: 75127a11-d792-4ccf-aaf9-63b6eb2823b6
---

2026-07-03: durante la supervisión del e2e del plan CI4, el operador delegó explícitamente: «las que vayan fallando, ves revisando el motivo e intenta corregirlo en caso de que sea de código de la aplicación».

**Why:** el operador quiere convergencia autónoma; los fallos de plataforma no deben esperarle (esa noche se corrigieron así la race de provisión de worktrees, el HOME del entrypoint y el budget de tokens).

**How to apply:** en cada task blocked/failed → diagnosticar (abort_code + steps_log + logs); si la causa es código de la plataforma → fix TDD + rebuild/redeploy + relanzar task; si es comportamiento del agente/tarea → reassign_with_guidance específica; escalar al operador solo lo que exige decisión humana (criterios imposibles, coste, producto).

**Push autorizado (2026-07-03):** «cuando des por finalizadas las tareas, haz los commits y push en la rama» — al cerrar trabajo, commit + `git push origin <rama>` (sustituye la convención previa de "sin push"; abrir PR sigue siendo decisión del operador). Relacionado: [[auditoria-runs-2026-07-02-remediacion]].
