# Instalación

Guía para levantar el stack en local. Producción real llegará con el
instalador de Fase 15.

## Prerequisitos

| Componente     | Versión mínima | Cómo verificar           |
| -------------- | -------------- | ------------------------ |
| Docker Engine  | 24+            | `docker --version`       |
| Docker Compose | v2+            | `docker compose version` |
| Python         | 3.12+          | `python --version`       |
| Node.js        | 20+            | `node --version`         |
| Git            | 2.40+          | `git --version`          |

En Windows funciona con **Docker Desktop**. El host por defecto del
Postgres es 15432 (no 5432) para no chocar con un postgres local
(Laragon, etc.).

## 1. Clonar el repositorio

```bash
git clone https://github.com/daycry/agent-ai-multitenant.git
cd agent-ai-multitenant
```

## 2. Bootstrap del entorno de desarrollo Python

Crea el `.venv/`, instala las dependencias de los paquetes Python
locales (`apps/api-server`, `apps/watchdog`) en modo editable y
registra el git hook de pre-commit:

```bash
# Windows
.\scripts\dev\bootstrap.ps1

# Linux / macOS
./scripts/dev/bootstrap.sh
```

Idempotente: si ya tienes `.venv/`, lo reutiliza.

## 3. Levantar el stack Docker

```bash
docker compose \
  -f docker/docker-compose.yml \
  -f docker/docker-compose.dev.yml \
  up -d
```

Esto arranca la infraestructura: **PostgreSQL** (+pgvector), **Redis**,
**MinIO**, **Vault** (modo dev), **ClamAV**, **docling-serve** (parseo de
documentos), **egress-proxy** y **Ollama** (embeddings; un init
`ollama-bootstrap` descarga el modelo y termina — `Exited (0)` es normal).
Espera 30-60 segundos a que estén healthy (`docker compose ps`).

Para añadir la **observabilidad** (Prometheus + Alertmanager + Grafana +
cAdvisor) suma el overlay de monitoring; en **Windows** añade además el override
de Windows (necesario para `node-exporter`):

```bash
# Linux/macOS
docker compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml \
  -f docker/docker-compose.monitoring.yml -f docker/docker-compose.monitoring.dev.yml up -d

# Windows (Docker Desktop / WSL2)
docker compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml \
  -f docker/docker-compose.monitoring.yml -f docker/docker-compose.monitoring.dev.yml \
  -f docker/docker-compose.windows.yml up -d
```

**Qué hace cada contenedor, en qué puerto y cómo acceder (URLs + credenciales
dev)** está en la referencia:
[`docs/04-reference/stack-services.md`](../04-reference/stack-services.md).
GPU (CUDA) opcional: [runbook Ollama](../06-runbooks/ollama-gpu-setup.md).

Si Vault se queda en `Restarting`, mira
[`docs/03-guides/gotchas/vault-dev-mode-port-conflict.md`](../03-guides/gotchas/vault-dev-mode-port-conflict.md).

## 4. Frontend (admin-panel)

```bash
cd apps/admin-panel
npm install
npm run dev
```

→ http://localhost:3000/

## 5. Variables de entorno

Las dev-only están en `docker/.env.example`. Cópialo a `docker/.env`
y ajusta si necesitas puertos diferentes:

```bash
cp docker/.env.example docker/.env
```

En producción, los secretos vienen de **Vault** —no de `.env`— vía
el instalador de Fase 15.

## 6. Tests

Suite completa (unit + integración):

```bash
.venv/Scripts/python -m pytest tests/ -v        # Windows
.venv/bin/python -m pytest tests/ -v            # Linux / macOS
```

## Próximos pasos

- [Primer arranque](./03-first-run.md) — registrar un user, promover
  a System Admin, crear un tenant.

## Si algo falla

Antes de inventar nada, **busca primero** en
[`docs/03-guides/gotchas/`](../03-guides/gotchas/) — un catálogo de trampas
ya documentadas (puertos, RLS, asyncpg, mypy, OTEL, Docker, Windows...).
