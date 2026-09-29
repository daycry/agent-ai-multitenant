---
name: usar-plugin-custom-agents-siempre
description: Orden del operador 2026-09-29 — implementar siempre con el plugin custom-agents (implementer/tdd/adversarial-review/qa); el ledger sigue siendo el plan del repo
metadata:
  node_type: memory
  type: feedback
  originSessionId: 21b9b7a2-9cad-4343-9a42-ea6bfde1b60c
  modified: 2026-09-29T15:38:06.970Z
---

Todo trabajo de implementación se hace con el método y los agentes del plugin
`custom-agents`: `implementer` o subagente de contexto fresco por tarea con la skill
`tdd`, `adversarial-review` (agente `reviewer`, lentes en paralelo) antes de dar un plan
por entregado, `qa` para E2E de UI, `documenter` al cierre, `debug-root-cause` al segundo
rojo. Configurado en `.claude/dev.json` (tdd + subagentes, sin worktree). Escrito en
CLAUDE.md §«Siempre con el plugin `custom-agents`».

**Why:** el 2026-09-29 el operador vio que yo implementaba a mano en la conversación
principal y pidió usar el sistema SDD del plugin, y después que constara en CLAUDE.md
para que sea permanente. Nada de lo implementado ese día había pasado revisión
independiente.

**How to apply:** no crear el `docs/roadmap/<fecha>-<slug>/tasks.md` del plugin: el
ledger es el `docs/roadmap/*.md` del plan (CLAUDE.md prohíbe estado duplicado). Las
piezas que exigen el ledger del plugin (`scope-check`, `task-brief`) van en modo sin
ledger. Adoptar el formato del plugin pediría ADR. Ver [[programa-harness-2026-09-17]].
