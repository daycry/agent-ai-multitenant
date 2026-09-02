---
title: Imágenes de app-preview para la validación humana (con ejemplos por stack)
audience: operador, tech lead de proyecto
updated: 2026-07-09
docs_language: es
related: [ADR 0062, ADR 0063, ADR 0107]
---

# Imágenes de app-preview: cómo construir la imagen que el validador prueba

Cuando un plan llega a `pending_human_validation`, la plataforma levanta un
**review-runtime**: un contenedor efímero que sirve la aplicación construida
por los agentes para que el validador la pruebe desde el navegador antes de
aprobar o rechazar el plan. Esta guía explica qué imagen necesita tu proyecto,
cómo configurarla y da **ejemplos listos por stack** (PHP, Node, Python, Go,
estático).

## El modelo en una frase

> La plataforma **nunca construye** la imagen de preview (ADR 0063): tu
> proyecto (o su CI) la construye y publica; la plataforma solo la referencia
> por tag, la lanza endurecida con el **código del plan montado en
> `/workspace`**, y la expone únicamente a través del **proxy firmado** del
> api-server.

## Cómo se lanza (el contrato que tu imagen debe cumplir)

| Aspecto    | Valor                                                                                                                                                                                                          |
| ---------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Código     | El **worktree del plan** (la rama `plan/...` con el trabajo de los agentes) montado en **`/workspace`**.                                                                                                       |
| Comando    | El **CMD de la imagen** debe arrancar la app **sin argumentos externos** (auto-servible) y escuchar en `0.0.0.0`.                                                                                              |
| Puerto     | `repository_config.review_port` (default **8080**).                                                                                                                                                            |
| Usuario    | uid **1000** no-root (forzado); la imagen no puede asumir root.                                                                                                                                                |
| Filesystem | Root **read-only** + `/tmp` tmpfs; `/workspace` también **read-only** salvo las `preview.writable_paths` declaradas (tmpfs) o `preview.workspace_rw`.                                                          |
| Red        | Bridge **interno** `agentic-agents` — sin salida a internet. La app solo es alcanzable vía el proxy firmado (`/api/review/{session}/app/...`).                                                                 |
| Hardening  | `cap_drop: ALL`, `no-new-privileges`, límites de memoria/procesos, **sin socket Docker** (tripwire).                                                                                                           |
| Cabeceras  | El proxy **respeta el `Content-Type` del upstream**: si tu app sirve HTML como `application/json`, el navegador mostrará texto plano — es un defecto de la app, no del proxy (lección del plan CI4, ADR 0107). |

## Configurarlo en la UI

Ajustes del proyecto → sección **«App-preview de validación»**:

- **Imagen de preview** → `repository_config.review_image` (p. ej.
  `mi-app-preview:latest`). Precedencia: `review_image` → `main_image` →
  `worker_config.review_main_image`.
- **Puerto** → `repository_config.review_port` (default 8080).
- **Escritura en el worktree** → desde el 2026-09-02 (`task_cv_26`) el worktree
  del plan se monta en `/workspace` en **sólo lectura**: el preview corre la
  app del tenant hasta 48 h y nada de lo que la app escriba (caché, uploads,
  logs) puede tocar el código que el humano valida. Las rutas que la app
  necesite escribir se declaran en `repository_config.preview.writable_paths`
  (relativas al worktree, p. ej. `["writable", "storage/logs"]`; tienen que
  existir en el repositorio) y se montan como tmpfs encima.
  `repository_config.preview.workspace_rw: true` es el opt-in al montaje RW
  completo de antes.

Sin imagen configurada la plataforma **no lanza ningún contenedor** (nada de
placeholders muertos): la sesión y sus URLs firmadas se crean igual, y tanto
el proxy como la consola de revisión explican honestamente que el proyecto no
tiene app-preview configurada. El plan nunca se queda atascado por esto.

## Ejemplos por stack

Todos los ejemplos cumplen el contrato: CMD auto-servible, `0.0.0.0:8080`,
funcionan como uid 1000 con root read-only, y usan `/workspace` como raíz del
código del plan.

### PHP — CodeIgniter 4 (el ejemplo validado en producción)

```dockerfile
# ci4-preview.Dockerfile — base: el runtime-template PHP de la plataforma
# (ya trae php-cli + composer + extensiones y corre como uid 1000).
FROM agentic-platform/agent-runtime-php-phpunit:v1

# El código NO se copia: llega montado en /workspace en el arranque.
WORKDIR /workspace
EXPOSE 8080

# spark serve usa el router de CI4; --host 0.0.0.0 para ser alcanzable
# desde el proxy. Si vendor/ no está en el worktree, instala primero.
CMD ["sh", "-c", "cd /workspace && ([ -d vendor ] || composer install --no-interaction) && php spark serve --host 0.0.0.0 --port 8080"]
```

Verificación local: `docker build -f ci4-preview.Dockerfile -t ci4-preview:latest .`
y en Ajustes → imagen `ci4-preview:latest`, puerto `8080`.

### PHP — genérico (Laravel/Slim/vanilla con front controller)

```dockerfile
FROM php:8.3-cli-alpine
WORKDIR /workspace
EXPOSE 8080
# El servidor embebido de PHP con docroot en public/ (ajusta -t si tu
# front controller vive en otra carpeta).
CMD ["php", "-S", "0.0.0.0:8080", "-t", "/workspace/public"]
```

