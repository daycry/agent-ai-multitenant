---
title: Seed demo — equipo WebScorpo (CI4) con KB completo
audience: tenant admin, operator
phase: demo-webscorpo-team-kb
updated: 2026-06-01
---

# Seed demo — equipo WebScorpo con KB completo

Esta guía explica cómo correr el **seed reproducible** que materializa en la
plataforma el proyecto real **WebScorpo** (el CMS corporativo de Mediapro en
PHP/CodeIgniter 4, en `C:/laragon/www/webscorpo`) como un **equipo usable** con
su **Knowledge Base completo**. El seed _usa_ la plataforma; **no** modifica el
proyecto webscorpo en disco (es solo-lectura).

> **TL;DR**: ejecuta `python scripts/setup_webscorpo.py` con el stack dev
> levantado. Crea (idempotente) el tenant **Mediapro**, el equipo **WebScorpo**
> con **10 agentes**, el proyecto **webscorpo** (comandos PHP + runtime
> `php-phpunit`), las tools por agente y los **11 KBs** (1 del equipo con 10
> docs + 1 privado por agente). Si no hay embedder (Ollama) accesible, los
> documentos se guardan y los **embeddings quedan diferidos** para re-indexar.

## Requisitos

- Stack de desarrollo levantado: PostgreSQL en `localhost:15432` (engine admin
  `migrations_user`, BYPASSRLS — el seed escribe en `organizations` y bajo el
  tenant de plataforma para las tools built-in).
- El intérprete del venv del repo: `.venv\Scripts\python.exe` (tiene
  `sqlalchemy`/`asyncpg` y el código de `api_server`).
- (Opcional) Un embedder accesible para calcular embeddings ya en el seed; si no
  lo hay, el seed degrada con elegancia y deja los embeddings para re-indexar.

## Cómo correr el seed

Desde la raíz del repo:

```powershell
.\.venv\Scripts\python.exe scripts\setup_webscorpo.py
```

La URL de BD por defecto apunta al stack dev (`...@localhost:15432/agentic_platform`).
Para apuntar a otra BD, exporta `WEBSCORPO_DATABASE_URL` (o reutiliza
`DEMO_DATABASE_URL` de `scripts/_demo_common.py`):

```powershell
$env:WEBSCORPO_DATABASE_URL = "postgresql+asyncpg://migrations_user:...@host:port/db"
.\.venv\Scripts\python.exe scripts\setup_webscorpo.py
```

El seed es **idempotente**: re-ejecutarlo no duplica nada (cada entidad tiene un
id estable `uuid5` derivado de su slug; las membresías, asignaciones de tools y
documentos se re-concilian con upsert + borrado de filas obsoletas).

## Qué crea

### Tenant + equipo + proyecto

| Entidad      | Valor                                                                                                                                  |
| ------------ | -------------------------------------------------------------------------------------------------------------------------------------- |
| **Tenant**   | `Mediapro` (slug `mediapro`)                                                                                                           |
| **Equipo**   | `WebScorpo` (10 agentes, namespace de memoria compartida `team:webscorpo`)                                                             |
| **Proyecto** | `webscorpo` — `status=active`, ligado al equipo                                                                                        |
| Comandos     | `allowed_commands = php, composer, vendor/bin/phpunit, vendor/bin/pest, vendor/bin/infection, npm, npx` (deny-by-default — Plan 06.16) |
| Runtime      | `default_runtime_template = php-phpunit` (los `run_*` corren en el runtime PHP, no en `python-pytest`)                                 |

### Los 10 agentes (roster del análisis §7)

| Agente                    | Rol                                | Tools (resumen)                                                 |
| ------------------------- | ---------------------------------- | --------------------------------------------------------------- |
| `webscorpo-pm`            | Project / Delivery Manager (líder) | base + `send-notification`                                      |
| `webscorpo-architect`     | Software Architect (CI4+Doctrine)  | base                                                            |
| `webscorpo-backend`       | Backend Dev — CodeIgniter 4        | base + `run_*`                                                  |
| `webscorpo-dba`           | Doctrine ORM / DBA                 | base + `run_*`                                                  |
| `webscorpo-frontend`      | Frontend Dev                       | base                                                            |
| `webscorpo-auth-security` | Auth / Security (daycry/auth+SSO)  | base + `http-get`                                               |
| `webscorpo-i18n`          | i18n / Localization (EN/ES)        | base                                                            |
| `webscorpo-qa`            | QA / Test Engineer                 | base + `run_*`                                                  |
| `webscorpo-reviewer`      | Code Reviewer / Quality Gatekeeper | base                                                            |
| `webscorpo-devops`        | DevOps / Release                   | base + `run_*` + `http-get` + `http-post` + `send-notification` |

**Base** (todos los agentes): `shell-exec` (deny-by-default por
`allowed_commands` del proyecto) + tools de fichero (`read-file`, `write-file`,
`apply-patch`, `list-files`, `search-code`) + git (`git-status`, `git-diff`,
`git-commit`, `git-log`) + `semantic-search` (búsqueda en el KB del equipo).
**`run_*`** = `run-pytest`, `run-lint`, `run-typecheck`, `run-build` (corren en
el runtime `php-phpunit` del proyecto). Los agentes son
`scope=global_tenant_template` (plantillas del tenant, pueden ser miembros de
equipo sin `project_id`).

### Los KBs (11 en total)

