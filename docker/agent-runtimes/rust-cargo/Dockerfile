# syntax=docker/dockerfile:1.7
# agent-runtime-rust-cargo:v1 — test-runtime template for Rust (Plan 06 task_06_02).

# Base pineada por DIGEST (prod-11 task_digest_pin_11, hallazgo gap5-3): un tag
# de imagen es mutable y esta es una de las imagenes donde el Principio Rector 2
# deposita el aislamiento del codigo NO confiable. El refresco lo hace Dependabot
# (ecosistema docker, .github/dependabot.yml).
# 2026-08-13: se sube de `rust:1.79-slim` a `rust:1.97-slim`. El digest anterior
# era de un bookworm 12.6 (junio 2024) y acumulaba 940 CVE HIGH/CRITICAL CON fix
# en apt; además, los propios binarios de `rustc`/`cargo` de la 1.79 vendorizan
# crates vulnerables que Trivy lee como `rustbinary` (mio 0.8.10, openssl-sys
# 0.10.38, rustls-webpki 0.102.6, aws-lc-sys 0.20.1, pyo3 0.20.0) y que sólo se
# renuevan con el toolchain. De ahí que aquí no baste refrescar el digest de la
# 1.79: el arreglo es la versión de Rust.
FROM rust:1.98-slim@sha256:17d1ba895198f9934c6314ec5346a0d5115372f3243390c3d731e242f35c2f27 AS runtime

LABEL org.opencontainers.image.title="agent-runtime-rust-cargo" \
      org.opencontainers.image.version="v1" \
      org.opencontainers.image.description="Test-runtime template: Rust 1.97 + cargo + cargo-nextest."

# La base de Rust se reconstruye con retraso respecto al archivo de seguridad
# de Debian: con el digest más reciente seguían apareciendo 49 CVE HIGH de
# `linux-libc-dev` (llega con el `libc6-dev` que necesita cualquier crate con
# dependencias C) que Debian YA tiene parcheadas. Aplicar ese parche es el
# arreglo; el pin del digest, por diseño, no lo trae. Se cura solo en cada
# build, que es más de lo que da refrescar el digest a mano.
RUN apt-get update \
 && apt-get -y upgrade \
 && rm -rf /var/lib/apt/lists/*

# nextest produces structured JSON for the rust_test_json parser. Cheaper
# to install in the image than to require every project to add it.
# El pin de 0.9.85 existía porque >=0.9.86 exige rustc 1.91+; con la base en
# 1.97 esa restricción desaparece y la versión al día trae además los crates
# vendorizados sin las CVE que Trivy contaba en el binario resultante.
#
# El `rm -rf` del final NO es sólo peso (371 MB): `cargo install` deja en
# `$CARGO_HOME/registry/src` el CÓDIGO FUENTE de todo el grafo de nextest, y
# con él los `Cargo.lock` que cada crate publica en su tarball. Trivy los lee
# como manifiestos y cuenta 35 HIGH (gix, openssl-sys, rustls-webpki,
# quinn-proto, pyo3...) que describen los lockfiles de DESARROLLO de esos
# crates, no lo que se compiló en el binario. Y en tiempo de ejecución esto
# tampoco sirve de nada: el worker reapunta `CARGO_HOME` a
# `/home/agent/.cargo` (ver `cache_env` de la plantilla en el catálogo), así
# que esta caché nunca se consulta. Borrar la fuente del ruido es lo correcto
# por los dos motivos.
RUN cargo install cargo-nextest --locked --version 0.9.143 \
 && rm -rf "${CARGO_HOME}/registry" "${CARGO_HOME}/git"

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
