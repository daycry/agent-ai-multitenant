---
plan_id: demo-webscorpo-team-kb
title: Seed demo — equipo WebScorpo (CI4) con KB completo (equipo + por-agente)
completed_at: null
docs_language: es
---

# Plan demo-webscorpo-team-kb — Equipo WebScorpo con KB completo

## Resumen

A partir del análisis ya realizado de **WebScorpo** (el CMS corporativo
multi-tenant de Mediapro en CodeIgniter 4 + Doctrine + Twig + daycry/auth, en
`C:/laragon/www/webscorpo`, solo-lectura), se construyó un **seed reproducible**
que materializa en la plataforma un **equipo usable** con su **Knowledge Base
completo**. El seed _usa_ la plataforma; **no** modifica el proyecto webscorpo en
disco. Reutiliza lo ya construido: project command config + runtime por stack
(Plan 06.16), asignación de tools por agente (Plan 06.15), KB/RAG (Plan 04) y
teams/agentes (Plan 16).

> **No es una fase de desarrollo de la plataforma** — es un seed demostrativo.
> El frontmatter del plan lo cierra el orquestador; esta entrada documenta lo
> que se entregó.

## Qué se entregó

### `task_demo_ws_01` — Corpus del KB (team-shared + por-agente)

Corpus markdown versionado bajo `scripts/webscorpo/kb/`: **10 documentos
`team/*.md`** (overview + glosario, mapa de arquitectura HMVC, routing/filtros +
helpers de rutas, data-model Doctrine/BaseEntity/SLC, estándares + toolchain con
los scripts composer `@ci`/`@quality`/`@fix`/`@test*`/`@mutation`, estrategia de
tests 3-suites + Selenium + cobertura, runbook CI/CD Azure dual-region, política
i18n EN/ES, baseline de seguridad + hallazgos, catálogo de dependencias
daycry/\* + GDI/Azure) + **un `agents/<role>/role-knowledge.md` por cada uno de
los 10 roles**. Contenido fiel al análisis (`C:/tmp/webscorpo-analysis.md`), con
relectura solo-lectura del proyecto para precisar; cita rutas reales. Cada doc
lleva frontmatter y no está vacío.

- **Tests**: `tests/integration/test_webscorpo_corpus.py` (existen los ficheros
  esperados; frontmatter; no vacíos).

### `task_demo_ws_02` — Seed: tenant + equipo + 10 agentes + proyecto + tools

`scripts/setup_webscorpo.py` (idempotente, patrón `_demo_common` +
`api_server.seeds`): tenant **Mediapro**; equipo **WebScorpo** con **10
agentes** (pm líder, architect, backend-CI4, dba-Doctrine, frontend,
auth-security, i18n, qa, reviewer, devops — roster del análisis §7); proyecto
**webscorpo** con `allowed_commands` = `{php, composer, vendor/bin/phpunit,
vendor/bin/pest, vendor/bin/infection, npm, npx}` + `default_runtime_template =
php-phpunit` (Plan 06.16); asignación de tools por agente vía la junction
`agent_tools` (Plan 06.15): `shell_exec` + fichero + git + `semantic-search` a
todos; `run_*` a backend/dba/qa/devops; `http_get` a auth-security/devops; etc.
Identidad estable por `uuid5(WEBSCORPO_NAMESPACE, "<kind>:<slug>")` — re-seedear
es un upsert real, nunca duplica; las membresías y asignaciones se re-concilian
(upsert + borrado de obsoletas). Tenant-scoped (los agentes son
`scope=global_tenant_template`, sin `project_id`, respetando
`ck_agents_scope_project`).

- **Tests**: `tests/integration/test_setup_webscorpo_entities.py` (equipo + 10
  agentes + proyecto con su config + asignaciones de tools; idempotente; tenant-scoped).

### `task_demo_ws_03` — KBs team-shared + por-agente + ingesta del corpus

En el mismo seed: **1 KB `team_shared`** ("WebScorpo — Conocimiento del equipo")
con los 10 docs compartidos, **concedido al proyecto** (`kb_projects`) **y a los
10 agentes** (`agent_knowledge_bases`); **10 KBs `private`**, uno por agente, con
su `role-knowledge.md` de rol, concedido sólo a ese agente. La ingesta reutiliza
el pipeline de Plan 04 (chunker markdown de `catalog_ingestion` + `Embedder`
Protocol) con **degradación elegante del embedder** (ver más abajo). Idempotente
por `corpus_hash` (si el `.md` no cambió, no re-trocea/re-embebe).

