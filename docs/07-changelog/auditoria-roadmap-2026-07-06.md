---
title: Auditoría y corrección de estados desactualizados en docs/roadmap
date: 2026-07-06
phase: auditoria-roadmap-2026-07-06
status: completado
docs_language: es
related: []
---

# Auditoría y corrección de estados desactualizados en docs/roadmap — 2026-07-06

## Resumen

El operador sospechaba estados desactualizados en `docs/roadmap/`. Se auditaron los 85
ficheros (extracción de frontmatter + 3 agentes en paralelo cruzando cada `status` contra
`git log` y el código real) y se corrigieron los hallazgos confirmados. 24 de 85 ficheros
tenían desactualización real.

## Hallazgos y correcciones

### Track córtex (7 ficheros) — el más grave

`cortex-fases.md`, `cortex-system-owner.md` y `cortex-f1`…`cortex-f5` seguían con banner
"🔒 GATED — NO IMPLEMENTAR" y `status: pending_approval`/`in_progress`, pese a que las 5 fases
están implementadas y desplegadas desde el 2026-06-24 (migraciones 0092-0095+0103, routers,
workers en `beat_schedule.py`, 45 ficheros de test). Se escribieron los 6 planes de fase en un
único commit (`cf8f7cd`) y nunca se volvieron a tocar. Corregido: `status: pending_human_validation`
en los 7, banners reemplazados por notas "IMPLEMENTADO Y DESPLEGADO" con evidencia (rutas de
código, commits, desviaciones reales vs. diseño). Los ADR 0075-0078 asociados siguen en
`proposed` — pendiente de promoción formal, señalado pero no forzado en esta pasada.

### Track prod-XX (18 ficheros) — 6 desactualizados en dos patrones

- **"Trabajo fantasma"** (`prod-03`, `prod-04`): partes de su alcance ya estaban en `master`
  (citadas explícitamente en commits de otra iniciativa) pero el checkbox seguía en `[ ]` sin
  nota. Corregido: `task_prod03_01` marcada `[x]` con evidencia; `task_prod03_12` anotada como
  parcial (solo `post_tool` cableado); `task_prod_04_01` anotada como parcial (fix del bug ya en
  código, falta el smoke test dedicado).
- **"in_progress" congelado** (`prod-06`, `prod-17`, `prod-18`): los tres se pararon el
  2026-06-26 tras fusionar sus PRs (#55, #56, #57) y el trabajo que los cerraría migró a un
  track paralelo (ADR 0086-0095, `refactor-pipeline-ejecucion-review.md`) sin cruce de vuelta.
  Corregido: `status: pending_human_validation` en los 3 + nota cruzando el track paralelo;
  fixed la inconsistencia interna Cabecera/frontmatter de `prod-06`.
- **Caso mixto** (`prod-12`): el cuerpo tenía una nota fechada al día pero el frontmatter no.
  Corregido: `status: in_progress`, `started_at: 2026-06-30`.

### Planes misceláneos (5 con hallazgo)

- `runs-visor-trabajo.md`: feature completo y desplegado (da nombre a la rama actual), pero
  `pending_approval` con 0% checkboxes. Corregido a `pending_human_validation`.
- `codeigniter-4-builtin-team.md`: PR #33 fusionado a `master` el 2026-06-08, nunca se subió el
  status. Corregido a `completed`.
- `ciclo-vida-planes-fixes.md`: T5/T8/T9/T10 verificadas y marcadas `[x]` (estaban `[ ]` pese a
  una nota interna que las daba por hechas); T1 verificada como **NO hecha** pese a que la misma
  nota interna la daba por hecha (`plan_progress.py` sigue con `Literal` divergente) — corregido
  también el `status` a `in_progress`.
- `plan-unificacion-provider-id.md`: Fases 1-3 verificadas y marcadas `[x]` en su mayoría
  (validate_model_config, herencia, `ProviderModelSelects`, `persona.ts`, borrado de
  `DefaultModelSection`); Fase 4 confirmada sin empezar (`GET /agents/model-options` sigue vivo).
- `refactor-pipeline-ejecucion-review.md`: spec `pending_approval` cuyo cuerpo documentaba
  implementación 100% completa con commits verificables. Corregido a `pending_human_validation`.
- `fixes-pesados-auditoria.md`: Fix 1 y Fix 3 implementados verbatim (commits `acc67e6`,
  `1579832`); Fix 2 redimensionado (ver `ciclo-vida-planes-fixes.md` T6). Corregido a `completed`
  con nota por sección.

### Documento maestro

`EXECUTION-SEQUENCE.md` (última actualización 2026-05-29) afirmaba que las fases 07-16 eran
"nuevas a implementar" cuando ya estaban todas `completed`/`pending_human_validation`. Marcado
`status: obsolete` con banner explicando por qué no usarlo para decidir qué falta.

## Ficheros NO tocados (verificados como correctos)

12 de 18 `prod-XX`, y la mayoría de los 18 misceláneos (los 5 `pending_human_validation` sin
merge a `master` estaban honestamente reportados: 100% checkboxes, consistentes con "esperando
validación humana"). La secuencia numerada 00-16 no viola la regla de "una fase `in_progress`"
dentro de sí misma.

## Nota de gobernanza

`prod-15-gobernanza-roadmap-docs.md` — el plan creado justamente para sincerar el roadmap — sigue
al 0% sin empezar. Esta auditoría cubre una parte de su alcance de forma ad-hoc, pero no lo
sustituye.
