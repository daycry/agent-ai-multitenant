---
title: Knowledge Bases — rol del agente vs stack del proyecto
audience: tenant admin, project owner
phase: 06.9-agent-scoped-kbs
updated: 2026-05-28
---

# Knowledge Bases — KBs de rol vs KBs de stack

Esta guía explica cuándo grantear una KB al **agente** (eje rol) y
cuándo al **proyecto** (eje stack). Es una decisión recurrente cada
vez que añades documentación al catálogo.

> **TL;DR**: el rol manda qué hace el agente, el stack manda con qué
> herramientas trabaja. Si tu doc es agnóstica de stack, va con el
> rol. Si tu doc menciona un framework concreto, va con el proyecto.

## El modelo en una imagen

```
                    KBs visibles en runtime
                    ───────────────────────
                              │
              ┌───────────────┼───────────────┐
              │               │               │
        ╔═════▼═════╗   ╔═════▼═════╗   ╔═════▼═════╗
        ║ Proyecto  ║   ║ Agente    ║   ║ Globales  ║
        ║  (stack)  ║   ║  (rol)    ║   ║ (builtin) ║
        ╚═══════════╝   ╚═══════════╝   ╚═══════════╝
              │               │               │
         Python+FastAPI    REST design    Catálogo del
         conventions      principles      sistema (sin
         React+Next.js    Test strategy   filtrar)
         conventions      OWASP top-10
```

Cuando un agente ejecuta dentro de un proyecto, el retrieval une las
tres listas y rankea los chunks. **Sin distinguir de dónde vino cada
chunk** — el ranking coseno decide.

## Cuándo crear KB de rol

Si el contenido aplicaría igual a un agente independientemente del
lenguaje / framework / cliente:

- ✅ "Principios de diseño REST" (recursos, verbos, status codes).
- ✅ "Patrones de testing unitario" (AAA, fakes vs mocks, table tests).
- ✅ "Guía de revisión de PRs" — qué mirar, cómo redactar comentarios.
- ✅ "OWASP top-10 explicado para devs".
- ✅ "Cómo escribir specs de UX accesibles".

**Grant en**: `/admin/agents/{id}` → tab "Knowledge Bases" → botón
"Grant KB". La KB se hace visible cuando el agente ejecute en
**cualquier** proyecto, sin necesidad de tocar el proyecto.

## Cuándo crear KB de stack

Si el contenido menciona herramientas concretas o convenciones de un
stack que no se aplican fuera de él:

- ✅ "Convenciones FastAPI: layout del repo, async/await, OpenAPI".
- ✅ "PostgreSQL: índices parciales, JSONB, RLS, vacuum".
- ✅ "React + Next.js 14: app router, server components, TanStack
  Query".
- ✅ "Convenciones internas de cliente Acme" (cómo escriben PRs,
  qué linters, qué pipelines).

**Grant en**: `/admin/projects/{id}` → sub-sección "Knowledge Bases"
→ "Grant KB". O — más típicamente — viene **automáticamente** desde
la plantilla del proyecto (las plantillas built-in declaran qué KBs
de stack pre-grantean al adoptarlas).

## El caso ambiguo: "documentación interna de la empresa"

Una guía como "Cómo desplegamos en Acme" tiene mezcla de cosas:

- Generalidades sobre el flujo (rol): "siempre un revisor antes de
  prod".
- Specifics de stack (stack): "usamos terraform + nuestro plugin X".

**Recomendación**: si el doc cabe en una página y no es trivial
separarlo, grant donde tenga **más** uso. Si lo va a leer cualquier
agente de Acme → atalo al template del agente (rol). Si lo lee sólo
quien toca infraestructura → atalo a los proyectos de DevOps (stack).

Cuando el doc crece y se separa naturalmente, **partelo en dos KBs**.
Es barato — son sólo metadata + chunks; los chunks no se duplican
entre KBs.

## Los equipos (teams) NO tienen KB

