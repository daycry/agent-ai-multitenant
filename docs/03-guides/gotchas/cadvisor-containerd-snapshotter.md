---
title: cAdvisor no ve ningún contenedor en Docker Desktop (containerd snapshotter)
area: monitoring
encountered: 2026-07-16
stack: docker desktop windows · cadvisor v0.49.1 · docker-compose.monitoring.yml · AUD16-07
---

## Síntoma

El contenedor de cAdvisor está `Up (healthy)` y Prometheus lo scrapea `up`,
pero **todas las métricas per-container están vacías**: los paneles de
contenedores de los dashboards `agentic-platform` y `host-overview` muestran
"No data" y `count(container_last_seen)` devuelve **1** (solo el cgroup raíz
`{id="/"}`). La alerta `ContainerOOMKilled` no puede disparar jamás.

En los logs de cAdvisor, repetido para CADA contenedor del host:

```
Failed to create existing container: /docker/<id>: failed to identify the
read-write layer ID for container <id>. - open
/rootfs/var/lib/docker/image/overlayfs/layerdb/mounts/<id>/mount-id:
no such file or directory
```

## Causa raíz

Docker Desktop moderno usa el **containerd snapshotter** como storage backend:
la metadata de capas vive en `image/overlayfs/` (gestionada por containerd),
no en el layout clásico `image/overlay2/` del graphdriver que cAdvisor
(v0.49.1, handler `--docker_only`) espera bajo `/var/lib/docker`. Al no poder
resolver la capa RW, cAdvisor **descarta el contenedor entero** — no degrada a
"stats sin filesystem", lo omite.

El healthcheck no lo detecta porque el endpoint HTTP responde igual: el
proceso está sano, sus datos no.

## Fix

1. **Visibilidad primero** (hecho, AUD16-07): regla Prometheus
   `CadvisorDegraded` (`count(container_last_seen) <= 1 for 15m`) en
   `docker/monitoring/prometheus/rules/host_alerts.yml` — la ceguera ahora
   alerta en vez de esconderse tras un healthcheck verde.
2. **En hosts Linux de despliegue real** (graphdriver overlay2 clásico) el
   problema no aplica — verificar tras cada deploy con
   `count(container_last_seen)` > 1 en Prometheus.
3. **En Docker Desktop dev**: probar el bump de imagen a cAdvisor >= v0.52
   (mejora el soporte del containerd snapshotter). Si sigue sin resolver,
   opciones: desactivar el containerd snapshotter en Docker Desktop
   (Settings → General → "Use containerd for pulling and storing images" OFF,
   vuelve al layout overlay2), o aceptar la degradación en dev (las métricas
   de HOST siguen funcionando; solo se pierde el detalle per-container) con la
   alerta `CadvisorDegraded` como recordatorio honesto.

## Cómo verificar

```promql
count(container_last_seen)   # sano: ~nº de contenedores del stack (>20)
```

```bash
docker logs agentic-platform-cadvisor-1 --tail 20   # sin "Failed to create existing container"
```
