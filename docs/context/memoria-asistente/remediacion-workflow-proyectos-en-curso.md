---
name: remediacion-workflow-proyectos-en-curso
description: "Remediación del workflow de proyectos 2026-07-25 — las 56 tareas COMPLETAS y empujadas (3523b257); pending_human_validation; SIN desplegar"
metadata:
  node_type: memory
  type: project
  originSessionId: c24b547e-58f5-4ecf-a67d-6507fb095bad
  modified: 2026-07-26T20:12:40.325Z
---

Implementación de `docs/roadmap/remediacion-gestion-proyectos-2026-07-25.md` —
**COMPLETA**. Las 56 tareas de las 8 olas cerradas con test, en
`plan/runs-visor-trabajo`, empujada a origin hasta `3523b257`. El ledger
canónico es el propio plan (`status: pending_human_validation`); changelog en
`docs/07-changelog/remediacion-gestion-proyectos-2026-07-25.md`. ADR 0132
(replanificación en caliente) `accepted`.

**NO desplegado** — sigue en pie [[no-desbloquear-sin-verificacion]].

Suites al cierre: unit **2766**, agent-runtime **465**, frontend **387**,
security **73**, mypy limpio sobre **581** ficheros. Integración por bloques
contra el Postgres de compose. CI sigue muerto por facturación de la cuenta
`daycry` (lo arregla el operador en https://github.com/settings/billing).

**Why:** la tesis de la auditoría se confirmó tarea a tarea — no falla el
diseño, falla el **cableado del último tramo**. El patrón dominante era
«mecanismo entero, cero llamantes» o «dato producido que ninguna pantalla lee»:
evals (7 módulos, 7 tablas vacías), `record_shadow_eval` sin un solo llamante
desde el Plan 14, `subject_prompt_version` que nadie poblaba,
`compute_plan_progress`, `pr_url`, y las filas `eval_results`.

**How to apply:** lo que queda es humano y está al final del changelog — tests
humanos del plan, despliegue (workers:ci + api-server:manuals) + rescate de las
2 tareas congeladas y los 3 planes varados, siembra del dataset dorado
(curaduría: qué tareas cerradas son «buenas» no lo decide el sistema), prueba en
navegador del OAuth MCP (ADR 0127) y smoke del seccomp estricto en dev. Para
`completed` faltan, por protocolo, los tests humanos y el PR mergeado.

## Gotchas que dejó esta remediación

- **Un eval que siempre pasa es peor que no tener eval.** Pasar el
  `expected_output` del item como salida del sujeto hace que el juez compare la
  referencia consigo misma: 100 % de aciertos, siempre. Estuve a punto de
  enviarlo; hay un test dedicado que lo fija.
- **Leer el contrato del seam ANTES de escribir el adaptador**: inventé un
  `judge()` síncrono devolviendo tuplas cuando el Protocol era async y devolvía
  dataclasses.
- **black y ruff-format se pelean en bucle** cuando hay un comentario DENTRO de
  una cadena `select().where().order_by()`. Sacar el comentario fuera del
  paréntesis los hace converger.
- En integración, el DSN para `asyncpg` es **`migrations_pg_dsn`**, no
  `admin_database_url` (ése lleva `+asyncpg` y asyncpg lo rechaza).
- Un `fireEvent.change` sobre un `<select>` cuyas `<option>` aún no cargaron
  deja el valor vacío **en silencio**.
- **El stage sobrevive a un pre-commit fallido**: re-`git add -A` y verificar
  `git status --short` antes de re-comitear.
- `ruff --fix` mueve el `# noqa` si los argumentos van en varias líneas.
- `Badge` no tiene variante `destructive`; es `danger`.
- **AppArmor**: un `deny` gana a cualquier `allow` — hay que QUITAR el deny.
- **Una suite que siempre falla no es una suite**: los 4 rojos de
  `tests/security/` tapaban dos hallazgos reales.
- **El compose versionado trae solo INFRAESTRUCTURA**: api-server, workers y
  orchestrator los genera el instalador.
- **La suite del agent-runtime no está en `testpaths`** — invocarla a mano desde
  `docker/agent-runtimes/agent-runtime`.

## La lección más cara

Tres regresiones mías (`1540f5e6`, `b77591e2`, `239e042b`) tenían la MISMA
forma: generalizar una inferencia que solo valía en un caso — y en dos de ellas
**el test que escribí bendecía la generalización** en vez de cuestionarla. Antes
de dar por buena una premisa «si no hay X entonces Y», buscar el caso donde X
falta por diseño.

Corolario del recon: 9 de 10 premisas del plan salieron `partly`. Verificar
contra el código antes de implementar lo que el plan afirma; tres premisas
resultaron directamente falsas (ADR 0108 ya aceptado con «no unificar», seccomp
ya pinado por el instalador, retirar `search_code`/`apply_patch` habría
regresado tools de MCP).

Relacionado: [[auditoria-gestion-proyectos-2026-07-25]],
[[deliverables-en-docs-roadmap]], [[prioridad-codigo-limpio-mantenible]],
[[no-desbloquear-sin-verificacion]].
