---
title: "Personalización de equipos/agentes built-in por tenant/proyecto + herencia de modelo"
date: 2026-06-19
status: design-approved
authors: [claude-code-2026-06, operador]
relates_to_adrs: ["0021", "0028", "0053", "0054", "0055", "0057"]
---

# Diseño — Personalización de built-ins + herencia de modelo

## Contexto y problema

El operador, al crear un proyecto plantilla con un equipo, encontró una serie de
fricciones reales (no un bug puntual) alrededor del ciclo de **adoptar y
personalizar equipos/agentes built-in**:

1. Los equipos/agentes **built-in son read-only** y no hay forma ergonómica de
   personalizarlos por tenant/proyecto (el fork existe **solo por-agente**; no
   hay "forkear el equipo entero").
2. **No se puede fijar el modelo por proyecto ni por equipo** — solo el default
   global de plataforma + override por agente. Poner un equipo a usar Claude
   obliga a forkear agente por agente o cambiar el default global.
3. Los agentes de **equipos built-in salen "vacíos" de capacidad**: el equipo
   CI4 cablea tools pero **no skills**; otros equipos pueden variar. (Los
   agentes built-in _sueltos_ de `builtin_agents.py` sí traen tools+skills.)
4. La **vista de Capacidad es confusa**: el aviso "agente global: no ve
   conocimiento ni memoria de proyecto en esta vista" se lee como una limitación,
   y no es evidente desde dónde se asigna cada vía ni cómo personalizar un
   built-in.

## Objetivos

- Poder **adoptar/personalizar un equipo built-in completo** en un proyecto o en
  el tenant, como copia editable (modelo + tools + skills + prompts), sin tocar
  el built-in global.
- Poder **fijar el modelo por defecto a nivel de proyecto y de equipo** (además
  del override por agente y el default de plataforma).
- Que los equipos built-in salgan **completos** (tools + skills sensatas por rol).
- Hacer la vista de Capacidad **clara**: aviso accionable + origen de cada vía.

## No-objetivos (YAGNI)

- No "overlay/referencia en runtime" para la adopción (se descartó por
  complejidad de runtime/RLS); usamos copia profunda con enlaces de fork.
- No device-flow propio de Claude (no existe endpoint de Anthropic; ADR 0064).
- No tocar el catálogo cerrado de proveedores (ADR 0021): solo cambia **de dónde
  se hereda** el `model_config`.
- No bloquear la re-adopción: adoptar varias veces crea copias nuevas
  (distinguibles por `forked_from`).

## Decisiones cerradas (brainstorming con el operador)

- **Destino de adopción:** proyecto (`project_local`) **o** tenant
  (`global_tenant_template`), elegido al adoptar.
- **Niveles de modelo:** cadena `plataforma → proyecto → equipo → agente`; gana
  el más específico que pinee `provider`+`model`.
- **Mecanismo de adopción:** copia profunda + enlaces de fork (`forked_from_*`)
  para diff/re-sync (reutiliza la maquinaria de fork por-agente ya existente).
- **Defaults de built-in:** mapa por rol `role → {tool_slugs, skill_slugs}`,
  overridable por agente en el seed.

---

## Parte A — Herencia del modelo (`plataforma → proyecto → equipo → agente`)

**Datos (migración):**

- `teams.model_config` JSONB default `'{}'` — default de modelo del equipo.
- `projects.model_config` JSONB default `'{}'` — default de modelo del proyecto.
- (`agents.model_config` y `platform_settings['model.default_config']` ya existen.)

**Resolución:** en el punto donde ya hay contexto tarea→proyecto→equipo
(`workers/model_resolver.py` + `orchestrator/dispatch.py`), elegir el primer
nivel que pinee `provider`+`model`, reusando `config_needs_default_model()`:
`agente → equipo (project.team_id) → proyecto → plataforma`. El `system_prompts`
del agente nunca se pisa por la herencia del modelo.

**UI:** sección "Modelo por defecto del equipo" en el detalle del equipo; campo
"Modelo por defecto del proyecto" en ajustes del proyecto. Reusan el selector
provider+modelo+temperatura y el validador (`validate_model_config`) existentes.

**Tests (TDD):** unit de la resolución de precedencia (cada nivel gana sobre el
de abajo; `system_prompts` se conserva); integración de dispatch resolviendo por
equipo y por proyecto.

## Parte B0 — Enriquecimiento del catálogo (skills + tools + prompts)

Precede a B (un catálogo más rico = mejores asignaciones por rol). Respeta las
**taxonomías cerradas**: skills ∈ {backend, frontend, devops, qa, research, docs}
(ADR 0050); tools ∈ {file, runtime, network, knowledge, notification, command}
(ADR 0049). Sin categorías nuevas → sin migración de taxonomía.

