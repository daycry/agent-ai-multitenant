# Plan demo-webscorpo-team-kb — tests humanos

Esta guía cubre el **test humano** del seed `demo-webscorpo-team-kb`
(equipo WebScorpo con KB completo). Valida que, tras correr el seed, en
el tenant **Mediapro** existe un **equipo "WebScorpo" con 10 agentes**,
un **proyecto "webscorpo"** con su config de comandos/runtime PHP, las
**asignaciones de tools por agente**, un **KB del equipo** con 10
documentos compartidos y un **KB privado por agente**, que **re-ejecutar
el seed no duplica nada** (idempotente) y que, si hay embedder, una
búsqueda semántica devuelve resultados — y si no, los documentos quedan
listos para re-indexar.

> **Estado del plan**: `pending_human_validation`. Las 4 tareas
> (`task_demo_ws_01`..`task_demo_ws_04`) están en verde (corpus markdown
> del KB derivado del análisis, seed `scripts/setup_webscorpo.py` con
> tenant + equipo + 10 agentes + proyecto + tools, KBs team-shared +
> por-agente con ingesta y degradación elegante del embedder, guía +
> changelog). Este test humano es el último paso antes de pasar a
> `completed`.

> **Nota:** este plan NO es una fase de desarrollo de la plataforma — es
> un **seed demostrativo** que USA la plataforma para materializar un
> equipo real (el proyecto PHP/CodeIgniter 4 WebScorpo) con su KB. No
> entra en el gate de fases del roadmap.

## TL;DR

```powershell
.\scripts\dev\up.ps1                                     # api-server :8001 + admin-panel :3000 + postgres + redis
.\.venv\Scripts\python.exe -m api_server.seeds           # built-in tools (incl. shell_exec) — necesario antes del seed
.\.venv\Scripts\python.exe scripts\setup_webscorpo.py    # tenant Mediapro + equipo + 10 agentes + proyecto + tools + KBs
```

No hay launcher `run-human-tests-*.ps1` dedicado: `setup_webscorpo.py`
es a la vez el setup y el reporter — al terminar imprime el resumen
(UUIDs del tenant/equipo/proyecto, nº de agentes con sus tools, IDs de
los KBs, nº de documentos ingestados y si los **embeddings quedaron
diferidos**). El seed es **idempotente** (upsert por `uuid5` estable):
re-ejecutarlo no duplica nada.

URLs útiles tras el seed (cambia al tenant **Mediapro** con el
picker si eres `system_admin`):

```
http://localhost:3000/admin/agents                 # los 10 agentes del equipo WebScorpo
http://localhost:3000/admin/projects               # el proyecto "webscorpo"
http://localhost:3000/admin/knowledge-bases         # el KB del equipo + los KBs privados por agente
http://localhost:3000/admin/llm-providers           # (system_admin) configurar el proveedor para embeddings → re-index
```

### Embeddings necesitan un proveedor LLM

La ingesta del corpus tiene **degradación elegante**: el seed construye
un `OllamaEmbedder` y hace un ping; si **Ollama no está alcanzable**, los
documentos + chunks se guardan con `embedding = NULL` y el seed imprime:

```
  AVISO: sin embedder (Ollama) alcanzable -> embeddings diferidos. Re-indexa cuando haya proveedor configurado.
```

Para tener **búsqueda semántica** hace falta un **proveedor LLM con
embeddings** configurado. Configúralo como **System Admin** en
[`/admin/llm-providers`](http://localhost:3000/admin/llm-providers) (Plan
11.2): crea un proveedor **Ollama** con su `base_url`, prueba la conexión
y, una vez activo, **re-indexa** el KB para calcular los embeddings de
los chunks diferidos. Sin proveedor, el BM25/keyword sigue sirviendo los
chunks (los documentos están), pero la búsqueda vectorial no devuelve
resultados hasta el re-index.

## Pre-requisitos

| Requisito                                        | Por qué                                                                          |
| ------------------------------------------------ | -------------------------------------------------------------------------------- |
| Stack dev arriba (`up.ps1`)                      | api-server + admin-panel + postgres + redis                                      |
| `.\.venv\Scripts\python.exe -m api_server.seeds` | El seed garantiza primero las tools built-in (incl. `shell_exec` del Plan 06.16) |
| Migración 0072 aplicada                          | El proyecto usa `allowed_commands` + `default_runtime_template`                  |
| `setup_webscorpo.py` ejecutado al menos 1×       | Crea tenant/equipo/agentes/proyecto/KBs. Idempotente, re-ejecutable.             |
| (Opcional) Ollama alcanzable o proveedor en BD   | Para que los embeddings NO queden diferidos / para re-indexar                    |
| Login como `system_admin` o admin de Mediapro    | Para ver el tenant Mediapro y, si aplica, configurar el proveedor                |

