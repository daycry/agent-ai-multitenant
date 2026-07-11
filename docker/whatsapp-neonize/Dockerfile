# Sidecar WhatsApp neonize/whatsmeow (ADR 0109) — implementación de referencia.
# EXPERIMENTAL: exige emparejamiento QR del operador (runbook
# docs/06-runbooks/whatsapp-neonize-pairing.md) y un volumen /data persistente.
FROM python:3.12-slim

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
