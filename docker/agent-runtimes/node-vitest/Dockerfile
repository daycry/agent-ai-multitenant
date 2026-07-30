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

# prod-12 task_prod12_img_01 (sandbox-6): el worker fuerza uid 1000 en cada
# run; la imagen le da un HOME escribible para que los dep-caches (pip/
# composer/npm/...) funcionen, y arranca no-root tambien por defecto
# (defensa en profundidad, como el agent-runtime). USER numerico: no
# necesita entrada en /etc/passwd (uniforme en debian y alpine).
RUN mkdir -p /home/agent && chown -R 1000:1000 /home/agent
ENV HOME=/home/agent
USER 1000:1000

ENTRYPOINT ["sleep", "infinity"]
