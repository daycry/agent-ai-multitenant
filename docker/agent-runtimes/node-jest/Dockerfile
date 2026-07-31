# syntax=docker/dockerfile:1.7
# agent-runtime-node-jest:v1 — test-runtime template for Node + Jest (Plan 06 task_06_02).

# Base pineada por DIGEST (prod-11 task_digest_pin_11, hallazgo gap5-3): un tag
# de imagen es mutable y esta es una de las imagenes donde el Principio Rector 2
# deposita el aislamiento del codigo NO confiable. El refresco lo hace Dependabot
# (ecosistema docker, .github/dependabot.yml).
FROM node:20-slim@sha256:2cf067cfed83d5ea958367df9f966191a942351a2df77d6f0193e162b5febfc0 AS runtime

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

# prod-12 task_prod12_img_01 (sandbox-6): el worker fuerza uid 1000 en cada
# run; la imagen le da un HOME escribible para que los dep-caches (pip/
# composer/npm/...) funcionen, y arranca no-root tambien por defecto
# (defensa en profundidad, como el agent-runtime). USER numerico: no
# necesita entrada en /etc/passwd (uniforme en debian y alpine).
RUN mkdir -p /home/agent && chown -R 1000:1000 /home/agent
ENV HOME=/home/agent
USER 1000:1000

ENTRYPOINT ["sleep", "infinity"]
