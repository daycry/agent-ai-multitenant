---
title: Hallazgos pendientes de implementar (QA e2e + refactor 2026-07-07/08)
date: 2026-07-08
status: in_progress
owner: operador (jmano)
branch: plan/runs-visor-trabajo
---

> **Estado (2026-07-08, tanda «adelante con la cola»)**: aprobado por el operador e implementado
> con TDD + commit atómico por hallazgo: **#1** (`d1d2f41` — publish tras soltar el run-lock),
> **#2** (`50f4e5d` — `transition_from_blocked` + re-evaluación en TODAS las vías humanas y el
> PUT del Kanban), **#3** (`c6e8d99` — botón Desbloquear en detalle + board), **#4** (`e983ada` —
> app-preview configurable, sin placeholder, 409 honesto), **#5** (`b2dd85f` — pcov en
> php-phpunit Y php-pest, imágenes reconstruidas), **#6** (`e661201` — contrato de claves de
> AgentState ejecutable). Además #8-parcial y #9-parcial: infra de render-tests jsdom + tests
> B2/B3/C1/C2/D1/T11 + i18n (`e1ff76c`) — runs-visor y ciclo-vida quedaron `completed`.
> **Siguen pendientes**: #7 (fusión canales verdict — decisión de producto del operador), #8
> (e2e del ciclo autónomo con Docker real) y el grueso de #9 (refactor por partes del frontend)
> y #10 (menores).
>
> **Estado (2026-07-09, tanda «analiza e implementa los hallazgos»)**: 8 de 9 hallazgos
> abordados con TDD + commit atómico:
>
> - **#2** (`c55597a`) — planes `blocked` se auto-revierten: 3 vías huérfanas
>   (delete/deps-only/free-task) reutilizan `reactivate_plan_if_unstuck` + red del reconciler
>   `_reconcile_unblocked_plans` (espejo de `_reconcile_complete_plans`, sin ping-pong).
> - **#6** (`9f95a9fc`) — PASO 0: los tests del agent-runtime corren en CI (nuevo step + meta-test);
>   PASO 1: la clave inyectada `written_files` vive tipada en `ReviewState` y el contrato deriva de
>   la jerarquía de TypedDicts. Cascada de firmas a ReviewState = polish diferido.
> - **#7** (`46655724`) — ADR 0108 `proposed` con las 3 opciones (recomienda C, status quo
>   documentado). **Decisión del operador.**
> - **#8** (`e3954baa`) — parcial: unit puro de `detect_outliers` (medido 31.2%→31.6%), ratchet
>   `--cov-fail-under` 30→31 + meta-test. **Pendiente: el e2e automatizado del ciclo autónomo
>   (Docker-real)** — el mapa dejó el diseño completo (seed→dispatch→run scripted→review→done con
>   `tests/e2e/test_autonomous_cycle.py`, marker `@pytest.mark.e2e`+requires_docker); siguiente tramo.
> - **#10c** (`5fd17cc1`) — la detección de truncado F32 protege también a `claude_sdk`:
>   `CompletionResponse.stop_reason` cosechado del SDK; guard extendido a la rama prose-FINISH.
> - **#10e** (`944085a`) — schema-gap del córtex: `schema_fn` inyectable, el córtex pasa
>   `cortex_tool_schemas` (verificado en vivo: web_search recibe args correctos).
> - **#10a** (`0642211c`) — `worktree_coordinates`, fuente única de coordenadas de worktree en 5
>   sitios (provisión ×2, review-runtime ×2, back-fill), con golden test. **Remate 2026-07-10**
>   (auditoría I-2/I-3): la resolución read-only del review (`_resolve_review_worktree`, el 6º
>   sitio, DooD-crítico) unificada vía la primitiva `worktree_layout`; golden test endurecido
>   (strings literales + guarda anti-`resolve()` efectiva en CI + contrato de fuente única
>   sobre `execution.py`).
> - **#9** (refactor frontend) — **DEFERIDO** como tramo dedicado: es mecánico sobre código que
>   funciona y con riesgo de big-bang; el mapa dejó el plan (extraer `plan-spec-types.ts` + 8-9
>   secciones del detalle de plan de 1703 líneas, model-prices/mcp-servers/knowledge-bases;
>   caracterizar jsdom → extraer → verde). Los ficheros extraídos deben llamarse `*-section.tsx`
>   (nunca `page.tsx`/`layout.tsx`/`route.ts` dentro de `app/**`) y llevar `'use client'`.
>
> **Estado (2026-07-09, tanda «implementa lo diferido»)**:
>
> - **#8 (e2e ciclo autónomo) — HECHO** (`2a0d5496`): `tests/integration/test_autonomous_cycle.py`
>   corre el ciclo COMPLETO sobre Docker real (implementador→in_review→reviewer approve→done, y
>   reject→backlog) con modelos scripted. VERDE local (2 tests, ~32 s), NO skippeado. Va en
>   integration/ (no e2e/) porque reutiliza el harness probado del smoke; e2e/ es solo el install.
> - **#9 (refactor frontend) — detalle de plan HECHO** (`415a2578`, `618e6844`): el peor hotspot
>   (1703 líneas) modularizado en 3 ficheros — `plan-spec-types.ts` (interfaces + STATUS\_\* +
>   formatCostRange), `plan-spec-sections.tsx` (8 secciones presentacionales puras),
>   `plan-interactive-sections.tsx` (7 secciones con hooks) — y `page.tsx` queda en **161 líneas**
>   de composición. Verbatim, testids intactos; tsc 0 + vitest 201/201 + `next build` OK. **RESTA
>   (tramo)**: model-prices (1311), mcp-servers (1105), knowledge-bases (1042) — NO tienen red de
>   tests, así que necesitan caracterización jsdom ANTES de extraer; mismo patrón probado aquí.

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

