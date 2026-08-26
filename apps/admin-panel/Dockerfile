# syntax=docker/dockerfile:1.7
# admin-panel — Next.js 15 (standalone output) System Admin + tenant UI.
#
# Plan prod-01 task_02 (finding deploy-2). Self-contained build context
# (apps/admin-panel — the frontend imports none of the Python packages), so it
# builds with context = the app dir (unlike the backend images which need the
# repo root). next.config.js already sets output:'standalone', so the runner
# stage ships only the minimal server bundle.
#
#   docker build -f apps/admin-panel/Dockerfile -t agentic-platform/admin-panel:ci apps/admin-panel

# ---- deps: install node_modules from the lockfile ----
# Base pineada por DIGEST y en Node 26, como el resto de imágenes de Node del
# repo (installer, node-jest, node-vitest). Era la ÚNICA que seguía en
# `node:20-slim` SIN pin, y las dos mitades importan: Node 20 salió de soporte
# y su base dejó de recibir los parches de Debian —el Dockerfile de node-jest
# lo dejó escrito el 2026-08-13, con libgnutls30 arrastrando dos CRITICAL—, y
# un tag sin digest es móvil, que es justo lo que prod-11 vino a cerrar.
#
# Se sube al hilo del bump de `@types/node` 20 -> 26 (PR #96): subir los tipos
# por encima del intérprete real es una incoherencia que `tsc` no delata,
# porque valida contra la declaración de tipos y no contra el runtime.
FROM node:26-slim@sha256:4ebb5ace66f15a24c14c492e01a8beeed4fddf970a856109f5126e703e5fe503 AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# ---- builder: compile the standalone server bundle ----
FROM node:26-slim@sha256:4ebb5ace66f15a24c14c492e01a8beeed4fddf970a856109f5126e703e5fe503 AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# Next bakes NEXT_PUBLIC_* at BUILD time. Injectable per environment; when unset
# the app falls back to its own default (finding frontend-8 — prod-09 makes the
# fallback fail-loud). public/ may not exist; ensure it does so the runner COPY
# is robust.
ARG NEXT_PUBLIC_API_URL=""
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL} \
    NEXT_TELEMETRY_DISABLED=1
RUN mkdir -p public && npm run build

# ---- runner: minimal standalone server, non-root ----
FROM node:26-slim@sha256:4ebb5ace66f15a24c14c492e01a8beeed4fddf970a856109f5126e703e5fe503 AS runner
LABEL org.opencontainers.image.title="admin-panel" \
      org.opencontainers.image.description="agentic-platform Next.js admin/tenant UI." \
      org.opencontainers.image.source="https://github.com/daycry/agent-ai-multitenant"
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
WORKDIR /app
# node:20-slim already ships a non-root `node` user at uid/gid 1000 — reuse it
# (do NOT groupadd/useradd 1000, that GID is taken).
# Standalone output: a self-contained server.js + minimal node_modules, plus the
# static assets and public/ that the server expects alongside it.
COPY --from=builder --chown=1000:1000 /app/.next/standalone ./
COPY --from=builder --chown=1000:1000 /app/.next/static ./.next/static
COPY --from=builder --chown=1000:1000 /app/public ./public
USER 1000:1000
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD node -e "require('http').get('http://localhost:3000/',r=>process.exit(r.statusCode<500?0:1)).on('error',()=>process.exit(1))"
CMD ["node", "server.js"]
