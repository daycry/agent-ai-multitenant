---
plan_id: builtin-customization
title: Personalización de equipos/agentes built-in — herencia de modelo, adopción y catálogo completo
completed_at: 2026-06-19
docs_language: es
---

# Personalización de equipos built-in

## Resumen

El operador reportó fricciones reales al usar los built-in: los equipos/agentes
built-in eran read-only sin forma ergonómica de personalizarlos, no se podía
fijar el modelo por proyecto ni por equipo, los agentes de equipos built-in
salían "a medias" de capacidad, y la vista de Capacidad confundía. Esta entrega
(diseño aprobado en brainstorming + plan TDD) lo cierra en varias olas, sin tocar
el catálogo cerrado de proveedores (ADR 0021) ni las taxonomías cerradas de
skills/tools (ADR 0049/0050). Git lo gestiona la plataforma, **no** es tool de
agente (corrección explícita del operador).

## Cambios

### Catálogo y completitud de built-in (Olas B0.1 + B)

- **18 skills nuevas** en `builtin_skills` (PHP/CodeIgniter 4, secure-coding-owasp,
  sql-optimization, rag-pgvector, web-performance, dependency-audit-sca,
  contract-testing, load-testing, prompt-engineering, web-research, etc.),
  respetando las 6 categorías cerradas.
- **Equipos built-in completos**: se cerraron dos huecos simétricos — el equipo
  CodeIgniter 4 cableaba tools pero **no** skills, y los agentes built-in sueltos
  (`builtin_agents`, miembros de los 5 equipos built-in) tenían skills pero **no**
  tools. Nuevo mapa DRY `builtin_role_capabilities` (rol → {tools, skills}),
  `seed_ci4_agent_skills` (skills por rol + extras PHP) y `seed_builtin_agent_tools`
  (tools por rol, con fallback). **Guardia de regresión** de integración: todo
  agente de un equipo built-in tiene ≥1 tool y ≥1 skill.

### Herencia de modelo (Ola A — ADR 0065)

- Migración `0085`: `teams.model_config` + `projects.model_config` (JSONB `{}`).
- `resolve_model_config_chain` (función pura): el modelo se resuelve por la cadena
  **plataforma → proyecto → equipo → agente**, gana el más específico que pinee
  `provider`+`model`; se preservan los `system_prompts` del agente. El dispatch
  (`_route_ai`) carga el equipo del proyecto y resuelve la cadena. El worker
  (ADR 0057) no cambia: la cadena es transparente aguas abajo.

### Adopción de equipos built-in (Ola C — ADR 0066)

- Migración `0086`: `teams.forked_from_team_id` + `forked_from_version` (espejo
  de `agents`).
- **`POST /teams/{id}/adopt`**: crea una copia editable del tenant (`is_builtin=
false`, enlazada al origen), forkea cada miembro (persona + KBs/tools/skills,
  reusando el helper de fork por agente) al scope destino (`project_local` o
  `global_tenant_template`) y recrea los `TeamMember`. El built-in no se muta;
  re-adopción permitida.

### UI y exposición (Olas A-UI / C-UI / D — admin-panel)

- `model_config` en `PUT /teams/{id}` y `PUT /projects/{id}` (alias JSON
  `model_config`, validado contra el catálogo); expuesto en sus responses.
- Frontend: `DefaultModelSection` (heredar vs pinear modelo, reusa el selector
  cerrado `PersonaModelFields`) en el detalle de Equipo y de Proyecto;
  `AdoptTeamDialog` (destino tenant/proyecto + nombre + modelo opcional) con botón
  "Adoptar / Personalizar" en el detalle de un built-in y "Adoptar" en las cards
  built-in de la lista.
- **Aviso de Capacidad reescrito** para ser accionable (explica que un global es
  read-only y cómo personalizarlo: forkear el agente o adoptar su equipo).
- **Modelo efectivo + origen** en el Hub de Capacidad (Ola D-2):
  `resolve_model_config_origin` + `CapabilitySer.model_origin`; el Hub muestra de
  qué nivel (agente/equipo/proyecto/plataforma) viene el modelo.

### Egress web (Ola B0.2 — ADR 0067, `proposed`)

- Las tools `run-tests`/`format-code`/`fetch-url` ya existen (run-pytest/run-lint/
  run-build/http-get). Lo genuinamente nuevo —`web-search` + un `web-fetch`
  anti-SSRF— abre **egress a Internet** desde los runtimes (Principio 2), así que
  queda como **ADR `proposed`** pendiente de aprobación del operador (proveedor de
  búsqueda, egress allowlist por proyecto, guardrails pre/post_tool, anti-SSRF).
  No implementado.

## Verificación

- Backend: regresión consolidada de las áreas tocadas verde (model chain, role
  capabilities, skills, teams, projects, capabilities, dispatch, adopción, guardia
  de completitud, seeds). Migraciones 0085/0086 reversibles (barrido up/down).
  mypy limpio; todos los commits pasaron pre-commit (black/ruff/mypy).
- Frontend: `tsc --noEmit` limpio, `next lint` sin errores nuevos, 87 tests vitest
  verdes (capability-hub/persona sin regresión).
- **Pendiente**: QA visual del admin-panel en navegador; aprobación + implementación
  de B0.2 (ADR 0067); estrategia de merge de la rama (apilada sobre `prod-01`).

## ADRs

- [ADR 0065](../05-architecture-decisions/0065-herencia-model-config-plataforma-proyecto-equipo-agente.md)
  — herencia de `model_config` en cadena.
- [ADR 0066](../05-architecture-decisions/0066-adopcion-equipos-built-in-deep-copy-fork.md)
  — adopción de equipos built-in por copia profunda + fork.
- [ADR 0067](../05-architecture-decisions/0067-tools-web-search-y-fetch-con-egress-guardrails.md)
  — tools web-search/web-fetch con egress + guardrails (`proposed`).
