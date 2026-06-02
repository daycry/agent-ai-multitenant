---
title: Índice de Planes de Construcción del Sistema
version: 1.0
last_updated: 2026-05-20
status: published
---

# Planes de Construcción del Sistema

Esta carpeta contiene **17 planes de construcción** (16 originales + 16-human-agents añadido tras la revisión vigente), uno por fase del Plan de Implementación (sección 33 del documento maestro). Cada archivo sigue la **plantilla canónica de Plan** definida en la sección 8.8 del documento maestro: la misma plantilla que el sistema usará después para generar sus propios planes.

Esto es intencional: el sistema se construye con el mismo formato con el que opera. Claude Code puede leer cada plan como si lo hubiera generado el Project Manager agente del propio sistema.

## Estructura de Cada Plan

Cada archivo contiene:

1. **Cabecera**: id, estado, dependencia con plan predecesor, tiempo estimado, previsión de coste (humano + IA), creado por, secciones del .docx relevantes.
2. **Descripción detallada**: resumen ejecutivo, contexto, alcance (incluido qué queda fuera), supuestos, decisiones clave, riesgos.
3. **Fases y tareas**: cada tarea con checkbox, título, descripción, tiempo estimado, dependencias, complejidad, rol sugerido, tests automáticos, runtime requerido.
4. **Tests humanos del plan**: checklist para validación humana al finalizar.

## Orden de Ejecución

| #     | Plan                                                                         | Duración | Depende de |
| ----- | ---------------------------------------------------------------------------- | -------- | ---------- |
| 00    | [00-fundaciones.md](./00-fundaciones.md)                                     | 3-4 sem  | —          |
| 01    | [01-dominio-minimo.md](./01-dominio-minimo.md)                               | 4-5 sem  | 00         |
| 02    | [02-ejecucion-agentes.md](./02-ejecucion-agentes.md)                         | 4-5 sem  | 01         |
| 03    | [03-chat-planning-aprobacion.md](./03-chat-planning-aprobacion.md)           | 4-5 sem  | 02         |
| 04    | [04-memoria-rag-kbs.md](./04-memoria-rag-kbs.md)                             | 4-5 sem  | 02         |
| 04.5  | [04.5-agent-runtime-integration.md](./04.5-agent-runtime-integration.md)     | 3-5 d    | 04         |
| 05    | [05-mcp-tools-avanzadas.md](./05-mcp-tools-avanzadas.md)                     | 2-3 sem  | 04, 04.5   |
| 06    | [06-testing-revision-git.md](./06-testing-revision-git.md)                   | 4-5 sem  | 03, 05     |
| 06.5  | [06.5-orchestrator-wiring.md](./06.5-orchestrator-wiring.md)                 | 1-2 sem  | 06         |
| 06.6  | [06.6-admin-ui-gaps.md](./06.6-admin-ui-gaps.md)                             | 3-5 d    | 06         |
| 06.7  | [06.7-memory-dedup.md](./06.7-memory-dedup.md)                               | 2 d      | 04         |
| 06.8  | [06.8-rbac-enforcement.md](./06.8-rbac-enforcement.md)                       | 3-5 d    | 00         |
| 06.9  | [06.9-agent-scoped-kbs.md](./06.9-agent-scoped-kbs.md)                       | 3-4 d    | 04         |
| 06.10 | [06.10-kb-categories.md](./06.10-kb-categories.md)                           | 1-2 d    | 06.9       |
| 06.11 | [06.11-kb-ingestion-fixes.md](./06.11-kb-ingestion-fixes.md)                 | 3-4 d    | 06.10      |
| 06.12 | [06.12-global-catalog-consistency.md](./06.12-global-catalog-consistency.md) | 2-3 d    | 06.11      |
| 06.13 | [06.13-kb-catalog-content.md](./06.13-kb-catalog-content.md)                 | 3-5 d    | 06.12      |
| 06.14 | [06.14-hardening-auditoria.md](./06.14-hardening-auditoria.md)               | 8-12 d   | —          |
| 06.15 | [06.15-agent-tools-assignment-ui.md](./06.15-agent-tools-assignment-ui.md)   | 4-6 d    | —          |
| 06.16 | [06.16-polyglot-tool-catalog.md](./06.16-polyglot-tool-catalog.md)           | 4-6 d    | —          |
| 07    | [07-documentacion-visor.md](./07-documentacion-visor.md)                     | 3 sem    | 06         |
| 08    | [08-sso-empresarial.md](./08-sso-empresarial.md)                             | 2-3 sem  | 00         |
| 09    | [09-marketplace.md](./09-marketplace.md)                                     | 3-4 sem  | 05         |
| 09.1  | [09.1-marketplace-seed-publish.md](./09.1-marketplace-seed-publish.md)       | 3-4 d    | 09         |
| 10    | [10-asistente-personal.md](./10-asistente-personal.md)                       | 3-4 sem  | 06         |
| 11    | [11-guardrails-precios.md](./11-guardrails-precios.md)                       | 3-4 sem  | 02         |
| 11.1  | [11.1-budgets-fx.md](./11.1-budgets-fx.md)                                   | 4-6 d    | 11         |
| 11.2  | [11.2-llm-provider-admin-ui.md](./11.2-llm-provider-admin-ui.md)             | 4-6 d    | —          |
| 12    | [12-backup-restore.md](./12-backup-restore.md)                               | 2-3 sem  | 00         |
| 13    | [13-api-publica-webhooks.md](./13-api-publica-webhooks.md)                   | 3-4 sem  | 01         |
| 14    | [14-evals-estadisticas.md](./14-evals-estadisticas.md)                       | 3-4 sem  | 06         |
| 15    | [15-instalador-produccion.md](./15-instalador-produccion.md)                 | 4-5 sem  | todos      |
| 16    | [16-human-agents.md](./16-human-agents.md)                                   | 4-5 sem  | 06, 10, 11 |

