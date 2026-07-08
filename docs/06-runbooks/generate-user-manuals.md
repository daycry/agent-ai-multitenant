---
title: Generar / regenerar los manuales de usuario (PDF)
docs_language: es
audience: operador, technical writer, system admin
updated: 2026-06-18
---

# Runbook — Generar los manuales de usuario en PDF

Los manuales de usuario (`docs/manuals/pdf/`) se generan **automáticamente** con
Playwright: cada manual navega la **app real** servida por Caddy (single-origin),
captura un pantallazo de cada pantalla y lo renderiza en un PDF de marca. Se
**regeneran** cuando la UI cambia. Hay un PDF por área + `manual-completo.pdf`.

## Cuándo regenerar

- Tras cambios visibles en el panel (admin-panel) o nuevas pantallas.
- Antes de una entrega/demo al comité de dirección.
- Cuando se añade un manual nuevo en `docs/manuals/specs/`.

## Prerrequisitos

- Infra dev arriba: `scripts/dev/up.ps1` (o `docker compose -f
docker/docker-compose.yml -f docker/docker-compose.dev.yml up -d`).
- Docker + `node`/`npm`.
- El admin del tenant **Demo Manuales** (lo crea
  `apps/api-server/seeds/init_tenant.py`; puede ser `is_system_admin=true` para
  capturar también las pantallas de administración de plataforma).

## Procedimiento (un comando)

```powershell
# Desde la raíz del repo.
./scripts/dev/generate-manuals.ps1
```

El runner:

1. Construye `admin-panel:manuals` (con `NEXT_PUBLIC_API_URL=/api`, single-origin)
   y `api-server:manuals`.
2. Levanta el overlay `docker/docker-compose.manuals.yml` (api-server +
   admin-panel + **Caddy**) sobre la infra dev.
3. Espera a Caddy en `http://localhost:8080`.
4. Siembra datos demo reales (proyecto **Hello World PHP** + plan + tareas) —
   idempotente (`docs/manuals/lib/seed-demo-data.mjs`).
5. Captura `docker compose ps` en `docs/manuals/assets/dockers.json` (lo usa el
   manual 11 · Arquitectura y despliegue).
6. Ejecuta los specs y combina todo en `manual-completo.pdf`.

Iteración rápida (sin reconstruir imágenes ni re-sembrar, un manual concreto):

```powershell
./scripts/dev/generate-manuals.ps1 -Grep "04" -SkipBuild -SkipSeed
```

## Verificación

- `docs/manuals/pdf/` contiene los 14 PDFs (00-13) + `manual-completo.pdf`.
- Abrir `manual-completo.pdf`: portada de marca, índice, y cada manual con sus
  pantallazos enmarcados (no pantallas de login: las capturas son de la app
  autenticada).

## Problemas frecuentes

- **Pantallazos de login en vez del contenido**: la app no estaba lista o el
  login falló. El runner usa la UI real; revisa que Caddy responde en `:8080` y
  que las credenciales (`-Email/-Password/-Tenant`) son válidas.
- **Contenedores `unhealthy` / stack no levanta**: ver los gotchas
  `docker-cap-drop-all-breaks-official-images.md`,
  `app-image-missing-runtime-deps.md` y `compose-healthcheck-tooling-missing.md`.
- **Rate limit de login** durante la captura: el overlay fija
  `API_SERVER_LOGIN_RATE_LIMIT_COUNT=1000`.

## Añadir un manual

Crea `docs/manuals/specs/NN-mi-area.manual.ts` siguiendo el patrón declarativo
(`ManualDef` con `steps`). Ver `docs/manuals/README.md`.
