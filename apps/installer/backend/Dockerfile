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
#
# Y falta la FINALIZACIÓN —inicializar Vault, sembrar el tenant inicial y revelar
# las credenciales una sola vez—, que por este camino está NO DISPONIBLE. El
# compose generado sí declara el one-shot `bootstrap` (bajo `profiles:`, para que
# el `up` no lo arranque), pero la imagen del api-server todavía no trae el módulo
# `api_server.bootstrap` que ejecuta: es la segunda mitad del paso 8 del ADR 0161
# y está pendiente. Aquí no se dicta ese comando a propósito — el banner del CLI
# lo dice con todas las letras al terminar, que es donde el operador lo lee.
#
# La instalación se TERMINA desde el host, con el repo clonado y el MISMO
# install.yaml: `python -m installer_backend.cli install --config install.yaml`
# reutiliza los secretos del `.env` recién escrito (no los regenera), levanta,
# inicializa Vault, siembra y revela. Runbook 01.
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
# Base pineada por DIGEST (ADR 0148 / prod-11 task_digest_pin_11). Es la ÚNICA
# imagen que un operador se descarga en el camino sin clon, y el artefacto que la
# trae se sella por digest justamente para que «lo que se baja sea lo que se
# publicó»: dejar su base en un tag mutable hacía que dos builds de la MISMA
# revisión de git dieran imágenes distintas según el día, y que un Trivy rojo en
# el job `installer` no se pudiera bisecar entre la base y una dependencia. Ya
# pasó una vez con esta familia de bases (CVE-2026-53615, 2026-08-18).
#
# Es el MISMO digest que ya usan agent-runtime, python-pytest y whatsapp-neonize:
# una sola base de Python vetada en el repo en vez de cuatro, y una que ya pasa el
# gate de HIGH/CRITICAL en esas tres. El refresco lo hace Dependabot (ecosistema
# docker, entrada `/apps/installer/backend`), sin el cual este pin sería PEOR que
# el tag que sustituye: congelaría sus CVE para siempre — cabecera de
# `.github/dependabot.yml`, condición 1 del ADR 0148. Lo vigilan las dos mitades
# de `tests/unit/test_installer_image_supply_chain.py`.
FROM python:3.12-slim@sha256:09f7da3bc104798d0afb40bc08d23ab2da20a76130cec1f2ef170848f5d85217

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

# ESTA IMAGEN CORRE COMO ROOT, Y ES A PROPÓSITO. NO LE PONGAS `USER` SIN LEER
# ESTO.
#
# Es la única de las siete publicadas que no fija usuario (api-server,
# admin-panel, orchestrator, notification-dispatcher y watchdog declaran
# `USER 1000:1000`; workers arranca como root y baja a uid 1000 con setpriv en su
# entrypoint), así que el siguiente que pase homogeneizando va a querer añadir la
# línea. Los dos motivos por los que no está:
#
#   * En el modo publicado (`generate`) este contenedor escribe el árbol de
#     arranque bajo una raíz de datos que el runbook manda crear con
#     `sudo mkdir -p /data/agent-platform` — o sea, root:root. Con
#     `USER 1000:1000` el primer `mkdir` de `RealDataTreeProvisioner` muere con
#     EACCES, y muere DENTRO de la instalación, en el host de otro.
#   * Los modos son parte del contrato: el `.env` sale 0600 y el compose 0640
#     (`config_generators`), que es lo correcto para ficheros con las
#     credenciales del stack en claro. Relajarlos no es la salida.
#
# LA CONSECUENCIA QUE HAY QUE CONOCER: el árbol queda **root:root**, y los
# comandos que quedan los teclea el operador con SU usuario. En la postura más
# común —usuario normal en el grupo `docker`, la que deja `curl get.docker.com |
# sh`— ese `docker compose up -d` no puede ni leer el compose (0640 root:root):
# `permission denied` sobre un árbol que él acaba de crear. Se resuelve con
# `sudo`, o tomando posesión del árbol una vez
# (`sudo chown -R $(id -u):$(id -g) /data/agent-platform`, que conserva los
# modos). Está escrito en la cabecera de
# `docker/bootstrap/docker-compose.generate.yml`, que es lo que el operador lee
# antes de ejecutar nada.
#
# Quien quiera poner `USER` aquí tiene que resolver ANTES la propiedad de ese
# árbol —p. ej. un `AGENT_PLATFORM_OWNER` (uid:gid) con `os.chown` en los dos
# seams reales, tras el `chmod`—, no después.
#
# En el modo wizard (`docker-compose.installer.yml` sobrescribe el entrypoint)
# root no compra nada, y ese servicio HTTP no tiene autenticación de ningún tipo:
# por eso ese compose publica en `127.0.0.1` y no en todas las interfaces.

# El CLI es el entrypoint: `docker run … generate --config …` tiene que llegar a
# `installer_backend.cli`, que es el único camino de instalación que aprovisiona
# de verdad. `--help` como CMD para que un `docker run` pelado explique qué hace
# en vez de morir con un exit 2 de argparse.
ENTRYPOINT ["python", "-m", "installer_backend.cli"]
CMD ["--help"]
