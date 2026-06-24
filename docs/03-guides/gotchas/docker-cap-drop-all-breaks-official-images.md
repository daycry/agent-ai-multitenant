---
title: cap_drop ALL rompe el arranque de imágenes oficiales (postgres/redis/vault/clamav/tinyproxy)
area: docker
encountered: 2026-06-18
stack: docker compose v2.x · prod-01 hardening
---

## Síntoma

Tras recrear los contenedores (`docker compose up -d` recrea por un cambio de
config), los servicios de infraestructura **entran en crash-loop** (`Restarting
(1)`), con errores como:

- postgres: `find: '/var/lib/postgresql/data': Permission denied` y
  `chmod: changing permissions of '/var/lib/postgresql/data': Operation not permitted`
- redis: `find: ./appendonlydir: Permission denied`
- clamav: `chown: /var/lib/clamav/...: Operation not permitted`
- vault: `unable to set CAP_SETFCAP effective capability: Operation not permitted`
- egress-proxy (tinyproxy): `tinyproxy: Unable to change to group "tinyproxy"`

El volumen de datos NO se pierde (el contenedor no puede acceder/chown-ear su
dir, pero los ficheros siguen ahí).

## Causa raíz

El baseline de endurecimiento de prod-01 aplica `cap_drop: [ALL]` a todos los
servicios (anchor `x-seccomp` en `docker/docker-compose.yml`,
`_hardening(cap_drop_all=True)` en `compose_generator.py`). Pero los **entrypoints
de las imágenes oficiales** arrancan como root para `chown`/`chmod` su directorio
de datos y luego **bajan de privilegios** al usuario de servicio vía gosu/su-exec.
Eso necesita capacidades que `cap_drop: ALL` quita: `CHOWN`, `DAC_OVERRIDE`,
`FOWNER`, `SETGID`, `SETUID` (y, en Vault, `SETFCAP` porque hace `setcap` sobre su
propio binario). Sin ellas el entrypoint falla y el contenedor reinicia en bucle.

Las imágenes de app propias (`api-server`, `workers`, …) NO lo sufren porque
corren directamente como `uid 1000` (no bajan privilegios en runtime).

## Fix

Devolver SOLO las caps de auto-inicialización encima del `cap_drop: ALL` (nunca
las peligrosas como `NET_ADMIN`/`SYS_ADMIN`). En el compose canónico, anchor
`x-infra-caps` aplicado a postgres/redis/clamav/egress-proxy; vault añade además
`SETFCAP`:

```yaml
x-infra-caps: &infra-caps [CHOWN, DAC_OVERRIDE, FOWNER, SETGID, SETUID]
# postgres / redis / clamav / egress-proxy:
    cap_add: *infra-caps
# vault:
    cap_add: [IPC_LOCK, SETFCAP, CHOWN, DAC_OVERRIDE, FOWNER, SETGID, SETUID]
```

En `compose_generator.py` el mismo conjunto vive en `_INFRA_CAPS` y se aplica en
cada builder de servicio oficial. Tests de regresión:
`tests/unit/test_canonical_compose_hardening.py::test_official_infra_images_keep_self_init_caps`
y `tests/unit/test_compose_generator.py::test_official_infra_images_keep_self_init_caps`.

## Cómo verificar el fix

```powershell
docker compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml up -d --force-recreate postgres redis vault clamav
docker ps --format "{{.Names}}`t{{.Status}}" | Select-String "postgres|redis|vault|clamav"
# Todos deben quedar (healthy), no Restarting.
```
