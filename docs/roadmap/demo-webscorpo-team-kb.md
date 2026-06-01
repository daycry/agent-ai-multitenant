---
plan_id: demo-webscorpo-team-kb
title: Demo/seed — equipo WebScorpo (CI4) con KB completo (equipo + por-agente)
status: in_progress
blocking_plan: []
started_at: 2026-06-01
completed_at: null
estimated_duration_calendar: 3-4 días
estimated_effort_person_days: 3
estimated_cost_human_eur: 1.200 € – 2.000 €
estimated_cost_ai_eur: 40 € – 90 €
created_by: system_architect
spec_sections_referenced: [4, 5, 13]
docs_language: es
---

# Plan demo-webscorpo-team-kb — Equipo WebScorpo con KB completo

> **Nota:** NO es una fase de desarrollo de la plataforma — es un **seed demostrativo** que USA la plataforma
> para materializar un equipo real (el proyecto PHP/CodeIgniter 4 `C:\laragon\www\webscorpo`) con su KB. Por eso
> lleva un `plan_id` descriptivo y no un número de fase; no entra en el gate de fases del roadmap.

## Cabecera

| Campo               | Valor                                                                                                      |
| ------------------- | ---------------------------------------------------------------------------------------------------------- |
| **ID del Plan**     | `demo-webscorpo-team-kb`                                                                                   |
| **Rama git**        | `plan/demo-webscorpo-team-kb`                                                                              |
| **Construye sobre** | #27 (asignación de tools), #30 (shell_exec + runtime por stack), Plan 04 (KB/RAG), Plan 16 (teams/agentes) |

## Resumen

A partir del análisis ya realizado de **WebScorpo** (CMS corporativo multi-tenant en CodeIgniter 4 + Doctrine +
Twig + daycry/auth; `C:/tmp/webscorpo-analysis.md`), se crea un **seed reproducible** que materializa en la
plataforma: un tenant, un **equipo de 10 agentes** especializados en este stack, el **proyecto** webscorpo con su
config de comandos/runtime PHP, y un **KB completo** — uno **compartido por el equipo** (10 documentos) + KBs
**por agente** con el conocimiento específico de su rol. Enfoque **seed** (decisión del operador): crea estructura

- contenido ya; los embeddings se calculan si hay embedder (Ollama) disponible y, si no, quedan para re-indexar.

## Alcance

**Entra**:

- **Corpus KB** (markdown en el repo, generado del análisis): 10 documentos **team-shared** (overview+glosario,
  mapa de arquitectura HMVC, routing/filtros + helpers de rutas, data-model/BaseEntity/Doctrine SLC, estándares +
  toolchain + scripts composer `@ci/@quality/@fix/@test*/@mutation`, estrategia de tests 3-suites + Selenium +
  cobertura, runbook CI/CD Azure + Docker + dual-region, política i18n EN/ES, baseline de seguridad + hallazgos,
  catálogo de dependencias daycry/\* + GDI/Azure) + documentos **por-agente** (uno por rol).
