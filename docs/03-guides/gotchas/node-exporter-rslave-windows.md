---
title: node-exporter no arranca en Docker Desktop (Windows/WSL2) por el mount rslave de /
area: windows, monitoring
encountered: 2026-06-09
stack: docker-compose, node-exporter, wsl2
---

## Síntoma

Con el overlay de monitoring en **Windows / Docker Desktop (WSL2)**, el
contenedor `node-exporter` se queda en `Created` y no arranca:

```
Error response from daemon: path / is mounted on / but it is not a shared or slave mount
```

Y si "arreglas" quitando volúmenes a lo bruto (con `!reset`), pasa a
crash-loop con:

```
panic: Couldn't create metrics handler: couldn't create collector:
  failed to open procfs: could not read "/host/proc": stat /host/proc: no such file or directory
```

## Causa raíz

Dos cosas:

1. **El mount `rslave` de `/`.** `node-exporter` (en
   `docker/docker-compose.monitoring.yml`) monta el rootfs del host con
   propagación recursive-slave para ver todos los filesystems del host:
   `/:/host/root:ro,rslave`. Para un bind `rslave`, el origen (`/`) debe estar
   marcado **shared** o **slave** en el kernel. En la VM WSL2 de Docker Desktop
   `/` es **private**, así que Docker rechaza el bind y el contenedor no
   arranca. En Linux (prod) `/` suele ser shared → funciona.

2. **`!reset` vs `!override`.** Al escribir el override, `volumes: !reset` NO
   significa "descarta la base y aplica esta lista": deja `volumes` en **null**
   (la lista de debajo se ignora). El contenedor arranca **sin** `/host/proc` ni
   `/host/sys` → panic. El tag que **reemplaza** una lista es **`!override`**.

## Fix

Override **solo-Windows** que se aplica el último (no degrada el dev de Linux):
`docker/docker-compose.windows.yml` redefine `node-exporter` quitando el mount
del rootfs (y el flag `--path.rootfs`) y **reemplazando** la lista de volúmenes
con `!override`:

```yaml
services:
  node-exporter:
    command:
      - --path.procfs=/host/proc
      - --path.sysfs=/host/sys
      - --collector.filesystem.mount-points-exclude=^/(sys|proc|dev|host|etc)($$|/)
      - --collector.textfile.directory=/host/textfile
    volumes: !override # NO !reset (eso lo deja en null)
      - /proc:/host/proc:ro
      - /sys:/host/sys:ro
      - node_exporter_textfile:/host/textfile:ro
```

Levantar en Windows añadiendo ese fichero al final:

```powershell
docker compose -f docker/docker-compose.yml `
               -f docker/docker-compose.dev.yml `
               -f docker/docker-compose.monitoring.yml `
               -f docker/docker-compose.monitoring.dev.yml `
               -f docker/docker-compose.windows.yml up -d
```

**Trade-off:** en Windows dev se pierden las métricas de **filesystem del host**
(`node_filesystem_*`); CPU / load / memoria / red sí funcionan. Las métricas
reales de host viven en el despliegue Linux, donde el compose base arranca tal
cual. Ver el runbook [Ollama en el stack](../../06-runbooks/ollama-gpu-setup.md)
para el resto del set de monitoring.
