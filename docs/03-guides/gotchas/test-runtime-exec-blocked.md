---
title: exec_run en test-runtimes falla (409 "not running" / 403 Forbidden)
area: docker
encountered: 2026-06-29
stack: docker · docker-socket-proxy · TestRuntimeRunner · ADR 0093
---

## Síntoma

Cualquier comando que el worker intenta correr DENTRO de un runtime-template
(`TestRuntimeRunner._exec` → `container.exec_run`) falla. Afecta a los checks
post-hoc de tests Y al puente `stack_exec` (ADR 0093). Dos errores encadenados:

```
docker.errors.APIError: 409 Client Error ... /containers/<id>/exec:
  Conflict ("container <id> is not running")
# y, tras arreglar el 409:
docker.errors.APIError: 403 Client Error ... /exec/<id>/start:
  Forbidden ("Request forbidden by administrative rules.")
```

## Causa raíz

Dos bugs independientes que se tapaban entre sí:

1. **Doble `sleep infinity` → el contenedor sale al instante (409).** Las imágenes
   runtime-template declaran `ENTRYPOINT ["sleep","infinity"]` para quedarse
   arriba. Pero `_build_test_kwargs` (en `apps/workers/.../test_runtime.py`)
   pasaba ADEMÁS `command ["sleep","infinity"]`. Docker compone
   `ENTRYPOINT + CMD` → ejecuta `sleep infinity sleep infinity` →
   `sleep: invalid time interval 'sleep'` → el contenedor muere nada más nacer,
   y el `exec_run` posterior pega contra un contenedor parado (409).

2. **`EXEC=0` en el docker-socket-proxy → exec prohibido (403).** El proxy de
   mínimo-privilegio (`tecnativa/docker-socket-proxy`, ADR 0060) gobierna qué
   endpoints de la API Docker pasan. Con `EXEC=0` bloquea
   `POST /exec/{id}/start` con 403. El worker SÍ necesita exec: corre comandos
   dentro de los runtime-templates que él mismo lanza.

## Fix

1. Keep-alive por `entrypoint`, sin `command` que se anexe al ENTRYPOINT de la
   imagen (`test_runtime.py`):

   ```python
   # antes:  "command": ["sleep", "infinity"],
   # ahora:
   "entrypoint": ["sleep", "infinity"],   # corre EXACTAMENTE `sleep infinity`
   ```

2. Activar exec en el proxy (`docker/docker-compose.manuals.yml`):

   ```yaml
   docker-socket-proxy:
     environment:
       EXEC:
         "1" # el worker exec-ea en los runtimes que lanza (no es regresión:
         # el proxy vive en la red dedicada agentic-docker solo-workers
         # y el agent-runtime NUNCA toca el socket — principio 2)
   ```

   Recrear SOLO el proxy: `docker compose ... up -d --force-recreate --no-deps docker-socket-proxy`.

## Cómo verificar el fix

```bash
# A) el contenedor NO debe morir con el sleep doblado:
docker run --rm --name t-x agent-runtime-php-phpunit:v1 sleep infinity   # -> "invalid time interval" (mal)
docker run -d --rm --entrypoint sleep agent-runtime-php-phpunit:v1 infinity  # -> queda Up (bien)

# B) el puente entero (worker -> runtime -> exec -> rc+logs) sobre un worktree real:
docker exec agentic-platform-workers-1 python -c "
from workers.tasks import run_stack_command
res = run_stack_command.apply(args=[{'tenant_id':'<org>','task_id':'<task>','command':'php -v','timeout_s':120}]).get()
print(res['exit_code'], res['logs'][:80])"   # -> 0  PHP 8.3...
```

## Relacionado

- `ADR 0093` — `stack_exec` (puente worker→stack), donde afloraron ambos bugs.
- [agent-runtime-egress-blocks-in-stack-llm](agent-runtime-egress-blocks-in-stack-llm.md)
  — el runtime-template corre en red `internal` sin egress: `composer install`
  con dependencias remotas no resuelve packagist (require vacío sí completa).
