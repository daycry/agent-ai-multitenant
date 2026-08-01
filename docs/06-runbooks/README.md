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

| Runbook                                                              | Cuándo usarlo                                                                                                                |
| -------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| [01-installation-from-scratch.md](./01-installation-from-scratch.md) | Instalar desde cero en una máquina virgen (wizard de 9 pasos o CLI desatendido)                                              |
| [02-troubleshooting.md](./02-troubleshooting.md)                     | Diagnóstico y fix de fallos frecuentes tras instalar o en operación                                                          |
| [03-system-upgrade.md](./03-system-upgrade.md)                       | Actualizar una instalación en marcha a una versión nueva (imágenes + esquema)                                                |
| [04-disaster-recovery.md](./04-disaster-recovery.md)                 | DR: punto de entrada canónico para restore completo o selectivo por tenant                                                   |
| [05-key-rotation.md](./05-key-rotation.md)                           | Rotar unseal keys + credenciales (estáticas/dinámicas) y revocación de emergencia                                            |
| [06-capacity-management.md](./06-capacity-management.md)             | Escalar workers/colas, concurrencia + límites de tiempo, sizing y capacity de GPU                                            |
| [07-custom-domain.md](./07-custom-domain.md)                         | Publicar bajo un dominio propio (DNS + TLS + origen/prefijo) y su efecto en el SSO                                           |
| [08-instalacion-produccion.md](./08-instalacion-produccion.md)       | Manual de PRODUCCIÓN de punta a punta: host + DNS + install.yaml + dominio (example.com) + verificación + endurecimiento     |
| [health-check.md](./health-check.md)                                 | Verificar que todos los servicios del stack están sanos                                                                      |
| [backups.md](./backups.md)                                           | Copia/restauración manual a nivel de volumen (procedimiento básico)                                                          |
| [restart-services.md](./restart-services.md)                         | Reiniciar el stack o un servicio concreto sin perder datos                                                                   |
| [data-durability-windows-wsl2.md](./data-durability-windows-wsl2.md) | Qué se pierde por acción (restart/down/-v/reset VM) en Windows/WSL2 + backup de /data                                        |
| [dr-full-restore.md](./dr-full-restore.md)                           | DR completo: restaurar todo el stack desde un backup                                                                         |
| [dr-tenant-restore.md](./dr-tenant-restore.md)                       | Restore selectivo de un solo tenant sin afectar a los demás                                                                  |
| [dr-manual-backup.md](./dr-manual-backup.md)                         | Backup manual con el motor, verificación y subida a destino remoto                                                           |
| [dr-drill.md](./dr-drill.md)                                         | Simulacro de DR: backup → máquina limpia → restore → ejecución de un plan (acta + RTO real)                                  |
| [dr-vault-unseal-rotation.md](./dr-vault-unseal-rotation.md)         | Rotar las unseal keys de Vault con `vault operator rekey`                                                                    |
| [internal-pentest-methodology.md](./internal-pentest-methodology.md) | Pentest interno: threat model, invariantes automáticas y plan de pruebas manuales                                            |
| [external-pentest-readiness.md](./external-pentest-readiness.md)     | Pentest externo: readiness, alcance, reglas de enfrentamiento y plantilla de informe                                         |
| [apparmor-profiles.md](./apparmor-profiles.md)                       | Cargar y verificar los perfiles AppArmor de confinamiento de contenedores                                                    |
| [sso-global-auth.md](./sso-global-auth.md)                           | Configurar SSO platform-global, login por provider, acceso por membership (ADR 0047)                                         |
| [triage-vulnerabilidades.md](./triage-vulnerabilidades.md)           | Leer un fallo de `security-scan` (pip-audit/npm audit/Trivy), decidir actualizar vs suprimir y revisar los PRs de Dependabot |
| [recuperacion-lockout-admin.md](./recuperacion-lockout-admin.md)     | Recuperar el acceso a `/admin/*` cuando la allowlist de IP, la MFA obligatoria o la sesión de 15 min dejan fuera al operador |

## Convención

Cada runbook sigue el mismo esqueleto: **cuándo**, **comprobación
previa**, **pasos** y **verificación**. Si un paso falla por una
trampa conocida del toolchain, el runbook enlaza a la nota
correspondiente en [`docs/03-guides/gotchas/`](../03-guides/gotchas/).
