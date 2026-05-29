---
title: Reiniciar el stack o un servicio
docs_language: es
audience: operador, system admin
updated: 2026-05-29
---

# Runbook — Reiniciar el stack o un servicio

## Cuándo

- Un servicio aparece en `Restarting` o `down` en
  [health-check.md](./health-check.md).
- Tras cambiar variables de entorno en `docker/.env`.
- Para aplicar una imagen actualizada de un servicio concreto.

## Comprobación previa

Reiniciar **no** borra los volúmenes de datos (`docker compose
restart` y `stop/start` los preservan). El único comando que destruye
datos es `docker compose down -v` (la `-v` borra volúmenes): **no lo
uses** en este runbook.

## Pasos

### Reiniciar un solo servicio

```bash
docker compose \
  -f docker/docker-compose.yml \
  -f docker/docker-compose.dev.yml \
  restart postgres
```

Sustituye `postgres` por el servicio afectado (`redis`, `minio`,
`vault`, `clamav`, `docling-serve`, `egress-proxy`).

### Recrear un servicio tras cambiar su configuración

`restart` reutiliza el contenedor existente. Si cambiaste imagen o
variables de entorno, hay que **recrearlo**:

```bash
docker compose \
  -f docker/docker-compose.yml \
  -f docker/docker-compose.dev.yml \
  up -d --force-recreate vault
```

### Reiniciar todo el stack de infraestructura

```bash
docker compose \
  -f docker/docker-compose.yml \
  -f docker/docker-compose.dev.yml \
  restart
```

### Procesos de desarrollo (api-server + admin-panel)

En dev, el api-server (`uvicorn`) y el admin-panel (`next dev`) corren
como procesos host gestionados por `scripts/dev/up` y `scripts/dev/down`,
no por Docker. Para reiniciarlos sin tocar el stack Docker:

```powershell
.\scripts\dev\down.ps1     # mata api-server + admin-panel (lee .dev/*.pid)
.\scripts\dev\up.ps1       # vuelve a levantarlos y reaplica migraciones
```

```bash
./scripts/dev/down.sh
./scripts/dev/up.sh
```

`down.ps1 -Docker` (o `down.sh --docker`) además baja el stack Docker;
úsalo solo si quieres parar todo.

## Verificación

Después de cualquier reinicio, ejecuta
[health-check.md](./health-check.md) y confirma que el servicio
reiniciado vuelve a `healthy` / `ok`. Si tras recrear Vault sigue
fallando, revisa
[`docs/03-guides/gotchas/vault-dev-mode-port-conflict.md`](../03-guides/gotchas/vault-dev-mode-port-conflict.md)
y [`vault-entrypoint-config-flag.md`](../03-guides/gotchas/vault-entrypoint-config-flag.md).
