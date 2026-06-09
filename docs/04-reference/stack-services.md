---
title: Stack Docker — servicios, puertos y cómo acceder
docs_language: es
audience: desarrollador, operador
updated: 2026-06-09
---

# Stack Docker — servicios, puertos y cómo acceder

Referencia de **todos los contenedores** del stack: qué hace cada uno, en qué
puerto escucha, **cómo acceder** en desarrollo (URL + credenciales dev) y qué
fichero `compose` lo define. El stack corre en **Docker Compose en una sola
máquina** (no Kubernetes).

> **Secretos:** las credenciales de abajo son **dev-only** (`changeme-*`,
> `dev-root-token`, `minioadmin`). En producción NADA de esto aplica: los
> secretos viven en **Vault** y los inyecta el instalador (Plan 15). Nunca
> commitees un `.env` real.

## Ficheros compose (capas)

El stack se compone por **capas** que se suman con `-f`:

| Fichero                                    | Para qué                                                                                   |
| ------------------------------------------ | ------------------------------------------------------------------------------------------ |
| `docker/docker-compose.yml`                | **Base** prod-shaped: define todos los servicios; sin puertos al host.                     |
| `docker/docker-compose.dev.yml`            | **Dev**: expone los puertos al host + Vault en modo dev (token conocido).                  |
| `docker/docker-compose.monitoring.yml`     | Overlay de **observabilidad**: Prometheus, Alertmanager, cAdvisor, Grafana, node-exporter. |
| `docker/docker-compose.monitoring.dev.yml` | Expone al host las UIs de monitoring (Grafana/Prometheus/Alertmanager).                    |
| `docker/docker-compose.gpu.yml`            | Overlay **GPU (CUDA)**: añade la reserva NVIDIA al servicio `ollama` (ADR 0056).           |
| `docker/docker-compose.windows.yml`        | Override **solo Windows/WSL2**: arranca `node-exporter` (mount `rslave` no soportado).     |

### Comandos de arranque

```bash
# Dev mínimo (infra + Ollama):
docker compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml up -d

# Dev + monitoring (Linux/macOS):
docker compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml \
  -f docker/docker-compose.monitoring.yml -f docker/docker-compose.monitoring.dev.yml up -d

# Dev + monitoring (Windows/Docker Desktop) — añade el override de Windows AL FINAL:
docker compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml \
  -f docker/docker-compose.monitoring.yml -f docker/docker-compose.monitoring.dev.yml \
  -f docker/docker-compose.windows.yml up -d

# Con GPU (NVIDIA + Container Toolkit): añade el overlay gpu
docker compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml \
  -f docker/docker-compose.gpu.yml up -d
```

Comprueba el estado con `docker compose ps` (espera 30-60 s a que estén
`healthy`).

## Servicios base (`docker-compose.yml`)

Todos viven en la red `agentic-net`. El puerto **host** es el que abre
`docker-compose.dev.yml`; en producción quedan internos.

| Servicio           | Imagen                                  | Qué hace                                                                                             | Puerto host (dev)                   | Acceso / credenciales (dev)                                                |
| ------------------ | --------------------------------------- | ---------------------------------------------------------------------------------------------------- | ----------------------------------- | -------------------------------------------------------------------------- |
| `postgres`         | `pgvector/pgvector:pg16`                | PostgreSQL 16 + **pgvector** (relacional + vectorial) + RLS.                                         | **15432**→5432                      | `postgresql://postgres:changeme-dev-only@localhost:15432/agentic_platform` |
| `redis`            | `redis:7-alpine`                        | Sesiones, broker de Celery, contadores de rate-limit.                                                | **6379**                            | `redis-cli -h localhost -p 6379`                                           |
| `minio`            | `minio/minio:RELEASE...`                | Almacenamiento de objetos S3-compatible.                                                             | **9000** (API) / **9001** (consola) | Consola http://localhost:9001 — `minioadmin` / `changeme-dev-only`         |
| `vault`            | `hashicorp/vault:1.17`                  | Gestión de secretos (único origen de credenciales en prod).                                          | **8200**                            | http://localhost:8200 — token dev `dev-root-token`                         |
| `clamav`           | `clamav/clamav:1.4`                     | Antivirus de ficheros subidos (ingesta RAG/KBs).                                                     | **3310**                            | `clamdscan` TCP en localhost:3310 (1ª vez descarga firmas, ~120 s)         |
| `docling-serve`    | `ghcr.io/docling-project/docling-serve` | Parseo de documentos (PDF/Office/HTML/audio) → chunks.                                               | **5001**                            | http://localhost:5001/health                                               |
| `egress-proxy`     | _build_ `./egress-proxy`                | tinyproxy con **allowlist**: única salida a internet del sandbox de agentes (ADR 0019).              | **8888**                            | http://localhost:8888 (proxy)                                              |
| `ollama`           | `ollama/ollama:0.5.7`                   | **Embeddings** (KBs + memoria) y LLMs locales opcionales (ADR 0056).                                 | **11434**                           | http://localhost:11434/api/tags                                            |
| `ollama-bootstrap` | `ollama/ollama:0.5.7`                   | **One-shot**: hace `ollama pull` del modelo de embeddings y **termina** (`Exited (0)` es lo normal). | — (no escucha)                      | Ver logs: `docker logs agentic-platform-ollama-bootstrap-1`                |

