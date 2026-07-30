---
title: Índice de Planes de Construcción del Sistema
version: 2.0
last_updated: 2026-07-29
status: published
---

# Planes de Construcción del Sistema

> **El estado de un plan vive en SU frontmatter, no aquí.** Este índice dice qué planes existen y
> cómo se agrupan; para saber si algo está hecho, abre el fichero y lee su `status:`. Esta página no
> replica estados a propósito: duplicarlos es la causa raíz de los hallazgos docsroadmap-3 y
> docsroadmap-6 (ver [prod-15](./prod-15-gobernanza-roadmap-docs.md)), y por lo mismo
> [`EXECUTION-SEQUENCE.md`](./EXECUTION-SEQUENCE.md) quedó archivado.

Esta carpeta contiene **35 planes de construcción** numerados (`00` a `16`, con los intermedios
`04.5`, `06.5`–`06.18`, `09.1`, `11.1`, `11.2`), derivados del Plan de Implementación (sección 33 del
documento maestro), más la **serie correctiva de producción** `prod-01`…`prod-18` y varios planes
descriptivos (córtex, correctivos, documentales, frontend, seeds) que no entran en el gate de fases.
Cada archivo sigue la **plantilla canónica de Plan** definida en la sección 8.8 del documento
maestro: la misma plantilla que el sistema usará después para generar sus propios planes.

Esto es intencional: el sistema se construye con el mismo formato con el que opera. Claude Code puede leer cada plan como si lo hubiera generado el Project Manager agente del propio sistema.

## Estructura de Cada Plan

Cada archivo contiene:

1. **Cabecera**: id, dependencia con plan predecesor, tiempo estimado, previsión de coste (humano + IA), creado por, secciones del .docx relevantes. **El estado NO está en la cabecera** — está en el frontmatter (prod-15, hallazgo docsroadmap-6).
2. **Descripción detallada**: resumen ejecutivo, contexto, alcance (incluido qué queda fuera), supuestos, decisiones clave, riesgos.
3. **Fases y tareas**: cada tarea con checkbox, título, descripción, tiempo estimado, dependencias, complejidad, rol sugerido, tests automáticos, runtime requerido.
4. **Tests humanos del plan**: checklist para validación humana al finalizar.

## Orden de Ejecución