### 2. Un plan `blocked` no se auto-revierte cuando su causa desaparece — ✅ RESUELTO 2026-07-09 (`c55597a`)

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

### 11. El rechazo humano de un plan no dispara rework — ✅ RESUELTO 2026-07-08 (ADR 0107)

**Visto en vivo (validación del plan CI4):** el operador rechazó el plan con un motivo
concreto y accionable (filtro Content-Type global → acotar a api/v1). El sistema persiste
`rejection_reason` en la sesión y transiciona el plan a `rejected`… y ahí muere: **nadie
consume el motivo** (verificado: 0 consumidores fuera de la capa de persistencia/UI), las
tareas siguen `done` y no se genera trabajo correctivo. El validador tiene que crear a mano
un plan nuevo en el chat de planning.

**Resolución (diseño del operador, ADR 0107 — commits `6f01531`, `71fb169`, `0eeafea`):**
rework en el MISMO plan. `POST /plans/{id}/generate-corrections` convierte el
`rejection_reason` en tareas correctivas (`origin: correction`, ids `fix-*`) dentro de
`specification.tasks` + entrada `proposed` en `specification.corrections`;
`POST /plans/{id}/accept-corrections` materializa la selección al Kanban y reactiva el plan
por la arista nueva `rejected → in_progress` en la MISMA transacción (anti-rebote del
reconciler garantizado). La UI del detalle del plan gana la tarjeta «Correcciones del
rechazo» (motivo + generar + checkboxes + aceptar) y el badge «corrección». Se descartaron
las opciones (a) plan correctivo separado (pierde rama/trazabilidad) y (b/c) — ver ADR.

## P2 — Deuda estructural anotada (del refactor y la revisión)

### 6. Estado tipado del runtime (H6-real) — ✅ RESUELTO 2026-07-09 (`9f95a9fc`)

El estado por-run vive repartido entre `AgentState` (TypedDict con claves string
compartidas por `graph.py` y `providers.py`) y la instancia `_AgentLoop` (read_targets,
has_produced, safeguard_stats…). Mitigado con el comentario-contrato en `state.py`; la
solución real es una dataclass/constantes de clave que hagan imposible el rename silencioso.

### 7. Fusión de los dos canales de veredicto (decisión de producto) — ⏳ ADR 0108 `proposed` 2026-07-09 (`46655724`)

El run reviewer cierra con tag `<verdict>` en prosa (parseado por el worker) y la
self-review interna usa la tool `submit_verdict`. Coherentes hoy (fuente única
`review_contract.py` + test de contrato cruzado), pero son dos formatos para el mismo
concepto. Unificarlos requiere decidir el canal ganador y migrar el otro.

### 8. A4 — e2e del ciclo autónomo como test automático + subir el floor de cobertura — 🟡 PARCIAL 2026-07-09 (`e3954baa`: ratchet 30→31; e2e Docker-real pendiente)

La validación de hoy fue manual (QA humano guiado). Falta el e2e automatizado del ciclo
completo (deuda aceptada de la auditoría) y seguir subiendo `--cov-fail-under` (hoy 30;
objetivo CLAUDE.md ≥70 en dominio crítico) por tramos con tests nuevos.

### 9. Ronda frontend por partes — ⏳ DEFERIDO (tramo dedicado; plan en el estado de cabecera)

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
- **Schema-gap del córtex (2026-07-09):** `LLMAssistantModel.decide` construye los schemas de
  tools con `tool_schemas` del **asistente** (`api_server.assistant.tools`), que no conoce las
  tools del córtex (`web_search`, `web_fetch`, `cortex_remember`, `cortex_recall_more`). Efecto
  medido: **todas** las llamadas `complete()` del córtex van con `tools=None` (el modelo nunca
  recibe los schemas). `web_search` funciona igualmente porque gpt-oss:120b la trae nativa; las
  demás tools del córtex operan «a ciegas» (el modelo infiere args del prompt/self-context).
  Fix propuesto: inyectar un `schema_fn` en `LLMAssistantModel` (default `tool_schemas`) y que
  `build_cortex_model`/`build_cortex_default_model` pase `cortex_tool_schemas`. RIESGO: cambio
  de comportamiento en un sistema que converge; validar que no rompe `web_search` (posible
  duplicado con la nativa) ni la escritura de memoria. NO se bundleó con el fix del answer
  vacío (`ffd38f4`, `FINISH_NUDGE`), que es la corrección correcta e independiente del cierre.

## Referencias

- Plan de refactorización y hallazgos H1-H6 (todo implementado):
  `docs/roadmap/refactorizacion-por-partes-2026-07-07.md`.
- Regresión A5 cazada en el QA: commit `faf2c78`; fix página review/active: commit
  posterior en la misma rama; gotcha de build `orchestrator-workers-base-image-arg.md`.
- Auditoría origen de la remediación: `docs/roadmap/auditoria-prod-implementados-2026-07-06.md`.
