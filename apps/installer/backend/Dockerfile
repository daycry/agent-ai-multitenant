# Imagen del instalador de la plataforma — la SÉPTIMA que publica
# `.github/workflows/release-images.yml` (ADR 0161, decisión del operador del
# 2026-08-27).
#
# QUÉ ES ESTA IMAGEN Y QUÉ NO ES
# ------------------------------
# Es el CLI del instalador, empaquetado para que instalar NO exija clonar el
# repositorio. Su forma de uso es la del ADR 0161 (opción D con envoltorio de B):
#
#   docker run --rm -v /data/agent-platform:/data/agent-platform \
#     ghcr.io/daycry/installer:v1.0.0 generate --config /data/agent-platform/install.yaml
#   cd /data/agent-platform && docker compose up -d --wait
#   docker compose run --rm bootstrap
#
# El contenedor **GENERA, no aprovisiona**: no habla con el daemon Docker, no se
# le monta el socket y no lo necesita. Eso no es una limitación pendiente de
# resolver, es la razón por la que esta opción se eligió: montar el socket es
# acceso root efectivo al host, que es literalmente lo que rechazó el ADR 0060, y
# el socket-proxy no es una vía de escape porque su ACL deniega `VOLUMES`.
#
# Por eso el ENTRYPOINT es el CLI y no el servidor. El wizard HTTP
# (`installer-backend`, `main.py`) sigue dentro de la imagen y se alcanza
# sobrescribiendo el entrypoint —así lo hace `docker-compose.installer.yml`—,
# pero no es el modo por defecto: su propio docstring dice que es una SIMULACIÓN
# y que las credenciales que revela no abren nada.
#
# `WORKDIR /app` no es la raíz de datos: pásale al `--config` una ruta ABSOLUTA
# dentro de lo que hayas montado, o la relativa resolverá contra `/app` y el CLI
# saldrá con exit code 2 (CONFIG) sin tocar nada.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    INSTALLER_HOST=0.0.0.0 \
    INSTALLER_PORT=8080


# Parches de seguridad del SO. La base `-slim` se reconstruye con retraso
# respecto al archivo de seguridad de Debian, asi que refrescar el digest no
# basta: el 2026-08-18 el gate cayo con CVE-2026-53615 (familia util-linux,
# fix `2.41.5-0+deb13u1` ya publicado en `trixie-security` y ausente todavia
# de `python:3.12-slim`). Esta capa NO puede cachearse — ver
# `docs/03-guides/gotchas/trivy-en-rojo-tres-causas-distintas.md`.
RUN apt-get update \
 && apt-get -y upgrade \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install the backend package from its own pyproject. The build context is
# apps/installer/backend/ (see docker-compose.installer.yml y el job `installer`
# de release-images.yml).
#
# `COPY src` se lleva TODO lo que el instalador necesita en destino, y eso es
# deliberado en dos sitios donde antes se leía del árbol:
#
#   * `stack_assets/` — los auxiliares que el compose generado monta (init de
#     Postgres, config de Vault, los dos tinyproxy, seccomp, monitorización). Un
#     `copytree` de `../../docker` fallaría aquí, y fallaría tarde: en mitad de la
#     instalación. Verificado: hatchling mete los 23 ficheros en el wheel, porque
#     `[tool.hatch.build.targets.wheel] packages` incluye el árbol entero del
#     paquete, no sólo los `.py`.
#   * `platform_images.json` — el manifiesto de digests de las seis imágenes de
#     plataforma. El job de release lo REESCRIBE justo antes de este build, de
#     modo que la imagen publicada pinea exactamente las seis que se acaban de
#     publicar y no las de la release anterior.
COPY pyproject.toml ./
COPY src ./src
RUN pip install .

EXPOSE 8080

# El CLI es el entrypoint: `docker run … generate --config …` tiene que llegar a
# `installer_backend.cli`, que es el único camino de instalación que aprovisiona
# de verdad. `--help` como CMD para que un `docker run` pelado explique qué hace
# en vez de morir con un exit 2 de argparse.
ENTRYPOINT ["python", "-m", "installer_backend.cli"]
CMD ["--help"]
