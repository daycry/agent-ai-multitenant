# syntax=docker/dockerfile:1.7
# watchdog — supervisa la salud de los contenedores de infraestructura y los
# reinicia con backoff exponencial; al agotarlo, avisa a un humano vía
# POST /internal/alerts/ingest (prod-08 task_prod08_watchdog_14).
#
# Comparte la imagen de la api-server como base, igual que el
# notification-dispatcher: importa `api_server.logging.configure_logging` para
# emitir logs JSON con enmascarado de PII (observability-3), así que basar en
# ella evita una segunda build pesada Y evita que este servicio sea el único del
# stack con logs de otro formato. Orden de build: primero la base. Contexto = raíz
# del repo:
#
#   docker build -f apps/watchdog/Dockerfile \
#     --build-arg BASE_IMAGE=agentic-platform/api-server:ci \
#     -t agentic-platform/watchdog:ci .
#
# NO necesita el socket Docker montado (principio rector 2 + ADR 0060): habla con
# el daemon por `DOCKER_HOST=tcp://docker-socket-proxy:2375`. La ACL del proxy le
# basta con CONTAINERS=1 (inspeccionar) + POST=1 (reiniciar).
ARG BASE_IMAGE=agentic-platform/api-server:ci
FROM ${BASE_IMAGE}

LABEL org.opencontainers.image.title="watchdog" \
      org.opencontainers.image.description="agentic-platform container health watchdog." \
      org.opencontainers.image.source="https://github.com/daycry/agent-ai-multitenant"

USER root
WORKDIR /build
COPY apps/watchdog/pyproject.toml ./
COPY apps/watchdog/src/ ./src/
RUN pip install --no-cache-dir .
WORKDIR /app
USER 1000:1000

CMD ["python", "-m", "watchdog"]
