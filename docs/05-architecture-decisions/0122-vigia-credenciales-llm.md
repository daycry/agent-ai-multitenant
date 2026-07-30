---
title: "ADR 0122: Vigía de credenciales LLM"
status: accepted
date: 2026-07-19
---

# ADR 0122: Vigía de credenciales LLM

Aprobada por el operador el 2026-07-19 (2ª tanda, «implementa todo»).

## Contexto

Una credencial de proveedor caducada mataba runs en silencio: dos incidentes
reales con el OAuth de `claude_sdk` («Not logged in») dejaron tareas
`blocked` sin ningún aviso hasta la inspección manual. El sistema ya tenía
las piezas — el probe de liveness por proveedor
(`api_server.llm_providers.liveness.probe_provider`, secret-free, usado por
el botón «Probar» del panel) y el event_type `provider_credential_invalid`
en el registry del dispatcher (lo emite el worker cuando un run muere por
credencial) — pero nadie sondeaba proactivamente.

## Decisión

Beat `workers.provider_watchdog` cada 30 min: sondea cada proveedor ACTIVO
(`llm_providers.is_active`) con el probe existente (secret desde Vault, nunca
en logs ni eventos) y notifica por el pipeline de notificaciones con
semántica de TRANSICIÓN, no de pasada:

- sana→caída → `provider_credential_invalid` (event_type reutilizado),
  señal de plataforma (`tenant_id=None`, como las alertas de rotación);
- caída persistente → recordatorio solo cada 6 h (`REMIND_AFTER_S`);
- caída→sana → `provider_recovered` (event_type nuevo, in_app).

Estado entre pasadas (último status + último aviso) en el Redis del worker
(TTL 7 días). El núcleo es puro con prober/notifier/state inyectados (TDD
con fakes); un probe que revienta cuenta como caída con el error como detail
y jamás rompe la pasada de los demás.

## Consecuencias

- El operador se entera de una credencial caducada en ≤30 min, antes de que
  un run la descubra muriéndose.
- Sin spam: avisos solo en transiciones + recordatorio espaciado.
- Reuso máximo (probe, event_type, patrón beat+notifier del ADR 0120):
  superficie nueva mínima.
