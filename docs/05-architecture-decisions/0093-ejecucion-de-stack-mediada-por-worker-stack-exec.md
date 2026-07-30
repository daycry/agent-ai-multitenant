---
adr: "0093"
title: Ejecución de stack mediada por el worker — el tool `stack_exec`
status: accepted
date: 2026-06-29
deciders: operador, System Architect (claude-opus)
phase: soporte-stacks-no-python
related: ["0012", "0044", "0045", "0048", "0051"]
docs_language: es
---

# ADR 0093 — Ejecución de stack mediada por el worker (`stack_exec`)

## Contexto

Un agente de un proyecto **no-Python** (p.ej. CodeIgniter 4 / PHP) no puede ejecutar su toolchain
(`composer install`, `vendor/bin/phpunit`, `php spark`) durante su bucle. Análisis del código:

- **`shell_exec`** corre `subprocess` **dentro del agent-runtime**, cuya imagen es `python:3.12-slim`
  - git (sin php/composer/…) → `FileNotFoundError`. Que sea fino es CORRECTO (principios 2 y 3: el
    agente orquesta, no carga todos los toolchains).
- **Los tools `run_*` (`docker_command`)** ejecutan `docker.from_env()` **dentro del agent-runtime**,
  que **no tiene el SDK docker ni el socket** (principio 2, `assert_no_docker_socket`). Están cableados
  (resuelven imagen) pero **nunca lanzan un contenedor** — rotos de forma latente, sin test e2e del
  launch real.
- Las imágenes de stack (`php-phpunit` = `php:8.3-cli` + composer, `sleep infinity`) **solo las lanza
  el WORKER** (`TestRuntimeRunner`) como verificación post-hoc, tras el commit. El agente no tiene
  volante sobre su stack.

Quién tiene Docker es el **WORKER** (vía docker-socket-proxy), no el agent-runtime (a propósito). El
problema no es "meter php en el sandbox" sino **separar "el agente pide ejecutar en el stack" de "quién
tiene Docker"**.

## Decisión

Se introduce el tool builtin **`stack_exec`**: "ejecuta este comando autorizado en el runtime-template
de MI stack (donde existe el toolchain)". Convive con `shell_exec` (entorno fino del agente:
git/python/fileops). El agente NO lanza contenedores; **pide al worker que lo haga**:

```
agente → stack_exec(command)
  runtime: InternalAgentAPI.run_stack(command)         (canal /internal/agent/*, token minteado, ADR 0012)
    → POST /internal/agent/run-stack {task_id, command}
      → encola Celery `run_stack_command` (worker) y espera el resultado (result backend + timeout)
        worker: StackRunner.run_command(worktree, runtime_template, command)
          (reusa TestRuntimeRunner: lanza la imagen del stack con el worktree RW + dep-cache, devuelve rc+logs)
    ← {exit_code, logs, timed_out}
  ToolResult(ok = exit_code==0, output={exit_code, logs})
```

### D1 — El WORKER ejecuta el stack; el agente solo lo solicita

