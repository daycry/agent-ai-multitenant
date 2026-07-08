---
title: Hallazgos pendientes de implementar (QA e2e + refactor 2026-07-07/08)
date: 2026-07-08
status: pending_approval
owner: operador (jmano)
branch: plan/runs-visor-trabajo
---

# Hallazgos pendientes — backlog de implementación

Hallazgos destapados durante el refactor por partes, la habilitación de mypy-total y —
sobre todo — el **QA e2e en vivo del plan CI4** (2026-07-07/08). Todo lo crítico de ese QA
ya quedó corregido en el momento (regresión A5 `faf2c78`, página `/admin/review/active`);
esto es lo que queda para implementar en otra tanda, ordenado por prioridad. Método al
implementarlos: TDD por hallazgo, commit atómico, sin big-bang.

## P1 — Fricciones reales vistas en el QA en vivo

### 1. Carrera lock A6 ↔ evento diferido (despacho de review perdido)

**Visto en vivo:** el implementador publica el evento `in_review` DENTRO de la sección con
run-lock (el publish diferido de prod-18 ocurre antes de soltar el lock en
`tasks/run_cycle._run_execution`). El orchestrator del mismo host despachó el review en
<10 ms y el worker lo recibió con el lock aún vivo → `run_lock_held_skip`
(`concurrent_run_locked`) → despacho descartado. La pasada (b) del reconciler lo re-anunció
a los ~6 minutos (settle 5 min + cadencia 90 s) — se auto-curó, pero son ~6 min de latencia
evitables en CADA ciclo review que pierda la carrera.

**Opciones (elegir una):**

- (a) Publicar el evento diferido DESPUÉS de soltar el lock: `conduct_execution` devuelve el
  evento pendiente y el publish se hace en el caller (`_run_execution`) tras el release.
  Mantiene el orden commit→publish de prod-18 y elimina la carrera de raíz. Preferida.
- (b) Retry con countdown (10-30 s, 1-2 intentos) del `run_execution` cuando el skip es
  `concurrent_run_locked` y `request.review=True`. Más simple, deja el reconciler de red.

### 2. Un plan `blocked` no se auto-revierte cuando su causa desaparece

**Visto en vivo:** el escalado transitivo (c3/prod-06 A1) bloqueó el plan CI4 correctamente
cuando su única vía de avance era una tarea bloqueada; al desbloquear la TAREA, el plan se
quedó `blocked` y el beat de promoción (que filtra `in_progress`) dejó una tarea `ready`
parada — hizo falta un segundo click humano a nivel de plan.

**Fix propuesto:** al desbloquear/reintentar una tarea (`apply_task_retry` /
`POST /tasks/{id}/human-action`), si su plan está `blocked` y la foto de tareas ya permite
avance, revertir plan→`in_progress` en la misma transacción (misma lógica inversa de
`transition_to_blocked`). Alternativa/red: una pasada del reconciler que re-evalúe planes
`blocked` cuyo snapshot ya no justifica el bloqueo.

**Reencuadre (2026-07-08, reconciliación de roadmap):** el fix propuesto YA existe en parte —
la acción `retry` de `POST /tasks/{id}/human-action` (`routers/task_lifecycle.py:175`, T7c)
reactiva el plan `blocked→in_progress` en la misma operación, y su test
(`test_retry_unsticks_blocked_task_and_reactivates_plan`) está verde. Lo que queda de este
hallazgo: (a) **investigar por qué en el QA el plan no revirtió** — probablemente el desbloqueo
del operador fue por otra vía (botón de la tarjeta/Kanban ≠ human-action retry) o el plan se
bloqueó DESPUÉS del desbloqueo de la tarea; (b) cubrir esas otras vías (que cualquier desbloqueo
de tarea re-evalúe el plan) o añadir la pasada del reconciler como red. El alcance real es menor
que el enunciado original.

### 3. Botón «Desbloquear plan» invisible en la superficie natural

