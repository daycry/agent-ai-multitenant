# syntax=docker/dockerfile:1.7
# admin-panel — Next.js 14 (standalone output) System Admin + tenant UI.
#
# Plan prod-01 task_02 (finding deploy-2). Self-contained build context
# (apps/admin-panel — the frontend imports none of the Python packages), so it
# builds with context = the app dir (unlike the backend images which need the
# repo root). next.config.js already sets output:'standalone', so the runner
# stage ships only the minimal server bundle.
#
#   docker build -f apps/admin-panel/Dockerfile -t agentic-platform/admin-panel:ci apps/admin-panel

# ---- deps: install node_modules from the lockfile ----
FROM node:20-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# ---- builder: compile the standalone server bundle ----
FROM node:20-slim AS builder
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
FROM node:20-slim AS runner
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
