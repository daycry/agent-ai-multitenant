---
plan_id: 07-documentacion-visor
title: Documentación Estructurada y Visor Cross-Proyecto
completed_at: null
docs_language: es
---

# Plan 07 — Documentación Estructurada y Visor Cross-Proyecto

## Resumen

Institucionaliza la **documentación como entregable del plan**, no como un
afterthought. Tres bloques:

1. **Estructura canónica `/docs`** (las 7 carpetas de CLAUDE.md) con bootstrap,
   validador estructural y lint de Markdown + idioma enforced en CI.
2. **Technical Writer agente** con renderizadores **deterministas** de
   changelog y ADR (mismo input ⇒ mismo Markdown, sin reloj ni I/O), invocados
   por el workflow de generación post-plan; más la sincronización
   `/docs ↔ kb_internal_docs` (con reindexación incremental) que alimenta la
   búsqueda.
3. **Visor cross-proyecto solo lectura** bajo `/admin/docs`: backend con cinco
   endpoints project-scoped (árbol, contenido, diff, búsqueda, export) gateados
   por RBAC/RLS, y UI Next.js con sidebar de árbol, render Markdown + Mermaid +
   TOC, búsqueda full-text/semántica, filtros, bookmarks y vista de diff entre
   refs Git.

Las 20 tareas se desarrollaron por TDD en cinco fases (A–E). El backend del
visor lee directo del filesystem persistente (worktrees de la Fase 6), no
replica almacenamiento; los renderizadores de docs son deterministas para que
la salida sea reproducible y revisable.

## Cambios

### Fase A — Estructura canónica y lint

- ✅ **`task_07_01`** — Bootstrap de las **7 carpetas obligatorias**
  (`api_server.docs_structure.bootstrap`): crea `01-overview` … `07-changelog`
  en un repo nuevo de forma idempotente.
- ✅ **`task_07_02`** — **Validador estructural** (`docs_structure.validator`)
  que comprueba la presencia de las 7 carpetas y rechaza el push/PR si faltan,
  con feedback claro.
- ✅ **`task_07_03`** — **Lint de Markdown** como check de CI vía
  `markdownlint-cli` + `.markdownlint.jsonc`: reglas estructurales/higiene
  activas (enlaces rotos, tabs, trailing-space) y desactivadas las estilísticas
  que el corpus rompe legítimamente a escala.
- ✅ **`task_07_04`** — **Detector de idioma** (`docs_structure.language`,
  `es`/`en`): detecta cuando un `.md` está en idioma distinto al declarado en
  `docs_language`.

### Fase B — Technical Writer agente y generación automática

- ✅ **`task_07_05`** — Agente builtin **Technical Writer** con `system_prompt`
  curado para responsabilidad explícita post-plan y skills de documentación.
- ✅ **`task_07_06`** — **Workflow de generación post-plan** determinista
  (`tech_writer.generation`): al cierre genera el changelog + ADRs si aplica +
  updates a `reference`, llamando a los renderizadores en vez de free-form LLM.
- ✅ **`task_07_07`** — **Plantilla canónica de changelog**
  (`tech_writer.changelog.render_changelog(PlanMeta) -> str`): frontmatter
  (`plan_id`/`title`/`completed_at`/`docs_language`), `## Resumen`, `## Cambios`
  por tarea, `## Decisiones` opcional y `## PR`; encabezados bilingües es/en.
- ✅ **`task_07_08`** — **Plantilla canónica de ADR** numerado secuencialmente
  (`tech_writer.adr.render_adr` + `next_adr_number`): asigna el siguiente número
  libre leyendo el listado de `05-architecture-decisions`, sin colisiones.

### Fase C — Indexación y KB interna

- ✅ **`task_07_09`** — **Sincronización `/docs ↔ kb_internal_docs`**
  (`docs_structure.kb_sync`): vuelca el árbol `docs/` a una KB interna por
  proyecto. El **disparador** (webhook Git / hook de merge de PR) queda diferido
  a **Plan 13** (depende del `webhook-dispatcher`); las funciones de sync son ya
  los callables que ese hook invocará.
