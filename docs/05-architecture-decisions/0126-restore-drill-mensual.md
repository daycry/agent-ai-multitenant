---
title: "ADR 0126: Restore-drill mensual del backup"
status: accepted
date: 2026-07-19
---

# ADR 0126: Restore-drill mensual

Aprobada por el operador el 2026-07-19 (2ª tanda, «implementa todo»).

## Contexto

El backup diario existe y tiene verificación estructural post-backup
(pg_restore --list, tar -tf, checksums — Plan 12), pero nadie prueba
NUNCA un restore completo. Un backup no restaurado es una hipótesis; el
sistema ya vivió una pérdida de historial de repos antes del volumen
durable.

## Decisión

Beat `workers.restore_drill` (día 2 de cada mes, 04:30 UTC): toma el
último bundle de `backup_root`, ejecuta la verificación estructural
existente y, solo si pasa, restaura el dump a una base EFÍMERA
(`drill_<timestamp>`, pg_restore, eliminada siempre al terminar) contando
filas de tablas clave (organizations, plans, executions). Restaurar cero
filas cuenta como fallo. El resultado se notifica SIEMPRE
(`restore_drill_result`, señal de plataforma) — éxito con conteos o fallo
con el motivo: un drill silencioso sería peor que ninguno.

## Consecuencias

- Prueba mensual real de que el desastre es recuperable, con evidencia
  (conteos) en el inbox del System Admin.
- Reuso de la verificación existente; la DB efímera aísla el drill del
  stack (sin tocar datos vivos) y se limpia incluso si el restore revienta.
