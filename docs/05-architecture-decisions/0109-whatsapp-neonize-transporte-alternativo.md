---
title: "ADR 0109: WhatsApp vía neonize (whatsmeow) como transporte alternativo a la Cloud API"
status: accepted
date: 2026-07-12
deciders: operador (petición explícita 2026-07-12), Claude
---

# ADR 0109: WhatsApp vía neonize como transporte alternativo

## Contexto

El canal WhatsApp del notification-dispatcher usa la **Cloud API de Meta**
(`channels/whatsapp.py`: Graph `POST /{version}/{phone_number_id}/messages`,
Bearer token de Business). Limitaciones operativas reales:

1. Exige cuenta WhatsApp Business + verificación de Meta + número dedicado.
2. Los mensajes business-initiated solo pueden ser **plantillas pre-aprobadas**
   por Meta (y además registradas en el catálogo hardcodeado del adapter).
3. Coste por conversación.

El operador pidió explícitamente (2026-07-12) añadir la opción **neonize**:
wrapper Python sobre **whatsmeow**, el cliente del protocolo WhatsApp Web
multi-device — self-hosted, sesión vinculada por QR a un número normal, texto
libre sin plantillas ni ventana de 24 h.

## Decisión

**Ramificar por `config.provider` dentro del canal `whatsapp` existente**
(mismo precedente que `email.py`: SMTP vs SendGrid), sin tocar el enum de
`channel_type` ni el modelo de BD:

- `provider: "cloud"` (default, backward-compat) → camino Cloud API actual.
- `provider: "neonize"` → `POST {base_url}/send` con `{"to", "text"}` contra un
  **sidecar** self-hosted, `Authorization: Bearer <secret del canal>`. El texto
  es el `message.body` ya renderizado (plantillas ES/EN del Plan 10) — sin
  registro de plantillas de Meta.

El **sidecar** es un servicio aparte (`docker/whatsapp-neonize/`) porque
neonize/whatsmeow mantiene una sesión persistente con event-loop propio y store
en disco — incompatible con el modelo stateless por-envío del dispatcher. El
contrato HTTP es deliberadamente mínimo para que cualquier bridge whatsmeow
compatible pueda sustituir la implementación de referencia:

```
POST /send   Authorization: Bearer <token>
             {"to": "<msisdn o JID>", "text": "<mensaje>"}
  → 200 {"ok": true, "id": "..."}       (enviado)
  → 401/403                              (token inválido)
  → 409 {"error": "not_paired"}          (sesión sin vincular — falta QR)
  → 5xx                                  (error del bridge → retry del dispatcher)
GET /health → 200 {"paired": true|false}
```

Config del canal (JSONB no-secreto): `provider: "neonize"`, `base_url`
(opcional; default `whatsapp_neonize_base_url` del dispatcher), `to` /
`target` = destinatario. Secreto del canal = token Bearer del sidecar
(Fernet, como el resto). Tunables: `whatsapp_neonize_base_url`,
`whatsapp_neonize_request_timeout_s`.

El sidecar corre bajo un **profile de compose** (`neonize`) apagado por
defecto: exige vinculación QR interactiva del operador (runbook) y un volumen
de sesión persistente.

## Consecuencias

- (+) Texto libre: esquiva de raíz la restricción de plantillas (y el
  WHATSAPP-BUG-1 de params vacíos ya corregido en NOTIF-1).
- (+) Sin Business API, sin coste por conversación, número normal.
- (+) Cero migración: mismas filas `notification_channels`, mismo canal en la UI
  (el editor genérico de config JSON + secreto ya sirve).
- (−) whatsmeow es un cliente NO oficial del protocolo: Meta puede banear el
  número. Uso recomendado: avisos operativos internos, no mensajería a
  clientes. Documentado en el runbook.
- (−) La sesión QR es estado operativo (volumen + re-vinculación ocasional).
- La implementación de referencia del sidecar se marca **experimental**: el
  adapter y su contrato están cubiertos por tests (MockTransport); el
  emparejamiento real solo puede validarlo el operador con un número.

## Alternativas descartadas

- **Nuevo `channel_type: whatsapp_neonize`**: aparecería como transporte
  separado en la UI, pero duplica preferencias/plantillas y obliga a migrar
  canales existentes para cambiar de transporte. La ramificación por provider
  permite cambiar Cloud↔neonize editando el canal.
- **Integrar neonize in-process en el dispatcher**: sesión persistente +
  event-loop propio dentro de un worker Celery stateless — frágil y acopla el
  ciclo de vida del dispatcher al de la sesión de WhatsApp.
