---
title: "Recetas de build que muerden: bases `:ci` desfasadas, `WITH_CLAUDE`, el tag del agent-runtime SIN prefijo y admin-panel desde PowerShell"
area: docker, build, windows
encountered: 2026-07-01 … 2026-07-28
stack: docker buildx, Next.js 14, Git Bash / PowerShell en Windows
---

## Síntoma

Seis fallos distintos con la misma forma: la imagen **construye bien** y falla
al arrancar o en caliente.

1. `workers` arranca y muere con `ImportError: cannot import name
'distil_execution_result'`.
2. El córtex responde 503 «no hay proveedor» aunque `claude_sdk` esté
   configurado.
3. Un cambio en el grafo del agente no surte efecto por más que se reconstruya
   `agent-runtime`.
4. El admin-panel pide la API a `C:/Program Files/Git/api` y todo da 404.
5. **`orchestrator` o `notification-dispatcher` arrancan con código viejo** aunque
   se acaben de reconstruir, y fallan con un `ImportError` de algo que sí existe
   en el repo (2026-07-28).
6. **Un cambio en el agent-runtime no llega a los runs** aunque
   `agentic-platform/agent-runtime:v1` se acabe de reconstruir y verificar
   (2026-07-28).

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
5. **No es solo `workers`: `orchestrator` y `notification-dispatcher` también
   declaran `ARG BASE_IMAGE=agentic-platform/api-server:ci`.** Son **tres**
   imágenes derivadas, no una. Esta nota documentó la trampa solo para `workers`
   durante casi un mes, así que las otras dos se reconstruían «bien» heredando
   una base de semanas atrás.
6. **El worker lanza el tag SIN prefijo.** `WORKERS_AGENT_RUNTIME_IMAGE` vale
   `agent-runtime:v1`, no `agentic-platform/agent-runtime:v1`. Son dos tags
   distintos que conviven en el daemon, y el comando de esta misma receta
   construía **el que los runs no usan**. Se descubrió al ver que el prefijado
   tenía 9 días y el sin prefijo 3 — la fecha del último despliegue.

## Fix

```bash
# 1. La base, PRIMERO: de ella cuelgan las otras tres (contexto = raíz)
docker build -t agentic-platform/api-server:manuals \
  --build-arg WITH_CLAUDE=1 -f apps/api-server/Dockerfile .

# 2 + 5. LAS TRES derivadas, con BASE_IMAGE explícito. Omitirlo hereda `:ci`.
docker build -t agentic-platform/workers:ci \
  --build-arg BASE_IMAGE=agentic-platform/api-server:manuals \
  -f apps/workers/Dockerfile .
docker build -t agentic-platform/orchestrator:manuals \
  --build-arg BASE_IMAGE=agentic-platform/api-server:manuals \
  -f apps/orchestrator/Dockerfile .
docker build -t agentic-platform/notification-dispatcher:manuals \
  --build-arg BASE_IMAGE=agentic-platform/api-server:manuals \
  -f apps/notification-dispatcher/Dockerfile .

# 3 + 6. agent-runtime con el SDK, contexto = raíz, y LOS DOS TAGS.
# El segundo `-t` no es redundancia: es el que el worker lanza de verdad.
docker build -t agentic-platform/agent-runtime:v1 -t agent-runtime:v1 \
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
- **Las tres derivadas cuelgan de la base nueva**: importar desde ellas un símbolo
  que solo exista en la api-server recién construida. Si `orchestrator` o
  `notification-dispatcher` fallan y `workers` no, es el `BASE_IMAGE` olvidado (5).
- **El tag que los runs usan es el correcto** — las fechas tienen que coincidir:

  ```bash
  docker exec agentic-platform-workers-1 sh -c 'echo $WORKERS_AGENT_RUNTIME_IMAGE'
  docker images --format "{{.Repository}}:{{.Tag}} {{.ID}} {{.CreatedSince}}" | grep agent-runtime
  ```

  Si `agent-runtime:v1` (sin prefijo) es más viejo que el prefijado, los runs
  siguen con la imagen anterior.

## La comprobación que de verdad las caza todas

Las seis tienen la misma raíz: **el tag que reconstruyes no es el que corre**. Y
tras un `up -d` la comprobación es distinta de la que se hace sobre la imagen,
porque un contenedor ya arrancado guarda el **ID** de la imagen, no el tag:

```bash
docker inspect agentic-platform-workers-1 --format '{{.Image}}'   # ID en uso
docker images --no-trunc --format "{{.Repository}}:{{.Tag}} {{.ID}}" | grep workers
```

Si el ID del contenedor no está entre los de los tags recién construidos, el
`up -d` no recreó ese servicio. Etiquetar las imágenes vivas
(`docker tag <img> <img>:predeploy-<fecha>`) **antes** de sobrescribir los tags
convierte el rollback en un `docker tag` de vuelta — cuesta un segundo y evita
depender de acordarse del ID.
