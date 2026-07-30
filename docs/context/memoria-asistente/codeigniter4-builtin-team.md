---
name: codeigniter4-builtin-team
description: "Equipo built-in CodeIgniter 4 (ex-WebScorpo): MERGEADO a master (PR #33, 2026-06-08), reworkeado."
metadata:
  node_type: memory
  type: project
  originSessionId: f7e54214-9978-4552-9197-70ecc3f15b3d
---

El operador pidió (2026-06-03) que el equipo demo **WebScorpo** (10 agentes, stack CodeIgniter 4)
pasara a ser **built-in del catálogo de fábrica**, renombrado **"CodeIgniter 4"** (`global_builtin`,
ADR 0030), purgando toda referencia a "webscorpo".

**Estado (2026-06-08): MERGEADO a master.** PR #33 (`plan/codeigniter-4-builtin-team`) añadió el
built-in CI4 (equipo + agentes + 8 KBs de catálogo); el demo WebScorpo se purgó (en #35/06.17 + el
propio #33). Tras mergear primero 06.18 (#34) y 06.17 (#35), **#33 se reworkeó antes de mergear**
porque chocaba:

- **Duplicaba el ADR 0055** (modelo de agente por defecto) que 06.17 ya metió en master → se
  descartó la implementación de CI4 (commit `f87ca62`) vía cherry-pick de los demás commits; los
  agentes CI4 NO pinean provider/model y heredan el default de master.
- Asignaba la **familia de tools `git-*` retirada por 06.18** (ADR 0049) → FK violation en
  `agent_tools`. Se quitó de los tool sets (`seeds/ci4_team.py`); git se hace vía `shell-exec`
  (mismo criterio que el follow-up de 06.17 para webscorpo).

8/8 de `tests/integration/test_seed_ci4_team.py` en verde tras el rework. CI4 no añade migraciones
(solo seeds). Para que aparezca en una DEV existente hay que re-correr los seeds
(`python -m api_server.seeds`) con el código de master. Ver [[estado-trabajo-en-curso]] y
[[model-per-agent-inheritance]].
