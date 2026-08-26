# Sidecar WhatsApp neonize/whatsmeow (ADR 0109) — implementación de referencia.
# EXPERIMENTAL: exige emparejamiento QR del operador (runbook
# docs/06-runbooks/whatsapp-neonize-pairing.md) y un volumen /data persistente.
# Base pineada por DIGEST (prod-11 task_digest_pin_11, hallazgo gap5-3): un tag
# de imagen es mutable y esta es una de las imagenes donde el Principio Rector 2
# deposita el aislamiento del codigo NO confiable. El refresco lo hace Dependabot
# (ecosistema docker, .github/dependabot.yml).
# 2026-08-14: digest refrescado (era `57cd7c3a…`) por los cuatro CVE HIGH de
# `libexpat1` 2.7.1-2, con fix en Debian (2.8.2-1~deb13u1). Este sidecar NO lo
# escanea ningún paso de Trivy —los pasos nombran api-server, installer,
# backend, agent-runtime y browser-runtime—, así que compartía la base
# vulnerable sin que ninguna guarda lo dijese. Se refresca con las otras tres
# justamente por eso: la que nadie mira es la que se queda atrás.
FROM python:3.12-slim@sha256:dd29372629eeba2dd003fd9e9d35a5b8236c44727875a0364254b5127af88e65

# Parches de seguridad del SO: la misma capa que llevan `agent-runtime` y los
# cuatro templates parcheados el 2026-08-19. Faltaba aquí por la razón que da el
# comentario de arriba — es la imagen que no escaneaba nadie, así que cuando el
# gate cayó con la familia `util-linux` de Debian 13 y se repartió el arreglo,
# ésta no estaba en la lista. Su primer escaneo (2026-08-22, al entrar en
# `ci.yml` con las otras dos de `docker/*/`) devolvió 36 HIGH, todos de esa
# familia.
#
# Refrescar el digest NO sirve, y está medido: el `python:3.12-slim` más reciente
# sigue trayendo `util-linux 2.41-5`, con el fix `2.41.5-0+deb13u1` publicado en
# `trixie-security`. El parche está en el repositorio de Debian, no en la imagen.
# Ver docs/03-guides/gotchas/trivy-en-rojo-tres-causas-distintas.md (causa 2).
# Tampoco se suprime en `.trivyignore`: eso es para vulnerabilidades CON fix que
# se decide no aplicar, y este fix está a un `apt-get upgrade` de distancia.
RUN apt-get update \
 && apt-get -y upgrade \
# `libmagic1` va DENTRO de este RUN y no en uno propio: los índices de apt los
# borra la última línea, así que un `apt-get install` posterior no encontraría
# nada que instalar. Falta desde el primer día y el síntoma no era un fallo de
# arranque sino un 503 permanente: `neonize` arrastra `python-magic`, cuyo
# import de nivel de módulo lanza «failed to find libmagic», el `_connect()` de
# app.py se lo traga por diseño («arranca degradado») y /send responde 503 para
# siempre. El contenedor se veía sano. Medido: `git log -S libmagic` sobre este
# directorio sale vacío, o sea que nunca estuvo.
 && apt-get install -y --no-install-recommends libmagic1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

RUN useradd --create-home --uid 10001 neonize \
    && mkdir -p /data && chown neonize:neonize /data
USER neonize
VOLUME ["/data"]

EXPOSE 8085
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8085"]
