---
plan_id: codeigniter-4-builtin-team
title: Equipo built-in CodeIgniter 4 (correctivo — migración del seed demo a catálogo de fábrica)
status: pending_human_validation
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

# Plan codeigniter-4-builtin-team — Equipo built-in CodeIgniter 4

> **Nota:** plan **correctivo de seed**, no una fase de desarrollo de la
> plataforma. Reemplaza al antiguo seed demostrativo standalone por un **equipo
> built-in de fábrica** servido por el runner del catálogo
> (`python -m api_server.seeds`). Lleva un `plan_id` descriptivo y no un número
> de fase; no entra en el gate de fases del roadmap.

## Cabecera

| Campo               | Valor                                                                                                            |
| ------------------- | ---------------------------------------------------------------------------------------------------------------- |
| **ID del Plan**     | `codeigniter-4-builtin-team`                                                                                     |
| **Rama git**        | `plan/codeigniter-4-builtin-team`                                                                                |
| **Construye sobre** | #27 (asignación de tools), #30 (shell_exec + runtime por stack), Plan 04 (KB/RAG), seeders built-in del catálogo |

## Motivación

La iteración anterior materializó un equipo CodeIgniter 4 mediante un **seed
demostrativo standalone** (un script `setup_*` bajo `scripts/`) que creaba un
tenant, un equipo y un proyecto bajo un namespace propio y NO se invocaba desde
el runner de fábrica. Ese enfoque tenía tres problemas: (1) acoplaba el catálogo a
un proyecto privado concreto y a su marca; (2) no aparecía de fábrica en
instalaciones nuevas (había que correr un script a mano); (3) los agentes eran
`global_tenant_template` ligados a un tenant, no parte del catálogo de
plataforma.

Este plan **corrige** todo eso convirtiendo CodeIgniter 4 en un **equipo
built-in de fábrica** (patrón `qa_e2e_automator` / `human_agent_templates`):
todo se siembra en build-time bajo `PLATFORM_TENANT_ID`, con los namespaces de
plataforma, sin tenant/proyecto/script propios y **sin ninguna referencia al
proyecto privado de origen** (corpus purgado y generalizado).

## Alcance

**Entra**:

- **Corpus KB purgado** en `apps/api-server/src/api_server/seeds/catalog/codeigniter-4-*.md`
  (8 ficheros, formato que espera `catalog_ingestion`): convenciones del equipo,
  arquitectura HMVC + routing, modelo de datos con Doctrine, estrategia de
  testing, seguridad/autenticación, i18n EN/ES, frontend/assets y CI/CD. Sin
  marca, sin nombres de tablas/clases reales, sin secretos, sin infra
  propietaria — solo conocimiento genérico del stack CodeIgniter 4 + ecosistema
  `daycry/*`.
- **10 agentes `BuiltinAgent`** (`scope='global_builtin'`, `is_template=true`,
  slugs `ci4-*`) sembrados por loader propio `seed_ci4_agents` (no dentro de
  `BUILTIN_AGENTS`, para no alterar el conteo de los 11 core). Cada agente lleva
  `system_prompts` bilingües (es/en) genéricos y **NO pinea provider/model**:
  hereda el default de modelo configurable por el operador (commit `f87ca62`).
- **Equipo built-in `codeigniter-4`** (`is_builtin=true`) con 10 miembros
  (`ci4-pm` líder), reusando `role_in_team` y `assignment_priority` 10..100.
- **8 KBs built-in `codeigniter-4-*`** (`is_builtin=true`): una de stack
  (`codeigniter-4-conventions`) + 7 temáticas de rol; el contenido lo ingiere
  `seed_catalog_ingestion` (1 Document + N Chunk bajo PLATFORM, source='catalog').
- **Tools por agente** cableadas en `agent_tools` por `seed_ci4_agent_tools`
  (la tabla no restringe scope), reusando los tool slugs built-in existentes.
- **BuiltinProjectTemplate `codeigniter-4-app`** (`team_slug='codeigniter-4'`)
  con `default_kb_grants` = las 8 KBs CI4: al adoptar la plantilla, el fork crea
  los `kb_projects` y el RAG ve el contenido built-in cross-tenant.
- Registro de los seeders en `__main__.py` en el orden correcto.
- **Purga total** del seed demo, su corpus, sus tests y todas las menciones a la
  marca del proyecto privado.

**Queda fuera**:

- Crear un tenant, un proyecto demo o un script standalone (se elimina todo eso).
- Cablear KBs per-agente vía `agent_knowledge_bases` (la migración 0026 lo
  prohíbe para agentes `global_builtin`; el conocimiento por-rol se modela como
  KBs built-in temáticas expuestas vía `default_kb_grants`).
- Pinear modelo por agente: los agentes CI4 heredan el default configurable.

## Decisiones clave

- **CI4 = equipo built-in de fábrica**, no seed demostrativo: aparece en toda
  instalación nueva vía `python -m api_server.seeds`.
- **Modelo heredado**: los agentes CI4 NO pinean `provider`/`model` en
  `model_config`; heredan el default de modelo operator-configurable (`f87ca62`).
  `model_config` lleva solo `system_prompts:{es,en}`.
