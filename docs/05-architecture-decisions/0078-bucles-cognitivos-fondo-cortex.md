---
adr_id: "0078"
title: "Bucles cognitivos de fondo del Córtex: reflexión, curiosidad autónoma y gobierno de coste/egress"
status: proposed
date: 2026-06-22
authors: [claude-opus, workflow-diseno-cortex]
plan_referenced: cortex-system-owner
docs_language: es
related: ["0074", "0075", "0076", "0077"]
supersedes: []
---

# ADR 0078 — Bucles cognitivos de fondo del Córtex

> **Estado: `proposed`** — introduce comportamiento **autónomo** (el córtex actúa cuando nadie habla) y consumo de LLM/egress no disparado por el owner. **Requiere aprobación del operador** y kill-switch.

## Contexto

Una mente reflexiona y aprende cuando no interactúa. Hoy no existe ningún job periódico de memoria. La curiosidad ("ganas de aprender DE MÍ") solo es real si hay un motor que la convierta en acción autónoma — pero la acción autónoma con LLM + búsqueda web exige gobierno estricto de coste y seguridad.

## Decisión

Subsistema **Celery beat NUEVO** con tres bucles, todos idempotentes (marcan lo procesado en `metadata_`) y que **NUNCA tocan tablas de tenant**:

1. **Reflexión** (cada N horas/M turnos): lee episodios, sintetiza insights (`semantic/reflection`), reescribe la narrativa de identidad y deriva `traits`/`baseline` **clampeado** (bound por ciclo + diff versionado); sacia `coherence`.
2. **Curiosidad**: si `curiosity` baja, elige tema de las entities que el owner menciona → WebSearch → digest → memoria (`learning`) → satisfacción afectiva; inicia el tema en el próximo encuentro.
3. **Mantenimiento**: decay del mood, `retention_score`, olvido (ADR 0077), snapshots.

**Gobierno (no negociable):**

- **Budget caps** de coste/llamadas por bucle y día en **Redis**; al superarse, el bucle se detiene.
- **Circuit-breaker** por comportamiento anómalo.
- **(Opcional) owner-approval gate** para las primeras persecuciones autónomas de curiosidad con WebSearch.
- Observabilidad **OTEL** de coste/latencia por bucle.

## Consecuencias

- ✅ Materializa la "mente" proactiva y curiosa con límites duros.
- ⚠️ Comportamiento autónomo = superficie de coste/seguridad nueva → los caps y el kill-switch son parte del MVP del bucle, no un fast-follow.
- ⚠️ Depende de ADR 0075 (drives) y 0076 (egress/razonamiento).

## Estado de implementación (2026-07-06 — plan "identidad real")

El "inicia el tema en el próximo encuentro" del bucle de curiosidad (punto 2)
estaba sin cablear (`surfaced_at` jamás se escribía). Implementado: el
self-context inyecta 1 pursuit `digested` por turno (con el digest de su
memoria `learning`) y lo marca `surfaced` en la MISMA transacción del turno
(migración 0103 añade el estado al CHECK); endpoint
`GET /owner/cortex/curiosity/pursuits` + tarjeta "Lo que está aprendiendo" en
el Panel de Mente. La reflexión produce además el `owner_model`
(`relationship_model` + memorias `kind='owner_model'`), cerrando el "aprender
DE MÍ". Ver [cortex-identidad-real](../roadmap/cortex-identidad-real.md).