- ✅ **`task_07_10`** — **Reindexación incremental**: solo los `.md` cambiados
  desde el último commit (change-set), evitando reindexar todo el corpus.

### Fase D — Visor cross-proyecto

- ✅ **`task_07_11`** — UI Next.js `/admin/docs` con **sidebar de árbol**
  proyecto → carpeta → archivo (RBAC/RLS server-side, lazy-load por proyecto;
  deep-link `?project=&path=` compartible).
- ✅ **`task_07_12`** — **Render Markdown** con react-markdown + remark-mermaid +
  rehype-highlight + **tabla de contenidos** autogenerada.
- ✅ **`task_07_13`** — **Búsqueda full-text** instantánea con resultados
  rankeados y snippets (UI + endpoint `…/docs/search` sobre los chunks de la KB
  interna).
- ✅ **`task_07_14`** — **Búsqueda semántica** sobre `kb_internal_docs`
  (reutiliza el pipeline RAG con el filtro de visibilidad de KB).
- ✅ **`task_07_15`** — **Filtros** (proyecto, tipo de doc/categoría, fecha,
  autor) y **bookmarks**.
- ✅ **`task_07_16`** — **Vista de diff** entre dos refs Git de un doc
  (`…/docs/diff?base=&head=`, path-traversal-safe + git-ref-injection-safe).
- ✅ **`task_07_17`** — **Exportación**: ZIP del árbol `docs/` (stdlib,
  determinista, path-safe) operativa; **PDF es un stub `501 Not Implemented`**
  documentado que redirige al export ZIP (no se añade un renderizador nativo
  pesado solo para esto).
- ✅ **`task_07_18`** — **RBAC respetado**: los cinco endpoints exigen miembro
  activo del tenant + proyecto visible bajo RLS; un proyecto inaccesible o
  cross-tenant es **404** (RLS oculta la fila) y la búsqueda nunca devuelve hits
  de otro tenant.

### Fase E — Cierre

- ✅ **`task_07_19`** — **Dogfooding**: la documentación interna del propio
  sistema se reorganiza usando su propia estructura canónica de 7 carpetas.
- ✅ **`task_07_20`** — **Changelog del plan** (este documento).

## Pendiente

- **e2e Playwright del visor**: las specs `docs-viewer-sidebar`, `-render`,
  `-search`, `-filters` y `-diff` (`apps/admin-panel/e2e/`) están **escritas
  pero PENDIENTES DE VERIFICACIÓN HUMANA** — este entorno no tiene app +
  navegador para ejecutarlas. El typecheck/lint/build del admin-panel sí pasan.
- **Export PDF**: es un **stub `501`** intencional; el render Markdown→PDF
  offline queda como follow-up (de momento se usa el export ZIP).
- **Disparador de `kb_sync`**: el webhook Git / hook de merge de PR que dispara
  la sincronización y la reindexación incremental se **difiere a Plan 13**
  (`webhook-dispatcher`).
- Tests humanos del plan (`human_07_01`…`human_07_04`) pendientes de ejecutar
  por un humano antes de pasar a `completed`.

## Verificación

- `pre-commit run --all-files` (black/ruff/mypy/prettier) ✅.
- `npx markdownlint-cli --config .markdownlint.jsonc "docs/**/*.md"` ✅.
- Validador estructural: las 7 carpetas canónicas presentes ✅
  (`task_07_19` deja ≥ 20 docs entre ellas).
- `pytest tests/unit` (incl. `test_bootstrap_docs`, `test_language_detector`,
  `test_changelog_template`, `test_adr_template`) ✅.
- `pytest tests/integration` (incl. `test_docs_structure_guardrail`,
  `test_tech_writer_agent`, `test_docs_generation_post_plan`,
  `test_docs_kb_sync`, `test_incremental_reindex`, `test_docs_semantic_search`,
  `test_docs_export`, `test_docs_rbac`) ✅.
- admin-panel: `npm run typecheck && lint && build` ✅; e2e Playwright del visor
  **pendiente de verificación humana**.
