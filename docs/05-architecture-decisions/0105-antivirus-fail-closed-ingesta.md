---
id: 0105
title: Antivirus fail-closed en la ingesta de documentos
status: accepted
date: 2026-07-08
deciders: [operador (delegación 2026-06-17: implementar eligiendo la mejor opción), claude]
related: [prod-12-hardening-tools-agentes (task_prod12_av_01), auditoría api-1]
---

# ADR 0105 — Antivirus fail-closed en la ingesta

## Contexto

La auditoría de producción (api-1, medium) encontró que el pipeline de ingesta
era **fail-open** ante un backend antivirus caído: con `AntivirusVerdict.ERROR`
(clamd inalcanzable o timeout) el documento se **indexaba igualmente** con un
warning en logs. Un atacante que tumbe/space ClamAV colaría documentos sin
escanear al RAG; un fallo operativo ordinario (contenedor clamav caído) tenía
el mismo efecto en silencio.

## Decisión

**Fail-closed por defecto**, con setting de plataforma:

- `WORKERS_AV_FAILURE_MODE` (`Settings.av_failure_mode`), valores
  `fail_closed` (default) | `fail_open`.
- **fail_closed**: ante `ERROR`, el documento queda en el estado nuevo
  **`pending_scan`** (CHECK ampliada, migración 0106), NO se indexa y no
  aparece en RAG. El sweep de pendientes existente
  (`workers.sweep_pending_documents`, cada 2 min con lease) re-encola también
  `pending_scan`, así el reescaneo es automático al volver el backend — sin
  intervención humana.
- **fail_open**: comportamiento anterior (indexar + warning). Solo aceptable
  en dev/sandbox; el default de producción es fail_closed.
- **Señal temprana al operador**: la racha de indisponibilidad se registra en
  Redis (`ingestion:av_down_since`); pasados **15 min** se emite UNA
  notificación `antivirus_unreachable` vía notification-dispatcher (in_app +
  telegram; re-aviso como mucho cada 6 h). La regla de alerta formal
  (Prometheus/Alertmanager) pertenece a prod-08 — esto es la señal que ese
  plan cablea.

## Alternativas consideradas

- **(b) fail-open con warning (status quo)**: rechazada — deja la decisión de
  seguridad en manos de quien lee logs; incumple el principio deny-by-default
  del resto de la plataforma.
- **fail-closed sin estado nuevo (reusar `pending`)**: rechazada — perdería la
  distinción operativa («a la espera del antivirus» vs «aún no procesado») que
  la UI y el runbook necesitan para diagnosticar.

## Consecuencias

- Si ClamAV queda caído mucho tiempo, la cola `pending_scan` crece: mitigado
  con la notificación temprana; la métrica de profundidad y su retención se
  definen en prod-13/prod-08 (coordinación anotada en el plan prod-12).
- El downgrade de la migración 0106 normaliza `pending_scan`→`pending`
  (reversible sin pérdida: el sweep legacy re-encola `pending`).
