---
title: Reiniciar el stack o un servicio
docs_language: es
audience: operador, system admin
updated: 2026-05-29
---

# Runbook — Reiniciar el stack o un servicio

## Cuándo

- Un servicio aparece en `Restarting` o `down` en
  [health-check.md](./health-check.md).
- Tras cambiar variables de entorno en `docker/.env`.
- Para aplicar una imagen actualizada de un servicio concreto.

## PASO 0 (tras cualquier reinicio del HOST) — desellar Vault

> Esto va **antes** que nada. No es una comprobación opcional: es el primer paso
> de la vuelta a la vida.

Vault con backend de fichero arranca **sellado** después de cada reinicio de la
máquina. Sellado significa: el contenedor está vivo, contesta HTTP, y **no puede
descifrar un solo secreto**. Y lo peor — parece sano:

- el healthcheck del compose pide
  `/v1/sys/health?...&sealedcode=200&uninitcode=200`, o sea traduce «sellado»
  (503) a **200 a propósito** (si fuese `unhealthy`, Vault se reiniciaría en
  bucle antes de que nadie llegue a desellarlo);
- el compose que genera el instalador arranca las apps con
  `depends_on: vault: service_healthy`, o sea detrás de ese 200;
- el watchdog da por sano cualquier `healthy|running|starting`.

Resultado sin este paso: todo el stack arranca contra un Vault inutilizable y las
averías aparecen una a una, dispersas — credenciales de proveedor LLM que «no
existen», `auth_ref` de MCP que devuelven AUTH_ERROR — sin nada que apunte a la
causa común.

### 1. ¿Está sellado?

```bash
docker compose -f docker/docker-compose.yml exec vault \
  vault status | grep -i sealed
```

`Sealed  true` ⇒ hay que desellar. Dos señales más, ya cableadas
(prod-10 `task_prod10_09`):

- `/admin/system-health` marca la plataforma **`degraded`** con el detalle
  `Vault is SEALED`;
- la métrica `agentic_vault_sealed` vale `1` y dispara la alerta `VaultSealed`
  a los 2 minutos.

### 2. Desellar

Hacen falta **3 de las 5** unseal keys, de custodias distintas (ver
[dr-vault-unseal-rotation.md](./dr-vault-unseal-rotation.md)). Se introducen de
una en una:

```bash
docker compose -f docker/docker-compose.yml exec vault vault operator unseal
# repetir 3 veces, una key por invocación
```

Si guardaste el blob cifrado que escribe `scripts/init-vault.sh`, descífralo en
memoria y **no lo dejes en disco**:

```bash
age -d -i ~/.age/vault.key vault-init-output/vault-init.age | less
```

### 3. Confirmar

```bash
docker compose -f docker/docker-compose.yml exec vault vault status | grep -i sealed
# Sealed  false
```

`/admin/system-health` vuelve a `ok` y `agentic_vault_sealed` a `0`.

> **En el stack de dev/manuales no hace falta**: el servicio `vault-unsealer`
> (`docker/vault/auto-unseal.sh`) lo desella solo con las keys que guarda en el
> volumen `vault_init`. Es un compromiso **consciente y sólo de desarrollo** —
> tener las keys junto a Vault anula el reparto de Shamir. En producción las 5
> keys viven en 5 custodias separadas y este paso es manual.

### 4. Rotar el token de servicio (si tocaba)

Los servicios ya NO usan el root token: llevan tokens periódicos por servicio
(`scripts/vault-mint-service-tokens.sh`) que el api-server renueva solo en
segundo plano. Si la alerta `VaultTokenExpiringSoon` está activa, la renovación
se ha roto: busca `vault.token.renew_failed` en los logs y re-mintea.

```bash
VAULT_TOKEN=<root>  ./scripts/vault-mint-service-tokens.sh >> docker/.env
docker compose ... up -d --force-recreate api-server workers orchestrator notification-dispatcher
```

## Comprobación previa

Desde prod-10 (`task_prod10_05`), **el compose no arranca sin `docker/.env`**.
Cada credencial se declara `${VAR:?…}` en vez de caer a `changeme-dev-only`: un
despliegue al que le falte una variable falla al arrancar en lugar de correr con
una contraseña publicada en este repositorio. Si el `docker compose` protesta con
`required variable POSTGRES_PASSWORD is missing a value`:

```bash
cp docker/.env.example docker/.env
```

La lista completa está en
[`docs/04-reference/mandatory-env-vars.md`](../04-reference/mandatory-env-vars.md).

Reiniciar **no** borra los volúmenes de datos (`docker compose
restart` y `stop/start` los preservan). El único comando que destruye
datos es `docker compose down -v` (la `-v` borra volúmenes): **no lo
uses** en este runbook.

En **Windows/Docker Desktop (WSL2)** hay un matiz extra: los bare repos + worktrees
viven en un bind dentro de la VM WSL2, frágil ante `wsl --shutdown` / Clean-Purge.
Antes de cualquier operación destructiva, consulta
[data-durability-windows-wsl2.md](./data-durability-windows-wsl2.md) y respalda
`/data/agent-platform` con `scripts/backup-data.ps1`.

## Pasos

### Reiniciar un solo servicio

```bash
docker compose \
  -f docker/docker-compose.yml \
  -f docker/docker-compose.dev.yml \
  restart postgres
```

Sustituye `postgres` por el servicio afectado (`redis`, `minio`,
`vault`, `clamav`, `docling-serve`, `egress-proxy`).

### Recrear un servicio tras cambiar su configuración

`restart` reutiliza el contenedor existente. Si cambiaste imagen o
variables de entorno, hay que **recrearlo**:

```bash
docker compose \
  -f docker/docker-compose.yml \
  -f docker/docker-compose.dev.yml \
  up -d --force-recreate vault
```

### Reiniciar todo el stack de infraestructura

```bash
docker compose \
  -f docker/docker-compose.yml \
  -f docker/docker-compose.dev.yml \
  restart
```

### Procesos de desarrollo (api-server + admin-panel)

En dev, el api-server (`uvicorn`) y el admin-panel (`next dev`) corren
como procesos host gestionados por `scripts/dev/up` y `scripts/dev/down`,
no por Docker. Para reiniciarlos sin tocar el stack Docker:

```powershell
.\scripts\dev\down.ps1     # mata api-server + admin-panel (lee .dev/*.pid)
.\scripts\dev\up.ps1       # vuelve a levantarlos y reaplica migraciones
```

```bash
./scripts/dev/down.sh
./scripts/dev/up.sh
```

`down.ps1 -Docker` (o `down.sh --docker`) además baja el stack Docker;
úsalo solo si quieres parar todo.

## Verificación

Después de cualquier reinicio, ejecuta
[health-check.md](./health-check.md) y confirma que el servicio
reiniciado vuelve a `healthy` / `ok`. Si tras recrear Vault sigue
fallando, revisa
[`docs/03-guides/gotchas/vault-dev-mode-port-conflict.md`](../03-guides/gotchas/vault-dev-mode-port-conflict.md)
y [`vault-entrypoint-config-flag.md`](../03-guides/gotchas/vault-entrypoint-config-flag.md).