**B0.1 — Skills nuevas (baratas: fila de catálogo + `prompt_fragment`).** Llenan
huecos reales, el mayor: **hay equipo CI4 (PHP) y CERO skills PHP**. Propuestas:

- backend: `php-phpunit`, `codeigniter4-hmvc`, `doctrine-orm`,
  `secure-coding-owasp`, `sql-optimization`, `rag-pgvector`
- frontend: `twig-templating`, `state-management`, `web-performance`
- devops: `dependency-audit-sca`, `backup-recovery`
- qa: `contract-testing`, `load-testing`
- research: `prompt-engineering`, `eval-design`, `web-research`
- docs: `changelog-authoring`, `openapi-authoring`
  Seed + test "todo slug del mapa de roles existe en el catálogo".

**B0.2 — Tools nuevas (FEATURE: el runtime las ejecuta; las de red exigen
proveedor + egress allowlist + guardrails — acción sensible).** ADR propio.

- network: `web-search` (búsqueda en internet vía proveedor + egress),
  `fetch-url` (navegar/leer página → markdown, con allowlist + límites).
- runtime: `run-tests` genérico por runtime-template (complementa `run-pytest`
  para PHP/Node) + `format-code`.

> **Git NO se añade como tool del agente** (decisión, corrección del operador):
> la plataforma **ya gestiona git** (Principios 4 y 5 de CLAUDE.md) — worktrees
> por tarea, commit con trailers `Plan-Id`/`Task-Id`/`Execution-Id` y PR por plan,
> en el worker (`plan_git.py`/`git_repos.py`). El agente solo edita ficheros
> (`write-file`/`apply-patch`); dar git-\* directo al agente le dejaría commitear
> fuera del flujo gestionado (rama/trailers incorrectos). Descartado.

**B0.3 — Revisión de prompts (donde aporte; proponer, no reescribir a ciegas).**

- Los `prompt_fragment` de las skills nuevas se escriben con criterio (parte de
  B0.1).
- Revisar los `system_prompt_es/en` de los agentes built-in (CI4 +
  `builtin_agents`) para **alinearlos con sus skills/tools** y ganar
  claridad/concisión; cada cambio se propone por agente (diff visible) y se
  mantiene la paridad es/en. Sin tocar lo que ya funciona bien.

**Tests B0:** unit de catálogo (slugs/categorías válidas, bilingüe es/en
presente); las tools de red/git con su suite propia en su ADR.

## Parte B — Built-ins completos (tools + skills por rol)

**Sin cambio de datos** (junctions `agent_tools`/`agent_skills` ya existen; el
fork ya las clona, agents.py:67-101).

- **Mapa por rol** `role → {tool_slugs, skill_slugs}` (un único módulo de seed,
  DRY), con slugs de `builtin_tools.py` / `builtin_skills.py`.
- Los seeds de equipos built-in (`ci4_team.py`, `builtin_teams.py`) referencian
  el mapa por el rol de cada agente; **override por agente** posible. CI4 gana
  skills; equipos nuevos nacen completos por construcción. Skills con su
  `proficiency` (`AgentSkillProficiency`).

**Tests (TDD):** aserción de que **todo agente de un equipo built-in tiene ≥1
tool y ≥1 skill** (red hoy para CI4 → verde tras el mapa).

## Parte C — Adopción/personalización de equipos built-in

**Datos (migración propia de la Ola C):**

- `teams.forked_from_team_id` (FK teams, nullable) + `teams.forked_from_version`
  (timestamp) — espejo de `agents`. (La Ola A añade `teams.model_config` /
  `projects.model_config` en su propia migración; A y C son PRs separados.)

**Endpoint:** `POST /teams/{source_id}/adopt`
body `{ target: "project"|"tenant", project_id?, name?, model_config? }`.
En una transacción (tenant-scoped, RLS):

1. Crea `Team` del tenant (`is_builtin=false`, `name`, `forked_from_team_id`/
   `version`, `model_config` si se eligió modelo al adoptar → engancha con A).
2. Por cada miembro: forkea el agente reusando el helper que clona persona +
   KBs/tools/skills + `forked_from`; `scope` = `project_local` (con `project_id`)
   o `global_tenant_template` según `target`. (Extiende el fork por-agente, que
   hoy solo crea `project_local`, para aceptar el scope destino.)
3. Recrea `TeamMember` (rol, `assignment_priority`, `review_capability`) → agentes
   forkeados.

El built-in original queda intacto (global, read-only). Re-adopción permitida
(copias nuevas).

