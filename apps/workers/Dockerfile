# syntax=docker/dockerfile:1.7
# workers — Celery worker pool (agent execution, ingestion, backup, rotation,
# FX, price-sync, human-escalation, maintenance sweeps).
#
# Plan prod-01 task_01 (finding deploy-2). workers imports api_server (db,
# memorizer, ingestion) AND shared-* (ADR 0021), all of which the api-server
# image already carries in /opt/venv — so this image SHARES the api-server
# image as its runtime base instead of recompiling the heavy/native deps
# (xmlsec, etc.) a second time. Build order: the base (api-server) image MUST
# be built first — handled by the publish workflow (task_03). Context = repo root:
#
#   docker build -f apps/workers/Dockerfile \
#     --build-arg BASE_IMAGE=agentic-platform/api-server:ci \
#     -t agentic-platform/workers:ci .
ARG BASE_IMAGE=agentic-platform/api-server:ci
FROM ${BASE_IMAGE}

LABEL org.opencontainers.image.title="workers" \
      org.opencontainers.image.description="agentic-platform Celery worker pool." \
      org.opencontainers.image.source="https://github.com/daycry/agent-ai-multitenant"

# Install the workers package into the shared venv (root owns it). Any workers
# dep already present in the base is seen as satisfied; only the delta installs.
USER root
# git + openssh-client (ADR 0072): el worker ejecuta operaciones git (git_repos:
# bare repos + worktrees + fetch/push autenticado a remotos GitHub/GitLab/DevOps).
# La base python-slim no los trae.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git openssh-client \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY apps/workers/pyproject.toml ./
COPY apps/workers/src/ ./src/
RUN pip install --no-cache-dir .
WORKDIR /app
USER 1000:1000

# Celery worker. Queue routing (heavy/gpu/test/review) is refined in Fase B
# (task_06); the default worker consumes the configured queues. Beat (periodic
# tasks) runs as its own service/command, wired in the compose (Fase B).
CMD ["celery", "-A", "workers.celery_app:app", "worker", "--loglevel=INFO"]
