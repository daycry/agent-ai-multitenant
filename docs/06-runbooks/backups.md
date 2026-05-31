---
title: Copias de seguridad y restauración
docs_language: es
audience: operador, system admin
updated: 2026-05-29
---

# Runbook — Copias de seguridad y restauración

## Cuándo

- Antes de aplicar una migración de base de datos no trivial.
- Antes de actualizar versiones de los servicios del stack.
- De forma periódica (programada) como red de seguridad operativa.

> Nota de alcance: el script unificado `scripts/backup.sh` y la sección
> de Backups del panel del System Admin se formalizan en fases
> posteriores (instalador, Fase 15). Este runbook describe el
> procedimiento **manual** sobre los volúmenes Docker, válido hoy.

## Qué hay que respaldar

El estado persistente vive en volúmenes Docker declarados en
`docker/docker-compose.yml`:

| Volumen         | Contenido                                | Prioridad |
| --------------- | ---------------------------------------- | --------- |
| `postgres_data` | Datos relacionales + embeddings pgvector | crítica   |
| `vault_data`    | Secretos cifrados (KV v2)                | crítica   |
| `minio_data`    | Object storage S3-compatible (uploads)   | alta      |
| `redis_data`    | Sesiones, broker Celery (AOF + RDB)      | media     |
| `clamav_data`   | Firmas de antivirus (regenerables)       | baja      |

En despliegue real con datos montados en host, los repos git y
worktrees viven bajo `/data/agent-platform/projects/` y se respaldan
junto con la base de datos.

## Pasos — copia de seguridad

### 1. PostgreSQL (lógico, recomendado)

Un volcado lógico es portable entre versiones y fácil de inspeccionar:

```bash
docker compose \
  -f docker/docker-compose.yml \
  -f docker/docker-compose.dev.yml \
  exec -T postgres \
  pg_dump -U postgres -d agentic_platform --format=custom \
  > backup-agentic_platform-$(date +%Y%m%d).dump
```

### 2. Vault y MinIO (a nivel de volumen)

Para los volúmenes que no tienen export lógico simple, copia el
volumen con un contenedor auxiliar:

```bash
docker run --rm \
  -v agentic_vault_data:/data:ro \
  -v "$(pwd)":/backup \
  alpine tar czf /backup/vault_data-$(date +%Y%m%d).tar.gz -C /data .
```

(El nombre real del volumen lleva el prefijo del proyecto Compose;
verifícalo con `docker volume ls`.)

## Pasos — restauración

### PostgreSQL

```bash
docker compose \
  -f docker/docker-compose.yml \
  -f docker/docker-compose.dev.yml \
  exec -T postgres \
  pg_restore -U postgres -d agentic_platform --clean --if-exists \
  < backup-agentic_platform-YYYYMMDD.dump
```

### Volumen Vault / MinIO

Con el stack **parado** (ver
[restart-services.md](./restart-services.md)), extrae el tar dentro
del volumen destino con un contenedor auxiliar análogo al de copia.

## Verificación

- Tras restaurar PostgreSQL, comprueba la salud del stack con
  [health-check.md](./health-check.md).
- Confirma que `GET /admin/system-health` reporta `postgres: ok`.
- Verifica que puedes hacer login en el admin-panel.
