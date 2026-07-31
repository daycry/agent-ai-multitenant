# syntax=docker/dockerfile:1.7
# agent-runtime-generic-shell:v1 — test-runtime template for arbitrary shell scripts (Plan 06 task_06_02).
#
# Used by tasks whose "tests" are a Makefile, a shell script orchestrating
# multiple stacks, or any non-canonical setup. No language toolchain
# beyond what alpine ships.

# Base pineada por DIGEST (prod-11 task_digest_pin_11, hallazgo gap5-3): un tag
# de imagen es mutable y esta es una de las imagenes donde el Principio Rector 2
# deposita el aislamiento del codigo NO confiable. El refresco lo hace Dependabot
# (ecosistema docker, .github/dependabot.yml).
FROM alpine:3.20@sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc AS runtime

LABEL org.opencontainers.image.title="agent-runtime-generic-shell" \
      org.opencontainers.image.version="v1" \
      org.opencontainers.image.description="Test-runtime template: alpine + bash + git + curl + jq."

RUN apk add --no-cache bash git curl jq ca-certificates make

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
