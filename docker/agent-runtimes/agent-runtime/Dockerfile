# syntax=docker/dockerfile:1.7
# agent-runtime:v1 — the sandboxed container an agent's loop runs inside.
#
# Plan 02 Fase B (task_02_05). The image is intentionally minimal: a
# Python 3.12 interpreter, LangGraph (the agent-loop engine, wired up
# in Fase C / task_02_10) and the `agent_runtime` package. It carries
# NO platform credentials and NO Docker client — isolation is enforced
# by the worker that launches it (cap-drop ALL, read-only FS, seccomp,
# dedicated network; see apps/workers/src/workers/isolation.py).
#
# Build (matches the task_02_05 automated test):
#   docker build -t agent-runtime:v1 docker/agent-runtimes/agent-runtime/

# ---- builder: resolve dependencies into an isolated venv -----------
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

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# ---- runtime: slim image, non-root, no build tools ----------------
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="agent-runtime" \
      org.opencontainers.image.version="v1" \
      org.opencontainers.image.description="Sandboxed runtime for the agent loop (Plan 02)." \
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

COPY --from=builder /opt/venv /opt/venv
COPY --chown=1000:1000 agent_runtime/ /app/agent_runtime/

WORKDIR /app
USER 1000:1000

# Fase B: the entrypoint is a self-check (verifies deps import, prints
# a JSON banner, exits 0). The LangGraph agent loop lands in Fase C.
ENTRYPOINT ["python", "-m", "agent_runtime"]
