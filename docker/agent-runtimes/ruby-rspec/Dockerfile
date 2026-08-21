# syntax=docker/dockerfile:1.7
# agent-runtime-ruby-rspec:v1 — test-runtime template for Ruby + RSpec (Plan 06 task_06_02).

# Base pineada por DIGEST (prod-11 task_digest_pin_11, hallazgo gap5-3): un tag
# de imagen es mutable y esta es una de las imagenes donde el Principio Rector 2
# deposita el aislamiento del codigo NO confiable. El refresco lo hace Dependabot
# (ecosistema docker, .github/dependabot.yml).
# 2026-08-14: digest refrescado (era `005ed5a2…`). Esta plantilla pasaba Trivy en
# verde el 13-08 y dejó de pasarlo el 14-08 sin que cambiara una línea de este
# Dockerfile: es deriva de CVE, no una regresión nuestra, y por eso el arreglo es
# refrescar el pin y no relajar el gate. Cerraba CVE-2026-6473 (HIGH) en
# `libpq-dev`/`libpq5` 17.10-0+deb13u1, con fix ya publicado por Debian en
# 17.11-0+deb13u1.
#
# El matiz que hace falta entender para no repetirlo: aquí `libpq` NO viene de la
# base —`ruby:3.3-slim` no lo trae, ni el digest viejo ni el nuevo—, lo instala el
# `apt-get` de abajo. Lo que sirvió el paquete viejo fue la CACHÉ DE CAPAS del
# workflow (`cache-from: type=gha`): en la corrida roja ese `RUN` salió `#10
# CACHED`, así que el índice de apt jamás se refrescó y se reutilizó el árbol de
# paquetes del día anterior. Mover el digest de la base invalida esa capa y todas
# las de abajo, que es exactamente lo que hace falta para que apt vuelva a
# resolver contra un índice fresco. Para ESTE caso un `apt-get upgrade` no
# habría servido de nada: con la capa entera cacheada, ese comando ni se habría
# llegado a ejecutar. (Abajo hay uno, pero es para otro CVE distinto y por otro
# motivo; está justificado ahí.)
FROM ruby:3.3-slim@sha256:649a5d3b7ca4f574a5f84a61bd79cdc7cca2bbd1f882fbe6c1eb4fdbc7454738 AS runtime

LABEL org.opencontainers.image.title="agent-runtime-ruby-rspec" \
      org.opencontainers.image.version="v1" \
      org.opencontainers.image.description="Test-runtime template: Ruby 3.3 + Bundler + RSpec."

ENV BUNDLE_PATH=/usr/local/bundle \
    BUNDLE_BIN=/usr/local/bundle/bin \
    PATH=/usr/local/bundle/bin:$PATH

# Build deps for native gems (nokogiri, pg, sqlite3, etc.). Kept here
# rather than in the project's pre_install so we don't pay apt-get on
# every task.
#
# 2026-08-18: se añade `apt-get upgrade` — y NO es una comodidad, es el único
# arreglo posible para el segundo CVE de esta tanda. CVE-2026-53615 (HIGH,
# desbordamiento de entero en `libblkid`) afecta a la familia `util-linux`
# 2.41-5, que ésa SÍ viene de la base. Refrescar el digest no lo cierra: a día
# de hoy `debian:trixie-slim` resuelve al MISMO manifiesto (`38a76d01…`) sobre
# el que está construida la `ruby:3.3-slim` pineada arriba, así que aguas
# arriba no hay nada más nuevo que coger, y el paquete corregido
# (2.41.5-0+deb13u1) vive sólo en `trixie-security`. O se aplica aquí, o el
# gate se queda rojo hasta que docker-library reconstruya la imagen oficial —
# esto es, esperando a un tercero para cerrar un HIGH que YA tiene parche.
#
# El precio, dicho en voz alta: un `upgrade` general hace el build menos
# reproducible en el tiempo. Se acepta por dos razones. La primera es que esta
# capa YA flotaba: el `install` de abajo no pinea versiones, así que cada build
# con la caché invalidada resolvía lo que hubiera ese día. La segunda, y la que
# decide: la reproducibilidad de estas imágenes no la da el Dockerfile, la da
# el ADR 0148 — lo que ejecuta el código NO confiable de un tenant es el digest
# que el pipeline publica y fija en el catálogo, idéntico en todas las
# instalaciones, resuelva apt lo que resuelva en el momento del build.
RUN apt-get update \
 && apt-get upgrade -y \
 && apt-get install -y --no-install-recommends \
      build-essential git libpq-dev libsqlite3-dev libxml2-dev libxslt1-dev \
 && rm -rf /var/lib/apt/lists/*

RUN gem install bundler rspec

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
