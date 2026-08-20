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
# 2026-08-14: digest refrescado (era `53eeac89…`), sin cambiar de tag. Ese
# digest traía el toolchain **1.26.5** y con él dos CVE HIGH de la stdlib con fix
# ya publicado en 1.26.6: CVE-2026-39821 (`golang.org/x/net/idna`, escalada de
# privilegios por procesado incorrecto de etiquetas Punycode) y CVE-2026-46600
# (`golang.org/x/net/dns/dnsmessage`, DoS al parsear registros DNS inválidos).
# Trivy los reportaba en LOS DOCE gobinary de la imagen —`/usr/local/go/bin/go`,
# `gofmt`, las siete herramientas de `pkg/tool/linux_amd64/` y nuestro
# `/usr/local/bin/gotestsum`—, porque todos enlazan la misma stdlib. Por eso el
# arreglo es UNO: el gotestsum de abajo se recompila con el toolchain nuevo en el
# mismo build y sale limpio sin tocar su pin de versión.
# Esta plantilla pasaba Trivy en verde el 13-08 y dejó de pasarlo sin que
# cambiara una línea de este fichero: es deriva de CVE —Go 1.26.6 se publicó
# entremedias— y no una regresión nuestra, y por eso el arreglo es refrescar el
# pin, no relajarlo.
FROM golang:1.26-bookworm@sha256:116d58cbd88c1297624acc6e967a060012422bacf9930927e23fb719189c6f36 AS runtime

LABEL org.opencontainers.image.title="agent-runtime-go-test" \
      org.opencontainers.image.version="v1" \
      org.opencontainers.image.description="Test-runtime template: Go 1.26 + go test."

ENV GOFLAGS="-mod=mod" \
    CGO_ENABLED=0

# Actualizacion de seguridad ACOTADA a un solo paquete, y por que existe.
#
# `libaprutil1` no lo instalamos nosotros: entra en la base porque
# `buildpack-deps` trae `subversion` (`libsvn1` -> `libserf-1-1` -> `libaprutil1`).
# El 2026-08-18 arrastra CVE-2026-34191 (HIGH, inyeccion SQL en
# `apr_dbd_oracle`), instalado 1.6.3-1 y **con fix ya publicado** en
# bookworm-security (1.6.3-1+deb12u1). Aqui refrescar el digest NO sirve: el
# digest mas reciente de `golang:1.26-bookworm` sigue trayendo 1.6.3-1 porque la
# imagen upstream no se ha reconstruido desde que Debian publico el parche.
#
# Se descartaron dos alternativas:
#  - Subir a `golang:1.26-trixie`: se probo y sale PEOR (9 HIGH de util-linux,
#    CVE-2026-53615, que Debian 13 aun no ha parcheado en su imagen).
#  - Fijar la version exacta con `=1.6.3-1+deb12u1`: romperia el build en cuanto
#    Debian la supersedie, y volveria a congelar la CVE siguiente, justo el
#    anti-patron que el ADR 0148 nombra como riesgo.
#
# Que cuesta: `--only-upgrade` de UN paquete nombrado flota a la version de
# seguridad vigente, asi que este build no es byte a byte reproducible. Es un
# `upgrade` de un solo paquete, no general: la base sigue pineada por digest y
# ningun otro paquete se mueve. Cuando upstream reconstruya la imagen con el
# parche dentro, esta capa pasa a ser un no-op y se puede retirar.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends --only-upgrade libaprutil1; \
    rm -rf /var/lib/apt/lists/*

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
#
# 2026-08-20: la misma maniobra, para una indirecta más. `golang.org/x/mod`
# v0.38.0 arrastra CVE-2026-56864 y CVE-2026-56865 (las dos HIGH: un GOSUMDB
# malicioso podía servir contenido de módulo arbitrario), con fix en 0.40.0.
# Trivy las localizó **sólo en `/usr/local/bin/gotestsum`** —los doce gobinary
# del toolchain salen a 0—, así que NO es deriva de la imagen base y refrescar su
# digest no habría servido: se comprobó escaneando los dos que ni el 1.26.6
# pineado ni el 1.26.7 más reciente traen esas CVE. La causa es la indirecta del
# módulo, y el arreglo va donde está la causa.
RUN set -eux; \
    mkdir -p /tmp/gotestsum-build; \
    cd /tmp/gotestsum-build; \
    go mod init gotestsum-build; \
    go get -tool gotest.tools/gotestsum@v1.13.0; \
    go get golang.org/x/text@v0.41.0; \
    go get golang.org/x/mod@v0.40.0; \
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
