---
title: Vault entrypoint añade `-config=` y duplica si lo pones en `command:`
area: docker
encountered: 2026-05-20
stack: hashicorp/vault:1.17
---

## Síntoma

```
vault | Error parsing listener configuration.
vault | Error initializing listener of type tcp:
        listen tcp4 0.0.0.0:8200: bind: address already in use
```

El config.hcl declara un solo listener; aún así el server intenta
bindar el puerto dos veces.

## Causa raíz

`docker-entrypoint.sh` de la imagen `hashicorp/vault` detecta el
subcomando `server` y **añade automáticamente** `-config=$VAULT_CONFIG_DIR`
(por defecto `/vault/config/`) al comando final. Si tu
`docker-compose.yml` también pasa `-config=/vault/config/config.hcl`,
el listener del file se carga **dos veces**.

## Fix

En el `command:` solo pasa el subcomando, sin flags `-config`:

```yaml
services:
  vault:
    command: ["server"]
    # NO añadas: ["server", "-config=/vault/config/config.hcl"]
    volumes:
      - ./vault/config.hcl:/vault/config/config.hcl:ro
```

El entrypoint se encarga del flag.

## Cómo verificar el fix

```bash
docker inspect agentic-platform-vault-1 --format '{{json .Args}}'
# -> ["server"]   (NO ["server", "-config=..."])
```

Y los logs de arranque no deben tener errores de listener.
