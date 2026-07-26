---
title: "Recetas de build que muerden: base de `workers`, `WITH_CLAUDE`, contexto del agent-runtime y admin-panel desde PowerShell"
area: docker, build, windows
encountered: 2026-07-01 … 2026-07-24
stack: docker buildx, Next.js 14, Git Bash / PowerShell en Windows
---

## Síntoma

Cuatro fallos distintos con la misma forma: la imagen **construye bien** y falla
al arrancar o en caliente.

1. `workers` arranca y muere con `ImportError: cannot import name
'distil_execution_result'`.
2. El córtex responde 503 «no hay proveedor» aunque `claude_sdk` esté
   configurado.
3. Un cambio en el grafo del agente no surte efecto por más que se reconstruya
   `agent-runtime`.
4. El admin-panel pide la API a `C:/Program Files/Git/api` y todo da 404.

## Causa raíz

1. **La base `:ci` está desfasada.** `workers` se construye SOBRE la imagen de
   api-server, y `agentic-platform/api-server:ci` quedó atrás (07-01).
2. **`claude-agent-sdk` es una dependencia opcional** (extra `claude`, ADR 0064).
   Sin el build-arg no está en la imagen y el proveedor degrada.
3. **El grafo del agente vive en la imagen BASE**, no en la de runtime:
   reconstruir solo `agent-runtime` no recoge el cambio. Y su contexto de build
   es la **raíz del repo**, no su carpeta.
4. **Git Bash mangla las rutas de los build-args en Windows**: convierte
   `--build-arg NEXT_PUBLIC_API_URL=/api` en una ruta absoluta de Windows. Es
   MSYS path conversion, y ocurre en `docker build` desde Git Bash.

## Fix

```bash
# 1. workers sobre la base BUENA (contexto = raíz)
docker build -t agentic-platform/workers:ci \
  --build-arg BASE_IMAGE=agentic-platform/api-server:manuals \
  -f apps/workers/Dockerfile .

# 2 + 3. api-server y agent-runtime con el SDK, contexto = raíz
docker build -t agentic-platform/api-server:manuals \
  --build-arg WITH_CLAUDE=1 -f apps/api-server/Dockerfile .
docker build -t agentic-platform/agent-runtime:v1 \
  --build-arg WITH_CLAUDE=1 -f docker/agent-runtimes/agent-runtime/Dockerfile .
```

```powershell
# 4. admin-panel: contexto = su carpeta y DESDE POWERSHELL, no Git Bash
docker build -t agentic-platform/admin-panel:manuals `
  --build-arg NEXT_PUBLIC_API_URL=/api apps/admin-panel
```

Ver también `admin-panel-build-context-is-app-dir.md`,
`api-server-manuals-needs-with-claude.md` y
`docker-msys-build-arg-leading-slash-windows.md`, que cubren piezas de esto por
separado; esta nota es la receta completa en un sitio.

## Cómo verificar el fix

- `docker exec agentic-platform-workers-1 python -c "import workers.celery_app"` sin error.
- `docker exec agentic-platform-api-server-1 python -c "import claude_agent_sdk"` sin error.
- El bundle del admin-panel contiene `/api` y no una ruta de Windows:
  `docker exec agentic-platform-admin-panel-1 grep -rl '"/api"' .next | head -1`.
