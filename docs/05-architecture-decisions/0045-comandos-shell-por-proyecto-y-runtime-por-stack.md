---
adr_id: "0045"
title: "Comandos shell por proyecto (allowlist deny-by-default) + runtime por stack para run_*"
status: accepted
date: 2026-06-01
authors: [system_architect]
plan_referenced: 06.16-polyglot-tool-catalog
docs_language: es
---

# ADR 0045 — Comandos shell por proyecto + runtime por stack

## Contexto

El operador necesita que los agentes **lancen comandos del stack del
proyecto** (`php`, `composer`, `vendor/bin/phpunit`, `pest`, `npm`,
`dotnet`…), no sólo Python. La verificación del motor confirmó que la
plataforma **ya es políglota** pero que el cableado estaba incompleto:

1. **`ShellExecTool` ya existía** (`agent_runtime/shell_exec.py`):
   parsea el comando con `shlex` en un argv (nunca a través de una
   shell → sin superficie de inyección), aplica un **timeout** y un
   **allowlist por basename** (deny-by-default), y trunca la salida.
   Pero **nadie lo instanciaba** con la allowlist de un proyecto, y
   **no estaba en el catálogo `tools`** (no asignable a un agente).
2. **El modelo `Project` no tenía campos** para declarar qué binarios
   autoriza ni qué stack/runtime usa.
3. **Los `run_*`** (`run_pytest` / `run_lint` / `run_typecheck` /
   `run_build`, `implementation_type=docker_command`) tenían el runtime
   `python-pytest` **fijo**: un proyecto PHP no podía correr sus tests
   en `php-phpunit`.

Los runtime templates políglotas (`php-phpunit`, `php-pest`,
`dotnet-test`, `node-*`, …) ya viven en `shared_test_runtimes.catalog`;
el motor de tests (`workers/test_runtime.py`) ya los resuelve por id.
Lo que faltaba era **config de proyecto + cableado + UI**. Este ADR fija
las dos decisiones de diseño de ese cableado.

## Decisión

### 1. Allowlist de comandos shell **por proyecto, deny-by-default**

Se añaden dos columnas a `projects` (migración reversible **0072**, cabeza
única sobre `0071_model_prices_provider_id`):

- `allowed_commands` — `TEXT[]` **NOT NULL DEFAULT `'{}'`**. La lista de
  **basenames** de programa que `shell_exec` puede ejecutar en ese
  proyecto. Array (no JSONB) porque la semántica es de **pertenencia**
  (membership-only), espejando `projects.default_kb_grants` (migr. 0027).
- `default_runtime_template` — `TEXT` **nullable**. El id del runtime
  template del stack (`php-phpunit`, `node-jest`, …) contra el que
  resuelven los `run_*`. `NULL` = mantener el default por-tool.

Ambas heredan la **RLS por tenant** de `projects` (sin cambio de policy).
Aditivas y backward-compatible: las filas existentes arrancan con `'{}'`
/ `NULL`.

**Deny-by-default es la regla dura**: `shell_exec` ejecuta **sólo** los
binarios cuyo basename (`Path(argv[0]).name`) esté en
`project.allowed_commands`. **Lista vacía ⇒ no ejecuta nada** (todos los
comandos rechazados con la lista de permitidos en el error). El operador
**autoriza explícitamente** `php`/`composer`/etc. por proyecto; no hay
allowlist heredada ni global. La validación sigue siendo argv vía `shlex`

- timeout, sin shell (sin inyección), y un `cwd` que escape del workspace
  se rechaza.

> **Contraste con el enforcement de tools por agente (ADR 0044)**: allí
> el sentinel `None` = "sin restricción" preserva el comportamiento de
> los agentes existentes (no se les quita ninguna tool). Aquí es lo
> opuesto **a propósito**: `shell_exec` es `privileged` y ejecuta
> binarios arbitrarios del stack, así que el default seguro es **denegar
> todo** hasta que el operador autorice. No es backward-incompatible
> porque `shell_exec` es nueva en el catálogo (Plan 06.16): ningún
> proyecto la usaba antes, así que arrancar deny-all no rompe nada.

