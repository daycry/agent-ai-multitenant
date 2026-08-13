# syntax=docker/dockerfile:1.7
# agent-runtime-node-vitest:v1 — test-runtime template for Node + Vitest (Plan 06 task_06_02).

# Base pineada por DIGEST (prod-11 task_digest_pin_11, hallazgo gap5-3): un tag
# de imagen es mutable y esta es una de las imagenes donde el Principio Rector 2
# deposita el aislamiento del codigo NO confiable. El refresco lo hace Dependabot
# (ecosistema docker, .github/dependabot.yml).
# 2026-08-13: se sube de `node:20-slim` a `node:26-slim` — mismo motivo que en
# node-jest: Node 20 salió de soporte (libgnutls30 con dos CRITICAL sin parchear
# en su base) y el npm de la línea 24 aún empaqueta `node-tar` 7.5.16
# (CVE-2026-59873, CRITICAL). La 26 trae npm 11.19 con `node-tar` 7.5.19.
FROM node:26-slim@sha256:4ebb5ace66f15a24c14c492e01a8beeed4fddf970a856109f5126e703e5fe503 AS runtime

LABEL org.opencontainers.image.title="agent-runtime-node-vitest" \
      org.opencontainers.image.version="v1" \
      org.opencontainers.image.description="Test-runtime template: Node 26 + Vitest."

ENV NODE_ENV=test \
    NPM_CONFIG_UPDATE_NOTIFIER=false \
    NPM_CONFIG_FUND=false

# npm publica su árbol de dependencias EMPAQUETADO (`bundleDependencies`), así
# que estas dos indirectas no se pueden actualizar con un `npm install -g npm@…`:
# la 12.0.2 —la última publicada el 2026-08-13— sigue trayendo brace-expansion
# 5.0.7 (CVE-2026-14257 y CVE-2026-69152) e ip-address 10.2.0 (CVE-2026-69192,
# SSRF). Las tres tienen fix publicado por sus autores, así que NO caen bajo
# `ignore-unfixed` ni son candidatas a `.trivyignore`: lo que falta es que npm
# reempaquete. Hasta entonces se sustituye el paquete dentro del bundle, que es
# aplicar el arreglo, no silenciarlo. Rango `^` a propósito: si npm acaba
# empaquetando una versión mayor, esto no la degrada.
#
# Retirar esta capa cuando `npm view npm dependencies` (transitivas incluidas)
# traiga brace-expansion >=5.0.9 e ip-address >=10.3.1.
RUN set -eux; \
    bundle="$(npm root -g)/npm/node_modules"; \
    for spec in 'brace-expansion@^5.0.9' 'ip-address@^10.3.1'; do \
      name="${spec%@*}"; \
      tmp="$(mktemp -d)"; \
      npm pack "$spec" --pack-destination "$tmp" --silent >/dev/null; \
      tar -xzf "$tmp"/*.tgz -C "$tmp"; \
      rm -rf "${bundle:?}/$name"; \
      mv "$tmp/package" "$bundle/$name"; \
      rm -rf "$tmp"; \
    done; \
    npm --version

# Vitest 1 no vale sólo por antigüedad: empaqueta el binario de esbuild, y el de
# esa rama está compilado con la stdlib de Go 1.22 — Trivy lo lee como
# `gobinary` y le cuenta 18 CVE HIGH/CRITICAL (CVE-2025-68121 entre ellas). Al
# no ser un paquete que se pueda actualizar suelto, la vía es la versión de
# vitest que trae un esbuild recompilado.
RUN npm install -g vitest@4 --omit=dev

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
