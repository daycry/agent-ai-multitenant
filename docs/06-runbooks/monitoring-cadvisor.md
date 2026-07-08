---
title: Runbook — cAdvisor sin privileged (métricas por contenedor)
audience: system-admin
updated: 2026-07-08
docs_language: es
---

# cAdvisor: postura endurecida y trade-off

Desde prod-12 `task_prod12_cadv_01` (hallazgo sandbox-8), cAdvisor corre **sin
`privileged` y sin `/dev/kmsg`**, con el mismo hardening que el resto del stack
(`cap_drop: [ALL]`, `apparmor=agentic-default`, `no-new-privileges`, límites de
CPU/RAM). Aplica igual al overlay de dev (`docker/docker-compose.monitoring.yml`)
y al compose generado por el instalador.

## Qué sigue funcionando

Las métricas por contenedor salen de los bind-mounts **read-only**
(`/rootfs`, `/sys`, `/var/lib/docker`, `/var/run`): CPU
(`container_cpu_usage_seconds_total`), memoria, red y filesystem — validado
empíricamente lanzando cAdvisor v0.49 no-privileged con esos montajes. Los
dashboards de Grafana (host-overview) no cambian.

## Qué se pierde

- La **decodificación de eventos OOM-kill del kernel** (leía `/dev/kmsg`):
  `container_oom_events_total` puede quedarse a 0 aunque haya OOM-kills. El
  OOM sigue siendo visible por otras vías (estado del contenedor, logs del
  runtime, `docker events`).
- Algunas métricas de disco de bajo nivel en kernels antiguos.

## Opt-in legacy (si tu host lo necesita)

Si en un host concreto las métricas se degradan de forma que te importe,
recupera el modo antiguo con un override local — consciente de que un
contenedor `privileged` desactiva el confinamiento LSM:

```yaml
# docker-compose.override.yml (solo ese host, decisión explícita)
services:
  cadvisor:
    privileged: true
    devices: ["/dev/kmsg"]
    cap_drop: []
    security_opt: ["no-new-privileges:true"]
```

Los tests de seguridad (`tests/security/test_pentest_findings.py`) tratan
cualquier servicio `privileged` en los compose comiteados como hallazgo — el
override es deliberadamente un fichero local no comiteado.

## Verificación tras un cambio

```bash
docker exec <cadvisor> wget -qO- http://localhost:8080/metrics | grep -c container_cpu_usage_seconds_total
# > 0 → las métricas fluyen; el target de Prometheus debe estar UP.
```
