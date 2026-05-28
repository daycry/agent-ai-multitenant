---
adr_id: "0026"
title: "Agent-scoped KBs — el rol del agente y el stack del proyecto son ejes independientes"
status: accepted
date: 2026-05-28
authors: [system_architect]
plan_referenced: 06.9-agent-scoped-kbs
docs_language: es
---

# ADR 0026 — Agent-scoped Knowledge Bases (rol ≠ stack)

## Contexto

Plan 04 introdujo las Knowledge Bases (KBs) con grants explícitos por
proyecto vía la junction `kb_projects`. Eso resolvía "qué docs ve un
proyecto" pero forzaba un acople incómodo:

- Si quieres que el agente `backend_dev` "sepa diseñar APIs REST",
  la KB **API REST design principles** debe granteársele a cada
  proyecto donde el agente corre.
- Si tienes varios stacks (Python, PHP, Node), aparece la tentación
  errónea de crear `backend_dev_python`, `backend_dev_php`,
  `backend_dev_js` — multiplicación de agentes que comparten el 90 %
  del prompt para no perder los grants de stack en cada copia.
- Si refactorizas la KB "API REST design", tienes que recordar grant
  en proyecto nuevo cada vez. Olvidos silenciosos.

Durante Plan 06.6 (admin UI gaps) los tests humanos sacaron a la luz
esto al revisar las plantillas de proyecto: la pregunta "¿qué KBs
tiene un proyecto recién creado?" no tenía respuesta clara — la
plantilla no las declaraba.

## Decisión

**Separar el rol y el stack como ejes independientes**, cada uno con
sus propios grants:

1. **KBs por rol del agente** → atadas al **agent template** vía la
   nueva tabla `agent_knowledge_bases`. Ejemplo: la KB
   "API REST design principles" se grants al `backend_dev` y aplica
   automáticamente en cualquier proyecto donde ese agente ejecute —
   el conocimiento es agnóstico de stack.
2. **KBs por stack** → atadas al **proyecto** vía la junction
   existente `kb_projects`. Ejemplo: "Python + FastAPI conventions"
   se grants al proyecto X. Las plantillas de proyecto declaran qué
   KBs grantear automáticamente al adoptarlas (campo nuevo
   `projects.default_kb_grants TEXT[]`).
3. **KBs globales (built-in del platform)** — el catálogo seedeado
   en PLATFORM_TENANT_ID que cualquier tenant puede grantear a sus
   proyectos / agentes (esto ya existía conceptualmente, formalizado
   ahora con `BUILTIN_KBS` y slugs estables).

En tiempo de retrieval, el resolver une las tres fuentes:

```
KBs visibles(project, agent) = KBs del proyecto
                              UNION KBs del agent template
                              UNION KBs globales granteadas
```

La dedup la hace el SQL (`DISTINCT kb.id`); el resto del pipeline
(BM25 + vector + RRF) trata la lista resultante como una sola pool
de chunks. **No se pondera por fuente** — el ranking coseno decide.

### Implementación concreta

- **Migration 0026** — tabla `agent_knowledge_bases(agent_id, kb_id,
tenant_id, granted_at, granted_by)` con PK compuesta + FKs CASCADE
  - RLS por `tenant_id`. Mismo shape que `kb_projects`.
- **Migration 0027** — `projects.default_kb_grants TEXT[]` (lista de
  slugs de KBs built-in). Las plantillas lo declaran; la wizard de
  adopción lo aplica vía `apply_template_kb_grants()`.
- **Resolver** `resolve_visible_kbs(session, *, tenant_id, project_id,
agent_id=None)` en `api_server/rag/visibility.py` — devuelve el set
  de KB ids. Misma SQL clause se reutiliza en el visibility filter de
  los chunks (`bm25_chunks` + `vector_chunks` + `recall_chunks` +
  `rag_search`).
- **Endpoints**:
  - `GET / POST / DELETE /agents/{id}/knowledge-bases` (grant /
    revoke al agent template).
  - `GET /knowledge-bases/{id}/projects` y `/agents` (inverse
    listings para el panel "Asignaciones" en la KB list page).
- **Seed `builtin_kbs.py`** — 6 KBs canónicas con slugs estables y
  UUIDs deterministas (`uuid5(KB_SLUG_NAMESPACE, slug)`).
- **Templates** — 5 de 8 plantillas existentes declaran sus
  `default_kb_grants` (api-rest, webapp, data-pipeline,
  legacy-migration, e2e-test-suite). Las 3 restantes (research-spec,
  devops-bootstrap, doc-modernization) son agnósticas de stack.

