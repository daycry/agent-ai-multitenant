# Temporary installer wizard UI image (Next.js standalone). Built and run ONLY
# by the bootstrap compose (docker-compose.installer.yml) and torn down after
# install completes (self-destructing installer, Plan 15 Fase A). NOT part of
# the runtime stack.
FROM node:20-slim AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci || npm install

FROM node:20-slim AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:20-slim AS runner
WORKDIR /app
ENV NODE_ENV=production \
    PORT=3100
# `output: 'standalone'` emits a self-contained server bundle.
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public
EXPOSE 3100
CMD ["node", "server.js"]