- **Tests**: `tests/integration/test_setup_webscorpo_kbs.py` (KB team-shared con
  N docs concedido al equipo/proyecto; cada agente con su KB privado; la ingesta
  no falla sin embedder; tenant-scoped).

### `task_demo_ws_04` — Ejecutar/verificar + guía + changelog + README

Ejecución end-to-end del seed contra la BD dev (PG 15432) verificada
(idempotente en 2.ª pasada), guía `docs/03-guides/demo-webscorpo.md`, esta
entrada de changelog y la fila en `docs/roadmap/README.md`.

- **Tests**: `test -f docs/07-changelog/demo-webscorpo-team-kb.md && test -f
docs/03-guides/demo-webscorpo.md`.

## Nota sobre embeddings (diferidos)

El seed **no falla** si no hay embedder (Ollama) accesible: construye un
`OllamaEmbedder`, hace un ping de salud, y si no responde **persiste los
documentos + chunks con `embedding = NULL`** y marca los embeddings como
**diferidos** (la búsqueda por palabra clave sigue sirviendo; la semántica no,
hasta re-indexar). En la verificación end-to-end Ollama estaba caído, así que
los 136 chunks quedaron sin embedding — comportamiento esperado y documentado.
El camino de re-index (una vez configurado un proveedor vía `/admin/llm-providers`
o un Ollama local) está en `docs/03-guides/demo-webscorpo.md`: borrar los chunks
diferidos del seed y re-ejecutar el script (el _fast-path_ por `corpus_hash`
hace que un simple re-run sin borrar no re-embeba).

## Verificación end-to-end (BD dev, PG 15432)

Tras dos ejecuciones consecutivas de `scripts/setup_webscorpo.py` (stack dev
levantado), los conteos confirman idempotencia (sin duplicados):

| Tabla                      | Conteo | Nota                                                       |
| -------------------------- | ------ | ---------------------------------------------------------- |
| `organizations` (Mediapro) | 1      | tenant                                                     |
| `teams` (WebScorpo)        | 1      | equipo                                                     |
| `agents`                   | 10     | roster §7                                                  |
| `team_members`             | 10     | membresía                                                  |
| `projects` (webscorpo)     | 1      | `allowed_commands` PHP + runtime `php-phpunit`             |
| `agent_tools`              | 131    | suma de tools por agente (12+11+15+15+11+12+11+15+11+18)   |
| `knowledge_bases`          | 11     | 1 team_shared + 10 private                                 |
| `documents`                | 20     | 10 team + 10 por-agente                                    |
| `chunks`                   | 136    | todos con `embedding = NULL` (diferidos, Ollama caído)     |
| `kb_projects`              | 1      | grant del KB del equipo al proyecto                        |
| `agent_knowledge_bases`    | 20     | 10 (KB equipo → cada agente) + 10 (KB privado → su agente) |

Suites de integración (verde, `TEST_PG_PORT=15432`,
`TEST_REDIS_URL=redis://localhost:6379/15`): **54 passed** entre
`test_webscorpo_corpus.py`, `test_setup_webscorpo_entities.py` y
`test_setup_webscorpo_kbs.py`. `pre-commit` (black/ruff/mypy/prettier) en verde
por tarea.

## Pendiente

- **Tests humanos del plan** — pendientes de ejecutar por un humano
  (`human_demo_ws_01`): comprobar en el tenant Mediapro el equipo WebScorpo con
  10 agentes, la config de comandos/runtime del proyecto, las tools por agente,
  el KB del equipo (10 docs) + el KB privado de cada agente, la idempotencia del
  re-seed, y (si hay embedder) una búsqueda semántica con resultados. Checklist
  completa en `docs/roadmap/demo-webscorpo-team-kb.md`.
- **Re-index de embeddings** — diferido hasta configurar un proveedor de
  embeddings; instrucciones en la guía.
- **Merge del PR** de `plan/demo-webscorpo-team-kb` a `main` — lo gestiona el
  humano. El plan queda en `in_progress`, NO en `completed`.

## PR

Pendiente de apertura/merge a `main` (lo gestiona el humano tras validar los
tests humanos del plan).
