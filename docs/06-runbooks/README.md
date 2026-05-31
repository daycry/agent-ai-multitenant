---
title: Runbooks operativos
docs_language: es
audience: operador, system admin
updated: 2026-05-31
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
| [01-installation-from-scratch.md](./01-installation-from-scratch.md) | Instalar desde cero en una máquina virgen (wizard de 9 pasos o CLI desatendido)   |
| [02-troubleshooting.md](./02-troubleshooting.md)                     | Diagnóstico y fix de fallos frecuentes tras instalar o en operación               |
| [03-system-upgrade.md](./03-system-upgrade.md)                       | Actualizar una instalación en marcha a una versión nueva (imágenes + esquema)     |
| [04-disaster-recovery.md](./04-disaster-recovery.md)                 | DR: punto de entrada canónico para restore completo o selectivo por tenant        |
| [05-key-rotation.md](./05-key-rotation.md)                           | Rotar unseal keys + credenciales (estáticas/dinámicas) y revocación de emergencia |
| [health-check.md](./health-check.md)                                 | Verificar que todos los servicios del stack están sanos                           |
| [backups.md](./backups.md)                                           | Copia/restauración manual a nivel de volumen (procedimiento básico)               |
| [restart-services.md](./restart-services.md)                         | Reiniciar el stack o un servicio concreto sin perder datos                        |
| [dr-full-restore.md](./dr-full-restore.md)                           | DR completo: restaurar todo el stack desde un backup                              |
| [dr-tenant-restore.md](./dr-tenant-restore.md)                       | Restore selectivo de un solo tenant sin afectar a los demás                       |
| [dr-manual-backup.md](./dr-manual-backup.md)                         | Backup manual con el motor, verificación y subida a destino remoto                |
| [dr-vault-unseal-rotation.md](./dr-vault-unseal-rotation.md)         | Rotar las unseal keys de Vault con `vault operator rekey`                         |
| [internal-pentest-methodology.md](./internal-pentest-methodology.md) | Pentest interno: threat model, invariantes automáticas y plan de pruebas manuales |
| [apparmor-profiles.md](./apparmor-profiles.md)                       | Cargar y verificar los perfiles AppArmor de confinamiento de contenedores         |

## Convención

Cada runbook sigue el mismo esqueleto: **cuándo**, **comprobación
previa**, **pasos** y **verificación**. Si un paso falla por una
trampa conocida del toolchain, el runbook enlaza a la nota
correspondiente en [`docs/03-guides/gotchas/`](../03-guides/gotchas/).
