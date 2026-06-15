# syntax=docker/dockerfile:1.7
# notification-dispatcher — Celery worker that delivers notifications across
# channels (email, webhook, in-app) from platform events.
#
# Plan prod-01 task_01 (finding deploy-2). It reads api_server.db.notification
# models + shares the platform deps, so it SHARES the api-server image as its
# runtime base (no second heavy build). Build order: base (api-server) first —
# handled by the publish workflow (task_03). Context = repo root:
#
#   docker build -f apps/notification-dispatcher/Dockerfile \
#     --build-arg BASE_IMAGE=agentic-platform/api-server:ci \
#     -t agentic-platform/notification-dispatcher:ci .
ARG BASE_IMAGE=agentic-platform/api-server:ci
FROM ${BASE_IMAGE}

LABEL org.opencontainers.image.title="notification-dispatcher" \
      org.opencontainers.image.description="agentic-platform notification delivery Celery worker." \
      org.opencontainers.image.source="https://github.com/daycry/agent-ai-multitenant"

USER root
WORKDIR /build
COPY apps/notification-dispatcher/pyproject.toml ./
COPY apps/notification-dispatcher/src/ ./src/
RUN pip install --no-cache-dir .
WORKDIR /app
USER 1000:1000

CMD ["celery", "-A", "notification_dispatcher.celery_app:app", "worker", "--loglevel=INFO"]
