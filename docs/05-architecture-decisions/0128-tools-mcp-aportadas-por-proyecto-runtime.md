---
title: "ADR 0128: Tools MCP aportadas por el proyecto en runtime (agentes compartidos + rol opcional)"
status: proposed
date: 2026-07-23
deciders: [operador]
relates_to: [0052, 0066, 0091, 0117, 0127]
---

# ADR 0128: Tools MCP aportadas por el proyecto en runtime

## Contexto

Los servidores MCP se declaran **por proyecto** (`project.mcp_servers`). Sin
embargo, HOY las tools MCP se conceden **por agente** (`agent_tools`), y la
asignación exige una comprobación de scope (agents.py:1064-1081): una tool MCP
solo puede asignarse a un agente **`project_local`** cuyo **proyecto declare ese
server**. Consecuencia: un agente **`global_tenant_template`** (el resultado de
adoptar un equipo a nivel de tenant, para reutilizarlo en varios proyectos) **no
puede llevar tools MCP** → no puede usar el MCP del proyecto.

Los dos caminos actuales son ambos incómodos:

1. **Forkear los agentes a `project_local` en cada proyecto** (ADR 0066 con
   `target=project`): funciona, pero **prolifera copias** de cada agente por
   proyecto y añade mantenimiento/deriva (aunque haya diff/merge). Contradice el
   caso "un equipo compartido reutilizado en N proyectos".
2. **Grant estático por-agente** de la tool MCP: **no tiene sentido para un agente
   compartido** usado en varios proyectos con distinto set de tools (proyecto A
   tiene Jira, B no). ¿A qué proyecto se refiere el grant? `agent_tools` es
   por-agente, no por-(agente, proyecto).

**Observación que motiva este ADR (operador, 2026-07-23):** las tools MCP son una
**capacidad del PROYECTO**, no del agente. Deben seguir el **contexto de
ejecución** (el proyecto en el que corre el run), no grabarse en el agente. Los
agentes se quedan **compartidos** (tenant-local, adoptados una vez); cada proyecto
**aporta sus propias tools MCP dinámicamente** al ejecutarse.

**Base ya existente (verificado):**

- El run ya **conecta los servers MCP declarados del proyecto** (paso `mcp_wire`);
  las tools ya están disponibles en la sesión.
- El dispatch (PROJ-04, dispatch.py:1648-1657) ya arma el pool con los
  `team_members` **∪ los agentes `project_local` del proyecto**.
- O sea: los servers MCP YA son project-scoped; lo único **estático por-agente** es
  la resolución del **allowlist** de tools MCP. Ese es el eslabón a cambiar.