### Reglas duras

- **Sólo agentes `global_tenant_template` y `project_local` aceptan
  grants** desde el endpoint de tenant. Los `global_builtin` los
  administra el sistema vía seeds — un tenant que quiera "customizar
  un built-in" lo forkea (creando un `global_tenant_template`) y
  grants sobre el fork. Mismo patrón que el agent fork-and-edit.
- **El backend NO copia chunks entre KBs**. Si dos agentes necesitan
  la misma doc, se grants la misma KB a ambos — los chunks viven una
  sola vez en la BD. Esto preserva consistencia: actualizar la KB
  upstream se propaga automáticamente a los dos agentes.
- **Re-granting es idempotente** (composite PK + `ON CONFLICT DO
NOTHING`) para que la wizard de adopción y los retries no
  rompan al ejecutar dos veces.
- **Revoke de un grant inexistente devuelve 204** (no 404) — mismo
  patrón que el resto del API. La UX en cliente queda limpia: borrar
  algo que ya no está no es un error.

## Consecuencias

### Lo que mejora

- Un solo `backend_dev` sirve para todos los stacks. Si el rol gana
  documentación nueva (e.g. una guía interna sobre seguridad de
  APIs), basta granteársela al template — todos los proyectos donde
  corre la ven.
- Las plantillas de proyecto pre-cocinan los grants de stack.
  Adoptar "Plantilla: API REST" sin tocar nada deja al proyecto con
  Python+FastAPI + API REST + PostgreSQL ya granteadas.
- La UI de "Asignaciones" en la KB list page hace visible algo que
  antes había que rascar por SQL: "¿qué proyectos y agentes están
  usando esta KB?".

### Lo que añade de complejidad

- **Una tabla nueva** (`agent_knowledge_bases`) y un campo nuevo
  (`projects.default_kb_grants`). Pequeño coste de schema; impacto
  cero en queries existentes (las reads del Plan 04 siguen pasando
  por `kb_projects` exclusivamente).
- El **visibility filter de chunks** ahora tiene dos branches
  (`kb_projects` OR `agent_knowledge_bases`) cuando se pasa
  `agent_id`. SQL extra de un `EXISTS` por path — bench de Plan 04
  (~80 ms con HNSW + 50k chunks) no se ve afectado meaningfully.
- El **resolver `resolve_visible_kbs`** es una superficie nueva que
  el wizard y los retrievers deben usar consistentemente. Si alguien
  pasa `agent_id=None` por descuido, sólo verá KBs de proyecto —
  silenciosamente pierde las del rol. El test parametrizado del
  resolver (`test_visible_kbs_resolver.py`) cubre la matriz para
  evitarlo.

### Trade-offs explícitos

- **No copiamos chunks entre KBs**: si dos agentes necesitan
  variaciones distintas de la misma doc, se crean dos KBs y se
  grants cada una donde toque. Duplica chunks pero mantiene
  consistencia clara — "el contenido vive en su KB de origen, no en
  cada grant".
- **No ponderamos por fuente**: si en el futuro hace falta favorecer
  "lo del rol" sobre "lo del proyecto" (o viceversa), se añade un
  parámetro `source_weights` al retriever — sin tocar el resolver.

## Alternativas consideradas

### Alt-1: KBs por agent template **en lugar** de por proyecto

Eliminar `kb_projects` y dejar sólo `agent_knowledge_bases`. Cada
proyecto vería las KBs de los agentes que se le asignen vía la
relación `project ↔ team ↔ agent`.

- ❌ Para que el `tester_qa` vea las "Convenciones de Python +
  FastAPI", esa KB tiene que estar atada al rol `tester_qa`. Pero
  el rol no es de stack — multiplica el trabajo de mantener KBs.
- ❌ Romper compatibilidad con Plan 04 (todos los kb_projects
  existentes se perderían). Inviable sin migration data + downtime.
- ❌ Pierde la capacidad de tener KBs específicas de UN proyecto
  ("memoria privada del proyecto").

Rechazada.

### Alt-2: KBs por team

Atar las KBs al team en lugar de al agent template. Un team
"backend-api" tiene sus KBs; todos los agentes del team las heredan.

- ✅ Encaja con la jerarquía actual (project → team → agents).
- ❌ Pero los agentes no siempre pertenecen a un solo team — el
  catálogo built-in son `global_tenant_template` no atados a team.