### Seeds demostrativos (no entran en el gate de fases)

Planes con `plan_id` descriptivo (no numerado) que **usan** la plataforma para
materializar escenarios reales en lugar de construir features. No bloquean ni
son bloqueados por las fases.

| Plan                                                     | Qué materializa                                                                  | Construye sobre      |
| -------------------------------------------------------- | -------------------------------------------------------------------------------- | -------------------- |
| [demo-webscorpo-team-kb.md](./demo-webscorpo-team-kb.md) | Equipo WebScorpo (CI4) de 10 agentes + proyecto + KB completo (equipo + por-rol) | 06.15, 06.16, 04, 16 |

### Planes documentales (no entran en el gate de fases)

Planes con `plan_id` descriptivo que producen **solo documentación** (no
features ni código). No bloquean ni son bloqueados por las fases.

| Plan                                                     | Qué produce                                                                                           |
| -------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| [docs-human-test-guides.md](./docs-human-test-guides.md) | Guías de tests humanos por plan en `docs/03-guides/human-tests/` (una por cada plan con bloque human) |

### Planes de frontend (no entran en el gate de fases)

Planes con `plan_id` descriptivo, **solo frontend** y
**behavior-preserving** (no tocan backend, rutas, llamadas API ni
`data-testid`). No bloquean ni son bloqueados por las fases.

| Plan                                               | Qué produce                                                                                                                                                             |
| -------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [ui-refresh-refactor.md](./ui-refresh-refactor.md) | Refresh visual moderado + refactor del `admin-panel`: tokens refinados, primitivas y componentes compartidos nuevos, a11y.                                              |
| [admin-menu-reorg.md](./admin-menu-reorg.md)       | Menú del `admin-panel` en 5 grupos con submenús colapsables + ámbito (Plataforma=System Admin, SSO movido ahí) + scrollbar y header modernos (tenant actual + usuario). |

### Planes correctivos (no entran en el gate de fases)

Planes con `plan_id` descriptivo que **corrigen** un comportamiento reportado por
el operador sobre features ya construidas. No bloquean ni son bloqueados por las
fases.

| Plan                                                               | Qué corrige                                                                                                                                                            |
| ------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [price-sync-active-providers.md](./price-sync-active-providers.md) | El sync de precios LiteLLM ahora solo importa las familias de los `llm_providers` activos (ADR 0028); 0 activos ⇒ nada; fuera del allowlist cierra periodo (no borra). |

## MVP Funcional

Las fases 0-6 forman un MVP funcional. Total estimado: 21-27 semanas.

## Cómo Leer un Plan

1. Lee la **cabecera** primero para tener métricas clave del plan.
2. Lee la **descripción detallada** para entender qué se construye y por qué.
3. Las **tareas** son la unidad de trabajo: cada una tiene checkbox, criterios de aceptación con tests automáticos, y dependencias con otras tareas del mismo plan.
4. Al cerrar el plan, **valida los tests humanos** definidos al final.

## Trazabilidad

Cada plan se mapea 1:1 a una rama git tipo `plan/00-fundaciones`, `plan/01-dominio-minimo`, etc. Los commits durante la construcción de cada fase llevan los trailers `Plan-Id`, `Task-Id`, `Execution-Id` (aunque en estas fases iniciales los commits los haga Claude Code ayudando al humano, la convención se mantiene).

Al cerrar cada plan, generar entrada en `/docs/07-changelog/{plan_id}.md` siguiendo el formato canónico de la sección 15 del documento maestro.
