# syntax=docker/dockerfile:1.7
# agent-runtime-node-vitest:v1 — test-runtime template for Node + Vitest (Plan 06 task_06_02).

FROM node:20-slim AS runtime

LABEL org.opencontainers.image.title="agent-runtime-node-vitest" \
      org.opencontainers.image.version="v1" \
      org.opencontainers.image.description="Test-runtime template: Node 20 + Vitest."

ENV NODE_ENV=test \
    NPM_CONFIG_UPDATE_NOTIFIER=false \
    NPM_CONFIG_FUND=false

RUN npm install -g vitest@1 --omit=dev

WORKDIR /workspace

ENTRYPOINT ["sleep", "infinity"]
