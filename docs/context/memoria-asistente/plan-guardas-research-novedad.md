---
name: plan-guardas-research-novedad
description: "Plan guardas-research-por-novedad (2026-07-03) IMPLEMENTADO y desplegado — guardas por novedad, digests de lecturas, safeguard_stats, workers-aux; pendientes F2/F3 (e2e+métricas) y decisión ADR 0097."
metadata:
  node_type: memory
  type: project
  originSessionId: 75127a11-d792-4ccf-aaf9-63b6eb2823b6
---

2026-07-03 (madrugada): a petición del operador («que las lecturas legítimas no fuercen write tool; parar al releer X veces el mismo fichero; provider-agnóstico; tareas de solo-análisis contempladas»), plan formal en `docs/roadmap/guardas-research-por-novedad.md` (status in_progress, 10/12 checkboxes) — commit `d034122`, imagen `agent-runtime:v1` reconstruida (los runs nuevos la usan sin tocar workers).

**Hecho:** per-target read_counts (nudge nominal a la 3.ª, backstop a la 5.ª — caza patrón intercalado), racha ESTÉRIL sustituye al streak ciego de 5 (explorar N ficheros nuevos = cero fricción; errores de lectura = estériles sin sumar novedad), retirado `_DISTINCT_READ_LIMIT=22`, límite duro relativo al presupuesto (`_sterile_hard_limit`: 25 %, suelo 10), sticky GUIDANCE se limpia con progreso, invariante D3 intacto; `safeguard_stats` en el step de finalize (steps_log, medible por SQL); digests LRU (20) de lecturas en PROGRESS; **workers-aux** en compose (test/review/privileged — cura la auto-inanición del pool que mataba stack_exec por ReadTimeout a 300 s, run 019f252e, y revive el backup diario); margen httpx run_stack +180 s.

**Pendiente:** F2 (relanzar «Tests de feature» y validar e2e con la imagen nueva — el operador pausó el monitor CI4), F3 (métricas vs baseline por SQL sobre safeguard_stats), decisión del ADR 0097 (sesión SDK persistente, proposed), 2 anomalías sin diagnosticar (tasks blocked con run done/running — transiciones raras vistas al pausar), y la pregunta abierta del operador: ¿exponer `timeout_s` de stack_exec por proyecto en la UI? (hoy: 600 default + override por-llamada del agente + tope 3600).

Relacionado: [[auditoria-runs-2026-07-02-remediacion]], [[supervision-runs-autofix-plataforma]].
