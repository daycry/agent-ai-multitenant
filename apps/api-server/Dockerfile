# syntax=docker/dockerfile:1.7
# api-server — FastAPI service (auth, multi-tenant middleware, REST/WS, admin).
#
# Plan prod-01 task_01 (finding deploy-2 / quality-2): the production image for
# the api-server. It did NOT exist — the installer's compose referenced
# ghcr.io/agentic-platform/api-server with no way to build it.
#
# Build context is the **repo ROOT** (like agent-runtime) so the builder can
# COPY the local path packages the app imports (shared-domain / shared-llm /
# shared-mcp / shared-test-runtimes / shared-guardrails — they are NOT on PyPI):
#
#   docker build -f apps/api-server/Dockerfile -t agentic-platform/api-server:ci .
#
# Multi-stage: the builder compiles the native xmlsec binding (python3-saml,
# Plan 08 SAML SSO) against libxmlsec1-dev; the slim runtime keeps only the
# runtime shared libs (libxmlsec1 / libxml2) and a non-root user.

# ---- builder: install api_server + its local path deps into a venv ---------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

# build-essential + pkg-config and the xmlsec/xml/ssl/ffi headers: only the
# xmlsec binding (python3-saml) truly needs them, but they cover any dep that
# ships an sdist. They live in the builder ONLY and never reach the runtime.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential pkg-config \
      libxml2-dev libxmlsec1-dev libxmlsec1-openssl \
      libssl-dev libffi-dev \
 && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Local path packages first, in dependency order (leaf -> dependent), so each
# `shared-*` name resolves against the already-installed local wheel instead of
# being looked up on PyPI (where they do not exist).
COPY packages/shared-domain/ /src/shared-domain/
RUN pip install /src/shared-domain
COPY packages/shared-llm/ /src/shared-llm/
RUN pip install /src/shared-llm
COPY packages/shared-mcp/ /src/shared-mcp/
RUN pip install /src/shared-mcp
COPY packages/shared-test-runtimes/ /src/shared-test-runtimes/
RUN pip install /src/shared-test-runtimes
COPY packages/shared-guardrails/ /src/shared-guardrails/
RUN pip install /src/shared-guardrails

# pyproject.toml is the single source of truth for the dependency set.
WORKDIR /build
COPY apps/api-server/pyproject.toml ./
COPY apps/api-server/src/ ./src/
RUN pip install .

# ---- runtime: slim, non-root, only the runtime shared libs -----------------
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="api-server" \
      org.opencontainers.image.description="agentic-platform FastAPI api-server (auth, multi-tenant, REST/WS, admin)." \
      org.opencontainers.image.source="https://github.com/daycry/agent-ai-multitenant"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

# Runtime shared libs the compiled xmlsec binding links against (no -dev, no
# compilers). libxml2 is pulled in transitively but we name it for clarity.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libxml2 libxmlsec1 libxmlsec1-openssl \
 && rm -rf /var/lib/apt/lists/*

# Non-root user (uid/gid 1000), matching the agent-runtime convention and the
# hardened compose (--user 1000:1000).
RUN groupadd --gid 1000 app \
 && useradd --uid 1000 --gid 1000 --no-create-home --home-dir /app app \
 && mkdir -p /app \
 && chown -R 1000:1000 /app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
USER 1000:1000

EXPOSE 8000

# Liveness against the app's own /healthz (main.py). Uses stdlib urllib so the
# slim image needs no curl.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/healthz', timeout=4).status==200 else 1)"

# create_app is a no-arg FastAPI factory (api_server.main:create_app).
CMD ["uvicorn", "api_server.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
