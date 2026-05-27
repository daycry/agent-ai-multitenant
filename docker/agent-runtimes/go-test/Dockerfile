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
RUN go install gotest.tools/gotestsum@latest

WORKDIR /workspace

ENTRYPOINT ["sleep", "infinity"]