### 2. `shell_exec` es **básica** (`is_builtin=true`) y `privileged`

`shell_exec` entra al seed `builtin_tools` (`category='command'`,
`implementation_type='builtin'`, `is_builtin=true`,
`security_level='privileged'`, `input_schema {command, cwd?}`). Por
tanto, según la taxonomía derivada de **ADR 0044**, aparece en la UI de
asignación de tools (Plan 06.15) en la pestaña **Básicas**, con un badge
**Privilegiada**. El nivel de seguridad es un **eje ortogonal** a la
dicotomía básica/avanzada (una básica puede ser `privileged`) — coherente
con ADR 0044.

### 3. Runtime **por proyecto, no por tool** (con fallback)

Los `run_*` resuelven su runtime template con esta **precedencia**
(`workers/test_runtime.resolve_run_runtime_id`):

1. `project.default_runtime_template` cuando el proyecto fija un stack
   (un proyecto PHP con `php-phpunit` corre ahí sus `run_*`).
2. el default propio del tool (`implementation_ref` → `runtime_template`
   en su config; p.ej. `run_pytest` → `python-pytest`) cuando el proyecto
   no fija nada (`NULL`).
3. `DEFAULT_RUN_RUNTIME_ID = "python-pytest"` como último fallback para
   los `run_*` que no traen `implementation_ref` (`run_lint` /
   `run_typecheck` / `run_build`).

Esto evita **duplicar tools por lenguaje**: hay un único `run_pytest`
asignable y el stack lo fija el proyecto. Un runtime id desconocido
surface como `RuntimeResolutionError` (subclase de `ValueError`) con el
conjunto conocido deletreado — un error **claro** para el operador, nunca
un `KeyError` que tumbe el boot.

### 4. Threading de la config al runtime (reutiliza el patrón de 06.15)

Ni `allowed_commands` ni `default_runtime_template` se aplican en el
frontend (la UI sólo configura). Viajan por la **misma ruta de spec** que
el allowlist de tools por agente de 06.15 / el allowlist de chat-mode de
06.14:

```
projects.{allowed_commands, default_runtime_template}
  → orchestrator.dispatch (lee el proyecto de la tarea)
  → ExecutionRequest.{allowed_commands, default_runtime_template}
  → workers.execution._agent_spec
  → AGENT_TASK_SPEC (json en el env del contenedor agent-runtime)
  → agent_runtime.__main__
       · allowed_commands → ShellExecTool(allowed_commands=frozenset(...))
         registrado en el ToolRegistry como `shell_exec`
       · default_runtime_template → WiringContext.project_default_runtime,
         consumido por _resolve_docker_image al construir los run_* (docker_command)
```

El agent-runtime **no depende** de `shared_test_runtimes`: el worker le
inyecta un `runtime_image_resolver` (respaldado por
`workers.test_runtime.resolve_run_runtime_image`) en el `WiringContext`,
así la precedencia y la lookup del catálogo viven en **un solo sitio**
(el worker).

**Semántica del sentinel para `allowed_commands` en el spec**:

- clave **ausente** (`None`) ⇒ `shell_exec` **no se registra** (un bare
  run / payload antiguo no tiene la tool).
- clave presente con lista (incl. **`[]`**) ⇒ `shell_exec` **se registra**
  bound a esa allowlist; **`[]` registra un `shell_exec` deny-all** (la
  tool existe pero rechaza todo). El dispatch siempre emite la clave para
  una tarea con proyecto (coerciona a `list[str]`), así que un proyecto
  sin binarios autorizados tiene un `shell_exec` deny-all explícito, no
  ausente.

## Consecuencias

### Lo que mejora

- Un proyecto puede ser de **cualquier stack**: el operador autoriza
  `php`/`composer`/`phpunit` (o `npm`, o `dotnet`) y elige su runtime, y
  el mismo `shell_exec` + los mismos `run_*` se comportan según el stack.
