# syntax=docker/dockerfile:1.7
# agent-runtime-python-pytest:v1 — test-runtime template for Python projects (Plan 06 task_06_02).
#
# Minimal image: Python + pytest. The project's own deps (requirements.txt /
# pyproject.toml) are installed at task start by the worker via the template's
# default_pre_install commands; the dep-cache mount at /root/.cache/pip keeps
# them warm across runs.

FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="agent-runtime-python-pytest" \
      org.opencontainers.image.version="v1" \
      org.opencontainers.image.description="Test-runtime template: Python 3.12 + pytest."

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=0

# pytest is the floor. Anything richer (pytest-cov, pytest-xdist, plugins)
# is installed from the project's own requirements at task start.
RUN pip install --no-cache-dir "pytest>=8,<9" "pytest-junitxml>=1.0"

# Worktree mount point. Matches RuntimeTemplate.workspace_mount_path.
WORKDIR /workspace

# The worker drives the container with `docker exec`; the entrypoint is
# just a sleep so the container stays up until the worker tears it down.
ENTRYPOINT ["sleep", "infinity"]
