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

| Runbook                                                              | Cuándo usarlo                                                                     |
| -------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| [health-check.md](./health-check.md)                                 | Verificar que todos los servicios del stack están sanos                           |
| [backups.md](./backups.md)                                           | Copia/restauración manual a nivel de volumen (procedimiento básico)               |
| [restart-services.md](./restart-services.md)                         | Reiniciar el stack o un servicio concreto sin perder datos                        |
| [dr-full-restore.md](./dr-full-restore.md)                           | DR completo: restaurar todo el stack desde un backup                              |
| [dr-tenant-restore.md](./dr-tenant-restore.md)                       | Restore selectivo de un solo tenant sin afectar a los demás                       |
| [dr-manual-backup.md](./dr-manual-backup.md)                         | Backup manual con el motor, verificación y subida a destino remoto                |
| [dr-vault-unseal-rotation.md](./dr-vault-unseal-rotation.md)         | Rotar las unseal keys de Vault con `vault operator rekey`                         |
| [internal-pentest-methodology.md](./internal-pentest-methodology.md) | Pentest interno: threat model, invariantes automáticas y plan de pruebas manuales |

## Convención

Cada runbook sigue el mismo esqueleto: **cuándo**, **comprobación
previa**, **pasos** y **verificación**. Si un paso falla por una
trampa conocida del toolchain, el runbook enlaza a la nota
correspondiente en [`docs/03-guides/gotchas/`](../03-guides/gotchas/).