- **Conocimiento por-rol como KBs built-in temáticas** (no per-agente), expuesto
  a los proyectos del tenant vía `default_kb_grants` de la project template
  (única vía válida para `global_builtin` — migración 0026).
- **Visibilidad cross-tenant del contenido**: un KB built-in no es auto-visible;
  el RAG lo ve cuando un tenant **adopta** la plantilla `codeigniter-4-app` (o
  concede la KB), que materializa los `kb_projects`. El equipo aparece en el
  catálogo aunque el RAG no esté activo hasta la adopción.
- **Corpus purgado y genérico**: sin marca, sin nombres de tabla/clase reales,
  sin secretos literales, sin infra propietaria. Gate de marca = 0 ocurrencias.

## Tareas

### Fase A — Corpus del KB

#### `ci4_kb_corpus` — Reescribir el corpus purgado a `seeds/catalog/`

- [x] **Título**: Reescribir y consolidar el corpus en 8 ficheros
      `apps/api-server/src/api_server/seeds/catalog/codeigniter-4-*.md` (formato
      `catalog_ingestion`, sin frontmatter de marca). Purgar toda referencia al
      proyecto privado de origen y a su infra (tablas/clases reales, secretos,
      dual-region Azure, deps propietarias, cifras de cobertura reales).
- **Tests**: tests de seed CI4 (ingesta del catálogo) — `tests/integration/test_seed_ci4_team.py::test_ci4_kbs_are_builtin_and_ingested`

### Fase B — Modelo default heredable

#### `ci4_default_model` — Default de modelo de agente operator-configurable

- [x] **Título**: Default de modelo de agente configurable por el operador +
      fallback en dispatch, de modo que los agentes built-in (incl. CI4) que NO
      pinean `provider`/`model` resuelvan al default. Commit `f87ca62`.
- **Tests**: suite de default de modelo (verde en `f87ca62`).

### Fase C — Seeders built-in del equipo

#### `ci4_seeders` — Agentes + equipo + KBs + tools + plantilla + registro

- [x] **Título**: `ci4_team.py` con `CI4_AGENTS` (10) + `seed_ci4_agents` +
      `seed_ci4_agent_tools` + `CI4_TEAM` + `seed_ci4_team` +
      `CI4_PROJECT_TEMPLATE` + `seed_ci4_project_template`; entradas de las 8 KBs
      `codeigniter-4-*` en `builtin_kbs.py`; registro en `__main__.py` en orden
      (agentes → tools → equipo → plantilla). Commit `f9d5324`.
- **Tests**: `tests/integration/test_seed_ci4_team.py` (8 tests verdes).

### Fase D — Purga total del seed demo

#### `ci4_purge` — Eliminar el seed demo, su corpus, sus tests y todas las menciones

- [x] **Título**: Eliminar el script standalone `setup_*` del seed demo, el
      directorio de corpus del seed bajo `scripts/` y los 3 tests de integración
      del seed demo (su cobertura la sustituyen los tests de seed CI4).
      Reescribir/renombrar las docs (este plan, changelog, guías, human-tests,
      índices) y eliminar las menciones a la marca del proyecto privado en otros
      planes/changelogs. Gate duro: la búsqueda case-insensitive de la marca del
      proyecto privado = 0 ocurrencias en todo el repo.
- **Tests**: `tests/integration/test_seed_ci4_team.py` verde + gate de marca = 0.

## Tests humanos del Plan

```yaml
- id: human_ci4_01
  description: "El equipo built-in CodeIgniter 4 y sus KBs existen de fábrica y son usables"
  checklist:
    - "Tras 'python -m api_server.seeds', el catálogo de teams built-in incluye 'CodeIgniter 4' (slug codeigniter-4, is_builtin=true) con 10 miembros y líder ci4-pm"
    - "Los 10 agentes ci4-* existen con scope='global_builtin', is_template=true, tenant_id=PLATFORM y sin provider/model pineado (heredan el default configurable)"
    - "Cada agente tiene sus tools en agent_tools (base + las de su rol)"
    - "Las 8 KBs codeigniter-4-* existen con is_builtin=true y categoría correcta; catalog_ingestion crea Document + chunks bajo PLATFORM"
    - "Al adoptar la plantilla 'codeigniter-4-app' en un tenant, el RAG ve las KBs CI4 vía kb_projects; un tenant sin adopción NO las ve (aislamiento intacto)"
    - "Re-correr el seed no duplica nada (idempotente)"
    - "La búsqueda case-insensitive de la marca del proyecto privado sobre el repo => 0 ocurrencias"
```

## Criterios de cierre

1. Todas las tareas `[x]` con su test automático en verde.
2. `tests/integration/test_seed_ci4_team.py` verde (TEST_PG_PORT).
3. `pre-commit run --all-files` (o sobre los ficheros tocados) verde.
4. Seed idempotente verificado.
5. Gate de marca: búsqueda case-insensitive de la marca del proyecto privado = 0 ocurrencias.
6. Tests humanos validados por un humano.
7. Changelog `docs/07-changelog/codeigniter-4-builtin-team.md` + fila en README.
8. PR de `plan/codeigniter-4-builtin-team` mergeado (lo hace el humano).

## Próximo Plan

Tras este correctivo: retomar el backlog de plataforma — cierre de teams/agentes,
scripts de tests humanos, modernización UI + refactor, y actualización integral
de documentación.
