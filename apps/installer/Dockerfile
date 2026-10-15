# Temporary installer wizard UI image (Next.js standalone). Built and run ONLY
# by the bootstrap compose (docker-compose.installer.yml) and torn down after
# install completes (self-destructing installer, Plan 15 Fase A). NOT part of
# the runtime stack.
#
# Base pineada por DIGEST, igual que las plantillas de runtime (prod-11
# task_digest_pin_11, hallazgo gap5-3): un tag es mutable, y «temporal» no
# significa inofensivo — este contenedor es el que recoge las credenciales de
# la instalación (Vault, BD, proveedores LLM), así que es justo donde una CVE
# de la base importa. El refresco lo hace Dependabot (ecosistema docker).
#
# 2026-08-14: se sube de `node:20-slim` (sin pin) a `node:26-slim`. Motivos
# medidos sobre el Trivy de la CI, no supuestos:
#   * La base era Debian 12.13 y arrastraba 6 CVE HIGH/CRITICAL CON fix ya
#     publicado en apt (libgnutls30 con dos CRITICAL —bypass de autenticación
#     por NUL en el usuario y DoS por fragmento DTLS de longitud cero— y
#     libcap2 con escalada por TOCTOU). Node 20 salió de soporte y su base dejó
#     de recibir esos parches.
#   * El npm EMPAQUETADO en la base aportaba otras 18, entre ellas
#     CVE-2026-59873 (CRITICAL, gzip bomb en node-tar 6.2.1).
# Es el mismo digest que ya usan node-jest y node-vitest, que pasan Trivy en
# verde: una sola base de Node vetada en el repo en vez de tres.
FROM node:26-slim@sha256:c0753125a3789977aefe869cbebccf70e3cfd7ea84ca48547458f02e4f1d7146 AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
# `npm ci` a secas, sin el `|| npm install` que había antes: ese fallback
# convertía un lockfile desincronizado en una resolución LIBRE de versiones —
# justo lo que el lockfile existe para impedir— y encima en silencio. Si el
# lock no cuadra, el build tiene que parar y decirlo.
RUN npm ci

FROM node:26-slim@sha256:c0753125a3789977aefe869cbebccf70e3cfd7ea84ca48547458f02e4f1d7146 AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:26-slim@sha256:c0753125a3789977aefe869cbebccf70e3cfd7ea84ca48547458f02e4f1d7146 AS runner
WORKDIR /app
ENV NODE_ENV=production \
    PORT=3100

# Dos capas de higiene sobre la imagen FINAL (las etapas `deps` y `builder` se
# descartan y no las escanea nadie):
#
# 1. `apt-get -y upgrade`: la base oficial de Node se reconstruye con retraso
#    respecto al archivo de seguridad de Debian, así que refrescar el digest no
#    basta por sí solo. Aplicar el parche que Debian YA publicó se cura solo en
#    el siguiente build, a diferencia de un pin.
# 2. Borrar npm y corepack: este contenedor arranca `node server.js` y nada
#    más — no instala nada en tiempo de ejecución. npm viaja aquí como equipaje
#    muerto, y su árbol EMPAQUETADO (`bundleDependencies`) no se puede parchear
#    con un `npm install -g npm@…`. Quitarlo no es silenciar el hallazgo: es
#    que el código vulnerable deja de estar en la imagen. De paso, un instalador
#    sin gestor de paquetes es un punto de apoyo menos si alguien llega a él.
RUN set -eux; \
    apt-get update; \
    apt-get -y upgrade; \
    rm -rf /var/lib/apt/lists/*; \
    npm_root="$(npm root -g)"; \
    rm -rf "${npm_root:?}/npm" "${npm_root:?}/corepack" \
           /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack; \
    node --version

# `output: 'standalone'` emits a self-contained server bundle.
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public
EXPOSE 3100
CMD ["node", "server.js"]
