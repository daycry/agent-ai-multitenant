---
title: Orchestrator/workers construidos sin BASE_IMAGE heredan un api_server viejo
area: docker build / deploy
encountered: 2026-07-08
stack: docker build, imágenes en capas (workers y orchestrator FROM api-server)
---

## Síntoma

Tras desplegar imágenes recién construidas, el orchestrator entra en crashloop:

```
ImportError: cannot import name 'transition_to_blocked' from 'api_server.plan_progress'
(/opt/venv/lib/python3.12/site-packages/api_server/plan_progress.py)
```

El símbolo SÍ existe en el repo (commit desplegado). `docker ps` muestra
`Restarting (1)` solo para el orchestrator; el resto de servicios sanos.

## Causa raíz

Las imágenes de **workers** Y **orchestrator** se construyen POR CAPAS sobre la
imagen del api-server (`FROM ${BASE_IMAGE}`) para no recompilar deps nativas.
Ambos Dockerfiles tienen el default:

```dockerfile
ARG BASE_IMAGE=agentic-platform/api-server:ci
```

Si construyes sin `--build-arg BASE_IMAGE=...`, la capa base es la última
`api-server:ci` LOCAL — que puede tener semanas — y tu código nuevo de
orchestrator/workers corre contra un `api_server` viejo dentro de la imagen.
El fallo aparece solo en el arranque del servicio (import), nunca en el build.

## Fix

Construir SIEMPRE la base primero y pasarla explícita a las dos imágenes hijas
(en dev el tag del stack `manuals` es `agentic-platform/api-server:manuals`):

```bash
docker build -f apps/api-server/Dockerfile -t agentic-platform/api-server:manuals .
docker build -f apps/workers/Dockerfile \
  --build-arg BASE_IMAGE=agentic-platform/api-server:manuals \
  -t agentic-platform/workers:ci .
docker build -f apps/orchestrator/Dockerfile \
  --build-arg BASE_IMAGE=agentic-platform/api-server:manuals \
  -t agentic-platform/orchestrator:manuals .
```

## Cómo verificar el fix

```bash
docker run --rm --entrypoint python agentic-platform/orchestrator:manuals \
  -c "from api_server.plan_progress import transition_to_blocked; print('ok')"
```

Y tras recrear el servicio, `docker ps` debe mostrarlo `healthy` (no
`Restarting`).
