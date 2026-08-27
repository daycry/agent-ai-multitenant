---
adr_id: "0078"
title: "Bucles cognitivos de fondo del Córtex: reflexión, curiosidad autónoma y gobierno de coste/egress"
status: accepted
date: 2026-06-22
authors: [claude-opus, workflow-diseno-cortex]
plan_referenced: cortex-system-owner
docs_language: es
related: ["0074", "0075", "0076", "0077"]
supersedes: []
---

# ADR 0078 — Bucles cognitivos de fondo del Córtex

> **Estado: `accepted`** (frontmatter desde el **2026-07-12**, commit `affb048e`;
> banner corregido el 2026-07-27, decía `proposed`). La fecha del `date:` es el
> 2026-06-22 y es la de REDACCIÓN, no la de aceptación: entre una y otra el ADR
> estuvo veinte días en `proposed`. El banner las confundía. Introduce comportamiento **autónomo** —el córtex actúa cuando nadie habla— y consumo de LLM/egress no disparado por el owner, así que lo aprobado incluye el **kill-switch** `cortex.autonomy_enabled`, que arranca APAGADO: las tres entradas del beat tickean siempre pero salen no-op mientras esté off. Cadencias en `WORKERS_CORTEX_{CURIOSITY,REFLECTION,MAINTENANCE}_CRON`.

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

## Estado de implementación (2026-07-12)