### Node — Express / API + web

```dockerfile
FROM node:22-alpine
ENV HOME=/tmp NODE_ENV=production
WORKDIR /workspace
EXPOSE 8080
# npm ci necesita escribir node_modules en /workspace (montado RW). Si el
# worktree ya trae node_modules (los agentes suelen instalarlo con
# stack_exec), el || true evita reinstalar.
CMD ["sh", "-c", "cd /workspace && ([ -d node_modules ] || npm ci --no-audit --no-fund) && PORT=8080 npm start"]
```

Para **Next.js** cambia el arranque: `npx next start -H 0.0.0.0 -p 8080`
(requiere `.next/` construido — pide a los agentes que el plan incluya
`npm run build`, o construye en el CMD aceptando el arranque lento).

### Python — FastAPI / Flask

```dockerfile
FROM python:3.12-slim
ENV HOME=/tmp PIP_NO_CACHE_DIR=1
WORKDIR /workspace
EXPOSE 8080
# Instala las deps del worktree en /tmp (root es read-only) y arranca uvicorn.
CMD ["sh", "-c", "pip install --target /tmp/deps -r /workspace/requirements.txt && PYTHONPATH=/tmp/deps:/workspace python -m uvicorn app.main:app --host 0.0.0.0 --port 8080"]
```

Para Flask: `python -m flask --app app run --host 0.0.0.0 --port 8080`.

### Go — servicio HTTP

```dockerfile
FROM golang:1.23-alpine
# El toolchain de Go escribe caches: root es read-only, así que a /tmp.
ENV HOME=/tmp GOCACHE=/tmp/gocache GOMODCACHE=/tmp/gomod GOFLAGS=-mod=mod
WORKDIR /workspace
EXPOSE 8080
# `go run .` compila el módulo del worktree al vuelo. Nota: sin red en el
# contenedor, `go mod download` no puede tirar deps — vendoriza (`go mod
# vendor` como tarea del plan) o usa una imagen con las deps pre-bajadas.
CMD ["sh", "-c", "cd /workspace && go run -mod=vendor ."]
```

> Alternativa recomendada para Go: que el CI del proyecto compile el binario
> estático y la imagen de preview sea `FROM scratch`/`alpine` + ese binario
> (`CMD ["/app/server", "-addr", ":8080"]`), ignorando `/workspace`. Es más
> rápida de arrancar, pero entonces valida el artefacto del CI, no el
> worktree del plan — elige según qué quieras validar.

### Sitio estático / SPA compilada

```dockerfile
FROM python:3.12-alpine
WORKDIR /workspace
EXPOSE 8080
# Cero dependencias: sirve la carpeta compilada (dist/ o build/) tal cual.
CMD ["python", "-m", "http.server", "8080", "--directory", "/workspace/dist"]
```

(Si prefieres nginx usa `nginxinc/nginx-unprivileged:alpine` — escucha en
8080 y corre no-root; recuerda que el root filesystem es read-only y su
`client_body_temp` debe ir a `/tmp`.)

## Probar la imagen antes de configurarla

```bash
# Simula el envelope de la plataforma: no-root, root RO, worktree montado.
docker run --rm -p 8080:8080 \
  --user 1000:1000 --read-only --tmpfs /tmp \
  -v /ruta/a/tu/checkout:/workspace \
  mi-app-preview:latest
# → http://localhost:8080 debe responder la app.
```

Si arranca así, arrancará en la plataforma.

## Problemas frecuentes

| Síntoma                                             | Causa y arreglo                                                                                                                                         |
| --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `review app unreachable: Name or service not known` | La sesión no tiene contenedor (imagen no configurada cuando se creó, o el contenedor salió al instante). Configura la imagen y fuerza una sesión nueva. |
| El contenedor sale con exit 0 inmediatamente        | El CMD no es auto-servible (p. ej. una imagen base sin comando de servidor). El CMD debe BLOQUEAR sirviendo la app.                                     |
| La portada HTML se ve como texto plano              | Tu app fija `Content-Type: application/json` global (filtro/middleware). Acótalo a las rutas API — el proxy pasa las cabeceras tal cual.                |
| `permission denied` al escribir                     | Root read-only + uid 1000: escribe solo en `/tmp` o `/workspace`; fija `HOME=/tmp` si la tool insiste en escribir en `$HOME`.                           |
| Timeouts instalando dependencias al arrancar        | La red del review-runtime es interna (sin internet). Deja las deps ya instaladas en el worktree (los agentes lo hacen con `stack_exec`) o en la imagen. |
| La sesión caducó                                    | `expires_at` (48 h por defecto). Fuerza una sesión nueva desde la consola de revisión o re-lanza la validación.                                         |

## Relación con el ciclo de validación

- **Aprobar** desde el detalle del plan (o la consola de revisión) cierra el
  plan (`completed`) y abre el PR del plan.
- **Rechazar con motivo** deja el plan en `rejected` y habilita la tarjeta
  **«Correcciones del rechazo»** (ADR 0107): generar tareas correctivas desde
  el motivo, aceptarlas, y el MISMO plan vuelve a `in_progress` — los agentes
  corrigen y una nueva sesión de review levanta la app otra vez.
