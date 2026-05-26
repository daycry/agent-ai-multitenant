---
title: docling-mcp no publica imagen en GHCR
area: docker-compose, plan-04
encountered: 2026-05-26
stack: docker-compose v2.x, GHCR
---

## Síntoma

```
✘ Image ghcr.io/docling-project/docling-mcp:latest  Error  error from registry: denied
Error response from daemon: error from registry: denied
docker compose up failed
```

El stack se cuelga al arrancar `docling-mcp`. El error no es de credenciales
ni de DNS — GHCR responde literalmente `denied`.

## Causa raíz

El proyecto upstream
[`docling-project/docling-mcp`](https://github.com/docling-project/docling-mcp)
**no publica imagen Docker en ningún registry**. Se distribuye como
paquete Python ejecutable con uvx:

```bash
uvx --from docling-mcp docling-mcp-server --transport stdio
```

GHCR devuelve `denied` indistintamente para paquetes privados y para
paquetes inexistentes — es la práctica estándar (no leakea qué
paquetes existen). Así que el error parece de auth pero en realidad
es "no hay tal imagen".

`docling-serve` sí publica
(`ghcr.io/docling-project/docling-serve:latest`, pública); ese es el
único contenedor Docling que el dev compose levanta.

## Fix

El servicio `docling-mcp` queda **comentado** en
`docker/docker-compose.yml` con la explicación inline. La variable
`DOCLING_MCP_PORT` queda comentada en `docker/.env.example`. El
api-server sigue exponiendo el `docling_mcp_url` setting + el
cliente `HttpDoclingMCPClient`, pero ningún flujo dev / demo lo
ejercita:

- Los tests de integración del Plan 04 usan `StaticDoclingMCPClient`
  (fake).
- Los demos `demo_human_04_5_02.py` leen los chunks directamente de
  la BD por `document-convert` y no invocan a docling-mcp.
- El flujo "re-parse-from-MinIO" del `HttpDoclingMCPClient` cae con
  Plan 07 (chat-file-upload), no antes.

## Cómo reactivarlo en el futuro

Tres caminos, por orden de preferencia:

1. **Esperar a que upstream publique imagen oficial.** Watch
   `https://github.com/docling-project/docling-mcp/pkgs/container/`.
   Cuando aparezca, descomenta el bloque en `docker-compose.yml` y la
   variable en `.env.example`.

2. **Dockerfile artesano**: clonar `docling-mcp` en `docker/docling-mcp/`,
   instalar con `uv` + un wrapper que exponga el transport sobre HTTP
   (el proyecto soporta `stdio` y `streamable-http`; lo segundo es lo
   que necesita la compose). Mantenerlo es trabajo extra cada vez que
   suban versión.

3. **Pin a una versión específica** de `docling-mcp` vía
   `uvx --from docling-mcp@<versión>` dentro de un contenedor
   minimal Python 3.12. Misma idea que (2), menos abstracción.

## Referencias

- Página del paquete (404 al cierre del Plan 04.5):
  `https://github.com/docling-project/docling-mcp/pkgs/container/docling-mcp`
- README de docling-mcp: la sección de instalación recomienda `uvx`,
  no menciona Docker.
- Código del cliente: `apps/api-server/src/api_server/ingestion/docling_mcp.py`
  — `HttpDoclingMCPClient.convert()` espera la misma shape multipart
  que docling-serve, así que apuntar `docling_mcp_url` a docling-serve
  **NO** funciona (la ruta `/tools/call/convert` no existe allí).
