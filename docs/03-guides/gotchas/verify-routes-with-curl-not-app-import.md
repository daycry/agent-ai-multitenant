---
title: "Importar `api_server.main:app` dentro del contenedor da una app PARCIAL — no sirve para verificar que una ruta existe"
area: api-server, verificación
encountered: 2026-07-24
stack: FastAPI, docker exec
---

## Síntoma

Tras desplegar, se comprueba que las rutas nuevas están:

```bash
docker exec agentic-platform-api-server-1 \
  python -c "from api_server.main import app; print(len(app.routes))"
# 77
```

Faltan rutas que sí están en el código (`/projects`, `/plans`, las nuevas de
preview…). Parece que el despliegue no cogió el cambio.

## Causa raíz

Ese `python -c` construye la app en un proceso **distinto** del que sirve, sin el
mismo entorno ni el mismo orden de importación. Routers que se montan de forma
condicional —o que fallan al importar en silencio por una dependencia opcional
ausente en ese contexto— no llegan a registrarse. El resultado es una app parcial
que **no representa lo que sirve el proceso real**.

## Fix

Verificar contra el **gateway**, que es lo que ve un cliente:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8080/api/plans/x/preview
```

Y leer el código con criterio: **401/403 significa que la ruta EXISTE** (está
protegida); **404 significa que no está montada**. Confundir los dos es el error
clásico al validar un despliegue.

## Cómo verificar el fix

`curl` al gateway devuelve 401 en las rutas nuevas antes de autenticar, y su
respuesta real después.