| #     | Plan                                                                         | Duración | Depende de      |
| ----- | ---------------------------------------------------------------------------- | -------- | --------------- |
| 00    | [00-fundaciones.md](./00-fundaciones.md)                                     | 3-4 sem  | —               |
| 01    | [01-dominio-minimo.md](./01-dominio-minimo.md)                               | 4-5 sem  | 00              |
| 02    | [02-ejecucion-agentes.md](./02-ejecucion-agentes.md)                         | 4-5 sem  | 01              |
| 03    | [03-chat-planning-aprobacion.md](./03-chat-planning-aprobacion.md)           | 4-5 sem  | 02              |
| 04    | [04-memoria-rag-kbs.md](./04-memoria-rag-kbs.md)                             | 4-5 sem  | 02              |
| 04.5  | [04.5-agent-runtime-integration.md](./04.5-agent-runtime-integration.md)     | 3-5 d    | 04              |
| 05    | [05-mcp-tools-avanzadas.md](./05-mcp-tools-avanzadas.md)                     | 2-3 sem  | 04, 04.5        |
| 06    | [06-testing-revision-git.md](./06-testing-revision-git.md)                   | 4-5 sem  | 03, 05          |
| 06.5  | [06.5-orchestrator-wiring.md](./06.5-orchestrator-wiring.md)                 | 1-2 sem  | 06              |
| 06.6  | [06.6-admin-ui-gaps.md](./06.6-admin-ui-gaps.md)                             | 3-5 d    | 06              |
| 06.7  | [06.7-memory-dedup.md](./06.7-memory-dedup.md)                               | 2 d      | 04              |
| 06.8  | [06.8-rbac-enforcement.md](./06.8-rbac-enforcement.md)                       | 3-5 d    | 00              |
| 06.9  | [06.9-agent-scoped-kbs.md](./06.9-agent-scoped-kbs.md)                       | 3-4 d    | 04              |
| 06.10 | [06.10-kb-categories.md](./06.10-kb-categories.md)                           | 1-2 d    | 06.9            |
| 06.11 | [06.11-kb-ingestion-fixes.md](./06.11-kb-ingestion-fixes.md)                 | 3-4 d    | 06.10           |
| 06.12 | [06.12-global-catalog-consistency.md](./06.12-global-catalog-consistency.md) | 2-3 d    | 06.11           |
| 06.13 | [06.13-kb-catalog-content.md](./06.13-kb-catalog-content.md)                 | 3-5 d    | 06.12           |
| 06.14 | [06.14-hardening-auditoria.md](./06.14-hardening-auditoria.md)               | 8-12 d   | —               |
| 06.15 | [06.15-agent-tools-assignment-ui.md](./06.15-agent-tools-assignment-ui.md)   | 4-6 d    | —               |
| 06.16 | [06.16-polyglot-tool-catalog.md](./06.16-polyglot-tool-catalog.md)           | 4-6 d    | —               |
| 06.17 | [06.17-capacitacion-agentes.md](./06.17-capacitacion-agentes.md)             | 3-4 sem  | 04, 06.9, 06.18 |
| 06.18 | [06.18-tools-overhaul.md](./06.18-tools-overhaul.md)                         | 2-3 sem  | 06.15, 06.16    |
| 07    | [07-documentacion-visor.md](./07-documentacion-visor.md)                     | 3 sem    | 06              |
| 08    | [08-sso-empresarial.md](./08-sso-empresarial.md)                             | 2-3 sem  | 00              |
| 09    | [09-marketplace.md](./09-marketplace.md)                                     | 3-4 sem  | 05              |
| 09.1  | [09.1-marketplace-seed-publish.md](./09.1-marketplace-seed-publish.md)       | 3-4 d    | 09              |
| 10    | [10-asistente-personal.md](./10-asistente-personal.md)                       | 3-4 sem  | 06              |
| 11    | [11-guardrails-precios.md](./11-guardrails-precios.md)                       | 3-4 sem  | 02              |
| 11.1  | [11.1-budgets-fx.md](./11.1-budgets-fx.md)                                   | 4-6 d    | 11              |
| 11.2  | [11.2-llm-provider-admin-ui.md](./11.2-llm-provider-admin-ui.md)             | 4-6 d    | —               |
| 12    | [12-backup-restore.md](./12-backup-restore.md)                               | 2-3 sem  | 00              |
| 13    | [13-api-publica-webhooks.md](./13-api-publica-webhooks.md)                   | 3-4 sem  | 01              |
| 14    | [14-evals-estadisticas.md](./14-evals-estadisticas.md)                       | 3-4 sem  | 06              |
| 15    | [15-instalador-produccion.md](./15-instalador-produccion.md)                 | 4-5 sem  | todos           |
| 16    | [16-human-agents.md](./16-human-agents.md)                                   | 4-5 sem  | 06, 10, 11      |

### Serie correctiva de producción `prod-*` (no entran en el gate de fases)

Salidas de la [auditoría de producción 2026-06](./auditoria-produccion-2026-06.md) (178 hallazgos).
Corrigen lo construido para que sea desplegable y operable; no añaden features de producto. Se
ordenan por **prioridad**, no por dependencias, y varios pueden ir en paralelo.

