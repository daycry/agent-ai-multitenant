# syntax=docker/dockerfile:1.7
# agent-runtime:v1 — the sandboxed container an agent's loop runs inside.
#
# Plan 02 Fase B (task_02_05) + Fase C (task_02_10). The image carries a
# Python 3.12 interpreter and the `agent_runtime` package — the LangGraph
# agent loop (perceive → … → self_review). It carries NO platform
# credentials and NO Docker client — isolation is enforced by the worker
# that launches it (cap-drop ALL, read-only FS, seccomp, dedicated
# network; see apps/workers/src/workers/isolation.py).
#
# Build context is the **repo root** so we can pull `packages/shared-llm/`
# (ADR 0021) into the builder stage. The CI step in `.github/workflows/ci.yml`
# uses:
#
#   docker build -f docker/agent-runtimes/agent-runtime/Dockerfile \
#                -t agent-runtime:v1 .

# ---- builder: install the agent_runtime package into an isolated venv ----
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

# build-essential covers any dependency that ships only an sdist; it
# lives in the builder stage only and never reaches the runtime image.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# shared-llm is a path dependency of agent-runtime (ADR 0021); install
# it first so the next `pip install .` resolves the `shared-llm` name
# from the already-installed local wheel instead of asking PyPI.
COPY packages/shared-llm/ /src/shared-llm/
RUN pip install --no-cache-dir /src/shared-llm

# pyproject.toml is the single source of truth for the dependency set.
WORKDIR /build
COPY docker/agent-runtimes/agent-runtime/pyproject.toml ./
COPY docker/agent-runtimes/agent-runtime/agent_runtime/ ./agent_runtime/
RUN pip install --no-cache-dir .

# ---- runtime: slim image, non-root, no build tools ----------------
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="agent-runtime" \
      org.opencontainers.image.version="v1" \
      org.opencontainers.image.description="Sandboxed LangGraph agent loop runtime (Plan 02)." \
      org.opencontainers.image.source="https://github.com/daycry/agent-ai-multitenant"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    HOME=/workspace

# Non-root user (uid/gid 1000). The worker also pins --user 1000:1000
# at run time; baking it in keeps a bare `docker run` safe as well.
RUN groupadd --gid 1000 agent \
 && useradd --uid 1000 --gid 1000 --no-create-home --home-dir /workspace agent \
 && mkdir -p /workspace \
 && chown -R 1000:1000 /workspace

# The venv already holds the installed agent_runtime package + its deps.
COPY --from=builder /opt/venv /opt/venv

WORKDIR /workspace
USER 1000:1000

# Fase B: the entrypoint is a dependency self-check. The LangGraph agent
# loop (agent_runtime.graph) is wired in Fase C (task_02_10).
ENTRYPOINT ["python", "-m", "agent_runtime"]
