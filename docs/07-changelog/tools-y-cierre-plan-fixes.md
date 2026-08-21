---
plan_id: tools-y-cierre-plan-fixes
title: Tools, guardrails de runtime y cierre de plan — paridad catálogo↔executor, gate humano que no falle-abierto y changelog automático
completed_at: null
status: pending_human_validation
docs_language: es
---

# Plan tools-y-cierre-plan-fixes

## Resumen

Tres causas raíz de la auditoría de plataforma de 2026-07-03, todas de la misma
familia: **el sistema declaraba una cosa y el runtime hacía otra**.

- **D** — el gate de validación humana y el motor de guardrails existían,
  estaban testeados y **nunca corrían** en ejecución de agentes.
- **E** — el catálogo ofrecía tools que el runtime no podía ejecutar.
- **G** — el cierre de plan se declaraba completo sin generar su changelog, que
  es el criterio 4 de CLAUDE.md.

Las 9 tareas están `[x]`. Las tres últimas (T2, T4, T8) se cerraron el
2026-07-27; el frontmatter del plan decía `pending_approval` con 6 de 9 tareas ya
hechas, y llevaba meses mintiendo.

## Cambios

### Fase A — el gate humano deja de fallar abierto

- **T1**: `DEFAULT_TOOL_CATEGORIES` del runtime emite las **13 categorías
  canónicas** de las plantillas de política (antes 4), con cada builtin mapeado
  a la suya (`shell_exec→code_changes`, `http_post→external_http_post`,
  `write_file→code_changes`…). Test de contrato: el vocabulario del gate ⊆ las 13
  canónicas, y el preset «Cliente Externo» (13/13 human_required) **detiene**
  `shell_exec`/`write_file`/`http_post`/`agent_invoke`, que antes pasaban.
- **T2 — tools MCP gateables**:
  `shared_domain.approval_categories.spec_approval_category(implementation_type,
security_level)` deriva la categoría de **lo que el operador ya declara al
  importar la tool**, sin pedirle un dato nuevo. Tres decisiones que merecen
  quedar escritas:
  - **fail-CLOSED** ante un `security_level` desconocido;
  - el **builtin gana la colisión** (un spec de tool no puede rebajar el gate de
    `write_file`);
  - se **descarta** cualquier categoría fuera de las 13 — propagarla habría
    reeditado en pequeño el fail-open que este plan venía a cerrar.
    `security_level='safe'` es el opt-out explícito y por-tool; sin él, la única
    palanca del operador sería apagar la categoría entera del proyecto.
- **T3**: `pre_tool`/`post_tool` del motor `shared-guardrails` cableados en el
  loop del runtime en modo `log`, de forma que las salidas MCP/HTTP/RAG pasan por
  los checks de `prompt_injection` y `secret_leakage` y dejan evento en
  `steps_log`; `security_level` de la Tool enforced. El motor completo con
  persistencia por proyecto y enforce quedó en el
  [ADR 0102](../05-architecture-decisions/0102-cableado-motor-guardrails-runtime.md).

### Fase B — paridad catálogo ↔ executor

- **T5** (2026-07-18): `apply-patch`, `search-code` y `summarize-text`
  **retirados** de `ROLE_DEFAULT_TOOLS` y de `ci4_team._FILE_TOOLS`; el anuncio
  al LLM ya filtraba por `is_runtime_wired`. Cierra la regresión del run
  `019f27ff`, donde un agente llamaba a `search_code` porque el catálogo se lo
  ofrecía y nadie podía ejecutarlo.
- **T4 — el candado**: `tests/unit/test_catalog_executor_parity.py` cruza las
  **tres vías declarativas** que escriben en `agent_tools` —
  `ROLE_DEFAULT_TOOLS`, `BUILTIN_AGENTS[*].resolved_tool_slugs()` (lo que el seed
  escribe DE VERDAD: pinear solo el diccionario dejaba abierta la puerta del
  override `tool_slugs=`) y `CI4_AGENTS[*].tool_slugs` — deriva los builtins sin
  ejecutor **de la semilla, no de una lista a mano**, y lleva guarda contra
  vaciarse en silencio. La cuarta vía (el PUT) se cubre por comportamiento en
  `test_agent_tools_assignment.py::test_cannot_assign_unwired_builtin` (422
  nombrando la tool, nada persistido, rechazo del conjunto entero — no un
  filtrado silencioso).
  **Verificado por mutación**: añadir `search-code` a `ROLE_DEFAULT_TOOLS["qa"]`
  pone el test en rojo nombrando al culpable. Sin esa comprobación, un candado
  que hoy pasa porque T5 ya limpió el árbol es indistinguible de un candado que
  no puede fallar.