> Nota sobre fuga cross-tenant: un grant **estático** a un agente compartido sí
> sería ambiguo/leaky. El modelo de este ADR es **contextual** ("el agente, al
> correr en el proyecto P, usa las tools de P"), que es inequívoco y NO filtra
> entre proyectos — es la forma limpia de relajar el scope-check.

## Decisión propuesta

1. **Las tools MCP pasan a ser aportadas por el proyecto en runtime.** El allowlist
   MCP efectivo de un run = las tools de los servers MCP **declarados en el
   proyecto del run** (elegidas/importadas a nivel de proyecto, supply-chain ADR
   0052). **Sin `agent_tools` para MCP.**
2. **Los builtins / tools de rol siguen por-agente** (capacidad intrínseca:
   `stack_exec`, `run_pytest`, ficheros, red…). Sin cambios.
3. **Granularidad por rol OPCIONAL a nivel de proyecto**: una política del proyecto
   que mapea tool (o server) MCP → roles permitidos (p. ej. `jira → project_manager`,
   `confluence → technical_writer`). **Default: todas las tools MCP del proyecto
   disponibles para todos los agentes del proyecto.** Se declara en el PROYECTO,
   nunca por-agente.
4. **Se deprecia el gate por-agente para MCP**: la comprobación de scope
   (agents.py:1064-1081) deja de ser la puerta; la puerta es la declaración en el
   proyecto (+ la política de rol opcional). Los `agent_tools` MCP existentes pasan
   a ser, como mucho, un **override** por-agente (o se retiran en migración).
5. **Agentes compartidos (tenant-local) por defecto.** Forkear a `project_local`
   (ADR 0066) sigue disponible para **personalizar de verdad un agente por proyecto**
   (persona/modelo propios), pero **deja de ser requisito para usar MCP**.

## Mecanismo (dónde cambia)

- **Dispatch / run-contract** (`orchestrator/dispatch.py`): al construir el request
  del run, calcular las tools MCP desde los servers del proyecto (+ filtro por rol
  de la tarea si hay política) e inyectarlas en el allowlist junto a los
  builtins/tools de rol del agente.
- **Effective-tools** (`shared_mcp` / endpoint de diagnóstico): el set efectivo =
  builtins/rol del agente ∪ tools MCP del proyecto (filtradas por rol). El panel de
  diagnóstico por-proyecto pasa a reflejar esto (y deja de salir vacío con agentes
  tenant-template, cerrando el gap que vimos el 2026-07-23).
- **`agents.py` PUT /tools**: MCP deja de validarse/exigirse por-agente (o se
  conserva como override opcional). Los builtins siguen igual.
- **UI**: la asignación MCP por-agente ("Avanzadas") se simplifica o se retira; la
  sección **MCP del proyecto** pasa a ser el sitio, con un editor **opcional** de
  política rol→tool. La importación al catálogo (ADR 0052) se mantiene a nivel de
  proyecto (supply-chain).

## Opciones consideradas

- **A. Aportadas por el proyecto en runtime + rol opcional (recomendada, este ADR).**
  Agentes compartidos, cero proliferación, MCP = capacidad del proyecto.
- **B. Forkear a `project_local` por proyecto (ADR 0066).** Válida para
  personalización real por proyecto; se conserva como tal, pero NO como mecanismo de
  MCP (prolifera).
- **C. Grant estático por-agente (statu quo).** Ambiguo para agentes compartidos;
  rechazada como modelo principal.
- **D. Relajar el scope-check para permitir grants estáticos a agentes compartidos.**
  Rechazada: introduce ambigüedad/fuga cross-proyecto. El modelo dinámico (A) es la
  forma limpia.

## Consecuencias

- **A favor:** agentes compartidos funcionan en N proyectos con distinto set de
  tools; sin proliferación; MCP = capacidad del proyecto (coherente con
  `project.mcp_servers`); supply-chain preservado a nivel de proyecto; UX más simple
  (adiós micro-asignación MCP por-agente); desaparece la rareza que señaló el operador.
- **Riesgos / a cuidar:** (a) por defecto todo agente del proyecto puede llamar toda
  tool MCP del proyecto — mitigado por la política de rol opcional (y el argumento
  "menos tools = mejor foco" se resuelve con el filtro por rol); (b) **migración** de
  los `agent_tools` MCP existentes (deprecar → nivel proyecto u override); (c) toca
  la **resolución del allowlist** del run (cuidado con effective-tools + el guard de
  `unknown tool`, que ahora ve las tools del proyecto siempre disponibles); (d) el
  panel de diagnóstico por-agente cambia de semántica.
- **Relación:** sustituye, PARA MCP, el modelo por-agente implícito en Plan
  06.15/06.18 (los builtins no cambian); complementa ADR 0117 (catálogo HTTP-only) y
  ADR 0127 (conector OAuth); ADR 0052 (import) se mantiene a nivel de proyecto; ADR
  0066 (adopción/fork) queda como vía de personalización, no de MCP; ADR 0091
  (asignación por rol) es el punto natural donde aplicar el filtro por rol.

## Alcance / no-goals

- Los **builtins/tools de rol siguen por-agente** — este ADR no los toca.
- **Forkear agentes sigue disponible** (personalización real por proyecto); no se
  elimina.
- La **política de rol es OPCIONAL**; el default es "todos los agentes del proyecto
  ven todas las tools MCP del proyecto".
- **Interino (2026-07-23):** NO se forkean los agentes de `hello-world`; se quedan
  como `global_tenant_template`. Context7 queda **declarado + importado a nivel de
  proyecto** pero, hasta que aterrice este modelo, los agentes tenant-template no lo
  usan en runs (o requeriría forkear, que se difiere a propósito).

## Estado de implementación

- **Fase 1 — HECHA (2026-07-23):** núcleo aditivo y unit-testeado.
  `agent_tools_enforcement.resolve_project_mcp_tool_names(session, project)`
  (nombres `<server>.<tool>` importados de los servers declarados del proyecto) +
  `extend_allowlist_with_project_mcp(base, project_mcp)` (unión; `None`→`None` para
  no restringir a un agente sin restricción). Cableado en
  `orchestrator/dispatch._build_request` tras `combine_tool_allowlists`. Es
  **aditivo**: no puede romper runs (peor caso = la tool MCP no callable, estado
  pre-0128). Tests: `tests/unit/test_project_mcp_allowlist.py`.
- **Pendiente:**
  - **Fase 2 — granularidad por rol** (política de proyecto rol→tool; filtro en el
    resolver por el rol de la tarea). Data-model nuevo (columna/tabla) + UI.
  - **Fase 3 — deprecar el gate por-agente de MCP** (`agents.py:1064-1081` +
    simplificar el PUT `/agents/{id}/tools` y el effective-tools/diagnóstico) +
    **migración** de los `agent_tools` MCP existentes.
  - **Fase 4 — UI**: quitar/atenuar la asignación MCP por-agente; sección MCP del
    proyecto como sitio (+ editor de política de rol opcional).
  - **Verificación e2e**: run real usando una tool MCP del proyecto, confirmando que
    el nombre del allowlist casa con el que el agent-runtime registra
    (`<server>.<tool>`, guion vs guion_bajo). No verificable en sesión headless.
