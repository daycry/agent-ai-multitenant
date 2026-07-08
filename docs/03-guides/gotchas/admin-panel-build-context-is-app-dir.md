---
title: El admin-panel se construye con contexto apps/admin-panel Y con NEXT_PUBLIC_API_URL=/api
area: docker build / deploy
encountered: 2026-07-08
stack: docker build, Next.js standalone, .dockerignore, NEXT_PUBLIC_*
---

## Síntomas

1. Reconstruyes `agentic-platform/admin-panel:manuals`, el build "termina
   bien", recreas el contenedor… y una página nueva NO está en el bundle
   desplegado (`ls .next/server/app/...` no la lista; el navegador
   404/crashea). Puede pasar en silencio: BuildKit resuelve capas desde caché
   y el tag queda apuntando a un build viejo.
2. **El login muestra «Could not reach the server.» y los botones OAuth/SSO no
   cargan** — TODAS las llamadas del panel al api fallan desde el navegador,
   aunque `curl http://localhost:8080/api/...` funcione desde el host. Puede
   quedar LATENTE: mientras haya sesiones vivas en Redis nadie hace login, y
   el síntoma aparece días después (p. ej. tras un reinicio de Docker que
   vacía Redis).

## Causas raíz

- **Contexto**: a diferencia de api-server/workers/orchestrator/agent-runtime
  (contexto = raíz del repo), el Dockerfile del admin-panel espera el
  **contexto en `apps/admin-panel`** (su `.dockerignore` — que excluye
  `node_modules`/`.next` del host — vive ahí y solo aplica si ESE directorio
  es el contexto).
- **Build-arg**: Next hornea `NEXT_PUBLIC_*` en **build-time**. `lib/api.ts`
  hace `process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001"`; el ARG
  del Dockerfile tiene default vacío. Sin
  `--build-arg NEXT_PUBLIC_API_URL=/api`, el bundle queda apuntando a
  `localhost:8001` (donde no escucha nada — el api solo existe detrás de
  caddy:8080 como `/api`) → síntoma 2. Así lo documenta el propio
  `docker-compose.manuals.yml` («Image must be built with that build-arg»).

- **Path-mangling de Git Bash (MSYS)**: si el build se lanza desde Git Bash,
  MSYS convierte el valor `/api` del build-arg en
  `C:/Program Files/Git/api` → el navegador acaba haciendo
  `fetch("file:///C:/Program Files/Git/api/auth/login")` («URL scheme "file"
  is not supported» en la consola) → MISMO síntoma 2. Es el mismo mangling
  que ya muerde en los `-v //ruta` de docker run.

## Fix (el comando COMPLETO, SIEMPRE desde PowerShell)

```powershell
Set-Location C:\laragon\python\agent-ai-multitenant
docker build -f apps\admin-panel\Dockerfile --build-arg NEXT_PUBLIC_API_URL=/api -t agentic-platform/admin-panel:manuals apps\admin-panel
```

(Desde Git Bash solo con `MSYS_NO_PATHCONV=1` delante; PowerShell es lo seguro.)

## Cómo verificar el fix

En la salida del build, la tabla «Route (app)» de Next debe listar la ruta
nueva; y tras recrear el servicio:

```bash
docker exec agentic-platform-admin-panel-1 sh -c "ls .next/server/app/admin/<ruta>/"
# La base URL horneada: el chunk de login debe llevar "/api" y NADA de
# localhost:8001 ni "Program Files" (mangling de MSYS).
docker exec agentic-platform-admin-panel-1 sh -c "grep -l 'localhost:8001' .next/static/chunks/app/login/*.js; grep -rl 'Program Files' .next/static/chunks/ | head -1"
```

La verificación definitiva es funcional: la sonda headless
(`apps/admin-panel` + playwright) debe ver «Invalid email or password» con
credenciales falsas — no «Could not reach the server».