- **T6**: la UI de asignación marca «No ejecutable» con tooltip a partir de
  `is_runtime_wired`; el badge «requiere import» de MCP conserva el
  comportamiento del ADR 0052.
- **T7**: el picker de plantillas MCP ya no existe (el formulario es entrada
  libre) y la vía operativa de ingesta documentada es docling-serve HTTP
  (`/v1/chunk/hybrid/file`).

### Fase C — el cierre de plan genera su propio changelog

- **T8**: `apps/workers/src/workers/plan_docs.py` cablea
  `generate_plan_docs`/`render_changelog` en el camino real de cierre.
  - **Vive en el WORKER, no en la api-server**, porque ésta no monta
    `agent-data`: toda operación de git o de disco sobre el repo del proyecto
    tiene que pasar por allí. Es la trampa que ya costó un 500 en el visor de
    diffs.
  - Provisiona un worktree **dedicado** (`plan-docs-{id8}`) para que el
    `git add -A` del commit no barra artefactos de una tarea hermana.
  - **Punto de enganche**: inline al principio de
    `plan_pr._open_plan_pr_async`, **por delante** del corte por
    `git_config`/`remote_url` — el criterio dice «con o sin PR», y un proyecto
    local sin remoto sí tiene bare donde escribir. Un solo disparador (encolarlo
    aparte habría hecho competir dos tasks por el mismo worktree), y así el PR
    contiene su propio changelog.
  - **Idempotente por construcción**: `generate_plan_docs` es skip-if-exists ⇒
    nada escrito ⇒ nada commiteado. Un reintento no duplica el commit **ni pisa
    un changelog que un humano haya reescrito** — que es lo que hace segura la
    convivencia entre este automatismo y entradas como esta.
  - Queda además la task `workers.generate_plan_closure_docs` para backfill de
    planes cerrados antes de T8.

### Fase D — deuda registrada, no disfrazada

- **T9**: la no materialización del marketplace (g3) es **diferimiento
  consciente** heredado del ADR 0081, no un defecto oculto. Adelantarlo exige el
  ADR 0100. Sin impacto en los runs actuales, que usan el catálogo builtin.

## Tests

- `tests/unit/test_mcp_tool_approval_category.py` (26) y
  `docker/agent-runtimes/agent-runtime/tests/test_boot_approval_mcp_gate.py` (3,
  **con control negativo**: sin la categoría en el spec, «Cliente Externo» NO
  para la tool — el estado exacto anterior a T2).
- `tests/unit/test_catalog_executor_parity.py` +
  `tests/unit/test_seed_tools_runtime_wired.py`.
- `tests/integration/test_plan_closure_docs.py` (7, git real contra `tmp_path`) y
  `tests/integration/test_plan_close_e2e.py` bloque (d), que lo comprueba **en el
  camino real que abre el PR**, no en un doble.

## Observación sobre este mismo documento

Esta entrada de changelog se escribió **a mano** aunque T8 ya automatiza la
generación. No es una contradicción: el generador cubre los planes que cierran a
partir de ahora por el camino del worker, y es skip-if-exists, así que una
entrada humana previa se respeta. Los 46 planes que quedaron en
`pending_human_validation` antes de T8 no pasaron por ese camino.

## Estado de cierre

Los criterios 2 y 3 (preset «Cliente Externo» verde, candado de paridad activo)
están cubiertos por test. Falta la **reconciliación de vocabularios con
prod-03**, que el propio plan declara como coordinación obligatoria y que
depende de un plan en `pending_approval`; y falta el gate humano del PR.

## PR

- _pendiente_
