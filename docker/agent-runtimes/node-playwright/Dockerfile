# syntax=docker/dockerfile:1.7
# agent-runtime-node-playwright:v1 — test-runtime template for Playwright e2e (Plan 06 task_06_02).
#
# The official Playwright base ships the browsers (Chromium, Firefox, WebKit)
# pre-installed with their system deps — re-installing them at task start
# would add several minutes per run.

FROM mcr.microsoft.com/playwright:v1.48.0-jammy AS runtime

LABEL org.opencontainers.image.title="agent-runtime-node-playwright" \
      org.opencontainers.image.version="v1" \
      org.opencontainers.image.description="Test-runtime template: Playwright 1.48 + browsers."

ENV NODE_ENV=test \
    NPM_CONFIG_UPDATE_NOTIFIER=false \
    NPM_CONFIG_FUND=false

WORKDIR /workspace

ENTRYPOINT ["sleep", "infinity"]
