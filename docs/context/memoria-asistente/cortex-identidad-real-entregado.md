---
name: cortex-identidad-real-entregado
description: '2026-07-06 plan "identidad real" del córtex ENTREGADO+DESPLEGADO en dev (8 commits, rama plan/runs-visor-trabajo); QA visual del operador pendiente; autonomía sigue OFF'
metadata:
  node_type: memory
  type: project
  originSessionId: 46819ab5-f853-4ca2-aea8-a56ed20f06f1
---

2026-07-06 — Plan **"Córtex: identidad real (self-model unificado)"** IMPLEMENTADO
con TDD y DESPLEGADO en dev (rama `plan/runs-visor-trabajo`, commits
`c2205db…b594776`, sin push/PR). Cierra los lazos "decorado" detectados por la
auditoría del 2026-07-06 (informe+plan en `docs/roadmap/cortex-identidad-real.md`,
changelog en `docs/07-changelog/cortex-identidad-real.md`):

- Self-context unificado (`cortex/self_context.py`) en chat y voz; afecto→tono
  y →reasoning_effort (±1 paso, `cortex/affect_policy.py`); traits Big-Five →
  estilo; baseline evolutivo conectado al motor PAD (caché Redis embebe el
  baseline, retrocompatible); surfacing de curiosidad (migración **0103**,
  estado `surfaced`) + endpoint pursuits + tarjeta "Lo que está aprendiendo";
  reflexión produce `owner_model` (relationship_model + memorias
  `kind='owner_model'`); `recall_frequency` real (contador `recall_count`,
  factor suelo 0.5, saturación 5).
- ADR 0075/0077/0078 anotados con estado de implementación.
- Arreglado de paso: el turno persistía `reasoning_effort=NULL`
  (`LLMAssistantModel` ahora lleva `reasoning_effort`/`provider_kind`).

**Why:** el operador pidió hacer "real" el córtex/memoria cognitiva (identidad
que gobierna conducta + emerge de la experiencia + self-model unificado);
aprobó el enfoque 2 de 3 en modo plan.

**How to apply:** pendiente QA visual del operador (chat córtex, Panel de Mente
con la tarjeta nueva, reflexión manual → relationship_model en /admin/cortex/identity).
Añadido después (commit `3a42d5b`, el operador no veía settings por UI): GET
/identity expone `relationship_model` + tarjeta "Lo que sabe de ti" (poll 10s),
y panel de Autonomía en el Panel de Mente con kill-switch + toggle de
`cortex.web_enabled` (PUT /autonomy ahora es update parcial — antes la web no
tenía setter ni UI). Login QA: demo@example.com en http://localhost:8080/admin.
`cortex.autonomy_enabled` sigue **OFF** (nada del plan lo enciende; decisión del
operador). Umbrales calibrables en `affect_policy.py`/`self_context.py`/
`forgetting.py` (constantes module-level). BD dev migrada a `0103`; imágenes
`api-server:manuals`+`workers:ci`(base manuals)+`admin-panel:manuals`
reconstruidas y stack recreado healthy. Relacionado: [[cortex-implementacion-autonoma]],
[[estado-trabajo-en-curso]].