| #       | Plan                                                                                     | Pri | Qué corrige                                                                           |
| ------- | ---------------------------------------------------------------------------------------- | --- | ------------------------------------------------------------------------------------- |
| prod-01 | [prod-01-despliegue-ejecutable.md](./prod-01-despliegue-ejecutable.md)                   | P0  | Imágenes, compose de apps, migraciones y TLS                                          |
| prod-02 | [prod-02-ci-en-verde.md](./prod-02-ci-en-verde.md)                                       | P0  | CI resucitado: triggers, gates obligatorios y cobertura                               |
| prod-03 | [prod-03-guardrails-validacion-humana.md](./prod-03-guardrails-validacion-humana.md)     | P0  | Guardrails cableados y validación humana operativa                                    |
| prod-04 | [prod-04-backup-dr-restaurable.md](./prod-04-backup-dr-restaurable.md)                   | P0  | Backup/DR restaurable de verdad: bug de tar, restore ejecutable, clave offsite, drill |
| prod-05 | [prod-05-rotacion-claves.md](./prod-05-rotacion-claves.md)                               | P0  | Rotación de claves ejecutable: MultiFernet, re-cifrado, dual JWT y job real           |
| prod-06 | [prod-06-ciclo-vida-ejecucion.md](./prod-06-ciclo-vida-ejecucion.md)                     | P1  | Ciclo de vida de ejecución: DAG, zombis, cancelación y budgets                        |
| prod-07 | [prod-07-fiabilidad-llm-costes.md](./prod-07-fiabilidad-llm-costes.md)                   | P1  | Capa LLM fiable y contabilidad de costes exacta                                       |
| prod-08 | [prod-08-observabilidad-alertas.md](./prod-08-observabilidad-alertas.md)                 | P1  | Observabilidad de aplicación y cadena de alertas funcional                            |
| prod-09 | [prod-09-sesiones-autorizacion-frontend.md](./prod-09-sesiones-autorizacion-frontend.md) | P1  | Sesiones y autorización: admin hardening, SSO, 401 global y cookies                   |
| prod-10 | [prod-10-vault-secretos-operables.md](./prod-10-vault-secretos-operables.md)             | P1  | Vault operable y secretos sin defaults conocidos                                      |
| prod-11 | [prod-11-cadena-suministro.md](./prod-11-cadena-suministro.md)                           | P1  | Cadena de suministro: SCA en CI, Dependabot, lockfiles y pin por digest               |
| prod-12 | [prod-12-hardening-tools-agentes.md](./prod-12-hardening-tools-agentes.md)               | P1  | Hardening de tools de agentes: SSRF, egress, reaper y marketplace                     |
| prod-13 | [prod-13-rendimiento-y-datos.md](./prod-13-rendimiento-y-datos.md)                       | P1  | Rendimiento y datos: event loop, pool, retención e índices                            |
| prod-14 | [prod-14-tenancy-defensa-profundidad.md](./prod-14-tenancy-defensa-profundidad.md)       | P2  | Multi-tenancy en profundidad (junctions, service_user, meta-test)                     |
| prod-15 | [prod-15-gobernanza-roadmap-docs.md](./prod-15-gobernanza-roadmap-docs.md)               | P2  | Gobernanza: roadmap sincerado, CLAUDE.md real, validación humana pendiente            |
| prod-16 | [prod-16-frontend-i18n-calidad.md](./prod-16-frontend-i18n-calidad.md)                   | P2  | Frontend: i18n ES+EN real y partición de componentes                                  |
| prod-17 | [prod-17-bucle-ai-reviewer.md](./prod-17-bucle-ai-reviewer.md)                           | P2  | Bucle del AI reviewer: `in_review` → veredicto → done/backlog                         |
| prod-18 | [prod-18-worktree-en-ejecucion.md](./prod-18-worktree-en-ejecucion.md)                   | P2  | Worktree en la ejecución del agente: código persistente, commit, test-runtime real    |

### Córtex del `system_owner` (no entran en el gate de fases)

Diseño maestro + 5 fases de la mente sintética del owner. ADRs 0074-0078.

