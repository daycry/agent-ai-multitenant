---
title: AppArmor — cargar y verificar los perfiles de confinamiento
docs_language: es
audience: system admin, responsable de seguridad, devops
updated: 2026-05-31
---

# Runbook — AppArmor profiles (carga en el host)

Confinamiento **MAC (Mandatory Access Control)** de los contenedores de la
plataforma (Plan 15 `task_15_16`). Es una capa de defensa **adicional** sobre el
seccomp default-deny (`task_15_15`), `cap_drop ALL` + `no-new-privileges`
(`task_06_14_11`) y los montajes read-only. AppArmor lo aplica **el kernel del
host**; por eso cargar los perfiles es un **paso del operador en un host Linux
con AppArmor activo** y NO puede ejecutarse en CI (la validación automática de
los perfiles vive en `tests/security/test_apparmor.py`; la aplicación real por
el kernel se confirma aquí, ver
[internal-pentest-methodology.md §5](./internal-pentest-methodology.md)).

> Alcance: **Docker Compose en una sola máquina** con AppArmor en el kernel
> (Ubuntu/Debian lo traen por defecto). En un host **sin** AppArmor (p.ej. dev
> en macOS/Windows) hay que **quitar** el pin `apparmor=…` del `security_opt` o
> el arranque del contenedor fallará — ver §4.

## Perfiles que entrega la plataforma

| Perfil                                    | Confina                                    | Pinned en                                                                                                                    |
| ----------------------------------------- | ------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------- |
| `docker/apparmor/agentic-default.profile` | Servicios largos (código propio/confiable) | `docker-compose.yml` + `docker-compose.monitoring.yml` (`security_opt: apparmor=agentic-default`) + generador del instalador |
| `docker/apparmor/agent-runtime.profile`   | Sandbox de agente/test (código hostil)     | El worker lo pina vía `WORKERS_APPARMOR_PROFILE=agent-runtime` (`apps/workers/.../isolation.py`)                             |

Ambos **deniegan** las primitivas de escape de contenedor (`mount`,
`pivot_root`, `ptrace`, carga de módulos del kernel, `reboot`, E/S cruda, acceso
al socket Docker) y **confinan las escrituras** a los directorios esperados. El
perfil del sandbox es **más estricto**: solo permite escribir en `/workspace` y
`/tmp`.

## 1. Comprobación previa

```bash
# AppArmor presente y activo en el host:
sudo aa-status
# Docker debe reportar el LSM apparmor:
docker info --format '{{.SecurityOptions}}'   # debe incluir 'name=apparmor'
```

Si `aa-status` no existe o Docker no lista `apparmor`, ve a §4.

## 2. Cargar / recargar los perfiles

Desde la raíz del repo (los perfiles se referencian por el **nombre** declarado
en la cabecera `profile <nombre> { … }`, no por la ruta del fichero):

```bash
sudo apparmor_parser -r -W docker/apparmor/agentic-default.profile
sudo apparmor_parser -r -W docker/apparmor/agent-runtime.profile
```

- `-r` recarga (idempotente: vuelve a cargarlo si ya estaba).
- `-W` espera a que el kernel confirme la carga.

El instalador automatiza este paso al desplegar en un host Linux (copia
`docker/apparmor/` junto al compose generado y ejecuta `apparmor_parser`); en un
despliegue manual hazlo tú antes del `docker compose up`.

## 3. Verificación

```bash
# Los dos perfiles deben aparecer cargados (modo enforce):
sudo aa-status | grep -E 'agentic-default|agent-runtime'

# Arrancar el stack (el pin apparmor= ya está en el compose):
docker compose -f docker/docker-compose.yml up -d

# Confirmar que un contenedor corre confinado (NO 'unconfined'):
docker inspect --format '{{ .AppArmorProfile }}' agentic-platform-postgres-1
# -> agentic-default
```

Prueba negativa rápida (debe FALLAR dentro del contenedor confinado):

```bash
docker compose exec postgres sh -c 'mount -o remount,rw / 2>&1' \
  # -> Permission denied  (mount denegado por el perfil)
```

## 4. Host sin AppArmor (dev / no-Linux)

En un host sin AppArmor el pin `apparmor=…` hace fallar el arranque. Opciones:

- **No usar el compose de producción endurecido** para dev (el flujo dev usa
  `docker-compose.dev.yml` por encima; si tu kernel no soporta AppArmor,
  sobreescribe `security_opt` en un overlay local sin el pin `apparmor=`).
- O cargar los perfiles en un host Linux real antes de desplegar (camino de
  producción, §2).

## 5. Cuándo re-ejecutar

- Tras **editar** cualquier perfil bajo `docker/apparmor/` → recargar (§2).
- Tras un **reinicio del host** si los perfiles no se persisten en
  `/etc/apparmor.d/` (para persistirlos, copia los ficheros allí y habilita el
  servicio `apparmor`).
- La validación estructural (`tests/security/test_apparmor.py`) corre en **cada
  commit** (CI) — no requiere host con AppArmor.
