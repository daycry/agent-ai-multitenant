# syntax=docker/dockerfile:1.7
# agent-runtime-generic-shell:v1 — test-runtime template for arbitrary shell scripts (Plan 06 task_06_02).
#
# Used by tasks whose "tests" are a Makefile, a shell script orchestrating
# multiple stacks, or any non-canonical setup. No language toolchain
# beyond what alpine ships.

FROM alpine:3.20 AS runtime

LABEL org.opencontainers.image.title="agent-runtime-generic-shell" \
      org.opencontainers.image.version="v1" \
      org.opencontainers.image.description="Test-runtime template: alpine + bash + git + curl + jq."

RUN apk add --no-cache bash git curl jq ca-certificates make

WORKDIR /workspace

ENTRYPOINT ["sleep", "infinity"]
