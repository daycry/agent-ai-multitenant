---
title: Volumen dev de MinIO escrito por una versión más nueva (`xl meta version N`)
area: docker
encountered: 2026-06-02
stack: docker compose v2.x, MinIO (imagen fijada en el compose), Windows 11
---

## Síntoma

Tras actualizar MinIO en el host (o reusar un `minio_data` que escribió
una build más reciente), el contenedor del compose **arranca pero no
sirve objetos**, y en los logs aparece:

```
API: SYSTEM()
Time: ...
Error: unable to read 'xl.meta': decodeXLHeaders: Unknown xl meta version 3
       (*errors.errorString)
```

(El número de versión concreto — `3`, `4`… — depende de qué build
escribió el volumen.) Las operaciones contra el bucket fallan o el
servidor queda en estado degradado.

## Causa raíz

MinIO guarda cada objeto con un fichero de metadatos `xl.meta` cuyo
formato lleva un **número de versión interno**. Ese formato solo es
**forward-compatible**: una build vieja **no sabe leer** un `xl.meta`
escrito por una build más nueva, y MinIO **no soporta downgrade** del
layout de datos.

Pasa cuando el volumen `minio_data` lo escribió primero una versión más
moderna (porque alguien levantó el stack con `minio:latest`, o con una
imagen más nueva en otra rama) y luego el compose lo monta con la
**imagen fijada** (más antigua). La imagen vieja se topa con un
`xl.meta` de un formato que no entiende.

## Fix

Es un volumen **de desarrollo** (datos descartables), así que la salida
limpia es **recrear el volumen** para que la imagen fijada lo
inicialice con su propio formato:

```powershell
docker compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml down
docker volume rm agent-ai-multitenant_minio_data   # ajusta el prefijo al de tu proyecto
docker compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml up -d minio
```

Alternativa si necesitas conservar los objetos: **subir el pin** de la
imagen de MinIO en el compose a una versión ≥ la que escribió el
volumen (nunca a la inversa).

> Recrear el volumen **borra** los objetos de dev (KB ingerida,
> artefactos, etc.). En dev es aceptable; en un entorno con datos
> reales, sube el pin en vez de borrar.

## Cómo verificar el fix

```powershell
docker compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml logs minio --tail 30
# Sin "Unknown xl meta version"; aparece el banner de arranque normal.

# Y la consola/health responde:
# (Invoke-WebRequest http://127.0.0.1:9000/minio/health/live).StatusCode  -> 200
```

## Notas

- Fija siempre la imagen de MinIO en el compose (no `:latest`) para que
  todos los devs escriban el mismo formato y este desajuste no aparezca.
- Si conviven varios stacks/ramas en la misma máquina, comparten el
  nombre de volumen por prefijo de proyecto: levantar uno con imagen
  nueva "contamina" el volumen para el otro con imagen vieja.
- Para puertos del host de localhost en Windows, usa la IP explícita —
  ver [powershell-invoke-restmethod-localhost-hang.md](./powershell-invoke-restmethod-localhost-hang.md).
