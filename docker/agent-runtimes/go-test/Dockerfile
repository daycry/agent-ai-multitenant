# syntax=docker/dockerfile:1.7
# agent-runtime-go-test:v1 — test-runtime template for Go (Plan 06 task_06_02).

FROM golang:1.22-bookworm AS runtime

LABEL org.opencontainers.image.title="agent-runtime-go-test" \
      org.opencontainers.image.version="v1" \
      org.opencontainers.image.description="Test-runtime template: Go 1.22 + go test."

ENV GOFLAGS="-mod=mod" \
    CGO_ENABLED=0

# go test is built in. gotestsum produces structured JSON for the
# go_test_json parser; we install it eagerly so projects don't have
# to add it to their go.mod.
# Pinned: v1.13+ requires Go 1.24+, incompatible con el base 1.22-bookworm.
# v1.12.1 es la ultima que builda con Go 1.22.
RUN go install gotest.tools/gotestsum@v1.12.1

WORKDIR /workspace

ENTRYPOINT ["sleep", "infinity"]