- **Seed `scripts/setup_webscorpo.py`** (idempotente, patrón `_demo_common`): crea/upserta tenant "Mediapro",
  el **equipo WebScorpo** con **10 agentes** (pm, architect, backend-CI4, dba-Doctrine, frontend, auth-security,
  i18n, qa, reviewer, devops), el **proyecto webscorpo** con `allowed_commands` = {php, composer, vendor/bin/phpunit,
  vendor/bin/pest, vendor/bin/infection, npm, npx} y `default_runtime_template` = `php-phpunit` (usa #30), y las
  **asignaciones de tools por agente** (usa #27): shell_exec + file/git a todos; run_tests/run_build a backend/dba/
  qa/devops; etc.
- **KBs**: un KB **team_shared** (concedido al equipo/proyecto) con los 10 docs compartidos + un KB **private** por
  agente con sus docs de rol. Ingesta reutilizando el pipeline existente (seed_builtin_kbs / catalog_ingestion) con
  **degradación elegante del embedder** (si no hay Ollama: documentos guardados, embeddings diferidos a re-index).
- Verificación + guía de uso + changelog.

**Queda fuera**:

- Modificar el proyecto webscorpo en disco (es solo-lectura; solo se analiza).
- Ingestión RAG en vivo desde la plataforma (se usa el seed; re-index real cuando haya embedder configurado).
- Crear un Plan de trabajo/Kanban para webscorpo (esto solo deja el equipo + KB listos; planificar es del operador).

## Decisiones clave

- **Seed idempotente** (upsert por identidad estable): re-ejecutar no duplica tenant/equipo/agentes/KBs.
- **Contenido del KB = del análisis**, no inventado: el corpus se deriva de `C:/tmp/webscorpo-analysis.md` (+ relectura
  read-only de `C:/laragon/www/webscorpo/app` para precisar). Cita rutas reales del proyecto.
- **Reutiliza lo construido**: project command config + runtime (#30), asignación de tools (#27), KB/RAG (Plan 04),
  teams/agentes (Plan 16). No reinventa.
- **Embeddings opcionales**: el seed no falla si no hay embedder; deja los documentos listos para re-indexar.

## Tareas

### Fase A — Corpus del KB

#### `task_demo_ws_01` — Generar el corpus KB (team-shared + por-agente)

- [x] **Título**: Generar el corpus markdown del KB de WebScorpo desde el análisis: 10 documentos team-shared +
      un documento por cada uno de los 10 roles, bajo `scripts/webscorpo/kb/` (`team/` + `agents/<role>/`). Contenido
      fiel (arquitectura HMVC, Doctrine/BaseEntity/SLC, scripts composer, suites de test, CI/CD Azure, i18n EN/ES,
      hallazgos de seguridad, dependencias daycry/\*). Read-only sobre `C:/laragon/www/webscorpo/app` para precisar.
- **Tests**: `pytest tests/integration/test_webscorpo_corpus.py -v` (existen los ficheros esperados; frontmatter/no-vacío)

### Fase B — Seed de entidades

#### `task_demo_ws_02` — Seed: tenant + equipo + 10 agentes + proyecto + tools

- [x] **Título**: `scripts/setup_webscorpo.py` (idempotente): tenant "Mediapro"; equipo "WebScorpo" con 10 agentes
      (roles del análisis); proyecto "webscorpo" con `allowed_commands` (php/composer/phpunit/pest/infection/npm) +
      `default_runtime_template=php-phpunit`; asignación de tools por agente (shell*exec + file/git a todos; run*\* a
      backend/dba/qa/devops). Patrón `_demo_common`.
- **Tests**: `pytest tests/integration/test_setup_webscorpo_entities.py -v` (tras el seed: equipo + 10 agentes + proyecto
  con su config + asignaciones de tools; idempotente: re-seed no duplica; tenant-scoped)

### Fase C — KBs + ingesta

#### `task_demo_ws_03` — KBs team-shared + por-agente + ingesta del corpus

- [x] **Título**: En el mismo seed: crear un KB `team_shared` (concedido al equipo/proyecto) con los 10 docs
      compartidos + un KB `private` por agente con sus docs de rol; ingestar el corpus reutilizando el pipeline
      existente con degradación elegante del embedder (sin Ollama → documentos guardados, embeddings diferidos).
      Conceder los KBs (agent_knowledge_bases / default_kb_grants).
- **Tests**: `pytest tests/integration/test_setup_webscorpo_kbs.py -v` (KB team-shared con N docs concedido al equipo;
  cada agente tiene su KB privado; ingesta no falla sin embedder; tenant-scoped)

### Fase D — Verificación + docs

#### `task_demo_ws_04` — Ejecutar/verificar + guía + changelog

- [ ] **Título**: Ejecutar el seed contra la BD dev y verificar el resultado end-to-end; guía
      `docs/03-guides/demo-webscorpo.md` (cómo correr el seed, qué crea, cómo usar el equipo + KB, cómo re-indexar
      embeddings cuando haya proveedor); changelog `docs/07-changelog/demo-webscorpo-team-kb.md`; fila en README.
- **Tests**: `test -f docs/07-changelog/demo-webscorpo-team-kb.md && test -f docs/03-guides/demo-webscorpo.md`

## Tests humanos del Plan

```yaml
- id: human_demo_ws_01
  description: "El equipo WebScorpo y su KB existen y son usables"
  checklist:
    - "Tras correr scripts/setup_webscorpo.py, en el tenant Mediapro hay un equipo 'WebScorpo' con 10 agentes"
    - "El proyecto 'webscorpo' tiene allowed_commands (php/composer/phpunit…) + runtime php-phpunit"
    - "Cada agente tiene asignadas sus tools (shell_exec + las de su rol)"
    - "El KB del equipo tiene los 10 documentos compartidos; cada agente ve su KB privado de rol"
    - "Re-ejecutar el seed no duplica nada (idempotente)"
    - "Si hay embedder, una búsqueda semántica en el KB del equipo devuelve resultados; si no, los docs están y se pueden re-indexar"
```

## Criterios de cierre

1. Todas las tareas `[x]` con su test automático en verde.
2. `pytest tests/unit tests/integration -v` (incluye los nuevos) verde.
3. `pre-commit run --all-files` verde.
4. Seed idempotente verificado.
5. Tests humanos validados por un humano.
6. Changelog + fila en README.
7. PR de `plan/demo-webscorpo-team-kb` mergeado (lo hace el humano).

## Próximo Plan

Tras este seed: retomar el backlog de plataforma — Plan 16 D–F (cierre), scripts de tests humanos, modernización
UI + refactor, y actualización integral de documentación.