| Plan                                                               | Qué produce                                                  |
| ------------------------------------------------------------------ | ------------------------------------------------------------ |
| [cortex-system-owner.md](./cortex-system-owner.md)                 | Diseño maestro: córtex del owner + rol `system_owner`        |
| [cortex-fases.md](./cortex-fases.md)                               | Descomposición del diseño en las 5 fases F1-F5               |
| [cortex-f1-memoria-cognitiva.md](./cortex-f1-memoria-cognitiva.md) | F1 — memoria cognitiva                                       |
| [cortex-f2-afectivo.md](./cortex-f2-afectivo.md)                   | F2 — modelo afectivo                                         |
| [cortex-f3-identidad.md](./cortex-f3-identidad.md)                 | F3 — identidad                                               |
| [cortex-f4-autonomia.md](./cortex-f4-autonomia.md)                 | F4 — autonomía                                               |
| [cortex-f5-voz-avatar.md](./cortex-f5-voz-avatar.md)               | F5 — voz y avatar                                            |
| [cortex-identidad-real.md](./cortex-identidad-real.md)             | Self-model unificado (identidad real, no declarada)          |
| [gaps-cortex-2026-07-27.md](./gaps-cortex-2026-07-27.md)           | Huecos del córtex verificados contra el código (76 casillas) |

### Seeds demostrativos (no entran en el gate de fases)

Planes con `plan_id` descriptivo (no numerado) que **usan** la plataforma para
materializar escenarios reales en lugar de construir features. No bloquean ni
son bloqueados por las fases.

| Plan                                                             | Qué materializa                                                                                     | Construye sobre      |
| ---------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- | -------------------- |
| [codeigniter-4-builtin-team.md](./codeigniter-4-builtin-team.md) | Equipo built-in CodeIgniter 4 de 10 agentes + 8 KBs de fábrica + plantilla de proyecto (correctivo) | 06.15, 06.16, 04, 16 |

### Planes documentales (no entran en el gate de fases)

Planes con `plan_id` descriptivo que producen **solo documentación** (no
features ni código). No bloquean ni son bloqueados por las fases.

| Plan                                                           | Qué produce                                                                                                                                                    |
| -------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [docs-human-test-guides.md](./docs-human-test-guides.md)       | Guías de tests humanos por plan en `docs/03-guides/human-tests/` (una por cada plan con bloque human)                                                          |
| [docs-comprehensive-update.md](./docs-comprehensive-update.md) | Pasada integral de la capa transversal (`docs/context/`, overview, referencia), diagramas Mermaid del sistema final, gotchas nuevos, coherencia + cross-links. |

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