Es una pregunta recurrente: "¿puedo asignar una KB a un equipo entero?".
**No, y es intencionado.** Sólo hay dos ejes de grant — **agente** (rol)
y **proyecto** (stack) — más el catálogo built-in. No existe
`team_knowledge_bases`.

Por qué la arquitectura lo descarta (ver [ADR 0026](../05-architecture-decisions/0026-agent-scoped-kbs.md),
alternativa "KBs por team", rechazada):

- En cuanto necesitas "el reviewer ve X pero el backend_dev del mismo
  equipo ve Y", ya necesitas el grant **por agente** — que es el eje que
  ya existe. El eje de equipo se vuelve redundante.
- Un agente no siempre pertenece a un único equipo, y el catálogo
  built-in es global, no ligado a equipo. Un eje de equipo dejaría
  built-ins y agentes sueltos sin cubrir.

> ⚠️ No confundas con la **memoria** `team_shared`. La memoria de equipo
> es conocimiento que los agentes **acumulan en runtime** y se comparte
> dentro del equipo. Una **KB** es un corpus **curado** por rol/stack.
> Que exista memoria `team_shared` NO implica que falten KBs de equipo —
> son conceptos distintos.

Si de verdad necesitas anclar conocimiento "del equipo", grántaselo al
**agente** que lo usa (si es doctrina de su rol) o al **proyecto** del
equipo (si es del stack). Cubre el 100 % de los casos sin un tercer eje.

## Anti-patrones

### ❌ Crear `backend_dev_python`, `backend_dev_php`, `backend_dev_js`

Era el error que motivó este plan. La separación correcta es:

- Un **único** `backend_dev` (rol agnóstico) con KBs de rol (REST,
  testing, OWASP).
- Cada **proyecto** tiene sus KBs de stack (Python+FastAPI / PHP+
  Symfony / Node+Express). La plantilla pre-grantea las de su stack.

Resultado: un `backend_dev` corre en proyecto Python y ve sus
convenciones, corre en proyecto PHP y ve las suyas — sin clonar
agentes.

### ❌ Granteer la misma KB al rol Y al proyecto

Si "REST design principles" ya está granteada al `backend_dev`, NO
hace falta grantearla otra vez al proyecto. El resolver dedupea, así
que no rompe — pero crea ruido al revisar las asignaciones.

### ❌ Copiar contenido entre KBs

Si dos agentes necesitan el mismo doc, **grant la misma KB a ambos**.
El backend NO copia chunks. Esto significa que actualizar la KB
upstream se propaga automáticamente — duplicar contenido es perderlo
en el divergence.

### ❌ Forkear un built-in para añadirle una KB

El catálogo built-in del platform (`global_builtin`) NO acepta
grants desde el tenant. La forma correcta:

1. **Forkea** el agente built-in en `/admin/agents/{id}` → botón
   "Hacer copia" (crea un `global_tenant_template`).
2. Grants tus KBs en el fork.

El fork mantiene el `forked_from_agent_id` y permite ver el diff
contra el upstream si éste evoluciona.

## Refresco de contenido

Los chunks de las KBs canónicas built-in vienen del open-source
upstream (Python docs, FastAPI docs, etc.). Hoy son chunks indexados
manualmente desde un fork; un cron de refresco automático viene en
Plan 04.5 o follow-up dedicado.

Si una KB built-in se queda desactualizada, abre un issue — el
operador puede re-correr el seed con el .md actualizado para
re-indexar (los chunk ids son estables vía hash del contenido, así
que sólo cambian los chunks que cambiaron).

## Ver qué grants tiene una KB

Desde `/admin/knowledge-bases`, cada fila tiene un botón
**"Asignaciones"** que abre un dialog con:

- Lista de **proyectos** con grant (`kb_projects`).
- Lista de **agentes** con grant (`agent_knowledge_bases`).
- Botón "Revoke" por fila (requiere `tenant_admin`).

Es la forma rápida de auditar "¿quién está leyendo esta KB?" antes
de borrarla.

## Cómo se aplican los grants automáticos al adoptar una plantilla

Cada `BuiltinProjectTemplate` declara un campo `default_kb_grants`
(lista de slugs de KBs built-in). Cuando un tenant crea un proyecto
desde la plantilla:

1. Se crea el proyecto vacío.
2. El backend lee `default_kb_grants` del template.
3. Resuelve cada slug → UUID de la KB built-in (`uuid5(KB_SLUG_NAMESPACE,
slug)`).
4. Crea las filas `kb_projects` correspondientes.

Si un slug del template no existe en el catálogo (el operador re-
seedeó parcialmente), se ignora silenciosamente. La adopción no
falla por un grant huérfano.

Las 5 plantillas built-in que llegan con grants no vacíos:

| Plantilla        | KBs pre-granteadas                                                           |
| ---------------- | ---------------------------------------------------------------------------- |
| api-rest         | python-fastapi-conventions, api-rest-guidelines, postgresql-best-practices   |
| webapp           | python-fastapi, react-nextjs, api-rest-guidelines, postgresql-best-practices |
| data-pipeline    | postgresql-best-practices                                                    |
| legacy-migration | api-rest-guidelines, python-fastapi, php-symfony, postgresql-best-practices  |
| e2e-test-suite   | react-nextjs-conventions, node-express-conventions                           |

Las 3 plantillas restantes (research-spec, devops-bootstrap,
doc-modernization) son agnósticas de stack y no traen grants.

## Categorías (Plan 06.10)

Cuando el catálogo crece (>10 KBs) el listado plano se vuelve difícil de
manejar. Las **categorías** agrupan KBs en el listado y en el filtro
`?category=<slug>` del endpoint. No alteran el ranking del retrieval —
son puramente organizativas.

### Built-in (sembradas por la plataforma)

Visibles a todos los tenants (`tenant_id IS NULL`):

| Slug           | Para qué                                                          |
| -------------- | ----------------------------------------------------------------- |
| `stack`        | Convenciones de un stack concreto (Python+FastAPI, React+Next.js) |
| `role`         | Doctrina del rol (REST design, testing, OWASP)                    |
| `compliance`   | Normativa que el agente debe respetar (PCI-DSS, GDPR)             |
| `architecture` | Patrones arquitectónicos (hexagonal, event-sourcing, CQRS)        |
| `process`      | Procesos de equipo (revisión de PRs, ramificación, releases)      |

Las 6 KBs built-in vienen pre-categorizadas: 5 como `stack`,
`api-rest-guidelines` como `role`.

### Custom (creadas por el tenant)

Cualquier `tenant_admin` puede añadir las suyas desde
`/admin/knowledge-bases/categories` o inline desde el dialog "Crear KB"
(botón `+` junto al selector). Los campos: **slug** (ASCII kebab),
**nombre** (cómo se ve en la UI) y **color** (chip en el listado).

Un slug no puede chocar con un built-in ni con otra custom del mismo
tenant — el endpoint POST devuelve 409.

### Built-ins son read-only

PUT y DELETE sobre una categoría built-in devuelven **403**. La
plataforma las re-seedea con cada arranque del runner; modificarlas
desde la UI tendría vida útil de minutos. Si una built-in no se ajusta
a tu uso, créate una custom y deja la built-in.

### Borrar una categoría no borra sus KBs

DELETE sobre una categoría custom hace soft-delete y nullifica
`category_id` en cada KB que la usaba. Las KBs quedan visibles en el
grupo "Sin categoría" hasta que se les reasigne otra.

## Reference técnica

- ADR formal del modelo agente×proyecto:
  [`docs/05-architecture-decisions/0026-agent-scoped-kbs.md`](../05-architecture-decisions/0026-agent-scoped-kbs.md).
- Matriz RBAC (qué endpoints requieren qué rol):
  [`docs/04-reference/rbac.md`](../04-reference/rbac.md).
- Planes que materializan todo esto:
  [`docs/roadmap/06.9-agent-scoped-kbs.md`](../roadmap/06.9-agent-scoped-kbs.md)
  (rol vs stack) +
  [`docs/roadmap/06.10-kb-categories.md`](../roadmap/06.10-kb-categories.md)
  (categorías).