- **Backward-compatible para los `run_*`**: sin `default_runtime_template`
  (NULL) un proyecto Python sigue corriendo `run_pytest` en
  `python-pytest` exactamente como antes.
- **Seguro por defecto para `shell_exec`**: deny-all hasta autorización
  explícita; nunca una shell, siempre argv + timeout + cwd confinado al
  workspace.
- Cero tools nuevas por lenguaje: el runtime es config de proyecto, no
  una proliferación de `run_pytest_php` / `run_pytest_node`.

### Lo que añade de complejidad

- El dispatch del orquestador lee dos campos más del proyecto y los emite
  en el spec; el agent-runtime registra `shell_exec` condicionalmente y
  resuelve la imagen de los `run_*` vía resolver inyectado. Coste de
  cómputo despreciable; la complejidad es de **cableado** (documentada en
  la ruta de spec arriba).
- El conjunto de runtime templates seleccionables en la UI (chips de
  `RUNTIME_TEMPLATES`) está **codificado en el frontend** además de en
  `shared_test_runtimes.catalog`. Si se añade un template hay que tocar
  ambos. Se acepta por simplicidad (un `<select>` estático evita una
  llamada extra); si la lista crece se expondrá por endpoint.

### Trade-offs explícitos

- **Allowlist por basename, no por ruta absoluta**: `vendor/bin/phpunit`
  matchea por su basename `phpunit`. Es deliberado (los binarios viven en
  rutas distintas según el runtime), pero significa que autorizar
  `phpunit` autoriza cualquier `phpunit` del PATH dentro del sandbox. El
  confinamiento real lo da el contenedor (sin socket Docker, red
  restringida, cap-drop ALL) — la allowlist es una **segunda barrera**,
  no la única.
- **Selección manual del stack**: no hay auto-detección del runtime a
  partir del repo (queda como mejora futura). El operador elige el preset
  y el runtime a mano.

## Alternativas consideradas

### Alt-1: Allowlist global / heredada (plataforma → tenant → proyecto)

Una allowlist por capas como los guardrails (Plan 11).

- ✅ Menos repetición si muchos proyectos comparten stack.
- ❌ Oscurece **qué** puede ejecutar un proyecto concreto (hay que
  componer tres capas mentalmente). Para una tool `privileged` el
  operador quiere ver la lista efectiva **en el proyecto**, explícita.
- ❌ El motor de capas es el de guardrails (Plan 11), no este; mezclarlos
  acopla dos features.

Rechazada. La allowlist es **plana y por proyecto**. Si emerge la
necesidad de herencia será un plan con su ADR.

### Alt-2: Una tool `run_*` por lenguaje (`run_pytest_php`, …)

Duplicar las tools de runtime por stack.

- ❌ Proliferación de tools (4 `run_*` × N stacks) en el catálogo y en la
  UI de asignación. El stack es propiedad del **proyecto**, no de la tool.

Rechazada a favor de **runtime por proyecto** con fallback.

### Alt-3: `shell_exec` como tool **avanzada** (no básica)

Marcarla `is_builtin=false` para que caiga en la pestaña "Avanzadas".

- ❌ Contradice ADR 0044: `is_builtin` es el **único** criterio de
  básica/avanzada, y `shell_exec` es una builtin de plataforma. La
  sensibilidad se expresa con `security_level=privileged` (eje
  ortogonal), no degradándola a avanzada.

Rechazada. `shell_exec` es **básica + privilegiada**.

### Alt-4: `shell_exec` con allowlist no-vacía por defecto (allow-some)

Sembrar la allowlist con un set "seguro" (`ls`, `cat`, `git`…).

- ❌ Cualquier default no-vacío es una decisión de seguridad implícita
  que el operador no tomó. Deny-all obliga a una autorización consciente
  y deja el rastro (la lista del proyecto = exactamente lo permitido).

Rechazada. **Deny-by-default** es la única opción coherente para una tool
`privileged`.

## Esquema (migración 0072 — reversible)