## Qué siembra `setup_webscorpo.py`

Bajo el tenant **Mediapro** (`slug=mediapro`), con identidad estable
`uuid5` (re-seed no duplica):

| Recurso                               | Detalle                                                                                                                                                                                               |
| ------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Equipo** "WebScorpo"                | 10 agentes `scope=global_tenant_template` (pm, architect, backend-CI4, dba-Doctrine, frontend, auth-security, i18n, qa, reviewer, devops).                                                            |
| **Proyecto** "webscorpo"              | `allowed_commands` = `php`, `composer`, `vendor/bin/phpunit`, `vendor/bin/pest`, `vendor/bin/infection`, `npm`, `npx`; `default_runtime_template` = `php-phpunit`.                                    |
| **Asignaciones de tools** por agente  | `shell_exec` + file/git a todos; `run_*` a backend/dba/qa/devops; `http_get` a auth-security/devops; etc.                                                                                             |
| **KB del equipo** (`team_shared`)     | 10 documentos compartidos (overview, arquitectura HMVC, routing/filtros, data-model Doctrine, estándares/toolchain, tests, CI/CD Azure, i18n, seguridad, dependencias). Concedido al equipo/proyecto. |
| **KB privado por agente** (`private`) | Un KB por rol con los docs específicos de su rol.                                                                                                                                                     |

El corpus markdown vive en `scripts/webscorpo/kb/` (`team/` + `agents/<role>/`)
y se deriva del análisis (`C:/tmp/webscorpo-analysis.md`), no inventado.
El seed **no toca** el proyecto en disco (`C:/laragon/www/webscorpo`, solo-lectura).

---

## `human_demo_ws_01` — El equipo WebScorpo y su KB existen y son usables

**Qué prueba**: tras correr el seed, el tenant Mediapro tiene el equipo
WebScorpo con 10 agentes, el proyecto webscorpo con su config de
comandos/runtime, cada agente con sus tools asignadas, el KB del equipo
con 10 documentos + un KB privado por agente; re-ejecutar el seed no
duplica nada; y la búsqueda semántica funciona si hay embedder (o los
docs están listos para re-indexar si no).

**Precondiciones**:

- Stack dev arriba; `api_server.seeds` corrido (tools built-in).
- `setup_webscorpo.py` ejecutado al menos una vez (mira su resumen
  final).
- Login como `system_admin` (o un admin del tenant Mediapro); con el
  picker, tenant activo = **Mediapro**.

**Pasos**:

1. Ejecuta el seed y **lee el resumen** que imprime al final:
   ```powershell
   .\.venv\Scripts\python.exe scripts\setup_webscorpo.py
   ```
   Debe listar `tenant: Mediapro`, `team: WebScorpo`,
   `project: webscorpo` con su `allowed_commands` + `default_runtime_template`,
   `agentes: 10` (cada uno con su nº de tools), `KB equipo`, `KBs
por-agente` y `documentos KB ingestados`. Si Ollama no está, verás el
   **AVISO de embeddings diferidos**.
2. En `/admin/agents` (tenant Mediapro): hay un **equipo "WebScorpo"**
   con **10 agentes** (pm, architect, backend, dba, frontend,
   auth-security, i18n, qa, reviewer, devops).
3. En `/admin/projects`: existe el proyecto **"webscorpo"**. Entra a su
   detalle → sección **"Comandos & runtime"**: `allowed_commands` incluye
   `php`/`composer`/`vendor/bin/phpunit`/… y `default_runtime_template`
   es **`php-phpunit`**.
4. Abre un par de agentes (p. ej. `backend` y `qa`) → sección "Tools del
   agente": cada uno tiene **`shell_exec` + file/git**; `backend`/`dba`/
   `qa`/`devops` además tienen los `run_*`.
5. En `/admin/knowledge-bases`: el **KB del equipo** WebScorpo tiene
   **10 documentos** (concedido al equipo/proyecto); cada agente tiene su
   **KB privado** con sus docs de rol.
6. **Idempotencia**: vuelve a ejecutar `setup_webscorpo.py`. El resumen
   debe ser el mismo (no aparecen agentes/KBs/proyectos duplicados en la
   UI tras F5).
