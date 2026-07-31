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
# Base pineada por DIGEST (prod-11 task_digest_pin_11, hallazgo gap5-3): un tag
# de imagen es mutable y esta es una de las imagenes donde el Principio Rector 2
# deposita el aislamiento del codigo NO confiable. El refresco lo hace Dependabot
# (ecosistema docker, .github/dependabot.yml).
FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de AS builder

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

# Resolución REPRODUCIBLE (prod-11 task_ci_lock_10 b, ADR 0147). Sin `-c`, cada
# build de esta imagen resuelve las transitivas por su cuenta y dos builds del
# MISMO commit dan dos imágenes distintas — justo en el contenedor donde corre
# el bucle del agente. constraints.txt se genera desde uv.lock; el contexto de
# build de esta imagen es la RAÍZ del repo (ver cabecera), así que está a mano.
COPY constraints.txt /constraints.txt

# shared-llm is a path dependency of agent-runtime (ADR 0021); install
# it first so the next `pip install .` resolves the `shared-llm` name
# from the already-installed local wheel instead of asking PyPI.
#
# WITH_CLAUDE (ADR 0064): Claude Agent SDK is an OPTIONAL extra so non-Claude
# deployments stay lean (no Node CLI). Build with `--build-arg WITH_CLAUDE=1`
# to install `shared-llm[claude]` (the Python SDK) — the matching Claude Code
# CLI (Node) is added to the runtime stage below. Both are needed to run a
# `claude_sdk` agent; the platform credential-wiring (ADR 0064) is independent.
ARG WITH_CLAUDE=0
COPY packages/shared-llm/ /src/shared-llm/
RUN if [ "$WITH_CLAUDE" = "1" ]; then \
        pip install --no-cache-dir -c /constraints.txt "/src/shared-llm[claude]"; \
    else \
        pip install --no-cache-dir -c /constraints.txt /src/shared-llm; \
    fi

# shared-mcp is a path dependency of agent-runtime since Plan 05
# task_05_03 (MCP tools adapter). Same trick as shared-llm: install
# from the local wheel before agent-runtime so its name resolves
# against the already-installed package rather than failing because
# it doesn't exist on PyPI.
COPY packages/shared-mcp/ /src/shared-mcp/
RUN pip install --no-cache-dir -c /constraints.txt /src/shared-mcp

# shared-domain is a path dependency of agent-runtime since Plan 06.18
# (ADR 0048: canonical tool-name source of truth used by the approval gate).
# Same trick as shared-llm/shared-mcp: install from the local wheel before
# agent-runtime so its name resolves locally rather than failing on PyPI.
COPY packages/shared-domain/ /src/shared-domain/
RUN pip install --no-cache-dir -c /constraints.txt /src/shared-domain

# shared-guardrails is a path dependency of agent-runtime since ADR 0102 (g1):
# the runtime runs the guardrail pipeline (post_tool prompt-injection defense)
# over tool outputs before they re-enter the model context. Same local-wheel
# trick so its name resolves locally rather than failing on PyPI.
COPY packages/shared-guardrails/ /src/shared-guardrails/
RUN pip install --no-cache-dir -c /constraints.txt /src/shared-guardrails

# pyproject.toml is the single source of truth for the dependency set.
WORKDIR /build
COPY docker/agent-runtimes/agent-runtime/pyproject.toml ./
COPY docker/agent-runtimes/agent-runtime/agent_runtime/ ./agent_runtime/
RUN pip install --no-cache-dir -c /constraints.txt .

# ---- runtime: slim image, non-root, no build tools ----------------
FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de AS runtime

LABEL org.opencontainers.image.title="agent-runtime" \
      org.opencontainers.image.version="v1" \
      org.opencontainers.image.description="Sandboxed LangGraph agent loop runtime (Plan 02)." \
      org.opencontainers.image.source="https://github.com/daycry/agent-ai-multitenant"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    HOME=/home/agent

# Non-root user (uid/gid 1000). The worker also pins --user 1000:1000 at run
# time; baking it in keeps a bare `docker run` safe as well. HOME is /home/agent
# (its own dir / a dedicated tmpfs at run time, isolation.py) and NOT /workspace,
# so the Claude Code CLI's config (.claude.json/.claude/) never pollutes the
# agent's project worktree mounted at /workspace.
RUN groupadd --gid 1000 agent \
 && useradd --uid 1000 --gid 1000 --no-create-home --home-dir /home/agent agent \
 && mkdir -p /workspace /home/agent \
 && chown -R 1000:1000 /workspace /home/agent

# git + openssh-client (ADR 0072): los agentes commitean en los worktrees y las
# operaciones git contra remotos pueden requerir ssh. La base no los trae.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git openssh-client \
 && rm -rf /var/lib/apt/lists/*

# The venv already holds the installed agent_runtime package + its deps.
COPY --from=builder /opt/venv /opt/venv

# WITH_CLAUDE (ADR 0064): the Claude Agent SDK (installed in the builder via the
# `[claude]` extra) spawns the Claude Code CLI (`claude`, a Node binary) at
# query time. Install Node + the CLI ONLY when Claude is enabled, so the
# default image stays lean (no Node). Done as root before dropping to uid 1000.
ARG WITH_CLAUDE=0
RUN if [ "$WITH_CLAUDE" = "1" ]; then \
        apt-get update \
     && apt-get install -y --no-install-recommends nodejs npm \
     && npm install -g @anthropic-ai/claude-code \
     && rm -rf /var/lib/apt/lists/* /root/.npm; \
    fi

WORKDIR /workspace
USER 1000:1000

# Fase B: the entrypoint is a dependency self-check. The LangGraph agent
# loop (agent_runtime.graph) is wired in Fase C (task_02_10).
ENTRYPOINT ["python", "-m", "agent_runtime"]
