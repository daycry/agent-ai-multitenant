---
plan_id: cortex-fases
title: "Córtex del system_owner — índice de planes por fase"
completed_at: null
status: pending_human_validation
docs_language: es
---

# Córtex del `system_owner` — índice de fases

## Resumen

Este documento no entrega código: es el **índice de las seis fases** del córtex
(F0 + F1..F5) y el orden en que se apilan. Su entrada de changelog existe porque
el índice también tiene un estado, y llevaba meses desactualizado mientras las
fases que indexa se implementaban.

Trazabilidad de lo entregado, una entrada por fase:

| Fase | Qué entrega                                               | Changelog                                                                 |
| ---- | --------------------------------------------------------- | ------------------------------------------------------------------------- |
| F0   | Rol `system_owner` (migración 0091, claim `own`, `/me`)   | [mejoras-2026-06-chat-coste-cortex](mejoras-2026-06-chat-coste-cortex.md) |
| F1   | Córtex conversacional + hilo persistente + recall híbrido | [cortex-f1-memoria-cognitiva](cortex-f1-memoria-cognitiva.md)             |
| F2   | Motor afectivo PAD + distilador + Panel de Mente          | [cortex-f2-afectivo](cortex-f2-afectivo.md)                               |
| F3   | Identidad versionada + reflexión periódica                | [cortex-f3-identidad](cortex-f3-identidad.md)                             |
| F4   | Curiosidad, budget, circuit-breaker, kill-switch          | [cortex-f4-autonomia](cortex-f4-autonomia.md)                             |
| F5   | Voz/avatar afectivo + olvido reversible                   | [cortex-f5-voz-avatar](cortex-f5-voz-avatar.md)                           |
| —    | Capa posterior: self-model unificado ("identidad real")   | [cortex-identidad-real](cortex-identidad-real.md)                         |

Las migraciones se encadenaron de verdad al implementar (los planes proponían
`0092` como marcador de posición en todos): F1=0092, F2=0093, F3=0094, F4=0095
(+0103 para `surfaced`). F5 no añadió tablas.

## Lo que este índice enseñó por las malas

El índice quedó en `in_progress` el 2026-06-23 y **no se tocó** mientras las
cinco fases se completaban (21 commits entre 2026-06-24 y 2026-07-06). Durante
esas dos semanas, los cinco planes de fase seguían mostrando el banner "GATED —
NO IMPLEMENTAR" con el código ya desplegado. Se corrigió el 2026-07-06, en una
auditoría de estado del roadmap que existía precisamente porque el estado no era
fiable.

Es el mismo patrón que documenta
[`verificar-antes-de-implementar.md`](../03-guides/verificar-antes-de-implementar.md)
§1: un plan «pendiente» miente más de lo que parece, y el coste no lo paga quien
no actualiza, lo paga quien lo lee después.

## Desactualización del cuerpo del índice — corregida el 2026-07-30

La sección **Gating** del documento decía: «Todas las fases F1-F5 siguen
`proposed` en sus ADR». Comprobado ADR por ADR, eso hacía tiempo que no era
cierto: **0073, 0075, 0076, 0077, 0078 y 0080 están `accepted`** en su
frontmatter, y **0074 estaba `accepted-f0`** — el único ADR del repo con ese
valor, normalizado después a `accepted` (ver la nota del 2026-08-27, más abajo).

Corregido:

- La sección **Gating** del índice pasa a decir la verdad: el gate **por fase**
  ya no existe (se levantó el 2026-06-23), y lo que sigue gated es la
  **autonomía**, por interruptor (`cortex.autonomy_enabled`, OFF) y no por fase.
  Se añade además una tabla que separa **«implementada»** de **«cerrada»**, que
  es la confusión que este índice venía alimentando.
- El **banner del ADR 0074** ya no declara «F1-F5 `proposed` (gated)».
- El `accepted-f0` del frontmatter del 0074 se conservó entonces, con su razón
  escrita en el propio ADR: ese ADR se aprobó **en dos tiempos** (cimiento F0
  primero, excepción a RLS después) y el valor lo registraba. No se ha inventado
  un `accepted-f5`: el corpus sólo usa `accepted`, y las fases se trazan por su
  plan y su changelog, no por el `status` del ADR.

  > **VENCIDO el 2026-08-27: el `accepted-f0` ya no existe.** El operador aprobó
  > normalizarlo a `accepted` en el mismo cambio que reparó el cuerpo del ADR.
  > Se comprobó que no era un estado sino una nota histórica en el campo
  > equivocado: fuera del vocabulario de estados del repo y con **cero
  > consumidores** (`AdrMeta.status` es texto libre; ningún `.py`/`.yml`/`.sh`/
  > `.ts` lo lee). No gateaba nada — sólo obligaba a diez documentos, éste
  > incluido, a explicar por qué existía. La traza de la aprobación en dos
  > tiempos sigue donde se lee de verdad: el banner del ADR 0074.

## Estado de cierre

El índice no puede cerrarse antes que las fases que indexa, y **F2, F3, F4 y F5
tienen casillas abiertas con hueco identificado** (inventario:
[gaps-cortex-2026-07-27.md](../roadmap/gaps-cortex-2026-07-27.md)). La más
importante de todas, por si alguien tiene la tentación de cerrar en falso: F4 no
tiene owner-approval gate ni tope de gasto en USD, y `cortex.autonomy_enabled`
sigue OFF por eso.

## PR

- _pendiente_
