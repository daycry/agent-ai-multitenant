# syntax=docker/dockerfile:1.7
# agent-runtime-node-jest:v1 — test-runtime template for Node + Jest (Plan 06 task_06_02).

# Base pineada por DIGEST (prod-11 task_digest_pin_11, hallazgo gap5-3): un tag
# de imagen es mutable y esta es una de las imagenes donde el Principio Rector 2
# deposita el aislamiento del codigo NO confiable. El refresco lo hace Dependabot
# (ecosistema docker, .github/dependabot.yml).
# 2026-08-13: se sube de `node:20-slim` a `node:26-slim`. Node 20 salió de
# soporte y su base dejó de recibir los parches de Debian (libgnutls30 con dos
# CRITICAL, libcap2), pero el motivo que descarta también la línea 24 —LTS
# activa, y por tanto la elección conservadora— es el npm que empaqueta: 11.17
# trae `node-tar` 7.5.16, o sea CVE-2026-59873 (CRITICAL) sin cerrar. La línea
# 26 embarca npm 11.19 con `node-tar` 7.5.19. El pin sigue siendo por digest.
FROM node:26-slim@sha256:4ebb5ace66f15a24c14c492e01a8beeed4fddf970a856109f5126e703e5fe503 AS runtime

LABEL org.opencontainers.image.title="agent-runtime-node-jest" \
      org.opencontainers.image.version="v1" \
      org.opencontainers.image.description="Test-runtime template: Node 26 + Jest."

ENV NODE_ENV=test \
    NPM_CONFIG_UPDATE_NOTIFIER=false \
    NPM_CONFIG_FUND=false

# Parches de seguridad del SO. Las bases `-slim` se reconstruyen cada pocas
# semanas, asi que entre reconstrucciones arrastran los paquetes de Debian sin
# los parches YA publicados: el 2026-08-18 el gate cayo con CVE-2026-53615
# (nueve paquetes de la familia util-linux; fix `2.41.5-0+deb13u1` disponible en
# `trixie-security` y ausente todavia de la imagen oficial).
#
# Esta capa NO se puede dejar cachear, y no es una precaucion teorica: el
# template `rust-cargo` YA tenia este mismo `apt-get -y upgrade` y aun asi
# entrego los paquetes viejos, porque buildx la sirvio del cache de GHA
# (`RUN apt-get update && apt-get -y upgrade` -> `CACHED` en el log). Por eso
# los workflows que construyen estas imagenes pasan `no-cache-filters`. Ver
# `docs/03-guides/gotchas/trivy-en-rojo-tres-causas-distintas.md`.
RUN apt-get update \
 && apt-get -y upgrade \
 && rm -rf /var/lib/apt/lists/*

# npm publica su árbol de dependencias EMPAQUETADO (`bundleDependencies`), así
# que estas TRES indirectas no se pueden actualizar con un `npm install -g npm@…`:
# la 12.0.2 —la última publicada el 2026-08-13— sigue trayendo brace-expansion
# 5.0.7 (CVE-2026-14257 y CVE-2026-69152), ip-address 10.2.0 (CVE-2026-69192,
# SSRF) y node-tar 7.5.19 (CVE-2026-73566, DoS al desempaquetar un archivo).
#
# `tar` se añade el 2026-08-24, y su historia dice algo que conviene retener: la
# cabecera de este fichero eligió `node:26-slim` PRECISAMENTE porque embarcaba
# node-tar 7.5.19 en vez de la 7.5.16 con un CRITICAL abierto. O sea que la
# versión que se eligió como la buena duró once días. Elegir la base por la
# versión que empaqueta no evita esta capa: la aplaza.
#
# Las tres tienen fix publicado por sus autores, así que NO caen bajo
# `ignore-unfixed` ni son candidatas a `.trivyignore`: lo que falta es que npm
# reempaquete. Hasta entonces se sustituye el paquete dentro del bundle, que es
# aplicar el arreglo, no silenciarlo. Rango `^` a propósito: si npm acaba
# empaquetando una versión mayor, esto no la degrada.
#
# Retirar esta capa cuando `npm view npm dependencies` (transitivas incluidas)
# traiga brace-expansion >=5.0.9, ip-address >=10.3.1 y tar >=7.5.21.
RUN set -eux; \
    bundle="$(npm root -g)/npm/node_modules"; \
    for spec in 'brace-expansion@^5.0.9' 'ip-address@^10.3.1' 'tar@^7.5.21'; do \
      name="${spec%@*}"; \
      tmp="$(mktemp -d)"; \
      npm pack "$spec" --pack-destination "$tmp" --silent >/dev/null; \
      tar -xzf "$tmp"/*.tgz -C "$tmp"; \
      rm -rf "${bundle:?}/$name"; \
      mv "$tmp/package" "$bundle/$name"; \
      rm -rf "$tmp"; \
    done; \
    npm --version

# Jest is the floor; project-specific jest plugins live in the project's
# own package.json and are installed by the worker at task start.
RUN npm install -g jest@29 --omit=dev

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
