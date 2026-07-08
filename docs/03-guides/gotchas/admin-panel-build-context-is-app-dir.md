---
title: El admin-panel se construye con contexto apps/admin-panel, no la raíz
area: docker build / deploy
encountered: 2026-07-08
stack: docker build, Next.js standalone, .dockerignore
---

## Síntoma

Reconstruyes `agentic-platform/admin-panel:manuals`, el build "termina bien",
recreas el contenedor… y una página nueva NO está en el bundle desplegado
(`ls .next/server/app/...` no la lista; el navegador 404/crashea). Puede pasar
en silencio: BuildKit resuelve capas desde caché y el tag queda apuntando a un
build viejo.

## Causa raíz

A diferencia de api-server/workers/orchestrator/agent-runtime (contexto = raíz
del repo), el Dockerfile del admin-panel espera el **contexto en
`apps/admin-panel`**: hace `COPY package.json package-lock.json ./` y
`COPY . .`, y su `.dockerignore` (que excluye `node_modules`/`.next` del host —
crítico para no arrastrar caché webpack rancia) vive en `apps/admin-panel/` y
solo aplica si ESE directorio es el contexto.

Construir con `docker build -f apps/admin-panel/Dockerfile … .` desde la raíz
(el patrón válido para las otras imágenes) usa el contexto equivocado.

## Fix

```powershell
Set-Location C:\laragon\python\agent-ai-multitenant
docker build -f apps\admin-panel\Dockerfile -t agentic-platform/admin-panel:manuals apps\admin-panel
```

## Cómo verificar el fix

En la salida del build, la tabla «Route (app)» de Next debe listar la ruta
nueva; y tras recrear el servicio:

```bash
docker exec agentic-platform-admin-panel-1 sh -c "ls .next/server/app/admin/<ruta>/"
```
