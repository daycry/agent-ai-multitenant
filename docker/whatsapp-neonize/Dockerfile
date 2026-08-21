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