| Plan                                                                                           | Qué corrige                                                                                                                                                                                                                                                     |
| ---------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [price-sync-active-providers.md](./price-sync-active-providers.md)                             | El sync de precios LiteLLM ahora solo importa las familias de los `llm_providers` activos (ADR 0028); 0 activos ⇒ nada; fuera del allowlist cierra periodo (no borra).                                                                                          |
| [sso-global-user-admin.md](./sso-global-user-admin.md)                                         | Re-arquitectura de auth a **platform-global** (ADR 0047, supersede la parte per-tenant de 0031): login por provider, acceso por membership (0 ⇒ pantalla "sin acceso"), `/admin/users`, providers en `/login`; password/MFA/SCIM intactos.                      |
| [cadena-pr-plan.md](./cadena-pr-plan.md)                                                       | Cadena auto-PR del cierre de plan: identidad git de fuente única (rama+bare), push incremental al remoto, persistencia del PR (P1-P8).                                                                                                                          |
| [ciclo-vida-planes-fixes.md](./ciclo-vida-planes-fixes.md)                                     | Máquina de estados autoritativa (PUT/tasks + submit_verdict), tenancy del orquestador, durabilidad del planning, fidelidad del planner y board por plan_id (c1-c11).                                                                                            |
| [tools-y-cierre-plan-fixes.md](./tools-y-cierre-plan-fixes.md)                                 | Guardrails de runtime + gate humano que no falle-abierto (P0), paridad catálogo↔executor, docling-mcp, changelog automático al cierre (g1-g6, c4).                                                                                                              |
| [remediacion-auditoria-integral-2026-07-14.md](./remediacion-auditoria-integral-2026-07-14.md) | Delta verificado de seguridad, córtex/RLS, embeddings, engines Celery, WebSocket y readiness. No duplica la serie prod.                                                                                                                                         |
| [remediacion-auditoria-dirigida-2026-07-16.md](./remediacion-auditoria-dirigida-2026-07-16.md) | AUD16: envelope OpenAI de tools de finalización (camino HTTP), notifs visibles (inbox plataforma + body), coste de catálogo + destilador de memorias, robustez de runs y monitorización del host. (22 commits, desplegado en dev).                              |
| [remediacion-proyecto-integral-2026-07-17.md](./remediacion-proyecto-integral-2026-07-17.md)   | Dominio Proyecto: resucitar conocimiento (ingesta docling 404 + seed KBs + GC), cerrar la puerta lateral del ciclo de vida de planes, paused/archived reales, slug único, adopción server-side, dispatch por equipo, higiene git/boards.                        |
| [remediacion-gestion-proyectos-2026-07-25.md](./remediacion-gestion-proyectos-2026-07-25.md)   | Workflow de gestión de proyectos en 6 olas: chat de planning que carga lo reciente, tools MCP anunciadas al modelo, `HOME` fuera del worktree, progreso/PR/coste visibles, acciones humanas por tarea, guardrails `pre_llm`/`post_llm` y versionado de prompts. |
| [plan-unificacion-provider-id.md](./plan-unificacion-provider-id.md)                           | Unificación de la selección/resolución de modelo por `provider_id` (ADR 0082).                                                                                                                                                                                  |
| [mejoras-2026-06-chat-coste-cortex.md](./mejoras-2026-06-chat-coste-cortex.md)                 | Tanda de mejoras de chat, coste y córtex de 2026-06.                                                                                                                                                                                                            |
| [hallazgos-pendientes-2026-07-07.md](./hallazgos-pendientes-2026-07-07.md)                     | Los 9 hallazgos que quedaron abiertos tras la auditoría de 2026-07-06.                                                                                                                                                                                          |
| [guardas-research-por-novedad.md](./guardas-research-por-novedad.md)                           | Guardas de investigación disparadas por novedad + digests en PROGRESS.                                                                                                                                                                                          |
| [fixes-pesados-auditoria.md](./fixes-pesados-auditoria.md)                                     | Los fixes de mayor coste salidos de las auditorías (agrupados aparte por esfuerzo).                                                                                                                                                                             |
| [refactor-pipeline-ejecucion-review.md](./refactor-pipeline-ejecucion-review.md)               | Refactor del pipeline de ejecución + review.                                                                                                                                                                                                                    |
| [refactorizacion-por-partes-2026-07-07.md](./refactorizacion-por-partes-2026-07-07.md)         | Modularización por tramos (P1-P7 + H1-H6) de los módulos más grandes.                                                                                                                                                                                           |
| [registry-egress-followups.md](./registry-egress-followups.md)                                 | Seguimientos del `registry-proxy` y del egress allowlisted (ADR 0094).                                                                                                                                                                                          |
| [runs-visor-trabajo.md](./runs-visor-trabajo.md)                                               | Visor del trabajo de un run (lo que el agente hizo, no solo su log).                                                                                                                                                                                            |

### Auditorías (no entran en el gate de fases)

Informes de auditoría del sistema construido (código + sistema vivo). Producen hallazgos verificados y, cuando
procede, planes de remediación propios (listados arriba en «Planes correctivos»). No bloquean fases.