`stack_exec` se ejecuta en el **runtime-template del proyecto** (resuelto desde
`project.default_runtime_template`, ADR 0045/0051), reutilizando el `TestRuntimeRunner` que ya monta el
worktree de la tarea RW y sabe lanzar la imagen del stack. Esto cubre `composer install`,
`vendor/bin/phpunit`, `php spark`, `npm ci`, etc., en **cualquiera de los 14 stacks** sin tools nuevas
por lenguaje (coherente con ADR 0045 Alt-2: "cero tools nuevas por lenguaje; el runtime es config de
proyecto").

### D2 — Misma allowlist de seguridad que `shell_exec`

El comando de `stack_exec` se valida contra `project.allowed_commands` (deny-by-default, ADR 0045)
**en el worker, antes de ejecutar**. Sin allowlist → deny-all. El runtime del stack mantiene el
aislamiento del test-runtime (`cap_drop ALL`, read-only root, red `internal`, non-root).

### D3 — `run_*` (docker_command in-sandbox) se retira

Los `run_*` que dependían de `docker.from_env()` dentro del sandbox **no funcionan** (no hay socket por
principio 2). Se retiran/redirigen por este puente. Se corrige también el docstring de `shell_exec`
(`__main__.py`) que prometía `php`/`composer` (binarios inexistentes en la imagen del agente).

**Alcance de la retirada (implementado):** se quitan los `run_*` del grant del equipo built-in CI4
(`seeds/ci4_team.py`) — ahí eran ruido que el modelo invocaba y fallaba; el toolchain va por
`stack_exec`. Las filas `run_*` permanecen en el catálogo de plataforma (`builtin_tools.py`) por
compatibilidad con proyectos que las tuvieran asignadas; su retirada total del catálogo +
`RUNTIME_WIRED_TOOL_NAMES` + contract tests se difiere a un cambio propio (afecta a no-CI4), para no
hacer big-bang aquí.

## Invariantes preservadas (principios 2 y 3)

- El agente sigue en su **contenedor efímero endurecido sin socket Docker**. El WORKER (que ya tiene
  Docker) ejecuta el stack — exactamente el reparto que el sistema ya hace para los tests post-hoc.
- La imagen del agent-runtime **sigue fina** (Python+git+claude opt-in) — NO se le añade php/composer
  (rechazado: engordar el sandbox por-lenguaje no escala y contradice ADR 0064).
- Se reusa la cañería de runtime-templates + dep-cache + pre_install (ADR 0045/0051); el cambio es el
  **puente de ejecución**, no toolchain en el sandbox.

## Alternativas rechazadas

- **A — agent-runtime por stack** (imagen con php+python+composer+agent_runtime por cada stack): el
  agente ejecutaría el stack nativo vía `shell_exec`. Reusa `default_runtime_template` para elegir
  imagen (1 punto), pero exige **N imágenes multi-lenguaje** a mantener (producto cartesiano con
  WITH_CLAUDE), arranque más gordo, y no reusa la máquina de dep-cache/pre_install. Más rápido para
  desbloquear UN stack, peor para escalar.
- **B — engordar `agent-runtime:v1`** con php+composer (multi-stack): antipatrón — cada stack engorda
  la imagen que se lanza en TODAS las ejecuciones; contradice el catálogo granular y ADR 0064.
- **Dar socket Docker al agent-runtime** (para que `docker_command` funcione in-sandbox): regresión de
  seguridad directa contra el principio 2. Intocable.

## Consecuencias / notas

- **Concurrencia**: el run del agente ocupa un slot de worker; `run_stack_command` necesita otro. Se
  usa una **cola dedicada** (o se documenta el requisito) para evitar deadlock si todos los slots
  esperan stack-execs.
- **Artefactos**: se arregla de paso el flujo de dep-cache (pasar `dep_cache_host_path`, alinear
  `HOME`/`composer cache`) para que `composer install` cachee y `composer.lock` se persista
  controladamente (y `vendor/` quede gitignored), en vez de generarse tras el commit del test-runtime.

## Hallazgos de despliegue (infra) — verificación e2e del puente

Al desplegar el puente y validarlo e2e (`stack_exec` → worker → php-phpunit sobre el worktree)
afloraron **tres bugs de infraestructura pre-existentes** que impedían que el exec en runtime-templates
funcionara para NADIE (también los checks post-hoc de tests). Arreglados:

1. **Doble `sleep infinity`** (`test_runtime.py`): las imágenes runtime declaran
   `ENTRYPOINT ["sleep","infinity"]` y `_build_test_kwargs` además pasaba `command ["sleep","infinity"]`
   → el daemon ejecutaba `sleep infinity sleep infinity` → _"invalid time interval 'sleep'"_ → el
   contenedor salía al instante y cada `exec_run` daba **409 "container is not running"**. Fix: keep-alive
   por `entrypoint` (sin `command` que se anexe al ENTRYPOINT de la imagen).
2. **`EXEC=0` en el docker-socket-proxy** (`docker-compose.manuals.yml`): el proxy de mínimo-privilegio
   bloqueaba `POST /exec/{id}/start` → **403 Forbidden** en todo `exec_run`. El worker necesita exec para
   correr comandos DENTRO de los runtimes que él lanza. Fix: `EXEC: "1"`. Sigue mínimo-privilegio (proxy
   en red dedicada solo-workers; el agent-runtime nunca toca el socket — principio 2 intacto).
3. **`project.slug` vacío**: el proyecto demo "Api CI" se creó sin `slug` (anomalía: los demás lo tienen).
   `execution.py` exige `project.slug` para provisionar el worktree (si falta → tmpfs efímero), y
   `_run_stack_command` devolvía _"project/org not resolvable"_. Fix de datos: `slug='api-ci'` (coincide
   con el bare repo ya creado en `demo/api-ci`). La causa de raíz (creación de proyecto sin slug) queda
   como follow-up de producto.

Verificado e2e directo (invocando `run_stack_command` sobre un worktree real): `php -v` → rc 0;
`composer install` → rc 0 y `vendor/autoload.php`+`composer.lock` escritos al worktree (mount RW
persiste); el gate deny-by-default deniega `rm`/`curl` y admite `composer`/`php`/`vendor/bin/phpunit`.

## Trazabilidad

Investigación multi-agente (workflow `php-stack-execution-design`) en el scratchpad de la sesión; plan
en `~/.claude/plans`. Implementación: `agent_runtime/stack_exec_tool.py` + `internal_api.py` +
`builtin_families.py` (runtime), `routers/internal_agent.py` (api-server), `workers/test_runtime.py`
(StackRunner) + `workers/tasks.py` (Celery), catálogo en las 4 fuentes (ADR 0048) + grants CI4.
