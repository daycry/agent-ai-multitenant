---
title: docker compose mergea (no reemplaza) `volumes:` entre overrides
area: docker
encountered: 2026-05-21
stack: docker compose v2.x
---

## Síntoma

Defines un servicio en `docker-compose.yml` con un bind mount y
luego en `docker-compose.dev.yml` (override) declaras una lista
`volumes:` más corta para "quitar" ese bind mount. `docker compose up
-d` ignora el override y el contenedor sigue con el mount original.

`docker compose config` puede mostrar correctamente la lista corta,
pero `docker inspect <container>` revela que el mount sigue ahí.

## Causa raíz

Las listas en compose overrides se **mergean** por defecto, no se
reemplazan. Un `volumes:` plano añade entradas a la lista heredada
en lugar de sustituirla.

## Fix

Usar el tag YAML `!reset` (o `!override`) para descartar la lista
heredada antes de aplicar la nueva:

```yaml
services:
  vault:
    volumes: !reset
      - vault_data:/vault/file
      - vault_logs:/vault/logs
```

## Cómo verificar el fix

```bash
docker compose -f base -f dev down vault
docker compose -f base -f dev rm -f vault
docker compose -f base -f dev up -d vault
docker inspect agentic-platform-vault-1 \
  --format '{{range .Mounts}}{{.Type}} {{.Destination}}{{println}}{{end}}'
```

La salida debe mostrar SOLO los volúmenes del override (sin el bind
mount heredado).
