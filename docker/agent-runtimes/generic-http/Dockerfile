# syntax=docker/dockerfile:1.7
# agent-runtime-generic-http:v1 — test-runtime template for HTTP probes (Plan 06 task_06_02).
#
# Used to talk to a running service (the task's compose, or a staging URL
# when the project opts into network_policy="open"). Carries curl + jq +
# httpie — enough to script API smoke tests in a shell script.

FROM alpine:3.20 AS runtime

LABEL org.opencontainers.image.title="agent-runtime-generic-http" \
      org.opencontainers.image.version="v1" \
      org.opencontainers.image.description="Test-runtime template: alpine + curl + jq + httpie."

RUN apk add --no-cache bash curl jq ca-certificates py3-pip \
 && pip install --break-system-packages --no-cache-dir httpie

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
