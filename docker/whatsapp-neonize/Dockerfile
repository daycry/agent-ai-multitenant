# Sidecar WhatsApp neonize/whatsmeow (ADR 0109) — implementación de referencia.
# EXPERIMENTAL: exige emparejamiento QR del operador (runbook
# docs/06-runbooks/whatsapp-neonize-pairing.md) y un volumen /data persistente.
# Base pineada por DIGEST (prod-11 task_digest_pin_11, hallazgo gap5-3): un tag
# de imagen es mutable y esta es una de las imagenes donde el Principio Rector 2
# deposita el aislamiento del codigo NO confiable. El refresco lo hace Dependabot
# (ecosistema docker, .github/dependabot.yml).
FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

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
