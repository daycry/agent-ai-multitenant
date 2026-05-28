# syntax=docker/dockerfile:1.7
# agent-runtime-rust-cargo:v1 — test-runtime template for Rust (Plan 06 task_06_02).

FROM rust:1.79-slim AS runtime

LABEL org.opencontainers.image.title="agent-runtime-rust-cargo" \
      org.opencontainers.image.version="v1" \
      org.opencontainers.image.description="Test-runtime template: Rust 1.79 + cargo + cargo-nextest."

# nextest produces structured JSON for the rust_test_json parser. Cheaper
# to install in the image than to require every project to add it.
# Pinned: las versiones >=0.9.86 requieren rustc 1.91+. v0.9.85 es la
# ultima compatible con el base rust:1.79-slim (soporta rustc 1.75).
RUN cargo install cargo-nextest --locked --version 0.9.85

WORKDIR /workspace

ENTRYPOINT ["sleep", "infinity"]