**Visto en vivo:** el botón solo existe en `/admin/plans/{id}/escalated` (tarjeta "Tareas
escaladas", cuyo subtítulo además habla de `awaiting_human`); el operador estaba en el
detalle del plan y no lo encontró.

**Fix propuesto:** ofrecer el botón (mismo mutation `POST /plans/{id}/unblock`) también en
la cabecera del detalle del plan cuando `status=blocked` y en la tarjeta del board de
planes. Renombrar/ampliar el subtítulo del enlace a escalaciones.

### 4. App-preview del review-runtime: sin UI de configuración y placeholder engañoso

**Visto en vivo:** `/api/review/{id}/app/` devolvió `Name or service not known` porque el
proyecto no tiene imagen configurada y el autostart lanzó el placeholder `alpine:3.20`,
que sale con exit 0 al instante. No existe UI para configurar la imagen.

**Fixes propuestos (dos piezas):**

- (a) Exponer `repository_config.review_image` (+ `review_main_port`) en el formulario de
  ajustes del proyecto, con ayuda inline: imagen auto-servible construida por la CI del
  proyecto (la plataforma solo la referencia — ADR 0063).
- (b) Sin imagen real configurada: NO lanzar el placeholder. Sesión sin contenedor +
  mensaje honesto en la SPA y en el proxy («este proyecto no tiene app-preview
  configurada») en lugar del error críptico de DNS. `resolve_review_main_image` puede
  devolver `None` y el spawn saltarse el contenedor (la fila + URLs ya sobreviven así).

### 5. pcov en el template `php-phpunit`

**Visto en vivo (dos veces):** PHPUnit sale con exit 1 pese a suite verde por el warning
«No code coverage driver available» (phpunit.xml con bloque `<coverage>` y `failOnWarning`).
El agente se auto-corrigió con `--no-coverage`, pero quema turnos y confunde al reviewer
(criterios que exigen exit 0). Añadir `pcov` al Dockerfile del template y re-publicar.

## P2 — Deuda estructural anotada (del refactor y la revisión)

### 6. Estado tipado del runtime (H6-real)

El estado por-run vive repartido entre `AgentState` (TypedDict con claves string
compartidas por `graph.py` y `providers.py`) y la instancia `_AgentLoop` (read_targets,
has_produced, safeguard_stats…). Mitigado con el comentario-contrato en `state.py`; la
solución real es una dataclass/constantes de clave que hagan imposible el rename silencioso.

### 7. Fusión de los dos canales de veredicto (decisión de producto)

El run reviewer cierra con tag `<verdict>` en prosa (parseado por el worker) y la
self-review interna usa la tool `submit_verdict`. Coherentes hoy (fuente única
`review_contract.py` + test de contrato cruzado), pero son dos formatos para el mismo
concepto. Unificarlos requiere decidir el canal ganador y migrar el otro.

### 8. A4 — e2e del ciclo autónomo como test automático + subir el floor de cobertura

La validación de hoy fue manual (QA humano guiado). Falta el e2e automatizado del ciclo
completo (deuda aceptada de la auditoría) y seguir subiendo `--cov-fail-under` (hoy 30;
objetivo CLAUDE.md ≥70 en dominio crítico) por tramos con tests nuevos.

### 9. Ronda frontend por partes

El admin-panel tiene los mismos hotspots que tenía el backend: detalle de plan (~1400
líneas), model-prices (~1300), mcp-servers (~1100), knowledge-bases (~1050)… Mismo método:
caracterizar → extraer componentes/hooks → verde → commit. El QA de hoy demostró además
que hay páginas enteras sin QA visual (escalated, review/active).

### 10. Menores aceptados/documentados

- Dedup pendiente de la triple resolución de worktree (`execution._provision_worktree`,
  `execution._resolve_review_worktree`, `tasks.review_runtime_task._resolve_review_
worktree_host_path`) — semánticas distintas, unificar con cuidado.
- `_decide_messages` interpola título/descripción de la task sin fencing — aceptado
  (posición de menor privilegio; tocarlo arriesga la convergencia calibrada). Documentado
  aquí como decisión consciente.
- `_completion_signals` colapsa a all-False para claude_sdk → la detección de truncado
  (F32) solo protege a los providers HTTP. Mejorable en `shared-llm`.
- P8 (unificar `db/domain.py` vs `db/models.py`): NO abordar — 273 ficheros importadores,
  beneficio moderado (registrado en el plan de refactorización).

## Referencias

- Plan de refactorización y hallazgos H1-H6 (todo implementado):
  `docs/roadmap/refactorizacion-por-partes-2026-07-07.md`.
- Regresión A5 cazada en el QA: commit `faf2c78`; fix página review/active: commit
  posterior en la misma rama; gotcha de build `orchestrator-workers-base-image-arg.md`.
- Auditoría origen de la remediación: `docs/roadmap/auditoria-prod-implementados-2026-07-06.md`.