- **1 KB `team_shared`** — _"WebScorpo — Conocimiento del equipo"_ con los **10
  documentos compartidos** (overview + glosario, mapa de arquitectura HMVC,
  routing/filtros + helpers de rutas, data-model Doctrine/BaseEntity/SLC,
  estándares + toolchain composer `@ci/@quality/@fix/@test*/@mutation`,
  estrategia de tests 3-suites + Selenium, runbook CI/CD Azure dual-region,
  política i18n EN/ES, baseline de seguridad + hallazgos, catálogo de
  dependencias daycry/\*). **Concedido al proyecto** (`kb_projects`) **y a los
  10 agentes** (`agent_knowledge_bases`).
- **10 KBs `private`** — uno por agente, con su `role-knowledge.md` específico de
  rol. Concedido **sólo a ese agente**.

El corpus markdown vive versionado en el repo bajo `scripts/webscorpo/kb/`
(`team/*.md` + `agents/<role>/role-knowledge.md`), derivado del análisis
(`C:/tmp/webscorpo-analysis.md`) con relectura solo-lectura del proyecto. No
contiene hechos inventados; cita rutas reales del proyecto.

## Cómo usar el equipo

1. Entra como admin del tenant **Mediapro** en el admin-panel.
2. Abre el proyecto **webscorpo**: en **Comandos & runtime** verás la allowlist
   PHP y el runtime `php-phpunit` (ver
   [comandos-y-runtime-por-proyecto.md](./comandos-y-runtime-por-proyecto.md)).
3. Cada agente tiene sus tools en **Tools del agente** (ver
   [asignar-tools-a-agentes.md](./asignar-tools-a-agentes.md)); `shell_exec`
   aparece en **Básicas** con badge **Privilegiada** y sólo puede ejecutar los
   binarios autorizados del proyecto.
4. Crea un Plan/Kanban contra el proyecto webscorpo y asigna tareas al equipo.
   El PM (`webscorpo-pm`) es el líder; backend/dba/qa corren tests en el runtime
   PHP; el reviewer hace cumplir `@quality`/`@ci`.
5. En el chat de un agente, el retrieval une el **KB del equipo** (stack
   WebScorpo) + el **KB privado del agente** (su rol) + los KBs globales (ver
   [knowledge-bases-rol-vs-stack.md](./knowledge-bases-rol-vs-stack.md)).

## Re-indexar embeddings (cuando haya proveedor)

Si el seed corrió **sin embedder** (Ollama caído), los documentos quedan en BD
con `status='indexed'` pero sus chunks tienen `embedding = NULL` (la búsqueda
por palabra clave sigue funcionando; la semántica no, hasta re-indexar). El
aviso aparece al final del seed:

```
AVISO: sin embedder (Ollama) alcanzable -> embeddings diferidos.
       Re-indexa cuando haya proveedor configurado.
```

Para re-indexar una vez configurado un proveedor de embeddings (vía
`/admin/llm-providers`, ver
[configurar-proveedores-llm.md](./configurar-proveedores-llm.md), o levantando
un Ollama local que sirva `nomic-embed-text-v1.5`):

1. **Asegura el embedder accesible.** El seed construye un `OllamaEmbedder` y
   hace un ping de salud; configura el host del embedder (Ollama) que el
   `OllamaEmbedder` resuelve.
2. **Fuerza el re-troceo de los docs diferidos** borrando sus chunks sin
   embedding (el seed tiene un _fast-path_ por `corpus_hash`: si los chunks ya
   existen con el hash actual, **no** re-embebe; borrarlos lo desactiva):

   ```sql
   -- chunks del seed WebScorpo sin embedding (tenant Mediapro)
   DELETE FROM chunks
    WHERE embedding IS NULL
      AND metadata->>'source' = 'webscorpo'
      AND tenant_id = (SELECT id FROM organizations WHERE slug = 'mediapro');
   ```

3. **Re-ejecuta el seed.** Con el embedder accesible y los chunks borrados, el
   seed re-trocea y **embebe** cada documento:

   ```powershell
   .\.venv\Scripts\python.exe scripts\setup_webscorpo.py
   ```

   Ya no aparecerá el aviso de embeddings diferidos.

> Nota: el endpoint genérico `POST /knowledge-bases/{kb}/documents/{doc}/reindex`
> re-corre la ingesta **desde MinIO**; el corpus del seed vive en el repo (clave
> de storage sintética `webscorpo/kb/...`), por eso el camino de re-index del
> seed es re-ejecutar el script, no ese endpoint.

## Verificación rápida

Tras correr el seed, comprueba los conteos (no debe duplicar entre ejecuciones):

```sql
SELECT
  (SELECT count(*) FROM teams       WHERE tenant_id = o.id) AS teams,
  (SELECT count(*) FROM agents      WHERE tenant_id = o.id) AS agents,
  (SELECT count(*) FROM projects    WHERE tenant_id = o.id) AS projects,
  (SELECT count(*) FROM knowledge_bases WHERE tenant_id = o.id) AS kbs,
  (SELECT count(*) FROM documents   WHERE tenant_id = o.id) AS documents
FROM organizations o WHERE o.slug = 'mediapro';
-- esperado: teams=1, agents=10, projects=1, kbs=11, documents=20
```

Los tests de integración cubren el seed end-to-end:

```powershell
$env:TEST_PG_PORT = "15432"; $env:TEST_REDIS_URL = "redis://localhost:6379/15"
.\.venv\Scripts\python.exe -m pytest `
  tests\integration\test_webscorpo_corpus.py `
  tests\integration\test_setup_webscorpo_entities.py `
  tests\integration\test_setup_webscorpo_kbs.py -q
```
