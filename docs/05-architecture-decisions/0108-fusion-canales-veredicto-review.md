---
id: 0108
title: Fusión (o no) de los dos canales de veredicto de review
status: accepted
date: 2026-07-09
deciders: [operador (pendiente), claude]
related: [hallazgo #7 (hallazgos-pendientes-2026-07-07), ADR 0084, ADR 0086, ADR 0087, ADR 0095, ADR 0096]
---

# ADR 0108 — Fusión (o no) de los dos canales de veredicto de review

## Contexto

El sistema tiene **dos canales de veredicto de review deliberados pero distintos**:

1. **Run reviewer externo** (prod-17, `RunRequest.review=True`): un loop multi-turn del
   graph del agent-runtime que cierra su prosa final con un tag
   `<verdict>approve|reject</verdict>`. El worker lo parsea con regex
   (`api_server.reviewer_bridge.parse_reviewer_output`, `_VERDICT_RE`) y lo aplica en
   `workers.execution._apply_review_verdict`. La fuente única del wire-format es
   `agent_runtime/review_contract.py` (H3), y el contrato cruzado runtime↔worker está
   clavado por `tests/unit/test_review_verdict_wire_contract.py` (los dos paquetes no
   pueden compartir la constante en runtime: el contenedor no lleva `api_server`).

2. **Self-review interna** de todo run normal: una llamada single-turn `model.review()`
   que anuncia la tool `submit_verdict(passed, feedback)` — forzada con `tool_choice` en
   los providers HTTP (F34) — con red de prosa 3-estados sólo como último recurso
   (`_parse_verdict`, ADR 0087).

La divergencia **no es un accidente**, es una respuesta a dos contextos de ejecución
distintos: la self-review es una llamada de un turno donde `tool_choice` sí es forzable
en HTTP; el run reviewer es un loop cuyo FINISH en `claude_sdk` es un turno de prosa
(un tool call fuerza `content=""` y perdería el resumen de review). Aun así, son **dos
formatos para el mismo concepto** (hallazgo #7): un mantenedor puede confundirlos, hay
dos parsers con semánticas de tolerancia distintas (`unknown → reject` defensivo en el
worker vs `inconclusive → humano` en el runtime), y los campos del reject viajan
distinto (tag `<rejection>` con 3 campos ricos vs `feedback` string).

Este ADR **no decidía la implementación** al redactarse (2026-07-09): enumeraba las
opciones y sus riesgos para que el operador eligiera, por ser una **decisión de
producto**. La elección está tomada — ver [Resolución](#resolución-2026-07-12) —
y el frontmatter refleja `accepted`.

## Resolución (2026-07-12)

**Aceptada la Opción C** (statu quo documentado) por delegación del operador
(«haz todas las implementaciones para que quede robusto/profesional» +
delegación 2026-06-17 de implementar ADRs proposed eligiendo la mejor opción).
Sus consecuencias YA están aplicadas: anclas cruzadas en ambos parsers
(`agent_runtime/providers.py::_review_from` y
`api_server/reviewer_bridge.py::parse_reviewer_output`), contrato de wire único
(`review_contract.py` + `test_review_verdict_wire_contract`) y semánticas de
tolerancia documentadas. Si en el futuro un canal diverge de verdad, reabrir
con la Opción B como candidata.

## Opciones

### Opción A — Todo por tool `submit_verdict`

El run reviewer externo también cierra con la tool (no con el tag).

- **Pros**: un único formato tipado (`passed` booleano) sin regex; elimina
  `_normalise_verdict` y la deriva de formato del lado worker; hereda la señal F32
  (corrupt/truncated) y el fail-closed; F34 ya demuestra fiabilidad en HTTP; `claude_sdk`
  ya emite `submit_verdict` por el host-tool MCP en el canal 2
  (`test_claude_sdk_review.py`).
- **Contras / riesgos de migración**:
  1. El run reviewer es un loop multi-turn, no una llamada `review()`: hay que registrar
     `submit_verdict` como **tool terminal** del loop (nueva ruta de parada) y propagar el
     veredicto estructurado en el payload del resultado del run hasta el worker (hoy sólo
     viaja `output` prosa) — toca el contrato run-result en **tres imágenes**
     (agent-runtime, workers, api-server).
  2. En `claude_sdk` un tool call fuerza `content=""` (`providers.py`): se pierde el
     resumen de review en prosa (deliverable para humano/auditoría) salvo que se añada un
     campo `summary` a la tool.
  3. El schema actual (`passed`+`feedback`) pierde los 3 campos ricos del reject
     (`failed_criterion`/`testreport_evidence`/`what_to_fix`) que alimentan
     `prior_review_feedback` al implementador (`run_contract.py`) — habría que ampliarlo.
  4. Convergencia calibrada: nudges/safeguards de review están redactados para «FINISH con
     tag en prosa»; rediseñar la condición de parada arriesga reabrir el bucle
     `max_iterations` del ADR 0095.
  5. ADR 0096 (precedencia asimétrica) y `_apply_review_verdict` deben reimplementarse
     sobre el campo estructurado con ventana **dual-format** para runs en vuelo.

### Opción B — Todo por tag en prosa

La self-review interna pide también el tag en el `content` (no la tool).

- **Pros**: 100% provider-agnóstico (no depende de tool-calling, flojo en Ollama con
  modelos pequeños); migración interna barata (`_REVIEW_SYSTEM` + `_review_from`); una
  sola gramática textual en `review_contract.py`.
- **Contras / riesgos**: **regresión directa de ADR 0086/0087** — la self-review se
  estructuró PORQUE la prosa fallaba (`max_review_retries_exceeded`, incendio de
  marcadores auth/JWT); se pierde F32 (un tag cortado por token-cap no tiene señal) y el
  fail-closed tipado; `tool_choice` da veredicto determinista en una llamada, la prosa
  reintroduce `inconclusive` → más escaladas a humano; multiplica la superficie de regex
  tolerante que ya demostró deriva entre modelos.

### Opción C — Status quo documentado (recomendada como mínimo viable)

No fusionar; documentar explícitamente los dos canales y su porqué, y normalizar sólo lo
que dé valor sin riesgo.

- **Pros**: **cero riesgo de regresión** — cada canal está calibrado a su contexto de
  ejecución (single-turn forzable con `tool_choice` vs loop multi-turn cuyo FINISH en
  `claude_sdk` es prosa); ambos son fail-closed, con tests y ADRs vigentes
  (0086/0087/0095/0096); la mitigación del riesgo real (deriva de wording) **ya está
  entregada** (`review_contract.py` + test cruzado H3); coste 0.
- **Contras**: dos formatos que un mantenedor puede confundir (origen del hallazgo #7);
  dos parsers con semánticas de tolerancia distintas a documentar; los campos del reject
  viajan distinto — normalización pendiente si se quiere una UI uniforme del feedback.

## Recomendación

**Opción C** como decisión por defecto: el hallazgo #7 es deuda de _claridad_, no un bug
— el riesgo real (deriva de wording entre los dos formatos) ya lo cierra la fuente única
`review_contract.py` + el test de contrato cruzado. Fusionar (A o B) toca el contrato
run-result en tres imágenes y arriesga la convergencia calibrada del reviewer (ADR 0095),
un coste alto para un beneficio de mantenibilidad. Si en el futuro se quiere UI uniforme
del feedback de reject, la sub-tarea de menor riesgo es **normalizar el modelo de datos
del feedback** (mapear el tag `<rejection>` de 3 campos al mismo shape que consume
`prior_review_feedback`) sin tocar el canal de transporte.

## Consecuencias

- Si se elige **C**: añadir esta explicación como comentario-ancla junto a los dos
  parsers (`reviewer_bridge.py` y `agent_runtime/providers.py::_review_from`) para que la
  divergencia sea intencional-y-visible, y cerrar el hallazgo #7 como «documentado».
- Si se elige **A** o **B**: este ADR pasa a `accepted` y marca `supersedes_partial`
  sobre ADR 0086 (canal) y ADR 0095 (convergencia del reviewer); la migración exige
  ventana dual-format en `_apply_review_verdict` + rebuild/redeploy coordinado de
  agent-runtime, workers y api-server, y portar la regla de precedencia del ADR 0096
  («un approve de un run no-`done` nunca cierra la task») al nuevo formato.
