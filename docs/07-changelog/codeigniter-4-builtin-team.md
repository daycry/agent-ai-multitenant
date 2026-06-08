---
plan_id: codeigniter-4-builtin-team
title: Equipo built-in CodeIgniter 4 (correctivo — migración del seed demo a catálogo de fábrica)
completed_at: null
docs_language: es
---

# Plan codeigniter-4-builtin-team — Equipo built-in CodeIgniter 4

## Resumen

La iteración anterior materializaba el equipo CodeIgniter 4 mediante un **seed
demostrativo standalone** (un script `setup_*` bajo `scripts/`) con un namespace
propio, un tenant y un proyecto dedicados, que **no** se invocaba desde el
runner de fábrica y arrastraba la marca de un proyecto privado de origen. Este
plan **correctivo** lo reemplaza por un **equipo built-in de fábrica**: todo se
siembra en build-time con `python -m api_server.seeds`, bajo `PLATFORM_TENANT_ID`
y los namespaces de plataforma, sin tenant/proyecto/script propios y sin ninguna
referencia al proyecto privado (corpus purgado y generalizado).

> **Plan correctivo de seed.** No es una fase de desarrollo de la plataforma; no
> entra en el gate de fases del roadmap.

## Qué cambió

### Seeders built-in (`f9d5324`, `ddad683`, `f87ca62`)

- **8 KBs de catálogo purgadas** en
  `apps/api-server/src/api_server/seeds/catalog/codeigniter-4-*.md`
  (convenciones de equipo, arquitectura HMVC + routing, modelo de datos Doctrine,
  testing, seguridad/auth, i18n EN/ES, frontend/assets, CI/CD). Contenido
  genérico del stack CodeIgniter 4 + ecosistema `daycry/*`; sin marca, sin
  nombres de tabla/clase reales, sin secretos literales, sin infra propietaria.
- **10 agentes `BuiltinAgent`** (`scope='global_builtin'`, `is_template=true`,
  slugs `ci4-*`) en `ci4_team.py`, sembrados por loader propio `seed_ci4_agents`
  (no dentro de `BUILTIN_AGENTS`: el conteo de los 11 agentes core se mantiene
  estable). Cada agente lleva `system_prompts` bilingües (es/en) genéricos y **NO
  pinea `provider`/`model`**: hereda el default de modelo operator-configurable
  (`f87ca62`).
- **Equipo built-in `codeigniter-4`** (`is_builtin=true`) con 10 miembros
  (`ci4-pm` líder), `role_in_team` y `assignment_priority` 10..100.
- **Tools por agente** cableadas en `agent_tools` por `seed_ci4_agent_tools`,
  reusando los tool slugs built-in existentes.
- **8 KBs built-in `codeigniter-4-*`** declaradas en `builtin_kbs.py`
  (`is_builtin=true`, categorías `stack`/`role`); el contenido lo ingiere
  `seed_catalog_ingestion` (1 Document estable + N Chunk bajo PLATFORM,
  `metadata.source='catalog'`).
- **BuiltinProjectTemplate `codeigniter-4-app`** (`team_slug='codeigniter-4'`)
  con `default_kb_grants` = las 8 KBs CI4: al adoptar la plantilla, el fork crea
  los `kb_projects` y el RAG ve el contenido built-in cross-tenant. Un tenant sin
  adopción NO ve las KBs (aislamiento intacto).
- Registro de los seeders en `__main__.py` en orden (agentes → tools → equipo →
  plantilla).

### Purga del seed demo (`ci4_purge`)

- **Eliminados**: el script standalone `setup_*` del seed demo (~1177 líneas),
  el directorio de corpus del seed bajo `scripts/` (20 .md) y los 3 tests de
  integración del seed demo. Su cobertura la sustituye
  `tests/integration/test_seed_ci4_team.py`.
- **Reescritas**: este plan de roadmap y su changelog (renombrados al plan_id
  `codeigniter-4-builtin-team`); eliminada la guía del seed demo y el human-test
  asociado (el equipo built-in no necesita guía de "correr el seed");
  re-redactados los índices (`docs/roadmap/README.md`, `docs/03-guides/README.md`,
  `docs/03-guides/human-tests/README.md`) y las menciones cruzadas en otros
  planes/changelogs (06.15, 06.16, 09.1, docs-human-test-guides).
- **Gate duro**: la búsqueda case-insensitive de la marca del proyecto privado
  sobre todo el repo devuelve **0 ocurrencias**.

## Tests / verificación

- `tests/integration/test_seed_ci4_team.py` (8 tests) verde con
  `TEST_PG_PORT=15432`: equipo `codeigniter-4` built-in con 10 miembros y líder
  `ci4-pm`; agentes `ci4-*` `global_builtin` sin modelo pineado; tools por
  agente; 8 KBs built-in ingeridas; plantilla `codeigniter-4-app` concede las 8
  KBs; visibilidad RAG solo tras adopción; conteo de 11 agentes core estable;
  seed idempotente.
- Gate de marca: la búsqueda case-insensitive de la marca del proyecto privado
  de origen = 0 ocurrencias (excluido `.git`, `.venv`, `node_modules`).

## Impacto

Toda instalación nueva trae de fábrica el equipo **CodeIgniter 4** (10 agentes
que heredan el modelo default configurable) + sus 8 KBs + la plantilla de
proyecto que las concede. Sin tenant ni proyecto demo, sin script manual, sin
ninguna referencia al proyecto privado de origen.