- ❌ Para refinar "el agente reviewer ve algo distinto que el
  backend_dev aunque están en el mismo team", harían falta grants
  per-agent dentro del team — volvemos a este ADR.

Rechazada como reemplazo. Anotada como follow-up si surge la
necesidad real: `team_knowledge_bases` con la misma maquinaria.

### Alt-3: KBs por rol (enum), no por agent template

Atar las KBs al rol del agente (`backend_dev`, `qa`, etc.) en lugar
de a la instancia del template.

- ✅ Más declarativo: "el rol qa siempre tiene la KB X".
- ❌ Si un tenant quiere customizar el rol `backend_dev` (e.g.
  forkearlo en `backend_dev_strict`), pierde los grants
  automáticamente — el fork tiene rol distinto.
- ❌ El catálogo built-in usa roles fijos pero los tenant templates
  pueden tener cualquier rol; un grant por rol no aplica a roles
  custom.

Rechazada. El agent template como pivote es estable: forkear un
agente preserva su template_id (vía `forked_from_agent_id`); el
fork puede recibir grants independientes que el upstream no tiene.

### Alt-4: Composición en runtime — wizard concatena KBs

No persistir `default_kb_grants`; cada vez que el wizard adopta una
plantilla, mostrar al usuario un picker de KBs canónicas y dejar que
elija. Sin grants automáticos.

- ✅ Maximiza flexibilidad. Cero magia.
- ❌ Fricción de UX: el wizard ya tiene 6 pasos. Añadir "elige tus
  KBs de stack" es otro paso para algo que el 95 % de los usuarios
  van a aceptar el default.
- ❌ Pierde la oportunidad de hacer "Plantilla = receta completa
  incluyendo KBs". Las plantillas pierden valor.

Rechazada como default. El campo `default_kb_grants` permite
override por tenant en una iteración futura (si una plantilla
universal añade una KB que un tenant no quiere, se desgrants después
de la adopción — el grant individual se mantiene).

## Esquema (resumido)

```sql
-- Nueva tabla (migration 0026):
CREATE TABLE agent_knowledge_bases (
    agent_id   UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    kb_id      UUID NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    tenant_id  UUID NOT NULL,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    granted_by UUID REFERENCES users(id) ON DELETE SET NULL,
    PRIMARY KEY (agent_id, kb_id)
);
-- RLS por tenant_id (idéntico al patron de kb_projects).

-- Campo nuevo (migration 0027):
ALTER TABLE projects
    ADD COLUMN default_kb_grants TEXT[] NOT NULL DEFAULT '{}';
-- Sólo se lee en filas con is_template=true. La wizard de adopción
-- crea kb_projects rows en el nuevo proyecto para cada slug.
```

## Riesgos

| Riesgo                                                                       | Probabilidad | Impacto | Mitigación                                                                                |
| ---------------------------------------------------------------------------- | ------------ | ------- | ----------------------------------------------------------------------------------------- |
| Agentes con muchos grants degradan el retrieval (demasiados chunks visibles) | Baja         | Bajo    | El recall ya cap-ea por `bm25_k` + `vector_k`; más KBs no aumentan el output.             |
| Slugs del catálogo cambian y orphan los `default_kb_grants`                  | Media        | Bajo    | El helper `apply_template_kb_grants` ignora slugs sin KB resoluble (drift-safe).          |
| Tenants olvidan que `tester_qa` no hereda KBs del proyecto                   | Media        | Bajo    | La guía `knowledge-bases-rol-vs-stack.md` lo explica en lenguaje natural.                 |
| Built-in agents reciben grants accidentales del tenant                       | Baja         | Medio   | Backend rechaza con 403 (test `test_grant_on_builtin_agent_is_403`); UI esconde el botón. |

## Trazabilidad

- Roadmap: `docs/roadmap/06.9-agent-scoped-kbs.md` (13 tareas, 4 fases).
- Tests integration: `test_agent_kb_grants.py`,
  `test_visible_kbs_resolver.py`,
  `test_builtin_kbs_and_template_adoption.py`.
- Frontend: `apps/admin-panel/components/ui/kb-combobox.tsx`,
  `apps/admin-panel/app/admin/agents/[id]/agent-kbs-section.tsx`,
  `apps/admin-panel/app/admin/knowledge-bases/kb-assignments-dialog.tsx`.
- Origen de la discusión: tests humanos del Plan 06.6 (¿stack = rol?).
