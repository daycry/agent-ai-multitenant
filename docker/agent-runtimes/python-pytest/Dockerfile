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
# 2026-08-14: digest refrescado (era `57cd7c3a…`), el mismo salto que agent-runtime
# y whatsapp-neonize: `libexpat1` 2.7.1-2 acumulaba cuatro CVE HIGH con fix ya
# publicado en Debian (2.8.2-1~deb13u1). Esta plantilla pasaba Trivy en verde el
# 13-08 y dejó de pasarlo sin que cambiara una línea: es deriva de CVE, no una
# regresión nuestra, y por eso el arreglo es refrescar el pin, no relajarlo.
FROM python:3.12-slim@sha256:dd29372629eeba2dd003fd9e9d35a5b8236c44727875a0364254b5127af88e65 AS runtime

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
