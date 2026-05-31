# Temporary installer backend image (FastAPI). Built and run ONLY by the
# bootstrap compose (docker-compose.installer.yml); torn down after install
# completes (self-destructing installer, Plan 15 Fase A). NOT part of the
# runtime stack.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    INSTALLER_HOST=0.0.0.0 \
    INSTALLER_PORT=8080

WORKDIR /app

# Install the backend package from its own pyproject. The build context is
# apps/installer/backend/ (see docker-compose.installer.yml).
COPY pyproject.toml ./
COPY src ./src
RUN pip install .

EXPOSE 8080

# The real install bindings (docker compose, /data writes, Vault) are mounted
# into this container by the bootstrap compose in Phase B. Phase A serves the
# shell API only.
CMD ["installer-backend"]