> ⚠️ **Este matiz está VENCIDO**, comprobado contra el código el 2026-08-19: lo que dice de la
> REFLEXIÓN (sin budget, sin saciado de `coherence`, no idempotente) y lo que dice de la
> CURIOSIDAD (sin tope en USD, sin owner-approval gate, sin métricas) ya no es cierto. Ver
> [§ Estado de implementación (2026-08-19 — la reflexión de F3)](#estado-de-implementación-2026-08-19--la-reflexión-de-f3)
> abajo, con evidencia `fichero:línea`. Se conserva el texto original porque describe el estado
> real en su fecha y explica por qué se abrió la revisión.
>
> **Matiz añadido el 2026-07-30, porque el párrafo de abajo se lee como «todo hecho» y el
> gobierno de este ADR no lo estaba.** Lo implementado se detalla en el plan
> [cortex-f4-autonomia](../roadmap/cortex-f4-autonomia.md) y su
> [changelog](../07-changelog/cortex-f4-autonomia.md); la reflexión y el mantenimiento salieron por
> los planes [F3](../roadmap/cortex-f3-identidad.md) y
> [F5](../roadmap/cortex-f5-voz-avatar.md). De los cuatro puntos que este ADR declara **«gobierno
> no negociable»**, la auditoría del [2026-07-27](../roadmap/gaps-cortex-2026-07-27.md) encontró
> tres incumplidos: no había tope de **coste en USD** (sólo de nº de búsquedas), no había
> **owner-approval gate**, y no existía **ninguna** de las cuatro métricas **OTEL**. A 2026-07-30
> las claves de settings y la capa USD del budget ya estaban escritas, pero
> `workers/cortex_curiosity.py` **todavía no las llamaba** (grep de
> `check_and_reserve|record_spend|cost_usd` → cero), y el bucle de **reflexión** no consulta budget
> alguno: el disparo manual desde `POST /owner/cortex/reflect` no mira ni budget ni kill-switch.
> Tampoco se sacia el drive `coherence` que el punto 1 promete, ni la reflexión marca lo procesado,
> así que **no es idempotente**: dos pasadas re-sintetizan los mismos turnos.
>
> Nada de esto puede gastar hoy porque `cortex.autonomy_enabled` sigue OFF. La consecuencia ⚠️ de
> arriba —«los caps y el kill-switch son parte del MVP del bucle, no un fast-follow»— es
> exactamente la que hay que satisfacer **con tests** antes de encenderlo. El estado vigente lo
> mandan el plan y sus tests, no este párrafo.

IMPLEMENTADO (fase F4 del cortex + tandas posteriores): los tres bucles beat existen y son idempotentes — `workers/cortex_reflection.py` (sintesis de insights + narrativa versionada + baseline clampeado), `workers/cortex_curiosity.py` (pursuits con kill-switch, budget caps en Redis, circuit-breaker `is_circuit_open` y gate de drive), `workers/cortex_maintenance.py` (decay, retention, snapshots). Ademas se anadieron `cortex_platform.py` (pulso de plataforma, 2026-07-12) y `cortex_initiative.py` (proactividad gated). La autonomia global sigue OFF (`cortex.autonomy_enabled`, decision del operador).

## Estado de implementación (2026-08-19 — la reflexión de F3)

**Anotado desde la casilla F3.7 del plan [cortex-f3-identidad](../roadmap/cortex-f3-identidad.md).**
De los cuatro puntos que este ADR llama «gobierno no negociable», el bucle **1
(reflexión)** los cumple hoy, y lo cumple **en el núcleo** —o sea también en el
disparo manual desde `POST /owner/cortex/reflect`, que era el hueco concreto que
denunciaba el matiz del 2026-07-30:

- **Budget cap**: `REFLECTION_DAILY_CAP = 12` pasadas/día por owner, ventana UTC,
  sobre el mismo esquema de claves de F4 (`cortex:budget:{owner}:reflection:{yyyymmdd}`)
  y con `kind` propio para no consumir la cuota de la curiosidad
  (`workers/cortex_reflection.py`, `_check_reflection_budget`). El gasto se contabiliza
  **por intento**, no por éxito: un modelo que devuelve basura consume tokens igual.
- **Kill-switch**: `cortex.autonomy_enabled` (OFF por defecto) se consulta en el mismo
  núcleo, antes de tocar identidad o LLM.
- **Idempotencia**: marca `metadata_.reflected_through`; dos pasadas seguidas ya no
  re-sintetizan los mismos 20 turnos.
- **Saciado de `coherence`**: el paso 1 de la «Decisión» («sacia `coherence`») existe —
  `_satisfy_coherence(...)`, con refresco del snapshot y de la caché de F2.

Acreditado por `tests/integration/test_cortex_f3_reflection.py` (16 tests: kill-switch,
budget agotado, consumo por pasada, fail-open que también consume, saciado, no-saciado en
fail-open, y las dos de idempotencia).

**Sobre el resto del gobierno, comprobado también hoy** —para no repetir en esta misma
sección el error que la abrió—: el matiz del 2026-07-30 daba por incumplidos el
owner-approval gate, el tope en USD y las métricas de la **curiosidad**, y eso también ha
cambiado. `workers/cortex_curiosity.py` llama hoy a `check_and_reserve` (:279) y
`record_spend` (:372) con `cost_usd` real, y respeta el gate parando en `selected` con
`approved IS NULL` (:303); las cuatro métricas del plan las publica
`workers/cortex_curiosity_metrics.py` por el **textfile-collector de node-exporter**, que
no es la instrumentación OTEL que este ADR pedía —divergencia a declarar donde toque— pero
sí deja las series. El estado exacto de F4 lo mandan su plan y su
[changelog](../07-changelog/cortex-f4-autonomia.md), no este párrafo.

Lo que **no** tiene métricas es el bucle de **reflexión**: no publica ninguna serie. Y
`cortex.autonomy_enabled` sigue OFF por decisión del operador, no por un hueco de gobierno
de la reflexión.

Sobre el `status`: este ADR se queda en **`accepted`**, no en un `accepted-f3`. El corpus
no usa estados por fase —el `accepted-f0` del ADR 0074 es el único, y por una razón
histórica escrita en él—, así que inventar un valor nuevo aquí crearía la ambigüedad que
aquel banner explica que se conserva a propósito. La trazabilidad por fase la dan el plan
y su changelog.