```sql
-- 0072_projects_command_config (Revises: 0071_model_prices_provider_id)
ALTER TABLE projects
  ADD COLUMN allowed_commands TEXT[] NOT NULL DEFAULT '{}'::text[],
  ADD COLUMN default_runtime_template TEXT NULL;
-- downgrade(): DROP COLUMN default_runtime_template; DROP COLUMN allowed_commands;
-- Heredan la RLS por tenant de `projects` (sin cambio de policy).
```

Probada up / down / up por
`tests/integration/test_project_command_config.py`.

## Riesgos

| Riesgo                                                                           | Probabilidad | Impacto | Mitigación                                                                                               |
| -------------------------------------------------------------------------------- | ------------ | ------- | -------------------------------------------------------------------------------------------------------- |
| Operador autoriza un binario peligroso (`bash`, `sh`) y deshace el confinamiento | Media        | Medio   | La allowlist es la 2ª barrera; el contenedor (sin socket Docker, red restringida, cap-drop) es la 1ª.    |
| `default_runtime_template` apunta a un id que no existe en el catálogo           | Baja         | Bajo    | `RuntimeResolutionError` con el set conocido deletreado; el `<select>` de la UI sólo ofrece ids válidos. |
| El set de runtimes de la UI se desincroniza de `shared_test_runtimes.catalog`    | Media        | Bajo    | Documentado aquí + en la guía; ambos listan los mismos ids. Si crece, se expone por endpoint.            |
| Un payload antiguo sin `allowed_commands` deja un agente sin `shell_exec`        | Baja         | Bajo    | Es el comportamiento correcto (clave ausente ⇒ no registrar); el dispatch nuevo siempre emite la clave.  |

## Trazabilidad

- Roadmap: `docs/roadmap/06.16-polyglot-tool-catalog.md` (5 tareas, 5 fases).
- Migración: `apps/api-server/migrations/versions/20260601_0072_projects_command_config.py`.
- ORM + schemas + endpoints:
  `apps/api-server/src/api_server/db/domain.py` (`Project`),
  `schemas/projects.py` (`allowed_commands`, `default_runtime_template`
  en `ProjectCreate`/`ProjectUpdate`/`ProjectResponse`),
  `routers/projects.py`.
- Seed de la tool: `apps/api-server/src/api_server/seeds/builtin_tools.py`
  (`shell-exec` → `shell_exec`, `command`, `privileged`, builtin).
- Resolución de runtime: `apps/workers/src/workers/test_runtime.py`
  (`resolve_run_runtime_id` / `resolve_run_runtime` /
  `resolve_run_runtime_image`, `RuntimeResolutionError`,
  `DEFAULT_RUN_RUNTIME_ID`).
- Threading de spec: `apps/orchestrator/src/orchestrator/dispatch.py`
  (`request["allowed_commands"]` / `["default_runtime_template"]`) →
  `apps/workers/src/workers/execution.py` (`ExecutionRequest`,
  `_agent_spec`) → `docker/agent-runtimes/agent-runtime/agent_runtime/`
  (`__main__.py` registra `ShellExecTool`; `tool_wiring.py`
  `WiringContext.project_default_runtime` + `_resolve_docker_image`;
  `shell_exec.py` `ShellExecTool`).
- Frontend: `apps/admin-panel/app/admin/projects/[id]/commands/page.tsx`
  (chips + presets por stack + selector de runtime; `RoleGuard`
  `tenant_admin`).
- Tests: `tests/integration/test_project_command_config.py`,
  `test_shell_exec_allowlist.py`, `test_run_tools_by_stack.py`; e2e
  `apps/admin-panel/e2e/project-commands.spec.ts` (escrito, no ejecutado).
- Guía: `docs/03-guides/comandos-y-runtime-por-proyecto.md`.
- ADRs relacionados: 0014 (tools builtin), 0025 (MCP + ejecutores),
  0040 (seccomp/apparmor por contenedor — el confinamiento de 1ª barrera),
  0044 (taxonomía básica/avanzada + `security_level` ortogonal).
