# syntax=docker/dockerfile:1.7
# agent-runtime-node-playwright:v1 — test-runtime template for Playwright e2e (Plan 06 task_06_02).
#
# The official Playwright base ships the browsers (Chromium, Firefox, WebKit)
# pre-installed with their system deps — re-installing them at task start
# would add several minutes per run.

# Base pineada por DIGEST (prod-11 task_digest_pin_11, hallazgo gap5-3): un tag
# de imagen es mutable y esta es una de las imagenes donde el Principio Rector 2
# deposita el aislamiento del codigo NO confiable. El refresco lo hace Dependabot
# (ecosistema docker, .github/dependabot.yml).
# 2026-08-13: se sube de `v1.48.0-jammy` a `v1.62.1-noble`. La 1.48 es de
# octubre de 2024: su capa de Ubuntu 22.04 acumulaba 16 CVE HIGH con fix
# publicado y su Node arrastraba otras 18 en `node-pkg`. Microsoft no reconstruye
# los tags viejos de playwright, así que aquí el refresco del digest NO existe
# como opción: hay que subir de versión de Playwright, y con ella la base a
# Ubuntu 24.04 (noble).
FROM mcr.microsoft.com/playwright:v1.62.1-noble@sha256:dcc5531e97840b9b5e794f2814476b21571c5124a3fca2267d73041f56e7580e AS runtime

LABEL org.opencontainers.image.title="agent-runtime-node-playwright" \
      org.opencontainers.image.version="v1" \
      org.opencontainers.image.description="Test-runtime template: Playwright 1.62 + browsers."

ENV NODE_ENV=test \
    NPM_CONFIG_UPDATE_NOTIFIER=false \
    NPM_CONFIG_FUND=false

# La imagen de Playwright fija su propio Node (24.18) y con él npm 11.16, cuyo
# árbol empaquetado trae undici 6.26.0 → CVE-2026-12151, con fix en 6.27.0. Se
# arregla actualizando npm, porque la 12.0.2 sí empaqueta 6.27.0 (y de paso
# node-tar 7.5.19). Versión EXACTA y no `@latest`: esta capa instala software
# dentro de la imagen donde corre código NO confiable, y ahí una referencia
# móvil es justo lo que el pin por digest del `FROM` viene a evitar.
RUN npm install -g npm@12.0.2 --no-fund --no-audit \
 && npm --version

# Mismo parche que en node-jest / node-vitest, y por el mismo motivo: npm
# publica su árbol de dependencias EMPAQUETADO, así que estas TRES indirectas no
# se mueven ni actualizando npm — la 12.0.2 sigue trayendo brace-expansion 5.0.7
# (CVE-2026-14257, CVE-2026-69152), ip-address 10.2.0 (CVE-2026-69192, SSRF) y
# node-tar 7.5.19 (CVE-2026-73566, DoS al desempaquetar), añadida el 2026-08-24.
# Las tres tienen fix del autor: no es supresión, es aplicar el fix mientras npm
# reempaqueta. Rango `^` para no degradar nada si el bundle ya viene por delante.
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
