# syntax=docker/dockerfile:1.7
# agent-runtime-rust-cargo:v1 — test-runtime template for Rust (Plan 06 task_06_02).

# Base pineada por DIGEST (prod-11 task_digest_pin_11, hallazgo gap5-3): un tag
# de imagen es mutable y esta es una de las imagenes donde el Principio Rector 2
# deposita el aislamiento del codigo NO confiable. El refresco lo hace Dependabot
# (ecosistema docker, .github/dependabot.yml).
FROM rust:1.79-slim@sha256:073c8e7ae12d2637d1d541cb0f3f9d92bfa22c97e2e45db87a9fc6a90c9a7f22 AS runtime

LABEL org.opencontainers.image.title="agent-runtime-rust-cargo" \
      org.opencontainers.image.version="v1" \
      org.opencontainers.image.description="Test-runtime template: Rust 1.79 + cargo + cargo-nextest."

# nextest produces structured JSON for the rust_test_json parser. Cheaper
# to install in the image than to require every project to add it.
# Pinned: las versiones >=0.9.86 requieren rustc 1.91+. v0.9.85 es la
# ultima compatible con el base rust:1.79-slim (soporta rustc 1.75).
RUN cargo install cargo-nextest --locked --version 0.9.85

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
