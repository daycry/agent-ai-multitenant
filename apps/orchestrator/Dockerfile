# syntax=docker/dockerfile:1.7
# orchestrator — consumes the task-event stream and assigns ready tasks to
# workers (DAG-aware dispatch).
#
# Plan prod-01 task_01 (finding deploy-2). orchestrator imports api_server (db,
# dispatch) + shared-*, already present in the api-server image, so it SHARES
# that image as its runtime base (no second heavy build). Build order: base
# (api-server) first — handled by the publish workflow (task_03). Context = root:
#
#   docker build -f apps/orchestrator/Dockerfile \
#     --build-arg BASE_IMAGE=agentic-platform/api-server:ci \
#     -t agentic-platform/orchestrator:ci .
ARG BASE_IMAGE=agentic-platform/api-server:ci
FROM ${BASE_IMAGE}

LABEL org.opencontainers.image.title="orchestrator" \
      org.opencontainers.image.description="agentic-platform task-event consumer + DAG dispatcher." \
      org.opencontainers.image.source="https://github.com/daycry/agent-ai-multitenant"

USER root
WORKDIR /build
COPY apps/orchestrator/pyproject.toml ./
COPY apps/orchestrator/src/ ./src/
RUN pip install --no-cache-dir .
WORKDIR /app
USER 1000:1000

# Long-running consumer loop (orchestrator.__main__:main, console script
# `orchestrator`).
CMD ["python", "-m", "orchestrator"]