**UI:** botón "Adoptar / Personalizar equipo" en el equipo built-in → diálogo
(destino proyecto/tenant, modelo opcional, nombre) → navega al equipo nuevo
editable.

**Tests (TDD):** integración — adoptar un equipo built-in a un proyecto crea un
team `is_builtin=false` con N agentes forkeados (con tools/skills clonadas) y N
`TeamMember`; adoptar a tenant crea agentes `global_tenant_template`; el built-in
no se muta; `model_config` opcional se aplica al team nuevo.

## Parte D — UX de las pantallas de Equipos/Agentes y de Capacidad (transversal)

Sin cambio de lógica; claridad y separación "mío vs catálogo":

1. **Listas "solo del tenant" + adoptar desde catálogo** (sugerencia del
   operador). La pantalla de **Equipos** (y, de forma análoga, la de **Agentes**)
   lista SOLO lo del tenant y editable (`project_local` + `global_tenant_template`,
   `is_builtin=false`). Los built-in **no se mezclan** en la lista; se acceden con
   un botón **"Adoptar equipo built-in"** que abre un **selector del catálogo
   built-in** (para explorarlos sin saturar la lista) → al elegir uno, abre el
   diálogo de adopción (Sección C: destino proyecto/tenant, modelo opcional,
   nombre). Así "Mis equipos" queda limpio y editable, y los built-in son un
   **catálogo del que adoptas** sin perder descubribilidad. (La pantalla de
   Agentes ya separa built-ins vs project_local; se alinea al mismo patrón:
   tenant en la lista + adoptar/forkear desde el catálogo.)
2. **Reescribir el aviso "agente global"** (capability-hub) a explicación +
   acción: "Agente global: aquí ves su config base; en una tarea de proyecto usa
   el contexto de ese proyecto (ADR 0054); para personalizarlo usa
   Adoptar/Personalizar."
3. **Banner built-in read-only** con el botón Adoptar/Personalizar a mano (en el
   detalle de un built-in que se abra desde el catálogo).
4. **Modelo efectivo + origen** ("heredado del equipo/proyecto/plataforma" o
   "propio") reflejando la cadena de A.
5. Cada una de las cuatro vías indica su origen y si es editable aquí.

**Tests:** unit del nuevo copy bilingüe (es/en) del aviso; vitest de que la lista
de equipos filtra `is_builtin=false`; el resto es visual.

---

## Multi-tenancy / seguridad

- Las filas creadas (team + agentes + junctions) son **tenant-scoped** (RLS). El
  origen built-in es global y legible por todos los tenants (solo lectura).
- Sin secretos nuevos; el modelo sigue resolviéndose con credenciales de Vault
  (ADR 0057). El catálogo de proveedores no cambia (ADR 0021).

## Olas de implementación (un PR/plan por ola)

1. **Ola B0.1** — skills nuevas en el catálogo (PHP/CI4 + security + data + LLM)
   - sus `prompt_fragment`. Barato (seed + test de catálogo). Ganancia inmediata.
2. **Ola B0.2** — tools de red (`web-search`/`fetch-url`) + `run-tests`/
   `format-code`. **FEATURE con ADR propio** (proveedor de búsqueda + egress
   allowlist + guardrails). (Git NO — lo gestiona el sistema.)
3. **Ola B0.3** — revisión de `system_prompt` de agentes built-in (alinear con
   skills/tools; proponer por agente). Puede solaparse con B0.1/B.
4. **Ola B** — mapa por rol + skills en equipos built-in + test "no vacío"
   (usa el catálogo enriquecido de B0.1).
5. **Ola A** — migración (`teams.model_config`, `projects.model_config`) +
   resolución de la cadena + UI de defaults por equipo/proyecto.
6. **Ola C** — migración (`teams.forked_from_*`) + endpoint `/teams/{id}/adopt`
   - extensión del fork por-scope + UI "Adoptar/Personalizar equipo".
7. **Ola D** — copy del aviso + banner read-only + modelo efectivo/origen en la
   UI + listas "solo del tenant" + adoptar desde catálogo.

Cada ola: TDD, migraciones reversibles, ADR donde proceda (la cadena de modelo y
la adopción de equipo justifican un ADR cada una, extendiendo 0055/0057).

## Riesgos / preguntas abiertas

- Precedencia de 4 niveles: cubrir bien el caso "nivel intermedio pinea solo
  parcial" — regla: un nivel "cuenta" solo si pinea `provider`+`model` juntos
  (`config_needs_default_model`); si no, se ignora y se baja.
- Extender el fork por-agente para `global_tenant_template` sin romper sus tests
  actuales (hoy asume `project_local`).
