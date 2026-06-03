---
adr_id: "0051"
title: "Exposición del catálogo de runtime templates por endpoint + validación de default_runtime_template"
status: accepted
date: 2026-06-03
authors: [system_architect]
plan_referenced: 06.18-tools-overhaul
docs_language: es
---

# ADR 0051 — Catálogo de runtime templates por endpoint + validación

> **Estado: `accepted`** (aprobado por el operador 2026-06-03, Fase 0 del Plan 06.18).
> Implementado por `task_06_18_08`; consumido por el Plan 06.17 (`task_06_17_14`).

## Contexto

El catálogo de runtime templates (la fuente de verdad es `shared_test_runtimes.CATALOG`, 14 entradas,
`catalog.py:197-235`) está **triple-hardcodeado y divergente**:

- `apps/admin-panel/app/admin/projects/[id]/commands/page.tsx:71-86` → `RUNTIME_TEMPLATES`, 14 ids a mano.
- `apps/admin-panel/app/admin/projects/[id]/dep-cache/page.tsx:36-49` → `RUNTIMES`, **solo 12** (faltan
  `generic-shell`/`generic-http`), con `label`/`lockFile` **inventados** que no existen en el catálogo.
- No existe `GET /runtime-templates`; `default_runtime_template` se persiste **sin validar**
  (`projects.py:189`); el guard correcto existe pero solo en `dep_cache.py:100-103` (`422` si fuera del
  CATALOG), no en el campo del proyecto.

ADR 0045 aceptó esto como deuda. El **patrón correcto ya existe** para MCP: `GET /mcp-catalog`
(`mcp-servers/page.tsx:96-105` — "el backend es la fuente de verdad, no se hardcodea").

## Opciones consideradas

- **A. `GET /runtime-templates`** que proyecta `shared_test_runtimes.CATALOG` (id, label ES+EN,
  `dep_cache_mount`, `network_policy`) + `field_validator` de `default_runtime_template` contra el
  CATALOG, espejando `GET /mcp-catalog`. ✅ Una fuente de verdad servida; ✅ etiquetas ES+EN
  centralizadas; ✅ elimina los dos arrays y los labels inventados. ❌ Un endpoint nuevo (trivial).
- **B. Centralizar los arrays en un único módulo TS compartido** + validación backend. ✅ Menos backend.
  ❌ El catálogo sigue duplicado en el frontend; se desincroniza con el backend igual que hoy.
- **C. Codegen del catálogo TS desde el backend en build-time.** ✅ Sin endpoint en runtime. ❌ Añade
  un paso de build y un artefacto generado; sobre-ingeniería para 14 entradas.

## Decisión

**Opción A.** Es el patrón ya probado (`GET /mcp-catalog`) y respeta el principio "tunables/catálogos
servidos por el backend, no hardcodeados":

1. `GET /runtime-templates` (lectura, `tenant_user`) devuelve, por entrada del CATALOG: `id`, `label`
   {es,en}, `dep_cache_mount`, `network_policy` y lo necesario para poblar selectores.
2. `commands/page.tsx` y `dep-cache/page.tsx` consumen el endpoint con `useQuery`; se **eliminan**
   `RUNTIME_TEMPLATES`/`RUNTIMES` y las etiquetas inventadas; los `<select>` muestran labels ES+EN y
   `optgroups` por lenguaje (no slugs crudos).
3. `field_validator` en `ProjectCreate/UpdateRequest` rechaza `422` cualquier `default_runtime_template`
   fuera del CATALOG, reutilizando el check de `dep_cache.py:100-103`.

## Consecuencias

**Mejora:** una sola fuente de verdad servida; fin del desfase 12 vs 14 y de los labels inventados;
imposible guardar un runtime inexistente; selector legible (consistente con 06.17, que lo consume).

**Complejidad:** un endpoint + dos refactores de consumo en el frontend. Mínima.

**Trade-offs:** una llamada extra al cargar esas pantallas (cacheable). Aceptable.

## Riesgos

| Riesgo                                                     | Prob. | Impacto | Mitigación                                                   |
| ---------------------------------------------------------- | ----- | ------- | ------------------------------------------------------------ |
| El DTO del endpoint omite un campo que el front necesita   | Baja  | Bajo    | Se modela a partir de los usos actuales de ambos arrays      |
| Validación rechaza un proyecto legacy con runtime inválido | Baja  | Bajo    | Saneo/migración previa; el CATALOG cubre los stacks vigentes |

## Alternativas rechazadas

B (sigue duplicando en frontend) y C (codegen, sobre-ingeniería para 14 entradas).

## Trazabilidad

- Roadmap: `docs/roadmap/06.18-tools-overhaul.md` (`task_06_18_08`); consumo en `06.17` (`task_06_17_14`).
- Backend: `apps/api-server/src/api_server/routers/runtimes.py` (nuevo), `schemas/projects.py`, `routers/projects.py`.
- Fuente de verdad: `packages/shared-test-runtimes/src/shared_test_runtimes/catalog.py`.
- Frontend: `commands/page.tsx`, `dep-cache/page.tsx`.
- ADRs relacionados: 0045 (comandos/runtime por proyecto — deuda que cierra), 0025 (patrón `mcp-catalog`).
