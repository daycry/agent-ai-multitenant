---
title: "Runbook: emparejar y operar el sidecar WhatsApp neonize"
status: active
date: 2026-07-12
related: ADR 0109
---

# WhatsApp vía neonize — emparejamiento y operación

El transporte `provider: "neonize"` del canal WhatsApp (ADR 0109) envía texto
libre a través de un **sidecar whatsmeow self-hosted** con una sesión de
WhatsApp Web vinculada por QR. **EXPERIMENTAL**: whatsmeow habla el protocolo
NO oficial — Meta puede banear el número. Úsalo para avisos operativos
internos, no para mensajería a clientes.

## 1. Arranque (dev)

```bash
docker build -t agentic-platform/whatsapp-neonize:dev docker/whatsapp-neonize
docker compose -p agentic-platform -f docker/docker-compose.yml \
  -f docker/docker-compose.manuals.yml --profile neonize up -d whatsapp-neonize
```

## 2. Emparejamiento inicial (una vez por sesión)

1. `docker logs -f agentic-platform-whatsapp-neonize-1` — al no haber sesión,
   neonize imprime un **QR en ASCII**.
2. En el móvil del número que enviará avisos: WhatsApp → Dispositivos
   vinculados → Vincular un dispositivo → escanear el QR de los logs.
3. Verifica: `curl http://localhost:8085/health` (desde dentro de la red:
   `whatsapp-neonize:8085`) → `{"paired": true}`.

La sesión persiste en el volumen `agentic_whatsapp_neonize_session`; borrarlo
obliga a re-emparejar.

## 3. Configurar el canal en la plataforma

En Admin → Notificaciones → Canales, crear/editar el canal `whatsapp`:

- **Config (JSON)**: `{"provider": "neonize", "to": "+34XXXXXXXXX"}`
  (`base_url` opcional si el sidecar no está en el default
  `http://whatsapp-neonize:8085`).
- **Secreto**: el valor de `NEONIZE_TOKEN` del sidecar (en dev,
  `dev-neonize-token`).

Sin `provider` (o `provider: "cloud"`) el canal sigue usando la Cloud API de
Meta como siempre.

## 4. Diagnóstico

| Síntoma                                      | Causa                                                           | Acción                                   |
| -------------------------------------------- | --------------------------------------------------------------- | ---------------------------------------- |
| `HTTP 409 (not_paired)` en notification_logs | Sesión sin vincular / caducada                                  | Repetir §2                               |
| `HTTP 503`                                   | Sidecar sin `NEONIZE_TOKEN` o neonize no instalado en la imagen | Revisar env / rebuild                    |
| `HTTP 401`                                   | El secreto del canal no coincide con `NEONIZE_TOKEN`            | Corregir el secreto del canal            |
| Transport error                              | Sidecar caído / fuera de la red                                 | `docker compose --profile neonize up -d` |
