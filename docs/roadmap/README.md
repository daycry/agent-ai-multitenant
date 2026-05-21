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

| #   | Plan                                                               | Duración | Depende de |
| --- | ------------------------------------------------------------------ | -------- | ---------- |
| 00  | [00-fundaciones.md](./00-fundaciones.md)                           | 3-4 sem  | —          |
| 01  | [01-dominio-minimo.md](./01-dominio-minimo.md)                     | 4-5 sem  | 00         |
| 02  | [02-ejecucion-agentes.md](./02-ejecucion-agentes.md)               | 4-5 sem  | 01         |
| 03  | [03-chat-planning-aprobacion.md](./03-chat-planning-aprobacion.md) | 4-5 sem  | 02         |
| 04  | [04-memoria-rag-kbs.md](./04-memoria-rag-kbs.md)                   | 4-5 sem  | 02         |
| 05  | [05-mcp-tools-avanzadas.md](./05-mcp-tools-avanzadas.md)           | 2-3 sem  | 04         |
| 06  | [06-testing-revision-git.md](./06-testing-revision-git.md)         | 4-5 sem  | 03, 05     |
| 07  | [07-documentacion-visor.md](./07-documentacion-visor.md)           | 3 sem    | 06         |
| 08  | [08-sso-empresarial.md](./08-sso-empresarial.md)                   | 2-3 sem  | 00         |
| 09  | [09-marketplace.md](./09-marketplace.md)                           | 3-4 sem  | 05         |
| 10  | [10-asistente-personal.md](./10-asistente-personal.md)             | 3-4 sem  | 06         |
| 11  | [11-guardrails-precios.md](./11-guardrails-precios.md)             | 3-4 sem  | 02         |
| 12  | [12-backup-restore.md](./12-backup-restore.md)                     | 2-3 sem  | 00         |
| 13  | [13-api-publica-webhooks.md](./13-api-publica-webhooks.md)         | 3-4 sem  | 01         |
| 14  | [14-evals-estadisticas.md](./14-evals-estadisticas.md)             | 3-4 sem  | 06         |
| 15  | [15-instalador-produccion.md](./15-instalador-produccion.md)       | 4-5 sem  | todos      |
| 16  | [16-human-agents.md](./16-human-agents.md)                         | 4-5 sem  | 06, 10, 11 |

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
