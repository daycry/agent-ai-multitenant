---
name: stack-exec-feature
description: stack_exec (ADR 0093) — el agente pide al worker correr su toolchain (composer/phpunit/php spark) en el runtime-template; implementado + desplegado + verificado e2e.
metadata:
  node_type: memory
  type: project
  originSessionId: 9b6ffa32-bda3-49a0-a5ed-708c0fca5208
---

**stack_exec** (ADR 0093, rama `plan/runs-visor-trabajo`, 2026-06-29) — los agentes de
stacks no-Python ya pueden ejecutar su toolchain. El sandbox (agent-runtime) es fino
(python+git, sin php/composer), así que **shell_exec NO puede** hacer `composer install`.
`stack_exec` lo resuelve: el agente PIDE al WORKER (que sí tiene Docker vía socket-proxy)
correr el comando en el runtime-template del proyecto (php-phpunit) sobre el worktree de la
tarea, y recibe rc+logs. Principio 2 intacto (el agente nunca toca el socket).

Camino: `StackExecTool` → `InternalAgentAPI.run_stack` → `POST /internal/agent/run-stack`
→ Celery `workers.run_stack_command` (cola **test**) → `TestRuntimeRunner.run_command`
(reusa `_start_main`/`_exec` sobre el worktree RW) → rc+logs sync por result backend.
Catálogo en las 4 fuentes (ADR 0048) + grant `stack-exec` en `_BASE_TOOLS` de CI4; los
`run_*` (docker_command rotos in-sandbox) retirados del grant CI4 (D3). Commits b9c8efe…e783706.

**3 bugs de infra hallados al verificar e2e** (afectaban TODO exec en runtime-templates, no
solo stack_exec) → ver [[gotcha-test-runtime-exec]]:

1. `_build_test_kwargs` doblaba `sleep infinity` (ENTRYPOINT imagen + command) → contenedor
   moría → exec_run 409. Fix: keep-alive por `entrypoint` (sin command).
2. `EXEC=0` en docker-socket-proxy → exec 403 Forbidden. Fix: `EXEC: "1"` en
   docker-compose.manuals.yml.
3. Proyecto demo "Api CI" se creó con `project.slug` VACÍO (anomalía) → execution.py no
   provisiona worktree y `_run_stack_command` no resuelve. Fix de datos: `slug='api-ci'`.
   Causa raíz (creación de proyecto sin slug) = follow-up de producto.

**RECETA DE BUILD (no olvidar `--build-arg WITH_CLAUDE=1`)**: agent-runtime:v1, api-server
(:ci + :manuals) y todo lo FROM api-server DEBEN construirse con `WITH_CLAUDE=1` o pierden el
Claude Agent SDK → claude_sdk (el equipo Demo usa opus) y el asistente rompen con
ModuleNotFoundError. El worker NO necesita el SDK (claude_sdk corre en el agent-runtime).
Deploy dev: `docker compose -p agentic-platform -f docker-compose.yml -f .dev.yml -f .manuals.yml
up -d --force-recreate --no-deps <svc>`. agent-runtime:v1 se recoge solo al lanzar (no recrear).

Verificado e2e directo (run_stack_command sobre worktree real): `php -v`→rc0;
`composer install`→rc0 + vendor/+composer.lock escritos al worktree; gate deny `rm`/`curl`,
allow `composer`/`php`/`vendor/bin/phpunit`. Falta solo el run LLM-driven real (lo lanza el
operador por UI). Relacionado: [[runs-no-convergen-causas-estructurales]].
