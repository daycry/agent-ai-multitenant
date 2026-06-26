---
title: "El worktree del agente aparece vacío en /workspace (bind DooD / safe.bareRepository)"
tags: [worktree, dood, bind, docker, agent-runtime, git, sandbox, prod-18]
---

# El worktree del agente aparece vacío en /workspace (bind DooD / safe.bareRepository)

## Síntoma

El agente implementador corre pero el código que escribe **no persiste** / el
test-runtime no encuentra nada que testear, o `/workspace` aparece **vacío** dentro
del contenedor aunque el worker resolvió un `workspace_host_path` correcto. O, antes
de eso, la provisión del worktree falla con:

```
fatal: cannot use bare repository '.../repos/<x>.git' (safe.bareRepository is 'explicit')
```

o, en un proyecto sin remoto:

```
fatal: not a valid object name: 'HEAD'   # al hacer `git worktree add ... HEAD`
```

## Causa raíz

Tres trampas distintas alrededor del worktree de ejecución (prod-18):

1. **Bind DooD resuelto por el daemon, no por el worker.** El worker corre dentro de
   un contenedor y lanza el `agent-runtime` hablando con el daemon Docker del host
   (Docker-out-of-Docker). En `docker run -v source:dest`, el `source` lo resuelve el
   **daemon en el FS del host**, no el rootfs del worker. Si `workspace_host_path` es
   una ruta que solo existe dentro del worker, el daemon monta un directorio host
   inexistente (lo crea vacío) → `/workspace` vacío. Por eso el path debe ser un
   **path absoluto real del host** bajo `data_root`, y el stack debe montar
   `{data_root}:{data_root}` (mismo path en worker y host).

2. **Volumen nombrado en vez de bind por-path.** Si un override usa un _volumen
   nombrado_ Docker para `/data` (como tenía el overlay de manuales antes de su fix)
   en vez del bind `{data_root}:{data_root}`, el daemon monta otro volumen y el
   worktree (que el worker escribió en su propio FS) **no se ve** → `/workspace`
   vacío. Ver también [docker-compose-volumes-merge.md] (`volumes:` se mergea, no se
   reemplaza; usar `!reset` para no arrastrar/perder binds).

3. **`safe.bareRepository=explicit`.** git moderno puede rechazar operar sobre un bare
   repo (`git -C <repo>.git branch …`) salvo que se permita explícitamente. La
   plataforma opera sobre **sus propios** bare repos (bajo `data_root`), así que es
   seguro permitirlo.

Y un caso aparte: un **bare local recién creado** (`git init --bare`, proyecto SIN
remoto/clone) está **vacío** — HEAD es unborn — y `git worktree add … HEAD` falla
("not a valid object name: 'HEAD'") porque no hay ningún commit del que ramificar.

## Fix

- **Path host idéntico (DooD):** `workspace_host_path` = ruta absoluta bajo `data_root`
  (`BareRepoLayout` → `{data_root}/projects/{tenant}/{project}/worktrees/{task_id}`), y
  el compose monta `{data_root}:{data_root}` en `workers` y `agent-runtime` (nunca un
  volumen nombrado para `/data`). El instalador ya lo genera así.
- **safe.bareRepository:** `workers.git_repos._run_git` inyecta
  `GIT_CONFIG_PARAMETERS='safe.bareRepository=all'` (prod-18), así las operaciones
  sobre los bare de la plataforma funcionan sea cual sea el default del host.
- **Bare vacío:** `BareRepoManager.seed_initial_commit_if_empty` siembra un commit raíz
  vacío (con identidad de plataforma) cuando el bare no tiene HEAD, para que el worktree
  pueda ramificar. No-op si el repo ya tiene commits (p.ej. clonado de un remoto, ADR 0072).

El perfil de seguridad del sandbox **no cambia** por montar el worktree: el bind es RW
sobre el worktree del proyecto, pero `build_hardened_run_kwargs` mantiene cap-drop ALL,
no-new-privileges, rootfs read-only, uid no-root, red restringida y el tripwire de socket
Docker (`assert_no_docker_socket`).
