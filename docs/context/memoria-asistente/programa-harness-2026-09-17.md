---
name: programa-harness-2026-09-17
description: Programa harness-00..06 y compat-modelos en la rama plan/harness-programa-2026-09-17; ADR 0168 y 0169 accepted; estado al 2026-09-29
metadata:
  node_type: memory
  type: project
  originSessionId: 21b9b7a2-9cad-4343-9a42-ea6bfde1b60c
  modified: 2026-09-29T16:25:00.853Z
---

Rama `plan/harness-programa-2026-09-17` (sobre la cadena marketplace → ui → instalador,
nada mergeado a master). Estado al 2026-09-29:

- **harness-00** `approved` (por el objetivo escrito del operador del 2026-09-29, «todo el
  roadmap implementado»): `task_h00_01` (ADR 0168 accepted) y `task_h00_02` (`claim_id` en
  `as_dict`) cerradas. La mitad «orchestrator sin dict a mano» se movió a `task_h01_08`.
- **compat-modelos-2026-09-29** `pending_human_validation`, 12/12: ADR 0169, modelos de 9B
  a Codex sin quinto proveedor. Prueba viva en CPU: gemma4:e4b y qwen3:8b `done`.
- **ui-reestructuracion** (fase activa): `task_ui_02` implementada y revisada, casilla
  ABIERTA hasta que corra su e2e contra el stack.
- Siete prod-* entregados pasaron a `pending_human_validation` con changelog.
- Revisión adversarial intento 1 hecha con el plugin; intento 2 lanzado.

**Why:** el operador pidió implementar todo el roadmap y soporte de modelos pequeños.

**How to apply:** siguiente trabajo en orden: cerrar revisión, e2e de task_ui_02, resto de
ui-reestructuracion, memoria-agentes, harness-01..06, prod-16. Todo con el plugin
([[usar-plugin-custom-agents-siempre]]). Stack de la plataforma parado salvo postgres,
redis y ollama (lo paré para los tests; el operador no pidió relanzar runs, ver
[[no-desbloquear-sin-verificacion]]). **Gotcha:** no mezclar árboles de test en una
invocación de pytest; los de integración necesitan WORKERS_BROKER_URL apuntando a la
Redis de tests o se cuelgan en send_task.