7. **Búsqueda semántica**:
   - Si hay embedder/proveedor: en el KB del equipo, una búsqueda (p. ej.
     "arquitectura HMVC" o "scripts composer") **devuelve resultados**.
   - Si NO hay embedder: los documentos están listados, pero la búsqueda
     vectorial no devuelve hits. Configura un proveedor Ollama en
     `/admin/llm-providers` (System Admin) y **re-indexa** el KB; tras el
     re-index la búsqueda devuelve resultados.

**Resultado esperado**: equipo + 10 agentes + proyecto configurado +
tools por agente + KB del equipo (10 docs) + KBs privados existen; el
re-seed no duplica; la búsqueda semántica funciona con embedder o los
docs quedan re-indexables sin él.

**Checklist**:

- [ ] Tras correr `scripts/setup_webscorpo.py`, en el tenant Mediapro
      hay un equipo "WebScorpo" con 10 agentes.
- [ ] El proyecto "webscorpo" tiene `allowed_commands`
      (php/composer/phpunit…) + runtime `php-phpunit`.
- [ ] Cada agente tiene asignadas sus tools (`shell_exec` + las de su
      rol).
- [ ] El KB del equipo tiene los 10 documentos compartidos; cada agente
      ve su KB privado de rol.
- [ ] Re-ejecutar el seed no duplica nada (idempotente).
- [ ] Si hay embedder, una búsqueda semántica en el KB del equipo
      devuelve resultados; si no, los docs están y se pueden re-indexar
      (proveedor en `/admin/llm-providers`).

**Pitfalls conocidos**:

- **`shell_exec` debe existir en el catálogo antes del seed**: corre
  `.\.venv\Scripts\python.exe -m api_server.seeds` primero — el seed de
  WebScorpo garantiza las tools built-in pero conviene tener el catálogo
  base sembrado.
- **Embeddings diferidos no es un fallo**: si no hay Ollama, el seed
  persiste documentos + chunks con `embedding = NULL` y lo dice por
  consola. La búsqueda vectorial vacía es esperada hasta el re-index;
  el BM25/keyword sigue sirviendo los chunks.
- **Tenant equivocado**: si no ves nada, comprueba que el picker del
  System Admin tiene activo el tenant **Mediapro** (el seed no usa
  `tenant-a`).
- **Idempotencia**: los IDs derivan de `uuid5(WEBSCORPO_NAMESPACE, slug)`;
  si ves duplicados, revisa que no editaste a mano los slugs/namespace
  del script.
- El proyecto en disco (`C:/laragon/www/webscorpo`) es **solo-lectura**:
  el seed nunca lo modifica.

---

## Cierre del plan

Tras pasar el test humano:

1. Edita `docs/roadmap/demo-webscorpo-team-kb.md`:
   ```yaml
   status: completed
   completed_at: 2026-MM-DD
   ```
2. Verifica la entrada en
   [`docs/07-changelog/demo-webscorpo-team-kb.md`](../../07-changelog/)
   y la guía
   [`docs/03-guides/demo-webscorpo.md`](../demo-webscorpo.md).
3. Verifica que el PR `plan/demo-webscorpo-team-kb` está mergeado a
   `master`.

## Troubleshooting

| Síntoma                                        | Causa probable                                        | Fix                                                                       |
| ---------------------------------------------- | ----------------------------------------------------- | ------------------------------------------------------------------------- |
| El seed falla en `shell_exec` / tools built-in | El catálogo base no estaba sembrado                   | `.\.venv\Scripts\python.exe -m api_server.seeds` antes del seed           |
| El proyecto no tiene `allowed_commands`        | Migración 0072 sin aplicar                            | `cd apps/api-server && alembic upgrade head`; re-corre el seed            |
| No veo el tenant Mediapro / sus recursos       | Picker del System Admin en otro tenant                | Cambia al tenant Mediapro con el picker                                   |
| Búsqueda semántica vacía en el KB del equipo   | Embeddings diferidos (sin embedder en el seed)        | Configura un proveedor Ollama en `/admin/llm-providers` y re-indexa el KB |
| `setup_webscorpo.py` "duplica" entidades       | (No debería) slugs/namespace editados                 | Restaura el script; los IDs son `uuid5` estables (upsert)                 |
| El re-index no calcula embeddings              | Proveedor inactivo o `secret_vault_path` sin resolver | Activa el proveedor en `/admin/llm-providers`; verifica Vault unsealed    |

Errores transversales viven en `docs/03-guides/gotchas/`.