> **`ollama-bootstrap` en `Exited (0)` no es un error** — es un init que descarga
> el modelo (`nomic-embed-text`) en el volumen `ollama_data` y sale. En el
> próximo `up` vuelve a correr y es un no-op si el modelo ya está. Ver
> [runbook Ollama](../06-runbooks/ollama-gpu-setup.md).

## Servicios de monitoring (`docker-compose.monitoring.yml`)

| Servicio        | Imagen                             | Qué hace                                                                   | Puerto host (dev) | Acceso (dev)                                          |
| --------------- | ---------------------------------- | -------------------------------------------------------------------------- | ----------------- | ----------------------------------------------------- |
| `prometheus`    | `prom/prometheus:v2.54.1`          | Recoge métricas + evalúa reglas de alerta.                                 | **9090**          | http://localhost:9090                                 |
| `alertmanager`  | `prom/alertmanager:v0.27.0`        | Rutea las alertas de Prometheus al notificador de la plataforma (webhook). | **9093**          | http://localhost:9093                                 |
| `grafana`       | `grafana/grafana:11.2.0`           | Dashboards (datasource + dashboards provisionados).                        | **3001**          | http://localhost:3001 — `admin` / `changeme-dev-only` |
| `node-exporter` | `prom/node-exporter:v1.8.2`        | Métricas del host (CPU/RAM/disco/red).                                     | — (no expuesto)   | Scrapeado por Prometheus dentro de la red             |
| `cadvisor`      | `gcr.io/cadvisor/cadvisor:v0.49.1` | Métricas por contenedor.                                                   | — (no expuesto)   | Scrapeado por Prometheus dentro de la red             |

> ℹ️ **Puerto 3000 = admin-panel:** Grafana usa el **3001** por defecto (dev)
> justamente para no chocar con el `admin-panel` (`npm run dev`, Next.js en 3000).
> Si prefieres otro puerto para Grafana, exporta `GRAFANA_PORT` antes del `up`.
>
> ⚠️ **node-exporter en Windows** no arranca sin el override de Windows (mount
> `rslave` de `/`). Ver [gotcha](../03-guides/gotchas/node-exporter-rslave-windows.md).

## Redes

| Red              | Tipo                | Para qué                                                                                                           |
| ---------------- | ------------------- | ------------------------------------------------------------------------------------------------------------------ |
| `agentic-net`    | bridge (con salida) | Red principal de los servicios; el `egress-proxy` es la puerta a internet.                                         |
| `agentic-agents` | bridge **internal** | Red **sin salida** donde corren los contenedores `agent-runtime`; solo alcanzan al `egress-proxy` (ADR 0012/0019). |

## Cómo conecta el api-server que corre FUERA de Docker (dev típico)

En desarrollo la `api-server` (y los workers) suelen correr **en el host**
(no en Docker), por eso el override de dev expone los puertos. La config
(`apps/api-server/src/api_server/config.py`) ya apunta a `localhost` con esos
puertos por defecto — **no necesitas `.env`** salvo que cambies puertos:

| Variable                       | Default (dev)                          | Servicio        |
| ------------------------------ | -------------------------------------- | --------------- |
| `API_SERVER_DATABASE_URL`      | `...@localhost:15432/agentic_platform` | postgres        |
| `API_SERVER_REDIS_URL`         | `redis://localhost:6379`               | redis           |
| `API_SERVER_OLLAMA_URL`        | `http://localhost:11434`               | ollama          |
| `API_SERVER_EMBEDDING_MODEL`   | `nomic-embed-text`                     | ollama (modelo) |
| `API_SERVER_DOCLING_SERVE_URL` | `http://localhost:5001`                | docling-serve   |
| `API_SERVER_MINIO_URL`         | `http://localhost:9000`                | minio           |

Dentro del stack (prod / contenedores) las mismas conexiones usan el **nombre
del servicio** como host (p. ej. `http://ollama:11434`, `http://api-server:8000`),
no `localhost`.

## Volúmenes (datos persistentes)

`postgres_data`, `redis_data`, `minio_data`, `vault_data`, `vault_logs`,
`clamav_data`, `ollama_data` (modelos de Ollama) y —con monitoring—
`prometheus_data`, `alertmanager_data`, `grafana_data`,
`node_exporter_textfile`. Borrar un volumen = empezar ese servicio de cero.

## Ver también

- [Runbook — Ollama en el stack (CPU/GPU)](../06-runbooks/ollama-gpu-setup.md)
- [Instalación (getting-started)](../02-getting-started/01-installation.md)
- [Instalación y producción (instalador)](./installation.md)
- [Gotchas de Docker/Windows](../03-guides/gotchas/README.md)
