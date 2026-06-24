---
title: `docker build --build-arg X=/valor` desde Git Bash (Windows) mangla `/valor` a una ruta Windows (MSYS)
area: windows
encountered: 2026-06-20
stack: Git Bash / MSYS2 en Windows, Docker Desktop, Next.js 14.2 (NEXT_PUBLIC_API_URL)
---

## Síntoma

Tras reconstruir la imagen `admin-panel` **desde Git Bash** en Windows con
`--build-arg NEXT_PUBLIC_API_URL=/api`, el admin-panel deja de hablar con la API:

- En la consola del navegador:
  `Not allowed to load local resource: file:///C:/Program%20Files/Git/api/auth/sso/providers`
- Todas las llamadas a la API fallan con `TypeError: Failed to fetch` (p.ej.
  "Could not load policies", y en `/login` **no cargan los SSO providers**).
- Pero `curl http://localhost:8080/api/auth/sso/providers` (vía Caddy) responde
  **200** y los logs del api-server no muestran ningún error: el problema es
  100% de cliente.

El valor horneado en el bundle no es `/api` sino `C:/Program Files/Git/api`:

```bash
docker exec <admin-panel> sh -c 'grep -rhoE "C:/Program Files/Git/api" .next/'
# C:/Program Files/Git/api   ← mangleado
```

## Causa raíz

**MSYS (la capa POSIX de Git Bash) convierte automáticamente los argumentos que
parecen rutas absolutas Unix.** Un argumento con barra inicial como `/api` se
reescribe a la ruta Windows del root de la instalación de Git **antes** de que
`docker` lo reciba: `/api` → `C:/Program Files/Git/api`. Así que el `--build-arg`
que llega a Docker es `NEXT_PUBLIC_API_URL=C:/Program Files/Git/api`.

Como `NEXT_PUBLIC_*` se **hornea en build** (ver
[nextjs-public-env-build-time.md](./nextjs-public-env-build-time.md)),
`lib/api.ts` queda con `API_URL = "C:/Program Files/Git/api"`. En el navegador,
`fetch("C:/Program Files/Git/api/...")` se resuelve como una URL `file://` local
y el navegador la **bloquea** (una página http no puede cargar recursos `file://`).

Es un artefacto **exclusivo de Windows + Git Bash**. No tiene nada que ver con el
Dockerfile, el despliegue ni el código: solo con la shell desde la que se lanzó
el build.

## Fix

Construir desde una shell que **no** haga conversión MSYS:

- **PowerShell** (lo que hace `scripts/dev/generate-manuals.ps1`): el argumento
  `/api` llega intacto.
  ```powershell
  docker build -t agentic-platform/admin-panel:manuals `
    --build-arg NEXT_PUBLIC_API_URL=/api -f apps/admin-panel/Dockerfile apps/admin-panel
  ```
- Si **tienes** que lanzarlo desde Git Bash, desactiva la conversión para ese
  comando con `MSYS_NO_PATHCONV=1` (o usa doble barra `//api`):
  ```bash
  MSYS_NO_PATHCONV=1 docker build --build-arg NEXT_PUBLIC_API_URL=/api ...
  ```

**Linux / producción NO están afectados.** El pipeline de release
(`.github/workflows/release-images.yml`) corre en `ubuntu-latest` y pasa
`NEXT_PUBLIC_API_URL=/api` por el campo YAML `build-args:` de
`docker/build-push-action` (sin shell de por medio), y el instalador
(`installer_backend`) solo **pull-ea** la imagen pre-construida del registry — no
la compila en el servidor. La trampa solo aparece al reconstruir a mano en un
Windows con Git Bash.

> Misma familia que otros args con barra inicial (`docker run -v /data:...`,
> `-e PATH=/x`): cualquier `/loquesea` en la línea de comandos de Git Bash es
> candidato a mangleo.

## Cómo verificar el fix

```bash
# 1) El bundle ya NO contiene la ruta mangleada (salida vacía = bien):
docker exec <admin-panel> sh -c 'grep -rhoE "C:/Program Files/Git/api" .next/'

# 2) El endpoint público responde y la página de login carga los SSO providers:
curl -s http://localhost:8080/api/auth/sso/providers   # → 200 + JSON
```

En el navegador, la pestaña Network debe mostrar las llamadas a `/api/...`
(mismo origen), no a `file:///C:/...`.
