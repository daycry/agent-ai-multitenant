---
title: Healthchecks con wget/--spider que dejan contenedores "unhealthy" para siempre
area: docker
encountered: 2026-06-18
stack: docker compose · python:3.12-slim · docling-serve · prod-01
---

## Síntoma

Un contenedor arranca bien y la app responde, pero `docker ps` lo marca
`(unhealthy)` indefinidamente. Como otros servicios dependen de él con
`depends_on: { condition: service_healthy }`, **el stack entero no llega a
levantar**.

Dos variantes vistas:

- **api-server / orchestrator** (`python:3.12-slim`): el healthcheck usaba
  `wget -q --spider http://localhost:8000/healthz`, pero la imagen slim **no
  trae wget ni curl** → el comando falla siempre.
- **docling-serve**: el healthcheck usaba `wget -q --spider .../health`; la
  imagen SÍ tiene wget, pero `/health` **rechaza el HEAD** que envía `--spider`
  (sirve 200 solo a GET).

## Causa raíz

El healthcheck depende de una herramienta o un método HTTP que la imagen no
soporta. `python:3.12-slim` es deliberadamente mínima (sin wget/curl);
`--spider` hace una petición HEAD que no todos los endpoints aceptan.

(Este bug estaba enmascarado porque la imagen del api-server **ni siquiera
arrancaba** — ver [app-image-missing-runtime-deps](./app-image-missing-runtime-deps.md);
al arreglar el arranque emergió el healthcheck roto.)

## Fix

- Apps Python (sin wget): healthcheck con la **stdlib de python**, que sí está
  en la imagen. En `compose_generator.py`, helper `_http_healthcheck(url)`:

  ```python
  "test": ["CMD", "python", "-c",
           "import urllib.request,sys; "
           "sys.exit(0 if urllib.request.urlopen('http://localhost:8000/healthz',timeout=5).status==200 else 1)"]
  ```

- docling: usar **GET** en vez de `--spider` (HEAD):

  ```yaml
  test: ["CMD-SHELL", "wget -q -O /dev/null http://localhost:5001/health || exit 1"]
  ```

Regla general: el comando del healthcheck debe usar SOLO binarios presentes en
esa imagen concreta, y un método HTTP que el endpoint acepte.

## Cómo verificar el fix

```powershell
docker compose ... up -d api-server docling-serve
docker inspect -f '{{.State.Health.Status}}' agentic-platform-api-server-1     # healthy
docker inspect -f '{{.State.Health.Status}}' agentic-platform-docling-serve-1  # healthy
```