| Auditoría                                                                                          | Qué cubre                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| -------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [auditoria-plataforma-2026-07-03.md](./auditoria-plataforma-2026-07-03.md)                         | Proyectos, planes, MCP/tools, git y runtime. 29 hallazgos verificados en Opus 4.8; 2 P0 (guardrails g1 + gate humano g6). → cadena-pr-plan, ciclo-vida-planes-fixes, tools-y-cierre-plan-fixes, guardas (Fase G).                                                                                                                                                                                                                                               |
| [auditoria-runs-2026-07-02.md](./auditoria-runs-2026-07-02.md)                                     | Ejecuciones/runs, memoria, workers, review (baseline de la anterior).                                                                                                                                                                                                                                                                                                                                                                                           |
| [auditoria-produccion-2026-06.md](./auditoria-produccion-2026-06.md)                               | Auditoría de producción (178 hallazgos → planes prod-01…18).                                                                                                                                                                                                                                                                                                                                                                                                    |
| [auditoria-2026-06-memoria-tools-marketplace.md](./auditoria-2026-06-memoria-tools-marketplace.md) | Memoria, tools y marketplace.                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| [auditoria-zonas-2026-06.md](./auditoria-zonas-2026-06.md)                                         | Auditoría por zonas del sistema.                                                                                                                                                                                                                                                                                                                                                                                                                                |
| [auditoria-integral-2026-07-14.md](./auditoria-integral-2026-07-14.md)                             | Auditoría actual contra el commit `ebee968`: implementación, lógica, seguridad, rendimiento, frontend, CI y gobernanza; incluye baseline ejecutable y candidatos refutados.                                                                                                                                                                                                                                                                                     |
| [auditoria-dirigida-2026-07-16.md](./auditoria-dirigida-2026-07-16.md)                             | Monitorización (data-flow real del stack Prometheus/Grafana), tools por proveedor LLM (envelope roto en el camino HTTP), notificaciones (invisibles para humanos) + barrido colateral verificado (27 hallazgos).                                                                                                                                                                                                                                                |
| [auditoria-verificacion-2026-07-17.md](./auditoria-verificacion-2026-07-17.md)                     | Verificación en vivo de la remediación AUD16 (22 fixes ✅, 0 regresiones) + 17 hallazgos NUEVOS (N-01…N-17) con top-5 por impacto/esfuerzo: device-flow copilot, healthchecks de proxies, TTL exec:\*, logging workers, índices FK.                                                                                                                                                                                                                             |
| [auditoria-proyecto-integral-2026-07-17.md](./auditoria-proyecto-integral-2026-07-17.md)           | TODO el dominio Proyecto (ciclo de vida, config→enforcement, planes/tareas/board, git/worktrees, KBs/RAG/memoria, equipo/tools/MCP, UI, API pública): 42 hallazgos nuevos (P1/PROY2/G/PROJ), ingesta KB muerta en el desplegado, y verificación de lo que SÍ funciona. → remediacion-proyecto-integral-2026-07-17.                                                                                                                                              |
| [auditoria-gestion-proyectos-2026-07-25.md](./auditoria-gestion-proyectos-2026-07-25.md)           | El WORKFLOW completo de implementación de proyectos (agentes, prompts, tools, MCP, contenedores de ejecución/test/review, git, generación de planes, modos de chat, observabilidad): 39 hallazgos verificados uno a uno + 4 candidatos refutados. Patrón dominante: funcionalidad correcta sin cablear al consumidor final. Incluye §7b «corrección mínima vs mejora real» (5 hallazgos con dos arreglos posibles). → remediacion-gestion-proyectos-2026-07-25. |

