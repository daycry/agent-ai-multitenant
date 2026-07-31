# syntax=docker/dockerfile:1.7
# agent-runtime-python-pytest:v1 — test-runtime template for Python projects (Plan 06 task_06_02).
#
# Minimal image: Python + pytest. The project's own deps (requirements.txt /
# pyproject.toml) are installed at task start by the worker via the template's
# default_pre_install commands; the dep-cache mount at /root/.cache/pip keeps
# them warm across runs.

# Base pineada por DIGEST (prod-11 task_digest_pin_11, hallazgo gap5-3): un tag
# de imagen es mutable y esta es una de las imagenes donde el Principio Rector 2
# deposita el aislamiento del codigo NO confiable. El refresco lo hace Dependabot
# (ecosistema docker, .github/dependabot.yml).
FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de AS runtime

LABEL org.opencontainers.image.title="agent-runtime-python-pytest" \
      org.opencontainers.image.version="v1" \
      org.opencontainers.image.description="Test-runtime template: Python 3.12 + pytest."

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=0

# pytest is the floor. Anything richer (pytest-cov, pytest-xdist, plugins)
# is installed from the project's own requirements at task start.
# Note: el flag `--junitxml=output.xml` esta built-in en pytest desde la 5.x —
# no hace falta instalar un paquete aparte (el viejo `pytest-junitxml` ni
# siquiera existe ya en PyPI, fue lo que rompia el build).
RUN pip install --no-cache-dir "pytest>=8,<9"

# Worktree mount point. Matches RuntimeTemplate.workspace_mount_path.
WORKDIR /workspace

# The worker drives the container with `docker exec`; the entrypoint is
# just a sleep so the container stays up until the worker tears it down.
# prod-12 task_prod12_img_01 (sandbox-6): el worker fuerza uid 1000 en cada
# run; la imagen le da un HOME escribible para que los dep-caches (pip/
# composer/npm/...) funcionen, y arranca no-root tambien por defecto
# (defensa en profundidad, como el agent-runtime). USER numerico: no
# necesita entrada en /etc/passwd (uniforme en debian y alpine).
RUN mkdir -p /home/agent && chown -R 1000:1000 /home/agent
ENV HOME=/home/agent
USER 1000:1000

ENTRYPOINT ["sleep", "infinity"]
