# syntax=docker/dockerfile:1.7
# agent-runtime-go-test:v1 — test-runtime template for Go (Plan 06 task_06_02).

# Base pineada por DIGEST (prod-11 task_digest_pin_11, hallazgo gap5-3): un tag
# de imagen es mutable y esta es una de las imagenes donde el Principio Rector 2
# deposita el aislamiento del codigo NO confiable. El refresco lo hace Dependabot
# (ecosistema docker, .github/dependabot.yml).
# 2026-08-13: se sube de `golang:1.22-bookworm` a `golang:1.26-bookworm`. El
# digest anterior era de un bookworm 12.9 y arrastraba 712 CVE HIGH/CRITICAL CON
# fix publicado (curl, git, gnutls, openssl, linux-libc-dev...), pero lo que no
# se arreglaba refrescando el digest de la 1.22 es CVE-2025-68121 (CRITICAL,
# validación incorrecta de certificados en crypto/tls): el toolchain 1.22 salió
# de soporte y el fix solo existe en 1.24.13, 1.25.7 y 1.26. Toda binaria Go que
# construyamos aquí hereda esa stdlib, así que el arreglo es el toolchain.
FROM golang:1.26-bookworm@sha256:53eeac89074db483fdf0ab3be1df32bf6e47562263d2d0d6baa7f26acb4957dd AS runtime

LABEL org.opencontainers.image.title="agent-runtime-go-test" \
      org.opencontainers.image.version="v1" \
      org.opencontainers.image.description="Test-runtime template: Go 1.26 + go test."

ENV GOFLAGS="-mod=mod" \
    CGO_ENABLED=0

# go test is built in. gotestsum produces structured JSON for the
# go_test_json parser; we install it eagerly so projects don't have
# to add it to their go.mod.
#
# Por qué NO es un `go install gotest.tools/gotestsum@v1.13.0` de una línea:
# `go install pkg@version` resuelve las dependencias con las versiones del
# go.mod de gotestsum, y el de v1.13.0 fija `golang.org/x/text v0.17.0`, que
# arrastra CVE-2026-56852 (HIGH, pánico con UTF-8 inválido) con fix en 0.39.0.
# El binario publicado quedaría vulnerable por una indirecta que upstream aún no
# ha subido. Construyéndolo desde un módulo propio podemos elevar esa indirecta
# sin tocar la versión de la herramienta —que sigue pineada— ni suprimir nada.
RUN set -eux; \
    mkdir -p /tmp/gotestsum-build; \
    cd /tmp/gotestsum-build; \
    go mod init gotestsum-build; \
    go get -tool gotest.tools/gotestsum@v1.13.0; \
    go get golang.org/x/text@v0.41.0; \
    go build -o /usr/local/bin/gotestsum gotest.tools/gotestsum; \
    /usr/local/bin/gotestsum --version; \
    cd /; rm -rf /tmp/gotestsum-build; \
    go clean -modcache

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
