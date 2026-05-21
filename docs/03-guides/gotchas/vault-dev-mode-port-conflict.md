---
title: Vault `-dev` choca con `config.hcl` montado en 8200
area: docker
encountered: 2026-05-20
stack: hashicorp/vault:1.17, docker compose v2.x
---

## Síntoma

```
vault-1 | Error parsing listener configuration.
vault-1 | Error initializing listener of type tcp:
         listen tcp4 0.0.0.0:8200: bind: address already in use
```

El contenedor entra en restart-loop. Pasa cuando lanzas vault con
`-dev` Y el `docker-compose.yml` base monta un `config.hcl` que
también declara `listener "tcp" address = "0.0.0.0:8200"`.

## Causa raíz

`-dev` abre su propio listener (in-memory backend, auto-unsealed).
Al mismo tiempo el config file declara otro listener en el mismo
puerto. Ambos intentan bindar 8200 y el segundo falla.

## Fix

En el override `docker-compose.dev.yml`, **quita el bind mount del
`config.hcl`** usando `volumes: !reset` (ver
[docker-compose-volumes-merge.md](./docker-compose-volumes-merge.md)):

```yaml
services:
  vault:
    command: ["server", "-dev", "-dev-listen-address=0.0.0.0:8200", ...]
    volumes: !reset
      - vault_data:/vault/file
      - vault_logs:/vault/logs
```

En modo dev no necesitas el config porque el storage es in-memory.

## Cómo verificar el fix

```bash
docker compose -f base -f dev up -d --force-recreate vault
sleep 8
docker compose -f base -f dev ps vault --format "{{.Status}}"
# -> "Up X seconds (healthy)"

docker compose -f base -f dev logs vault --tail=5
# Ningún "address already in use".
```
