---
title: La imagen del api-server no arranca por dependencias ausentes (celery, paquete workers)
area: docker
encountered: 2026-06-18
stack: docker · python:3.12-slim · prod-01
---

## Síntoma

El contenedor del `api-server` (imagen propia) entra en crash-loop nada más
arrancar, antes de servir nada:

```
ModuleNotFoundError: No module named 'celery'
# y, tras añadir celery:
ModuleNotFoundError: No module named 'workers'
```

En desarrollo NO pasa: `uvicorn api_server.main:app` arranca bien con el `.venv`.

## Causa raíz

El `.venv` de desarrollo tiene instalados **todos** los paquetes del monorepo
(api-server, workers, …) de forma editable, así que oculta las dependencias que
el api-server importa pero **no declara**:

- `api_server.celery_client` hace `from celery import Celery` (el api-server es
  PRODUCTOR de tareas Celery) pero `celery` no estaba en
  `apps/api-server/pyproject.toml`.
- `api_server.routers.review` hace `from workers.review_runtime import ...` a
  nivel de módulo (y backup/git_repos/kb_sync importan de `workers` de forma
  lazy). El paquete `workers` no se instalaba en la imagen.

La imagen solo instalaba `apps/api-server` + los `packages/shared-*`, así que en
runtime faltaban `celery` y `workers` → fallo en import → crash-loop. Una imagen
de **producción** tendría exactamente el mismo fallo.

## Fix

1. Declarar `celery[redis]>=5.4,<6` en `apps/api-server/pyproject.toml` (mismo
   constraint que `apps/workers`).
2. Instalar el paquete `workers` en la imagen (`apps/api-server/Dockerfile`),
   junto a los `shared-*`:

   ```dockerfile
   COPY apps/workers/ /src/workers/
   RUN pip install /src/workers
   ```

> Nota de arquitectura: que el api-server dependa del paquete `workers` es un
> acoplamiento; extraer los helpers compartidos (`review_runtime`,
> `git_repos`, `backup_*`) a un paquete `shared-*` queda para prod-03.

## Cómo verificar el fix

```powershell
# WITH_CLAUDE=1: el asistente corre en el api-server y necesita el Claude Agent
# SDK si usa claude_sdk (ADR 0064). Sin él, /assistant/chat con Claude da 500
# (ImportError en claude_agent.py:_build_options).
docker build -t agentic-platform/api-server:manuals --build-arg WITH_CLAUDE=1 -f apps/api-server/Dockerfile .
docker run --rm agentic-platform/api-server:manuals python -c "import api_server.main; print('import OK')"
# Debe imprimir "import OK" sin ModuleNotFoundError.
docker run --rm --entrypoint python agentic-platform/api-server:manuals -c "import claude_agent_sdk; print('claude SDK OK')"
# Debe imprimir "claude SDK OK" (build WITH_CLAUDE=1).
```