| [auditoria-prod-implementados-2026-07-06.md](./auditoria-prod-implementados-2026-07-06.md) | Qué de la serie `prod-*` estaba ya implementado el 2026-07-06 (informe de verificación, no plan). |
| [auditoria-hallazgos-implementados-2026-07-10.md](./auditoria-hallazgos-implementados-2026-07-10.md) | Verificación de los hallazgos remediados el 2026-07-10 (1 crítico + 7 importantes + 7 menores). |
| [auditoria-cortex-2026-07-27.md](./auditoria-cortex-2026-07-27.md) | Auditoría del córtex del owner contra el código; produce [gaps-cortex-2026-07-27.md](./gaps-cortex-2026-07-27.md). |

### Investigaciones y análisis (no producen código)

Informes previos a un plan: exploran el problema y proponen opciones. No llevan checkboxes ni gate.

| Documento                                                                                                                      | Qué investiga                                                                 |
| ------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------- |
| [investigacion-inteligencia-agentes-2026-07-11.md](./investigacion-inteligencia-agentes-2026-07-11.md)                         | Por qué los agentes no son más listos de lo que son; plan P0/P1/P2 propuesto. |
| [investigacion-cortex-asistente-profesionalidad-2026-07-11.md](./investigacion-cortex-asistente-profesionalidad-2026-07-11.md) | Profesionalidad del córtex/asistente en la conversación con el owner.         |
| [analisis-diferidos-2026-07-12.md](./analisis-diferidos-2026-07-12.md)                                                         | Inventario de lo diferido a lo largo del roadmap y qué merece rescatarse.     |
| [propuesta-simplificacion-kb-2026-07-12.md](./propuesta-simplificacion-kb-2026-07-12.md)                                       | Propuesta de simplificación del modelo de KBs.                                |

## Cola de validación humana

35 planes están en `pending_human_validation`: **código mergeado en `master` sin sign-off humano**.
Eso no es un detalle burocrático — es la razón de que casi ningún `blocking_plan` esté `completed` y,
en cascada, de que 6 fases se empezaran con el gate incumplido (hallazgo docsroadmap-2; inventario
exacto y opciones en el **ADR 0138**, `proposed`, pendiente de decisión humana).

**Orden recomendado de validación** (primero lo que toca producción directamente):

| Orden | Plan                                                         | Por qué primero                                               |
| ----- | ------------------------------------------------------------ | ------------------------------------------------------------- |
| 1     | [12-backup-restore.md](./12-backup-restore.md)               | Sin restore verificado no hay red de seguridad para nada más  |
| 2     | [15-instalador-produccion.md](./15-instalador-produccion.md) | Es la puerta de entrada a producción (y arrancó con override) |
| 3     | [08-sso-empresarial.md](./08-sso-empresarial.md)             | Superficie de autenticación expuesta                          |
| 4     | [09-marketplace.md](./09-marketplace.md)                     | Instala código de terceros en el tenant                       |

El resto sigue por prioridad de la serie `prod-*` (P0 → P1 → P2). Responsable y ventana de cada
validación los fija el ADR 0138 al aprobarse; este índice no los inventa.

## MVP Funcional

Las fases 0-6 forman un MVP funcional. Total estimado: 21-27 semanas.

## Cómo Leer un Plan

1. Lee la **cabecera** primero para tener métricas clave del plan.
2. Lee la **descripción detallada** para entender qué se construye y por qué.
3. Las **tareas** son la unidad de trabajo: cada una tiene checkbox, criterios de aceptación con tests automáticos, y dependencias con otras tareas del mismo plan.
4. Al cerrar el plan, **valida los tests humanos** definidos al final.

## Trazabilidad

Cada plan se mapea 1:1 a una rama git tipo `plan/00-fundaciones`, `plan/01-dominio-minimo`, etc. Los commits de tareas de plan llevan los trailers `Plan-Id`, `Task-Id`, `Execution-Id`; en mantenimiento son opcionales (política real en [`../context/conventions.md`](../context/conventions.md), decisión D2 de prod-15).

Al cerrar cada plan, generar entrada en `/docs/07-changelog/{plan_id}.md` siguiendo el formato canónico de la sección 15 del documento maestro.
