---
title: Runbooks operativos
docs_language: es
audience: operador, system admin
updated: 2026-05-29
---

# 06-runbooks — Runbooks operativos

Procedimientos paso a paso para **operar** la plataforma: comprobar la
salud del stack, hacer copias de seguridad y reiniciar servicios.
Orientados a quien mantiene el sistema corriendo en una sola máquina
(Docker Compose), no a quien lo desarrolla.

> El alcance actual es **Docker Compose en una sola máquina** (no
> Kubernetes, no multi-máquina). El instalador de producción y los
> runbooks de despliegue formal llegan con la Fase 15.

| Runbook                                      | Cuándo usarlo                                                   |
| -------------------------------------------- | --------------------------------------------------------------- |
| [health-check.md](./health-check.md)         | Verificar que todos los servicios del stack están sanos         |
| [backups.md](./backups.md)                   | Hacer y restaurar copias de seguridad de los volúmenes de datos |
| [restart-services.md](./restart-services.md) | Reiniciar el stack o un servicio concreto sin perder datos      |

## Convención

Cada runbook sigue el mismo esqueleto: **cuándo**, **comprobación
previa**, **pasos** y **verificación**. Si un paso falla por una
trampa conocida del toolchain, el runbook enlaza a la nota
correspondiente en [`docs/03-guides/gotchas/`](../03-guides/gotchas/).
