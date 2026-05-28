# syntax=docker/dockerfile:1.7
# agent-runtime-node-jest:v1 — test-runtime template for Node + Jest (Plan 06 task_06_02).

FROM node:20-slim AS runtime

LABEL org.opencontainers.image.title="agent-runtime-node-jest" \
      org.opencontainers.image.version="v1" \
      org.opencontainers.image.description="Test-runtime template: Node 20 + Jest."

ENV NODE_ENV=test \
    NPM_CONFIG_UPDATE_NOTIFIER=false \
    NPM_CONFIG_FUND=false

# Jest is the floor; project-specific jest plugins live in the project's
# own package.json and are installed by the worker at task start.
RUN npm install -g jest@29 --omit=dev

WORKDIR /workspace

ENTRYPOINT ["sleep", "infinity"]
