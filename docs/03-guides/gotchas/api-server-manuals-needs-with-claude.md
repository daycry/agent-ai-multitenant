---
title: api-server:manuals sin WITH_CLAUDE=1 rompe asistente, córtex, voz y generate-corrections
area: docker build / deploy
encountered: 2026-07-09
stack: docker build, claude_agent_sdk, ADR 0064/0073
---

## Síntoma

Tras un rebuild aparentemente sano del api-server, cualquier camino que use el
proveedor `claude_sdk` DENTRO del api-server devuelve 500/503:

- `/assistant/chat` y `POST /plans/{id}/generate-corrections` → 500
  `ModuleNotFoundError: No module named 'claude_agent_sdk'`.
- Los WS de voz (`/ws/assistant/voice`, `/ws/owner/cortex/voice`) mueren con
  un **1006 mudo** antes del frame `ready` (el 503 de «no hay proveedor» tenía
  un detail >123 bytes y el close fallaba en silencio — ya arreglado con el
  frame `error` previo, pero el 503 de fondo sigue siendo este gotcha).

Los agentes de ejecución NO se ven afectados (el SDK corre en la imagen
agent-runtime, no aquí) — por eso el fallo pasa desapercibido en los runs.

## Causa raíz

El Dockerfile del api-server tiene `ARG WITH_CLAUDE=0`: el Claude Agent SDK
(+ CLI de node) solo se instala con `--build-arg WITH_CLAUDE=1`. El runner de
manuales (`scripts/dev/generate-manuals.ps1`) SÍ lo pasa, pero un rebuild a
mano que lo omita **regresiona la imagen** `agentic-platform/api-server:manuals`
sin que ningún build/arranque falle: el hueco solo aflora al primer uso de
`claude_sdk` en el proceso del api-server (asistente, córtex, voz, corrections).

## Fix

Construir SIEMPRE la base del stack manuales con el SDK:

```bash
docker build -f apps/api-server/Dockerfile --build-arg WITH_CLAUDE=1 \
  -t agentic-platform/api-server:manuals .
# y las hijas sobre esa base (ver orchestrator-workers-base-image-arg.md):
docker build -f apps/workers/Dockerfile --build-arg BASE_IMAGE=agentic-platform/api-server:manuals -t agentic-platform/workers:ci .
docker build -f apps/orchestrator/Dockerfile --build-arg BASE_IMAGE=agentic-platform/api-server:manuals -t agentic-platform/orchestrator:manuals .
```

## Cómo verificar

```bash
docker exec agentic-platform-api-server-1 python -c "import claude_agent_sdk; print('ok')"
```
