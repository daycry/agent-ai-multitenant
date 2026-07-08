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

# prod-12 task_prod12_img_01 (sandbox-6): el worker fuerza uid 1000 en cada
# run; la imagen le da un HOME escribible para que los dep-caches (pip/
# composer/npm/...) funcionen, y arranca no-root tambien por defecto
# (defensa en profundidad, como el agent-runtime). USER numerico: no
# necesita entrada en /etc/passwd (uniforme en debian y alpine).
RUN mkdir -p /home/agent && chown -R 1000:1000 /home/agent
ENV HOME=/home/agent
USER 1000:1000

ENTRYPOINT ["sleep", "infinity"]
